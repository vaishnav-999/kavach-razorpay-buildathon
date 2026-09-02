"""The neutral LLM types and the provider Protocol (BUILD_SPEC §11.7).

This is the boundary. `agent.py`, `executor.py` and `tools.py` may use only
what is defined here; the four adapter modules beside this file are the only
code in the project that knows what an LLM is (invariant 11, test 22).

Nothing here imports a provider SDK, and nothing here talks to a network.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar

# §11.4 — one value, used by every adapter. The agent produces one short line
# plus tool calls; a larger budget buys nothing and costs on every turn.
MAX_TOKENS = 1024

# §11.7 — the retry schedule, verbatim: 1s, 2s, 4s, 8s plus 0–500 ms jitter,
# four attempts in total, then give up. Free tiers rate-limit routinely.
RETRY_DELAYS_SECONDS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)
MAX_ATTEMPTS = 4
JITTER_SECONDS = 0.5

# The ladder above spans about 15 seconds, which is shorter than a per-minute
# quota window: against a free-tier 429 all four attempts land inside the same
# blocked minute and the run dies having learned nothing. When the provider
# tells us how long to wait, that hint wins over the ladder.
#
# Capped so that a wrong or hostile value cannot park a request indefinitely.
# 65 s clears a one-minute window with a second to spare, which is the longest
# wait that is ever useful here.
MAX_SINGLE_WAIT_SECONDS = 65.0

# §11.7 — the HTTP statuses worth retrying. A 400 or a 401 is a bug in our
# request or our key; retrying either just burns the rate limit.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503})


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema, provider-neutral


@dataclass
class ToolCall:
    id: str  # provider call id, or generated
    name: str
    arguments: dict
    # Opaque provider passthrough. Written by the adapter that produced this
    # call and read only by that same adapter when it rebuilds history; it must
    # be JSON-serialisable so a cassette can record and replay it. Nothing
    # outside `app/buyer/llm/` reads, interprets or depends on the contents,
    # and nothing in this module knows what any provider puts in it
    # (invariant 11).
    #
    # It exists because some providers attach state to a tool call that has to
    # come back byte-identical on the next turn, and a neutral layer that
    # rebuilds history from name and arguments alone would throw it away.
    provider_metadata: dict = field(default_factory=dict)


@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None  # set when role == "tool"
    # Not part of §11.7's dataclass. The Gemini function_response part is keyed
    # by tool *name* rather than by id, so the adapter needs the name that goes
    # with `tool_call_id`; carrying it here beats rebuilding an id→name map in
    # every adapter.
    name: str | None = None
    # Same contract as `ToolCall.provider_metadata` above: opaque, adapter-
    # owned, JSON-serialisable, never read outside `app/buyer/llm/`.
    provider_metadata: dict = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall]
    stop_reason: Literal["end_turn", "tool_use", "max_tokens", "error"]
    raw: dict | None = None  # provider payload, for the cassette
    # Seconds this call spent *waiting* rather than working: retry backoff and
    # any configured throttle. Neutral — it says how long the transport made us
    # wait, not who made us wait or why.
    #
    # `agent.py` adds it back to the §11.4 wall clock. A session sitting on a
    # rate-limit delay the provider itself asked for is not a stuck session,
    # and killing it would report a limit breach for time the agent never got
    # to use. The budget stays a budget for agent work.
    transport_wait_seconds: float = 0.0


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec]
    ) -> LLMResponse: ...


class LLMUnavailable(RuntimeError):
    """The provider could not be reached, after the full §11.7 retry schedule.

    This is a transport outcome and **never** a purchase outcome. The router
    streams it as `{"type": "error", "code": "LLM_UNAVAILABLE"}`; no state
    moves, no order exists, and nothing downstream may read it as a block, an
    approval or a failure to pay.
    """

    def __init__(
        self, message: str, *, provider: str, model: str, attempts: int
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.attempts = attempts


class CassetteExhausted(RuntimeError):
    """A replay ran past the end of its cassette (§11.8)."""


T = TypeVar("T")


def call_with_backoff(
    call: Callable[[], T],
    *,
    is_retryable: Callable[[BaseException], bool],
    provider: str,
    model: str,
    retry_hint: Callable[[BaseException], float | None] | None = None,
    on_wait: Callable[[float], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run `call`, retrying transport failures on the §11.7 schedule.

    `is_retryable` is the adapter's own classifier — each SDK spells a 429
    differently — and it is the only thing that decides. Per §11.9(5) this is
    for transport errors only: there is no retry anywhere in this system for
    "the model gave an answer I did not like."

    `retry_hint` is the adapter's reader for a server-supplied delay: Google
    puts a `RetryInfo` in the error body, Anthropic puts `retry-after` in a
    header, and this module knows about neither. When it returns a number that
    number is waited instead of the ladder step, because the service telling us
    when its window reopens beats us guessing. Jitter is added only to the
    ladder — a hint is honoured as given, and every wait is capped at
    `MAX_SINGLE_WAIT_SECONDS`.

    `on_wait` is called with each slept interval so the adapter can report the
    total as `LLMResponse.transport_wait_seconds`.
    """
    last: BaseException | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return call()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            if not is_retryable(exc):
                raise
            last = exc
            if attempt == MAX_ATTEMPTS - 1:
                break

            hinted = retry_hint(exc) if retry_hint is not None else None
            if hinted is not None and hinted > 0:
                delay = min(hinted, MAX_SINGLE_WAIT_SECONDS)
            else:
                delay = min(
                    RETRY_DELAYS_SECONDS[attempt] + random.uniform(0, JITTER_SECONDS),
                    MAX_SINGLE_WAIT_SECONDS,
                )

            if on_wait is not None:
                on_wait(delay)
            sleep(delay)

    raise LLMUnavailable(
        f"{provider} ({model}) did not answer after {MAX_ATTEMPTS} attempts: {last}",
        provider=provider,
        model=model,
        attempts=MAX_ATTEMPTS,
    ) from last


def status_code_of(exc: BaseException) -> int | None:
    """Best-effort HTTP status from an SDK exception.

    Each SDK names it differently and some carry it only in the message. A
    missing status is not treated as retryable by any caller.
    """
    for attribute in ("status_code", "code", "status"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def tool_result_payload(value: Any) -> dict:
    """Normalise a tool result into the dict shape both SDKs want back."""
    return value if isinstance(value, dict) else {"result": value}
