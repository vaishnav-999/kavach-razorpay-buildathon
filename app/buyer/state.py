"""The agent state machine (BUILD_SPEC §11.3).

The LLM does not set state. It emits tool calls; the dispatcher decides what
state a successful call implies, and this module decides whether that move is
legal. An illegal move raises, writes `ILLEGAL_STATE_TRANSITION` and halts the
session with `terminal_reason="illegal_transition"`.

`AWAITING_AUTHORIZATION -> AUTHORIZED` is not reachable from anything in the
agent loop. It happens in exactly one place — `POST
/api/buyer/sessions/{id}/authorize`, called by the human.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.audit import emit
from app.models import AgentSession

# §11.3, verbatim, plus two edges that the diagram implies rather than draws:
#
# * `QUOTED -> SUBMITTING` lets the second of the two permitted submit attempts
#   proceed without asking the human the same question twice. `agent.py` allows
#   it only when an ACTIVE mandate already exists, and it grants nothing:
#   authority still comes from a signature only the human can cause, and the
#   Guard re-runs all nine rules on every submission.
# * `BLOCKED -> CART_BUILDING` is the re-plan arrow up the left-hand side of
#   the §11.3 diagram.
LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "INIT": {"DISCOVERING", "HALTED"},
    "DISCOVERING": {"DISCOVERING", "EVALUATING", "HALTED"},
    "EVALUATING": {"EVALUATING", "CART_BUILDING", "HALTED"},
    "CART_BUILDING": {"CART_BUILDING", "EVALUATING", "QUOTED", "HALTED"},
    "QUOTED": {
        "AWAITING_AUTHORIZATION",
        "SUBMITTING",
        "CART_BUILDING",
        "EVALUATING",
        "HALTED",
    },
    "AWAITING_AUTHORIZATION": {"AUTHORIZED", "HALTED", "EXPIRED"},
    "AUTHORIZED": {"SUBMITTING", "HALTED"},
    "SUBMITTING": {"ORDER_CREATED", "BLOCKED", "HALTED"},
    "BLOCKED": {"CART_BUILDING", "EVALUATING", "HALTED"},
    # Terminal (§11.3).
    "ORDER_CREATED": set(),
    "HALTED": set(),
    "EXPIRED": set(),
}

TERMINAL_STATES = frozenset({"ORDER_CREATED", "HALTED", "EXPIRED"})


class IllegalTransition(RuntimeError):
    """A tool asked for a state move §11.3 does not permit."""

    def __init__(self, *, from_state: str, to_state: str, tool: str) -> None:
        super().__init__(
            f"{from_state} -> {to_state} is not a legal transition "
            f"(attempted by {tool})."
        )
        self.from_state = from_state
        self.to_state = to_state
        self.tool = tool


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def can_transition(from_state: str, to_state: str) -> bool:
    return to_state in LEGAL_TRANSITIONS.get(from_state, set())


def halt(db: Session, session: AgentSession, *, reason: str) -> None:
    """Terminal stop. Writes the reason and closes the session row."""
    session.state = "HALTED"
    session.terminal_reason = reason
    session.ended_at = utcnow()
    db.add(session)
    db.commit()


def _record_illegal(
    db: Session, session: AgentSession, *, to_state: str, tool: str, reason: str
) -> IllegalTransition:
    emit(
        db,
        correlation_id=session.correlation_id,
        session_id=session.id,
        event_type="ILLEGAL_STATE_TRANSITION",
        actor="platform",
        payload={
            "from_state": session.state,
            "to_state": to_state,
            "tool": tool,
            "reason": reason,
        },
    )
    exc = IllegalTransition(from_state=session.state, to_state=to_state, tool=tool)
    halt(db, session, reason="illegal_transition")
    return exc


def apply_transition(
    db: Session, session: AgentSession, to_state: str, *, tool: str
) -> str:
    """Move the session, or raise `IllegalTransition`. The model never calls this."""
    if session.state == to_state:
        return session.state
    if not can_transition(session.state, to_state):
        raise _record_illegal(
            db,
            session,
            to_state=to_state,
            tool=tool,
            reason="not in the §11.3 transition table",
        )

    session.state = to_state
    db.add(session)
    db.commit()
    return to_state


def reject_transition(
    db: Session, session: AgentSession, *, to_state: str, tool: str, reason: str
) -> IllegalTransition:
    """Refuse a move the table permits but the situation does not.

    One case needs this: a submit from `QUOTED` is legal only once the human
    has issued a mandate. Recorded and halted exactly like any other illegal
    transition, and returned rather than raised so the caller can stream it.
    """
    return _record_illegal(
        db, session, to_state=to_state, tool=tool, reason=reason
    )
