"""Record and replay (BUILD_SPEC §11.8).

The largest token saving in the build. M7 and M9 run entirely on replay, at
zero cost and with no network.

Cassettes are development fixtures and demo insurance, not fakery: a replayed
run records `llm_provider = "cassette"` on the session row and in the audit
trail, so the trail always says which it was.

Matching is hash first, position second (§11.8). Positional fallback is not
sloppiness — the system prompt gets edited while the frontend is built, which
changes every hash in the file, and a cassette that stops working the moment
you touch a docstring is a cassette nobody keeps up to date.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.buyer.llm.base import (
    CassetteExhausted,
    LLMProvider,
    LLMResponse,
    Message,
    ToolCall,
    ToolSpec,
)

CASSETTE_SUFFIX = ".json"


def cassette_path(directory: str, name: str) -> Path:
    # A cassette name comes from an API body, so it never leaves its directory.
    safe = Path(name).name
    if not safe.endswith(CASSETTE_SUFFIX):
        safe += CASSETTE_SUFFIX
    return Path(directory) / safe


def _without_provider_metadata(message: Message) -> dict[str, Any]:
    """A message as the hash sees it: opaque provider state left out.

    `provider_metadata` carries per-call transport state — Gemini's encrypted
    `thought_signature`, for one — that differs on every run. Hashing it would
    mean no recorded request ever matched again and every replay fell through
    to the positional path, which is the fallback rather than the mechanism.
    It is excluded because it is not part of the logical request.
    """
    data = asdict(message)
    data.pop("provider_metadata", None)
    for call in data.get("tool_calls") or []:
        call.pop("provider_metadata", None)
    return data


def request_hash(
    *, system: str, messages: list[Message], tools: list[ToolSpec]
) -> str:
    """sha256 of canonical_json({system, messages, tools}) (§11.8)."""
    payload = {
        "system": system,
        "messages": [_without_provider_metadata(m) for m in messages],
        "tools": [asdict(t) for t in tools],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _response_to_dict(response: LLMResponse) -> dict[str, Any]:
    return {
        "text": response.text,
        "tool_calls": [asdict(c) for c in response.tool_calls],
        "stop_reason": response.stop_reason,
    }


def _response_from_dict(data: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        text=data.get("text"),
        tool_calls=[
            ToolCall(
                id=str(c.get("id") or ""),
                name=str(c.get("name") or ""),
                arguments=dict(c.get("arguments") or {}),
                # Recorded and replayed verbatim, so a replayed run hands the
                # provider back exactly what the live run did.
                provider_metadata=dict(c.get("provider_metadata") or {}),
            )
            for c in data.get("tool_calls") or []
        ],
        stop_reason=data.get("stop_reason") or "end_turn",
        raw=None,
    )


class CassettePlayer:
    """`CASSETTE_MODE=replay`. No network, ever."""

    name = "cassette"

    def __init__(self, *, directory: str, cassette: str) -> None:
        self.cassette = cassette
        self._path = cassette_path(directory, cassette)
        if not self._path.exists():
            raise CassetteExhausted(
                f"Cassette {self._path} does not exist. Record it first with "
                "CASSETTE_MODE=record (§11.8)."
            )
        data = json.loads(self._path.read_text(encoding="utf-8"))
        self._turns: list[dict[str, Any]] = list(data.get("turns") or [])
        self.model = str(data.get("model") or "")
        self.recorded_provider = str(data.get("provider") or "")
        self._index = 0

    def complete(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec]
    ) -> LLMResponse:
        wanted = request_hash(system=system, messages=messages, tools=tools)

        # 1. Exact request match.
        for turn in self._turns:
            if turn.get("request_hash") == wanted:
                self._index = int(turn.get("index", self._index)) + 1
                return _response_from_dict(turn.get("response") or {})

        # 2. Positional fallback.
        if self._index < len(self._turns):
            turn = self._turns[self._index]
            self._index += 1
            return _response_from_dict(turn.get("response") or {})

        # 3. Past the end.
        raise CassetteExhausted(
            f"Cassette {self.cassette!r} has {len(self._turns)} turn(s); the "
            f"agent asked for turn {self._index}. Re-record it."
        )


class CassetteRecorder:
    """`CASSETTE_MODE=record`. Live calls, written down as they happen.

    The file is rewritten after every turn rather than at the end, so a run
    that dies at turn 9 still leaves eight usable turns behind.
    """

    def __init__(
        self,
        inner: LLMProvider,
        *,
        directory: str,
        cassette: str,
        intent: str | None = None,
    ) -> None:
        self._inner = inner
        self.name = inner.name
        self.model = inner.model
        self.cassette = cassette
        self._path = cassette_path(directory, cassette)
        self._intent = intent
        self._turns: list[dict[str, Any]] = []

    def complete(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec]
    ) -> LLMResponse:
        response = self._inner.complete(system=system, messages=messages, tools=tools)
        self._turns.append(
            {
                "index": len(self._turns),
                "request_hash": request_hash(
                    system=system, messages=messages, tools=tools
                ),
                "response": _response_to_dict(response),
            }
        )
        self._write()
        return response

    def set_intent(self, intent: str) -> None:
        self._intent = intent
        if self._turns:
            self._write()

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "name": self._path.stem,
            "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "provider": self._inner.name,
            "model": self._inner.model,
            "intent": self._intent,
            "turns": self._turns,
        }
        self._path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8"
        )
