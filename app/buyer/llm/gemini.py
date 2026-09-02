"""Gemini adapter (BUILD_SPEC §11.7).

One of the two live adapters, and one of the four modules allowed to import an
LLM SDK. Everything it hands back is a neutral `LLMResponse`; nothing above
this line knows that Gemini exists.

Three translation details §11.7 calls out, because each of them silently
corrupts a tool loop if you get it wrong:

* the system prompt is `system_instruction`, not a message;
* Gemini returns **no tool-call ids**, so we generate one per call and keep the
  name alongside it so results pair back correctly;
* Gemini may return several `function_call` parts in one response — they are
  executed in order and their results all go back in a single turn.

A fourth arrived after BUILD_SPEC was written. **Gemini 3 attaches an encrypted
`thought_signature` to `functionCall` parts and rejects the next turn with 400
INVALID_ARGUMENT unless that signature comes back inside the very part it was
issued on.** The SDK handles this for you only if you hand its own response
content object straight back, which a neutral-types layer that rebuilds history
from names and arguments cannot do.

So this adapter keeps the whole part. `Part.model_dump(mode="json")` is
lossless — the signature is `bytes`, and pydantic round-trips it through
urlsafe-base64 — so the exact part is stashed in `ToolCall.provider_metadata`
and restored with `Part.model_validate()` on the way back in. Nothing is
reassembled by hand, which is what makes the API's rules hold automatically:

* only the first of a set of parallel calls carries a signature, and it stays
  on that part because each part is restored individually;
* no part is ever merged with another, so a signature can neither be dropped
  nor doubled up;
* the model turn is all `functionCall` parts and the following turn is all
  `functionResponse` parts, never interleaved.

`provider_metadata` is opaque above this file. `agent.py`, `tools.py` and the
executor neither read it nor know it exists (invariant 11).
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from app.buyer.llm.base import (
    MAX_TOKENS,
    RETRYABLE_STATUS_CODES,
    LLMResponse,
    Message,
    ToolCall,
    ToolSpec,
    call_with_backoff,
    status_code_of,
    tool_result_payload,
)
from app.config import settings

# Google's error body carries this alongside a 429.
RETRY_INFO_TYPE = "type.googleapis.com/google.rpc.RetryInfo"

# The optional self-throttle (`GEMINI_MIN_CALL_INTERVAL_SECONDS`) is per
# process, and SSE turns run in a threadpool, so the last-call stamp is shared
# state and needs a lock.
_throttle_lock = threading.Lock()
_last_call_at: float = 0.0


# The key `provider_metadata` is stored under. Namespaced, because the field is
# shared with every other adapter and none of them may read another's entry.
PART_KEY = "gemini.part"


def _new_call_id() -> str:
    # §11.7 — Gemini returns no ids, so we mint them.
    return f"call_{uuid.uuid4().hex[:8]}"


def _stash(part: types.Part) -> dict:
    """The exact part, in a JSON-safe form a cassette can carry.

    `mode="json"` is what makes this work: `thought_signature` is `bytes`, and
    pydantic encodes it as urlsafe-base64 rather than failing or lossily
    decoding it as UTF-8.
    """
    return {PART_KEY: part.model_dump(mode="json", exclude_none=True)}


def _restore(call: ToolCall) -> types.Part:
    """The part exactly as Gemini issued it, signature included.

    Falls back to building a fresh `function_call` part when there is nothing
    stashed — a replayed cassette recorded before this field existed, or a
    provider that never sent one. A fabricated part carries no signature, which
    is correct: inventing one would be worse than omitting it.
    """
    stashed = (call.provider_metadata or {}).get(PART_KEY)
    if stashed:
        return types.Part.model_validate(stashed)
    return types.Part(
        function_call=types.FunctionCall(
            name=call.name, args=dict(call.arguments or {})
        )
    )


def _is_retryable(exc: BaseException) -> bool:
    """429, 500, 502, 503 and read timeouts (§11.7). Nothing else."""
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return True
    if isinstance(exc, errors.APIError):
        return status_code_of(exc) in RETRYABLE_STATUS_CODES
    return False


def _parse_duration(value: Any) -> float | None:
    """A protobuf duration string — `"27s"`, `"1.5s"` — as seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.endswith("s"):
        return None
    try:
        return float(value[:-1])
    except ValueError:
        return None


def _retry_hint(exc: BaseException) -> float | None:
    """How long Google says to wait, or None.

    A 429 body carries `error.details[]` with a `google.rpc.RetryInfo` entry
    holding `retryDelay`. That number is the only reliable signal for when a
    per-minute quota window reopens; the §11.7 ladder spans ~15 s and would
    otherwise spend all four attempts inside the same blocked minute.

    A `Retry-After` header is read as a fallback. Anything unparseable returns
    None and the caller falls back to the ladder.
    """
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        entries = (details.get("error") or {}).get("details") or []
        if isinstance(entries, list):
            for entry in entries:
                if (
                    isinstance(entry, dict)
                    and entry.get("@type") == RETRY_INFO_TYPE
                ):
                    seconds = _parse_duration(entry.get("retryDelay"))
                    if seconds is not None:
                        return seconds

    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is not None:
        try:
            return float(headers.get("retry-after"))
        except (TypeError, ValueError):
            return None
    return None


