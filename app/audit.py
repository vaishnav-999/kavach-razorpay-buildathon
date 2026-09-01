"""The audit trail (BUILD_SPEC §13).

`emit()` is the **only** code in this system that writes to `audit_events`.
The table is append-only: there is no update path and no delete path here, and
test 24 scans the source tree to prove no other module writes one.

Redaction is an allowlist per event type, never a denylist. A denylist leaks
the field you forgot to think about; an allowlist fails closed on the field you
forgot to add.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.crypto import signature_fingerprint
from app.ids import new_audit_event_id
from app.models import AuditEvent

# §13.1 — the exact event type strings, each with its one legitimate actor.
EVENT_ACTORS: dict[str, str] = {
    "USER_INTENT_RECEIVED": "user",
    "MERCHANT_DISCOVERED": "agent",
    "MERCHANT_REJECTED": "agent",
    "CATALOG_FETCHED": "agent",
    "UNTRUSTED_CONTENT_FLAGGED": "platform",
    "CART_CREATED": "agent",
    "CART_ITEM_ADDED": "agent",
    "CHECKOUT_QUOTED": "merchant",
    "MANDATE_PROPOSED": "agent",
    "AUTHORIZATION_GRANTED": "user",
    "MANDATE_REVOKED": "user",
    "POLICY_APPROVED": "platform",
    "POLICY_BLOCKED": "platform",
    "CHECKOUT_VALIDATED": "merchant",
    "CHECKOUT_REJECTED": "merchant",
    "ORDER_CREATED": "merchant",
    "RAZORPAY_ORDER_CREATED": "platform",
    "PAYMENT_VERIFIED": "platform",
    "PAYMENT_SIGNATURE_INVALID": "platform",
    "WEBHOOK_RECEIVED": "razorpay",
    "WEBHOOK_SIGNATURE_INVALID": "razorpay",
    "WEBHOOK_DUPLICATE_IGNORED": "razorpay",
    "ORDER_COMPLETED": "platform",
    "ORDER_FAILED": "platform",
    "RECONCILIATION_PERFORMED": "platform",
    "AGENT_FINDING_REPORTED": "agent",
    "ILLEGAL_STATE_TRANSITION": "platform",
    "AGENT_LIMIT_REACHED": "platform",
    "LLM_UNAVAILABLE": "platform",
    "DEMO_ACTION_TRIGGERED": "demo",
}

# §13.2 — the allowlist. Any key not named here for that event type is dropped.
ALLOWED_KEYS: dict[str, set[str]] = {
    "USER_INTENT_RECEIVED": {
        "cassette_name", "intent", "llm_model", "llm_provider", "user_email",
    },
    "MERCHANT_DISCOVERED": {
        "capabilities", "category", "count", "merchant_id", "merchants", "name",
        "slug", "transactable",
    },
    "MERCHANT_REJECTED": {
        "category", "merchant_id", "missing_capabilities", "name", "reason",
        "slug",
    },
    "CATALOG_FETCHED": {"merchant_id", "product_count", "skus", "slug"},
    "UNTRUSTED_CONTENT_FLAGGED": {"excerpt", "field", "merchant_id", "reason", "sku"},
    "CART_CREATED": {"cart_id", "merchant_id"},
    "CART_ITEM_ADDED": {
        "cart_id", "cart_item_id", "line_count", "qty", "sku",
        "unit_price_paise_snapshot",
    },
    "CHECKOUT_QUOTED": {
        "cart_id", "currency", "expires_at", "issued_at", "line_items",
        "merchant_id", "quote_id", "signature", "total_paise",
    },
    "MANDATE_PROPOSED": {
        "allowed_categories", "allowed_merchant_ids", "cumulative_cap_paise",
        "currency", "expires_at", "mandate_id", "max_amount_paise",
        "max_transactions", "prompt_playback",
    },
    "AUTHORIZATION_GRANTED": {
        "allowed_categories", "allowed_merchant_ids", "clamps_applied",
        "cumulative_cap_paise", "currency", "expires_at", "issued_at",
        "mandate_id", "max_amount_paise", "max_transactions", "signature",
        "user_email",
    },
    "MANDATE_REVOKED": {"mandate_id", "reason", "revoked_at"},
    "POLICY_APPROVED": {
        "duration_ms", "guard_decision_id", "mandate_id", "merchant_id",
        "quote_id", "requested_total_paise", "rules", "verdict",
    },
    "POLICY_BLOCKED": {
        "block_code", "duration_ms", "failed_rule_id", "guard_decision_id",
        "mandate_id", "merchant_id", "quote_id", "requested_total_paise", "rules",
        "verdict",
    },
    "CHECKOUT_VALIDATED": {
        "checks", "mandate_id", "merchant_id", "quote_id", "total_paise",
    },
    "CHECKOUT_REJECTED": {
        "code", "detail", "mandate_id", "merchant_id", "quote_id", "rule_id",
    },
    "ORDER_CREATED": {
        "amount_paise", "currency", "guard_decision_id", "idempotency_key",
        "line_items", "mandate_id", "merchant_id", "order_id", "quote_id",
        "status",
    },
    "RAZORPAY_ORDER_CREATED": {
        "amount_paise", "currency", "order_id", "razorpay_order_id", "receipt",
    },
    "PAYMENT_VERIFIED": {
        "amount_paise", "currency", "method", "order_id", "payment_id",
        "razorpay_order_id", "razorpay_payment_id", "signature_verified", "status",
    },
    "PAYMENT_SIGNATURE_INVALID": {
        "order_id", "razorpay_order_id", "razorpay_payment_id", "reason",
    },
    "WEBHOOK_RECEIVED": {
        "event_type", "order_id", "razorpay_event_id", "razorpay_order_id",
        "razorpay_payment_id", "signature_valid", "webhook_event_id",
    },
    "WEBHOOK_SIGNATURE_INVALID": {"event_type", "razorpay_event_id", "reason"},
    "WEBHOOK_DUPLICATE_IGNORED": {
        "event_type", "order_id", "razorpay_event_id", "webhook_event_id",
    },
    "ORDER_COMPLETED": {
        "amount_paise", "currency", "order_id", "razorpay_order_id",
        "razorpay_payment_id", "source",
    },
    "ORDER_FAILED": {
        "order_id", "razorpay_order_id", "razorpay_payment_id", "reason", "source",
    },
    "RECONCILIATION_PERFORMED": {
        "after_status", "before_status", "changed", "order_id",
        "razorpay_order_id",
    },
    "AGENT_FINDING_REPORTED": {"excerpt", "merchant_id", "severity", "sku", "summary"},
    "ILLEGAL_STATE_TRANSITION": {"from_state", "reason", "to_state", "tool"},
    "AGENT_LIMIT_REACHED": {"limit", "observed", "threshold", "unit"},
    "LLM_UNAVAILABLE": {"model", "provider", "reason"},
    "DEMO_ACTION_TRIGGERED": {"action", "params", "result"},
}

# Any value under one of these keys is reduced to a fingerprint (§13.2). A full
# signature is never written to the audit trail.
SIGNATURE_KEYS = frozenset(
    {"signature", "mandate_signature", "quote_signature", "raw_signature"}
)

# §13.2 — free text is capped. The two evidence events carry more, because the
# offending merchant text *is* the evidence, and it is merchant data rather
# than a secret.
DEFAULT_TEXT_LIMIT = 500
TEXT_LIMIT: dict[str, int] = {
    "UNTRUSTED_CONTENT_FLAGGED": 1000,
    "AGENT_FINDING_REPORTED": 1000,
}

# Belt and braces on top of the allowlist: a key that reads like a credential
# never lands in the audit trail, even if someone later adds it to a set above.
_SECRET_MARKERS = ("secret", "api_key", "apikey", "password", "token", "seed")


class UnknownEventType(ValueError):
    """Raised for an event type outside §13.1.

    A typo in an event type is a bug, not a runtime condition to swallow.
    """


def _iso_z(value: datetime | date) -> str:
    # §18.3 — ISO 8601 UTC with a Z suffix.
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return value.isoformat()


def _jsonable(value: Any, limit: int) -> Any:
    if isinstance(value, (datetime, date)):
        return _iso_z(value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str):
        # The ellipsis counts against the limit, so the stored string is never
        # longer than the number §13.2 gives.
        return value if len(value) <= limit else value[: limit - 1] + "…"
    if isinstance(value, dict):
        return {str(k): _jsonable(v, limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v, limit) for v in value]
    if isinstance(value, int):
        return value
    return _jsonable(str(value), limit)


def redact(event_type: str, payload: dict) -> dict:
    """Apply the §13.2 allowlist for `event_type`. Pure; touches no database."""
    allowed = ALLOWED_KEYS.get(event_type)
    if allowed is None:
        raise UnknownEventType(f"{event_type!r} is not one of the §13.1 event types.")

    limit = TEXT_LIMIT.get(event_type, DEFAULT_TEXT_LIMIT)
    out: dict[str, Any] = {}
    for key, value in (payload or {}).items():
        if key not in allowed:
            continue
        if any(marker in key.lower() for marker in _SECRET_MARKERS):
            continue
        if key in SIGNATURE_KEYS:
            out[key] = signature_fingerprint(value if isinstance(value, str) else None)
            continue
        out[key] = _jsonable(value, limit)
    return out


def emit(
    db: Session,
    *,
    correlation_id: str,
    event_type: str,
    actor: str,
    payload: dict,
    session_id: str | None = None,
) -> None:
    """Append one row to `audit_events`. The only writer to that table.

    Flushes but does not commit, so the event lands inside whatever transaction
    the caller is already in. An audit row can never outlive the state change
    it describes, and it can never describe one that was rolled back.
    """
    expected_actor = EVENT_ACTORS.get(event_type)
    if expected_actor is None:
        raise UnknownEventType(f"{event_type!r} is not one of the §13.1 event types.")
    if actor != expected_actor:
        raise ValueError(
            f"{event_type} is emitted by {expected_actor!r}, not {actor!r} (§13.1)."
        )

    db.add(
        AuditEvent(
            id=new_audit_event_id(),
            correlation_id=correlation_id,
            session_id=session_id,
            event_type=event_type,
            actor=actor,
            payload=redact(event_type, payload),
        )
    )
    db.flush()
