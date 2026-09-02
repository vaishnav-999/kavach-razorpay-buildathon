"""The three buyer endpoints (BUILD_SPEC §11.6).

| POST | `/api/buyer/sessions`            | create a session          |
| POST | `/api/buyer/sessions/{id}/message`   | **SSE stream**        |
| POST | `/api/buyer/sessions/{id}/authorize` | the human grants     |

The message endpoint streams `thought · tool_call · tool_result · state ·
awaiting_authorization · message · error · done`. An `LLMUnavailable` failure
streams `{"type": "error", "code": "LLM_UNAVAILABLE"}` and is never a purchase
outcome.

The authorize endpoint is the **only** way `AWAITING_AUTHORIZATION` becomes
`AUTHORIZED` (§11.3). No tool can perform that transition, which is why it is
an HTTP endpoint a person calls rather than an eleventh tool.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.audit import emit
from app.buyer.agent import run_turn
from app.buyer.llm import LLMProviderNotConfigured, get_provider
from app.buyer.llm.base import CassetteExhausted
from app.buyer.state import TERMINAL_STATES, IllegalTransition, apply_transition
from app.config import settings
from app.db import SessionLocal, get_db
from app.errors import KavachError
from app.ids import new_correlation_id, new_session_id
from app.models import AgentSession
from app.platform import mandate as mandate_authority
from app.schemas import MandateOut

router = APIRouter(prefix="/api/buyer", tags=["buyer"])


# ── wire models ───────────────────────────────────────────────────────────


class SessionCreateIn(BaseModel):
    user_email: str
    # §11.6 — ignored unless a cassette is actually in use.
    cassette: str | None = None


class SessionCreateOut(BaseModel):
    session_id: str
    correlation_id: str
    state: str
    # §11.7 provenance: recorded on the row, echoed here, and repeated in the
    # USER_INTENT_RECEIVED payload and the UI header.
    llm_provider: str
    llm_model: str
    cassette_name: str | None = None


class MessageIn(BaseModel):
    content: str = Field(min_length=1)


class AuthorizeIn(BaseModel):
    mandate_id: str
    # The human's numbers, not the agent's. §8.3 clamps them server-side, so
    # this can lower a limit but never raise one past the ceiling.
    max_amount_paise: int = Field(ge=1)
    ttl_minutes: int = Field(default=30, ge=1)


class AuthorizeOut(BaseModel):
    session_id: str
    state: str
    mandate: MandateOut


# ── helpers ───────────────────────────────────────────────────────────────


def get_session(db: Session, session_id: str) -> AgentSession:
    session = db.get(AgentSession, session_id)
    if session is None:
        raise KavachError(
            "SESSION_NOT_FOUND",
            f"No agent session with id {session_id}.",
            detail={"session_id": session_id},
            status_code=404,
        )
    return session


def _provider_for(session: AgentSession):
    """Build the provider named by `LLM_PROVIDER`. Never falls back silently."""
    return get_provider(cassette=session.cassette_name)


def _sse(event: dict[str, Any]) -> dict[str, str]:
    return {"event": str(event.get("type") or "message"), "data": json.dumps(event)}


# ── endpoints ─────────────────────────────────────────────────────────────


@router.post("/sessions", response_model=SessionCreateOut)
def create_session(
    body: SessionCreateIn, db: Session = Depends(get_db)
) -> SessionCreateOut:
    """§11.6 — open a session and pin the provider it will run on.

    `get_provider()` is called here rather than at the first message so that a
    misconfigured `LLM_PROVIDER` fails immediately and visibly, instead of
    halfway through a stream.
    """
    try:
        provider = get_provider(cassette=body.cassette)
    except (LLMProviderNotConfigured, CassetteExhausted) as exc:
        # A missing cassette is a configuration problem like any other, and it
        # leaves through the §18.1 envelope rather than as an unhandled 500.
        raise KavachError(
            "LLM_UNAVAILABLE",
            str(exc),
            detail={
                "llm_provider": settings.LLM_PROVIDER or None,
                "cassette_mode": settings.CASSETTE_MODE,
            },
        ) from exc

    session = AgentSession(
        id=new_session_id(),
        correlation_id=new_correlation_id(),
        user_email=body.user_email,
        intent=None,
        state="INIT",
        llm_provider=provider.name,
        llm_model=provider.model,
        cassette_name=body.cassette,
        tool_call_count=0,
        submit_attempt_count=0,
    )
    db.add(session)
    db.commit()

    return SessionCreateOut(
        session_id=session.id,
        correlation_id=session.correlation_id,
        state=session.state,
        llm_provider=session.llm_provider or "",
        llm_model=session.llm_model or "",
        cassette_name=session.cassette_name,
    )


@router.post("/sessions/{session_id}/message")
def post_message(session_id: str, body: MessageIn) -> EventSourceResponse:
    """§11.6 — run the agent and stream what it does.

    The generator opens its own database session. A `Depends(get_db)` one would
    be closed the moment this function returns, which is before the first event
    is produced.
    """
    # Fail fast on a bad id, before the stream starts and a 200 is committed.
    probe = SessionLocal()
    try:
        get_session(probe, session_id)
    finally:
        probe.close()

    return EventSourceResponse(_stream(session_id, body.content))


def _stream(session_id: str, content: str) -> Iterator[dict[str, str]]:
    db = SessionLocal()
    try:
        session = get_session(db, session_id)

        if session.state in TERMINAL_STATES:
            yield _sse(
                {
                    "type": "error",
                    "code": "AGENT_LIMIT_REACHED",
                    "detail": (
                        f"This session is {session.state} and cannot be "
                        "continued. Start a new one."
                    ),
                }
            )
            return

        if not session.intent:
            session.intent = content
            db.add(session)
            emit(
                db,
                correlation_id=session.correlation_id,
                session_id=session.id,
                event_type="USER_INTENT_RECEIVED",
                actor="user",
                payload={
                    "intent": content,
                    "user_email": session.user_email,
                    # §11.7 — provenance travels with the intent.
                    "llm_provider": session.llm_provider,
                    "llm_model": session.llm_model,
                    "cassette_name": session.cassette_name,
                },
            )
            db.commit()

        try:
            provider = _provider_for(session)
        except (LLMProviderNotConfigured, CassetteExhausted) as exc:
            yield _sse(
                {"type": "error", "code": "LLM_UNAVAILABLE", "detail": str(exc)}
            )
            return

        for event in run_turn(db, session, provider=provider, content=content):
            yield _sse(event)
    finally:
        db.close()


@router.post("/sessions/{session_id}/authorize", response_model=AuthorizeOut)
def authorize(
    session_id: str, body: AuthorizeIn, db: Session = Depends(get_db)
) -> AuthorizeOut:
    """§11.6, §11.3 — the human presses Authorize.

    The state is checked *before* the mandate is signed, so a session that is
    not waiting for authorization never produces a signed mandate it has no use
    for.
    """
    session = get_session(db, session_id)

    if session.state != "AWAITING_AUTHORIZATION":
        raise KavachError(
            "ILLEGAL_STATE_TRANSITION",
            f"Session {session.id} is {session.state}; authorization is only "
            "meaningful while it is AWAITING_AUTHORIZATION.",
            correlation_id=session.correlation_id,
            detail={"from_state": session.state, "to_state": "AUTHORIZED"},
            status_code=409,
        )

    mandate = mandate_authority.get_mandate(db, body.mandate_id)
    if mandate.session_id and mandate.session_id != session.id:
        raise KavachError(
            "MANDATE_NOT_FOUND",
            f"Mandate {mandate.id} does not belong to session {session.id}.",
            correlation_id=session.correlation_id,
            detail={"mandate_id": mandate.id, "session_id": session.id},
        )

    # The only call to `issue()` in the buyer plane, and the only path in the
    # system that signs a mandate (§8.2).
    issued = mandate_authority.issue(
        db,
        mandate_id=mandate.id,
        max_amount_paise=body.max_amount_paise,
        ttl_minutes=body.ttl_minutes,
    )

    try:
        apply_transition(db, session, "AUTHORIZED", tool="human:authorize")
    except IllegalTransition as exc:  # pragma: no cover - guarded above
        raise KavachError(
            "ILLEGAL_STATE_TRANSITION",
            str(exc),
            correlation_id=session.correlation_id,
        ) from exc

    return AuthorizeOut(
        session_id=session.id,
        state=session.state,
        mandate=MandateOut.model_validate(issued),
    )
