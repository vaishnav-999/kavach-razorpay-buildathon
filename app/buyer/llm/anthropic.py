"""Anthropic adapter (BUILD_SPEC §11.7).

The second live adapter. `tool_use` blocks come back, `tool_result` blocks go
in, and the system prompt is the `system` parameter rather than a message.

Anthropic returns real tool-call ids, so unlike the Gemini adapter this one
generates nothing: `tool_result.tool_use_id` is the id the model sent.

It writes and reads no `provider_metadata`. The field is a shared, opaque
passthrough, and an entry written by another adapter is none of this one's
business — `base.py` special-cases no provider, and neither does this file.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic

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


def _is_retryable(exc: BaseException) -> bool:
    """429, 500, 502, 503 and read timeouts (§11.7). Nothing else."""
    if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return status_code_of(exc) in RETRYABLE_STATUS_CODES
    return False


def _retry_hint(exc: BaseException) -> float | None:
    """How long Anthropic says to wait, or None.

    Where Google puts a `RetryInfo` in the body, Anthropic puts it in a header:
    `retry-after-ms` when it has millisecond precision, `retry-after` in whole
    seconds otherwise. `APIStatusError` carries the response, so both are
    reachable.
    """
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    try:
        return float(headers["retry-after-ms"]) / 1000.0
    except (KeyError, TypeError, ValueError):
        pass
    try:
        return float(headers["retry-after"])
    except (KeyError, TypeError, ValueError):
        return None


def _to_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Neutral messages → the Anthropic wire shape.

    Consecutive tool results are merged into one `user` message: the API wants
    every `tool_result` answering a single assistant turn in one message, and
    §11.7 requires parallel results to return together.
    """
    out: list[dict[str, Any]] = []

    for message in messages:
        if message.role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id or "",
                "content": json.dumps(
                    tool_result_payload(message.content), ensure_ascii=False
                ),
            }
            if (
                out
                and out[-1]["role"] == "user"
                and isinstance(out[-1]["content"], list)
                and all(b.get("type") == "tool_result" for b in out[-1]["content"])
            ):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue

        if message.role == "user":
            if message.content:
                out.append({"role": "user", "content": message.content})
            continue

        blocks: list[dict[str, Any]] = []
        if message.content:
            blocks.append({"type": "text", "text": message.content})
        for call in message.tool_calls:
            blocks.append(
                {
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    "input": dict(call.arguments or {}),
                }
            )
        if blocks:
            out.append({"role": "assistant", "content": blocks})

    return out


def _to_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    # §11.7 — `input_schema`, where Gemini says `parameters`. Same JSON Schema.
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.parameters,
        }
        for spec in tools
    ]


class AnthropicProvider:
    """Implements the §11.7 `LLMProvider` Protocol."""

    name = "anthropic"

    def __init__(self, *, api_key: str, model: str) -> None:
        self.model = model
        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec]
    ) -> LLMResponse:
        waited = 0.0

        def record_wait(seconds: float) -> None:
            nonlocal waited
            waited += seconds

        response = call_with_backoff(
            lambda: self._client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                # §11.7 — the system prompt is a parameter, never a turn.
                system=system,
                messages=_to_messages(messages),
                tools=_to_tools(tools),
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
    texts: list[str] = []
    tool_calls: list[ToolCall] = []

    for block in getattr(response, "content", None) or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            texts.append(block.text)
        elif block_type == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=dict(block.input or {}),
                )
            )

    stop = getattr(response, "stop_reason", None) or ""
    if tool_calls:
        stop_reason = "tool_use"
    elif stop == "max_tokens":
        stop_reason = "max_tokens"
    else:
        stop_reason = "end_turn"

    return LLMResponse(
        text="\n".join(texts).strip() or None,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        raw={"stop_reason": stop, "model": getattr(response, "model", None)},
    )
