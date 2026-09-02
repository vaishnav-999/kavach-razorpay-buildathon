"""The tool loop (BUILD_SPEC §11.1, §11.4).

A plain loop. No framework, no graph, no planner. `provider.complete()` is
called, tool calls come back, the dispatcher runs them, results go back in.

The state machine is in `state.py`, the ten tool implementations are behind
`executor.execute()`, and the context rules are in `history.py`. What is left
here is the sequence and the four hard limits.

Only the neutral types from `llm/base.py` appear below. This module has never
heard of Gemini.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from sqlalchemy.orm import Session

from app.audit import emit
from app.buyer import executor as tool_executor
from app.buyer.executor import ToolContext
from app.buyer.history import compact_json, prune, situation_brief
from app.buyer.llm.base import LLMProvider, LLMUnavailable, Message, ToolCall
from app.buyer.prompts import SYSTEM_PROMPT
from app.buyer.state import (
    TERMINAL_STATES,
    IllegalTransition,
    apply_transition,
    halt,
    reject_transition,
    utcnow,
)
from app.buyer.tools import TOOL_SPECS
from app.models import AgentSession

# §11.4 — the hard limits. They bound the blast radius of a runaway agent and
# the cost of a run at the same time.
MAX_TOOL_CALLS = 20
# **Deviation from §11.4, which says 120 seconds.** `gemini-3.6-flash` takes
# roughly 30 s per call, and a full run from intent to authorization needs
# about 220 s, so 120 halts legitimate sessions before they can reach the
# human — measured, not estimated. 300 leaves headroom and still kills a stuck
# session.
#
# This loosens a budget, not a bound. What the agent is *able* to do is capped
# by MAX_TOOL_CALLS and MAX_SUBMIT_ATTEMPTS, and both stay exactly as §11.4
# specifies: more seconds buys no extra tool call, no extra submit and no
# authority.
WALL_CLOCK_SECONDS = 300
MAX_SUBMIT_ATTEMPTS = 2


def run_turn(
    db: Session,
    session: AgentSession,
    *,
    provider: LLMProvider,
    content: str,
) -> Iterator[dict[str, Any]]:
    """One HTTP request's worth of agent. Yields the §11.6 SSE event dicts."""
    ctx = ToolContext(db=db, session=session)
    deadline = time.monotonic() + WALL_CLOCK_SECONDS
    messages = _opening_messages(db, session, content)

    yield {"type": "state", "state": session.state}

    while True:
        breach = _limit_breached(session, deadline)
        if breach is not None:
            yield from _limit_reached(db, session, **breach)
            return

        try:
            response = provider.complete(
                system=SYSTEM_PROMPT, messages=prune(messages), tools=TOOL_SPECS
            )
        except LLMUnavailable as exc:
            yield _llm_unavailable(db, session, exc)
            return
        except Exception as exc:  # noqa: BLE001 - surfaced, never swallowed
            halt(db, session, reason="provider_error")
            yield {"type": "error", "code": "AGENT_ERROR", "detail": str(exc)}
            yield _done(session)
            return

        # Time the transport made us wait — a rate-limit delay the provider
        # itself asked for, or a configured throttle — is given back to the
        # budget. The check above is only ever evaluated between turns, so it
        # can never fire mid-backoff; without this, though, a session that sat
        # out a documented 60 s window would be halted on the next pass and
        # reported as a limit breach for time the agent never got to use.
        # Nothing here knows which provider waited, or why.
        deadline += response.transport_wait_seconds

        if response.text:
            yield {
                "type": "thought" if response.tool_calls else "message",
                "text": response.text,
            }

        if not response.tool_calls:
            if response.stop_reason == "max_tokens":
                yield {
                    "type": "error",
                    "code": "AGENT_ERROR",
                    "detail": "The model reached its output budget before answering.",
                }
            session.ended_at = utcnow()
            db.add(session)
            db.commit()
            yield _done(session)
            return

        messages.append(
            Message(
                role="assistant",
                content=response.text,
                tool_calls=list(response.tool_calls),
            )
        )

        paused = False
        for call in response.tool_calls:
            events, stop = _run_one_call(db, session, ctx, call, messages)
            for event in events:
                paused = paused or event.get("type") == "awaiting_authorization"
                yield event
            if stop:
                return

        if paused:
            # The human has the next move. AWAITING_AUTHORIZATION -> AUTHORIZED
            # is not ours to make, so the loop ends rather than spinning.
            yield _done(session)
            return


# ── one tool call ─────────────────────────────────────────────────────────


def _run_one_call(
    db: Session,
    session: AgentSession,
    ctx: ToolContext,
    call: ToolCall,
    messages: list[Message],
) -> tuple[list[dict[str, Any]], bool]:
    """Execute one tool call. Returns (events, stop_the_loop)."""
    events: list[dict[str, Any]] = [
        {
            "type": "tool_call",
            "id": call.id,
            "name": call.name,
            "arguments": call.arguments,
        }
    ]

    if call.name == "submit_purchase":
        blocked, stop = _prepare_submit(db, session, ctx, call)
        events.extend(blocked)
        if stop:
            return events, True

    session.tool_call_count += 1
    db.add(session)
    db.commit()

    outcome = tool_executor.execute(ctx, call.name, call.arguments)

    events.append(
        {
            "type": "tool_result",
            "id": call.id,
            "name": call.name,
            "result": outcome.result,
        }
    )
    messages.append(
        Message(
            role="tool",
            content=compact_json(outcome.result),
            tool_call_id=call.id,
            name=call.name,
        )
    )

    if outcome.next_state:
        try:
            state = apply_transition(db, session, outcome.next_state, tool=call.name)
        except IllegalTransition as exc:
            events.append(_illegal(exc))
            return events, True
        events.append({"type": "state", "state": state})

    if outcome.awaiting_authorization is not None:
        events.append(
            {"type": "awaiting_authorization", **outcome.awaiting_authorization}
        )

    if session.state in TERMINAL_STATES:
        session.ended_at = utcnow()
        session.terminal_reason = session.terminal_reason or session.state.lower()
        db.add(session)
        db.commit()
        events.append(_done(session))
        return events, True

    return events, False


