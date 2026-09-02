"""Token discipline (BUILD_SPEC §11.9).

Three jobs, all of them about keeping context small enough that a session costs
8 to 14 calls rather than 15 to 25:

1. **Prune tool results before they re-enter context.** The catalog is the
   largest single payload in a run — five products with long descriptions — and
   resending it every turn is most of the difference. It is kept for the turn
   that needs it and summarised to one line afterwards.
2. **Cap the conversation.** The system prompt, the original intent and the
   last 8 turns.
3. **Rebuild what was lost.** The agent is stateless between HTTP requests
   (§11.1), so after the human authorizes there is no conversation in memory.
   Rather than keep one, `situation_brief()` reads the facts back out of
   `audit_events` — the record that actually matters — as a single note.

Only the neutral `Message` type from `llm/base.py` appears here.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.buyer.llm.base import Message
from app.models import AgentSession, AuditEvent

# §11.9(2) — the system prompt, the intent, and the last 8 turns.
MAX_HISTORY_TURNS = 8
# Tool results older than this many assistant rounds are summarised.
FULL_RESULT_ROUNDS = 2
# The brief never grows without bound, however long the session ran.
MAX_BRIEF_LINES = 12


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def situation_brief(db: Session, session: AgentSession) -> str | None:
    """What has already happened, rebuilt from the audit trail."""
    events = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.correlation_id == session.correlation_id)
            .order_by(AuditEvent.seq)
        ).all()
    )

    lines: list[str] = []
    for event in events:
        line = _line_for(event.event_type, event.payload or {})
        if line:
            lines.append(line)

    if not lines:
        return None
    return "So far in this session:\n- " + "\n- ".join(lines[-MAX_BRIEF_LINES:])


def _line_for(event_type: str, payload: dict[str, Any]) -> str | None:
    if event_type == "CART_CREATED":
        return (
            f"Cart {payload.get('cart_id')} was opened at merchant "
            f"{payload.get('merchant_id')}."
        )
    if event_type == "CHECKOUT_QUOTED":
        return (
            f"Quote {payload.get('quote_id')} was signed by the merchant for "
            f"{payload.get('total_paise')} paise."
        )
    if event_type == "MANDATE_PROPOSED":
        return (
            f"Mandate {payload.get('mandate_id')} was proposed. PROPOSED is "
            "unsigned and carries no authority."
        )
    if event_type == "AUTHORIZATION_GRANTED":
        return (
            f"The human authorized mandate {payload.get('mandate_id')}: at most "
            f"{payload.get('max_amount_paise')} paise per transaction, "
            f"{payload.get('max_transactions')} transaction(s), merchants "
            f"{payload.get('allowed_merchant_ids')}."
        )
    if event_type == "POLICY_BLOCKED":
        return (
            "The Transaction Guard BLOCKED a submission on "
            f"{payload.get('failed_rule_id')} ({payload.get('block_code')})."
        )
    if event_type == "ORDER_CREATED":
        return f"Order {payload.get('order_id')} exists and is awaiting payment."
    if event_type == "AGENT_FINDING_REPORTED":
        return f"A finding was already reported for sku {payload.get('sku')}."
    return None


def summarise(name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Collapse an older tool result to one line (§11.9(1))."""
    if name == "get_catalog":
        skus = [str(p.get("sku")) for p in (result.get("products") or [])]
        return {
            "note": (
                f"catalog for {result.get('merchant_id')}: {len(skus)} skus "
                f"({', '.join(skus)}). Full product text omitted from context; "
                "call get_catalog again if you need it."
            )
        }
    if name == "discover_merchants":
        return {
            "note": (
                f"{len(result.get('merchants') or [])} merchants were listed. "
                "Only those advertising quote.signed and checkout.submit are "
                "transactable."
            )
        }
    for key in ("order_id", "quote_id", "mandate_id", "cart_id", "status"):
        if key in result:
            return {key: result[key], "note": f"{name} result pruned from context."}
    return {"note": f"{name} completed; result pruned from context."}


def prune(history: list[Message]) -> list[Message]:
    """Cap the history and shrink what is left (§11.9).

    The first message — the human's intent — is always kept. It is the only
    thing in context that came from the person who is actually paying.
    """
    if not history:
        return history

    head, rest = history[:1], history[1:]

    starts = [i for i, m in enumerate(rest) if m.role == "assistant"]
    if len(starts) > MAX_HISTORY_TURNS:
        rest = rest[starts[-MAX_HISTORY_TURNS] :]
        starts = [i for i, m in enumerate(rest) if m.role == "assistant"]

    keep_from = starts[-FULL_RESULT_ROUNDS] if len(starts) >= FULL_RESULT_ROUNDS else 0

    pruned: list[Message] = []
    for index, message in enumerate(rest):
        if message.role == "tool" and index < keep_from and message.content:
            try:
                parsed = json.loads(message.content)
            except (ValueError, TypeError):
                parsed = {}
            pruned.append(
                Message(
                    role="tool",
                    content=compact_json(summarise(message.name or "", parsed)),
                    tool_call_id=message.tool_call_id,
                    name=message.name,
                )
            )
        else:
            pruned.append(message)

    return head + pruned
