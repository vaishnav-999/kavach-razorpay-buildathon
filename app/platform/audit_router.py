"""§13.3 — `GET /api/audit/{correlation_id}`.

Judge check #3, and the answer to every "prove it" question in the demo: one
correlation id, the whole ordered chain, plus every object the chain refers to.

Read-only. This module writes nothing — `audit_events` is append-only and
`app/audit.py` is its only writer (invariant 8).

Ordering is strictly `audit_events.seq`. The linked collections are ordered by
their own creation so the chain and the objects tell the same story in the same
direction.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import KavachError
from app.models import (
    AgentSession,
    AuditEvent,
    GuardDecision,
    Mandate,
    Order,
    Payment,
    Quote,
    WebhookEvent,
)
from app.schemas import (
    AuditEventOut,
    MandateOut,
    OrderOut,
    PaymentOut,
    QuoteOut,
    WebhookEventOut,
)

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditSessionOut(BaseModel):
    """The §13.3 session summary. Also the frontend's source of truth for
    session state — the SSE stream narrates, this reports."""

    id: str
    state: str
    llm_provider: str | None = None
    llm_model: str | None = None
    cassette_name: str | None = None
    user_email: str
    intent: str | None = None
    tool_call_count: int
    submit_attempt_count: int
    terminal_reason: str | None = None


class AuditChainOut(BaseModel):
    correlation_id: str
    session: AuditSessionOut | None = None
    events: list[AuditEventOut]
    guard_decisions: list[dict[str, Any]]
    mandates: list[MandateOut]
    quotes: list[QuoteOut]
    orders: list[OrderOut]
    payments: list[PaymentOut]
    webhook_events: list[WebhookEventOut]


def _decision_dict(row: GuardDecision, currency: str) -> dict[str, Any]:
    """The §9.3 result shape, rebuilt from the stored row.

    `rules` was written by `RuleResult.to_dict()` at evaluation time, so all
    nine come back exactly as the Guard reported them — on ALLOW as well as
    BLOCK (invariant 10). Nothing is recomputed here; a decision is a record of
    what was decided, not something to be re-derived later.
    """
    return {
        "verdict": row.verdict,
        "decision_id": row.id,
        "correlation_id": row.correlation_id,
        "session_id": row.session_id,
        "mandate_id": row.mandate_id,
        "quote_id": row.quote_id,
        "merchant_id": row.merchant_id,
        "requested_total_paise": row.requested_total_paise,
        "currency": currency,
        "evaluated_at": row.evaluated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_ms": row.duration_ms,
        "failed_rule_id": row.failed_rule_id,
        "block_code": row.block_code,
        "rules": list(row.rules or []),
    }


@router.get("/{correlation_id}", response_model=AuditChainOut)
def get_chain(correlation_id: str, db: Session = Depends(get_db)) -> AuditChainOut:
    """The entire ordered chain for one correlation id."""
    events = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.correlation_id == correlation_id)
            .order_by(AuditEvent.seq)
        )
    )
    session = db.scalars(
        select(AgentSession)
        .where(AgentSession.correlation_id == correlation_id)
        .order_by(AgentSession.started_at)
    ).first()

    if not events and session is None:
        raise KavachError(
            "SESSION_NOT_FOUND",
            f"No audit chain for correlation id {correlation_id}.",
            correlation_id=correlation_id,
            detail={"correlation_id": correlation_id},
            status_code=404,
        )

    mandates = list(
        db.scalars(
            select(Mandate)
            .where(Mandate.correlation_id == correlation_id)
            .order_by(Mandate.created_at)
        )
    )
    quotes = list(
        db.scalars(
            select(Quote)
            .where(Quote.correlation_id == correlation_id)
            .order_by(Quote.created_at)
        )
    )
    orders = list(
        db.scalars(
            select(Order)
            .where(Order.correlation_id == correlation_id)
            .order_by(Order.created_at)
        )
    )
    decisions = list(
        db.scalars(
            select(GuardDecision)
            .where(GuardDecision.correlation_id == correlation_id)
            .order_by(GuardDecision.evaluated_at)
        )
    )

    order_ids = [order.id for order in orders]
    payments: list[Payment] = []
    webhooks: list[WebhookEvent] = []
    if order_ids:
        payments = list(
            db.scalars(
                select(Payment)
                .where(Payment.order_id.in_(order_ids))
                .order_by(Payment.created_at)
            )
        )
        webhooks = list(
            db.scalars(
                select(WebhookEvent)
                .where(WebhookEvent.order_id.in_(order_ids))
                .order_by(WebhookEvent.created_at)
            )
        )

    # §9.3 carries a currency; `guard_decisions` does not store one, so it comes
    # from the mandate the decision was evaluated against.
    currency_by_mandate = {m.id: m.currency for m in mandates}

    return AuditChainOut(
        correlation_id=correlation_id,
        session=(
            AuditSessionOut.model_validate(session, from_attributes=True)
            if session is not None
            else None
        ),
        events=[AuditEventOut.model_validate(e) for e in events],
        guard_decisions=[
            _decision_dict(d, currency_by_mandate.get(d.mandate_id or "", "INR"))
            for d in decisions
        ],
        mandates=[MandateOut.model_validate(m) for m in mandates],
        quotes=[QuoteOut.model_validate(q) for q in quotes],
        orders=[OrderOut.model_validate(o) for o in orders],
        payments=[PaymentOut.model_validate(p) for p in payments],
        webhook_events=[WebhookEventOut.model_validate(w) for w in webhooks],
    )