def _prepare_submit(
    db: Session, session: AgentSession, ctx: ToolContext, call: ToolCall
) -> tuple[list[dict[str, Any]], bool]:
    """Everything that has to be true before a submit is even attempted."""
    # §11.4 — two attempts in a session. The second BLOCK is terminal.
    if session.submit_attempt_count >= MAX_SUBMIT_ATTEMPTS:
        return (
            list(
                _limit_reached(
                    db,
                    session,
                    limit="submit_attempts",
                    observed=session.submit_attempt_count,
                    threshold=MAX_SUBMIT_ATTEMPTS,
                    unit="attempts",
                )
            ),
            True,
        )

    # `QUOTED -> SUBMITTING` is the re-plan path after a block, and it is open
    # only because the human already issued a mandate. Without one the agent
    # has to go through the human; it cannot reach SUBMITTING by naming a
    # mandate that was never granted.
    if session.state == "QUOTED":
        mandate_id = str((call.arguments or {}).get("mandate_id") or "")
        if ctx.active_mandate(mandate_id) is None:
            return (
                [
                    _illegal(
                        reject_transition(
                            db,
                            session,
                            to_state="SUBMITTING",
                            tool=call.name,
                            reason=(
                                "no ACTIVE mandate for this session; only the "
                                "human authorize endpoint can grant one"
                            ),
                        )
                    )
                ],
                True,
            )

    try:
        apply_transition(db, session, "SUBMITTING", tool=call.name)
    except IllegalTransition as exc:
        return [_illegal(exc)], True

    session.submit_attempt_count += 1
    db.add(session)
    db.commit()
    return [], False


# ── limits and errors ─────────────────────────────────────────────────────


def _limit_breached(session: AgentSession, deadline: float) -> dict[str, Any] | None:
    """Checked before the call that would breach it, never after."""
    if session.tool_call_count >= MAX_TOOL_CALLS:
        return {
            "limit": "tool_calls",
            "observed": session.tool_call_count,
            "threshold": MAX_TOOL_CALLS,
            "unit": "calls",
        }
    if time.monotonic() > deadline:
        return {
            "limit": "wall_clock",
            "observed": WALL_CLOCK_SECONDS,
            "threshold": WALL_CLOCK_SECONDS,
            "unit": "seconds",
        }
    return None


def _limit_reached(
    db: Session,
    session: AgentSession,
    *,
    limit: str,
    observed: Any,
    threshold: Any,
    unit: str,
) -> Iterator[dict[str, Any]]:
    emit(
        db,
        correlation_id=session.correlation_id,
        session_id=session.id,
        event_type="AGENT_LIMIT_REACHED",
        actor="platform",
        payload={
            "limit": limit,
            "observed": observed,
            "threshold": threshold,
            "unit": unit,
        },
    )
    db.commit()
    halt(db, session, reason=f"limit_{limit}")
    yield {
        "type": "error",
        "code": "AGENT_LIMIT_REACHED",
        "detail": f"{limit}: {observed} of {threshold} {unit}.",
    }
    yield _done(session)


def _llm_unavailable(
    db: Session, session: AgentSession, exc: LLMUnavailable
) -> dict[str, Any]:
    """§11.6 — a transport failure, and **never** a purchase outcome.

    No state moves. Nothing was submitted, nothing was blocked, nothing was
    bought, and the session can be resumed once the provider answers again.
    """
    emit(
        db,
        correlation_id=session.correlation_id,
        session_id=session.id,
        event_type="LLM_UNAVAILABLE",
        actor="platform",
        payload={"provider": exc.provider, "model": exc.model, "reason": str(exc)},
    )
    db.commit()
    return {
        "type": "error",
        "code": "LLM_UNAVAILABLE",
        "detail": (
            "The model could not be reached after retries. Nothing was "
            "submitted and nothing was purchased."
        ),
    }


def _illegal(exc: IllegalTransition) -> dict[str, Any]:
    return {"type": "error", "code": "ILLEGAL_STATE_TRANSITION", "detail": str(exc)}


def _done(session: AgentSession) -> dict[str, Any]:
    return {
        "type": "done",
        "state": session.state,
        "tool_call_count": session.tool_call_count,
        "submit_attempt_count": session.submit_attempt_count,
        "terminal_reason": session.terminal_reason,
    }


def _opening_messages(
    db: Session, session: AgentSession, content: str
) -> list[Message]:
    """Intent, then what the database says has happened, then the new message."""
    messages = [Message(role="user", content=session.intent or content)]
    brief = situation_brief(db, session)
    if brief:
        messages.append(Message(role="assistant", content=brief))
    if (session.intent or "") != content:
        messages.append(Message(role="user", content=content))
    return messages