def _throttle() -> float:
    """Hold `GEMINI_MIN_CALL_INTERVAL_SECONDS` between calls. Returns seconds slept.

    Off by default (0). It exists so a free-tier per-minute quota can be traded
    against speed from `.env` alone, without touching code: the retry hint
    above recovers from a 429, and this avoids provoking one.
    """
    global _last_call_at
    interval = float(settings.GEMINI_MIN_CALL_INTERVAL_SECONDS or 0)
    if interval <= 0:
        return 0.0

    with _throttle_lock:
        wait = _last_call_at + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        else:
            wait = 0.0
        _last_call_at = time.monotonic()
    return wait


def _to_contents(messages: list[Message]) -> list[types.Content]:
    """Neutral messages → Gemini `Content` turns.

    Consecutive tool results collapse into one `user` turn: §11.7 requires all
    results from a parallel call to come back together, and Gemini rejects a
    turn whose `function_response` count does not match the calls it answers.
    """
    contents: list[types.Content] = []

    for message in messages:
        if message.role == "tool":
            part = types.Part(
                function_response=types.FunctionResponse(
                    name=message.name or "",
                    response=tool_result_payload(message.content),
                )
            )
            # Append to the open tool turn rather than starting a new one.
            if (
                contents
                and contents[-1].role == "user"
                and all(p.function_response is not None for p in contents[-1].parts or [])
            ):
                contents[-1].parts.append(part)
            else:
                contents.append(types.Content(role="user", parts=[part]))
            continue

        parts: list[types.Part] = []
        if message.content:
            parts.append(types.Part(text=message.content))
        # One part per call, restored individually. Never merged, never
        # reordered: the signature has to come back on the part it was issued
        # on, and on a set of parallel calls only the first one has one.
        for call in message.tool_calls:
            parts.append(_restore(call))
        if not parts:
            continue
        contents.append(
            types.Content(
                role="model" if message.role == "assistant" else "user", parts=parts
            )
        )

    return contents


def _to_tool(tools: list[ToolSpec]) -> list[types.Tool]:
    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=spec.name,
                    description=spec.description,
                    parameters=spec.parameters,
                )
                for spec in tools
            ]
        )
    ]


class GeminiProvider:
    """Implements the §11.7 `LLMProvider` Protocol."""

    name = "gemini"

    def __init__(self, *, api_key: str, model: str) -> None:
        self.model = model
        self._client = genai.Client(api_key=api_key)

    def complete(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec]
    ) -> LLMResponse:
        config = types.GenerateContentConfig(
            # §11.7 — the system prompt is configuration, not a turn the model
            # can be talked into treating as merchant data.
            system_instruction=system,
            tools=_to_tool(tools),
            max_output_tokens=MAX_TOKENS,
            # The SDK must never dispatch a tool itself. Every call in this
            # system goes through our dispatcher, which is what sets state and
            # writes the audit trail.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )
        contents = _to_contents(messages)

        waited = _throttle()

        def record_wait(seconds: float) -> None:
            nonlocal waited
            waited += seconds

        response = call_with_backoff(
            lambda: self._client.models.generate_content(
                model=self.model, contents=contents, config=config
            ),
            is_retryable=_is_retryable,
            provider=self.name,
            model=self.model,
            retry_hint=_retry_hint,
            on_wait=record_wait,
        )
        parsed = _parse(response)
        parsed.transport_wait_seconds = waited
        return parsed


def _parse(response: Any) -> LLMResponse:
    candidates = getattr(response, "candidates", None) or []
    candidate = candidates[0] if candidates else None
    content = getattr(candidate, "content", None)
    parts = list(getattr(content, "parts", None) or [])

    texts: list[str] = []
    tool_calls: list[ToolCall] = []
    for part in parts:
        # A thought summary is not an answer and must not be replayed back as
        # assistant text on the next turn.
        if getattr(part, "thought", False):
            continue
        if getattr(part, "function_call", None) is not None:
            call = part.function_call
            tool_calls.append(
                ToolCall(
                    id=getattr(call, "id", None) or _new_call_id(),
                    name=call.name or "",
                    arguments=dict(call.args or {}),
                    # The whole part, so the next turn can hand back exactly
                    # what arrived rather than an approximation of it.
                    provider_metadata=_stash(part),
                )
            )
        elif getattr(part, "text", None):
            texts.append(part.text)

    finish_reason = getattr(candidate, "finish_reason", None)
    finish = getattr(finish_reason, "name", None) or (
        str(finish_reason) if finish_reason else ""
    )

    if tool_calls:
        stop_reason = "tool_use"
    elif finish.upper().endswith("MAX_TOKENS"):
        stop_reason = "max_tokens"
    else:
        stop_reason = "end_turn"

    return LLMResponse(
        text="\n".join(texts).strip() or None,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        raw={"finish_reason": finish, "model_version": getattr(response, "model_version", None)},
    )
