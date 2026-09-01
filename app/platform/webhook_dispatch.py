"""Reading a Razorpay webhook envelope and applying it (BUILD_SPEC §12.5 step 6).

Nothing here runs until `app/platform/webhooks.py` has verified the HMAC over
the raw request bytes. The two modules together are "the webhook handler" of
invariant 4: with `verify_checkout_signature()` they are the only code in the
system that writes `payments.status`.

The body is parsed here only to be read and recorded. Razorpay's signature
makes it authentic, not authoritative: every field is treated as absent unless
it arrives with the type we expect, and money is read as integer paise or not
at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import emit
from app.ids import new_correlation_id, new_payment_id
from app.models import Order, Payment

# §12.5 step 6 — the four events we act on. Anything else is stored and
# acknowledged with 200, and changes no state.
PAID_EVENTS = frozenset({"payment.captured", "order.paid"})
FAILED_EVENTS = frozenset({"payment.failed"})
AUTHORIZED_EVENTS = frozenset({"payment.authorized"})

UNKNOWN_EVENT_TYPE = "unknown"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# -- the envelope ----------------------------------------------------------


@dataclass(frozen=True)
class Envelope:
    """What we were willing to read out of a webhook body."""

    event_type: str
    payment_entity: dict[str, Any]
    order_entity: dict[str, Any]
    razorpay_order_id: str | None
    razorpay_payment_id: str | None


def _as_int(value: Any) -> int | None:
    # Money crosses this boundary as integer paise and nothing else.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _entity(parsed: dict[str, Any], name: str) -> dict[str, Any]:
    """`payload.<name>.entity` from Razorpay's webhook envelope, or `{}`."""
    container = (parsed.get("payload") or {}).get(name)
    if not isinstance(container, dict):
        return {}
    entity = container.get("entity")
    return entity if isinstance(entity, dict) else {}


def parse_envelope(raw: bytes) -> Envelope:
    """Never raises. A body we cannot read is an `unknown` event with no entities.

    A malformed body that carries a valid signature is still a business
    outcome, and a business outcome is a 200.
    """
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}

    payment_entity = _entity(parsed, "payment")
    order_entity = _entity(parsed, "order")
    return Envelope(
        event_type=_as_str(parsed.get("event")) or UNKNOWN_EVENT_TYPE,
        payment_entity=payment_entity,
        order_entity=order_entity,
        # `order.paid` carries the order entity; the payment events carry the
        # order id on the payment.
        razorpay_order_id=(
            _as_str(payment_entity.get("order_id")) or _as_str(order_entity.get("id"))
        ),
        razorpay_payment_id=_as_str(payment_entity.get("id")),
    )


def find_order(db: Session, razorpay_order_id: str | None) -> Order | None:
    if not razorpay_order_id:
        return None
    return db.scalar(select(Order).where(Order.razorpay_order_id == razorpay_order_id))


def correlation_id_for(order: Order | None) -> str:
    # A webhook we cannot attribute to an order still gets an audit chain of
    # its own rather than being dropped or attached to someone else's.
    return order.correlation_id if order is not None else new_correlation_id()


# -- payments --------------------------------------------------------------


def _upsert_payment(
    db: Session, *, order: Order, entity: dict[str, Any], status: str
) -> Payment | None:
    """Write the `payments` row for a signature-verified webhook.

    Returns None when the event carried no payment entity — `order.paid` need
    not carry one, and the order transition does not depend on it.

    `CAPTURED` is never downgraded. A later `payment.failed` or a re-delivered
    `payment.authorized` leaves a captured payment captured.
    """
    razorpay_payment_id = _as_str(entity.get("id"))
    if razorpay_payment_id is None:
        return None

    payment = db.scalar(
        select(Payment).where(
            Payment.order_id == order.id,
            Payment.razorpay_payment_id == razorpay_payment_id,
        )
    )
    amount_paise = _as_int(entity.get("amount"))
    currency = _as_str(entity.get("currency"))

    if payment is None:
        payment = Payment(
            id=new_payment_id(),
            order_id=order.id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_order_id=order.razorpay_order_id,
            # The order's own amount is the fallback. Razorpay's figure is
            # compared against ours by §12.6, which is the endpoint whose job
            # that is; a webhook is not the place to adjudicate a mismatch.
            amount_paise=(
                amount_paise if amount_paise is not None else order.amount_paise
            ),
            currency=currency or order.currency,
            method=_as_str(entity.get("method")),
            status=status,
            # The row's contents arrived under a verified webhook signature.
            # `source` records which of the two verified channels it came from.
            signature_verified=True,
            source="WEBHOOK",
            raw_payload=entity,
        )
        db.add(payment)
    elif payment.status == "CAPTURED" and status != "CAPTURED":
        pass  # monotonic: nothing downgrades a captured payment
    else:
        payment.status = status
        payment.method = _as_str(entity.get("method")) or payment.method
        payment.updated_at = _now()

    db.flush()
    return payment


def _razorpay_payment_id(payment: Payment | None) -> str | None:
    return payment.razorpay_payment_id if payment is not None else None


# -- the state machine -----------------------------------------------------


def apply_event(db: Session, *, envelope: Envelope, order: Order | None) -> bool:
    """Apply the event to our state. Returns True if the order status changed.

    Transitions are idempotent and monotonic toward `PAID`. Nothing here can
    take a `PAID` order back out of `PAID`.
    """
    if order is None:
        return False

    if envelope.event_type in PAID_EVENTS:
        return _mark_paid(db, order=order, envelope=envelope)
    if envelope.event_type in FAILED_EVENTS:
        return _mark_failed(db, order=order, envelope=envelope)
    if envelope.event_type in AUTHORIZED_EVENTS:
        # Recorded, not finalised. An authorization is not a capture.
        _upsert_payment(
            db, order=order, entity=envelope.payment_entity, status="AUTHORIZED"
        )
        return False
    return False


def _mark_paid(db: Session, *, order: Order, envelope: Envelope) -> bool:
    payment = _upsert_payment(
        db, order=order, entity=envelope.payment_entity, status="CAPTURED"
    )
    if order.status == "PAID":
        # Already paid, by an earlier delivery or by §12.4. Idempotent: no
        # transition, so no second ORDER_COMPLETED.
        return False

    order.status = "PAID"
    order.updated_at = _now()
    db.flush()
    emit(
        db,
        correlation_id=order.correlation_id,
        event_type="ORDER_COMPLETED",
        actor="platform",
        payload={
            "order_id": order.id,
            "razorpay_order_id": order.razorpay_order_id,
            "razorpay_payment_id": _razorpay_payment_id(payment),
            "amount_paise": order.amount_paise,
            "currency": order.currency,
            "source": "WEBHOOK",
        },
    )
    return True


def _mark_failed(db: Session, *, order: Order, envelope: Envelope) -> bool:
    # The failed attempt is recorded whatever the order's state — it is
    # evidence, and §12.6 reconciles against Razorpay's own record.
    payment = _upsert_payment(
        db, order=order, entity=envelope.payment_entity, status="FAILED"
    )
    if order.status in ("PAID", "FAILED"):
        # A failure arriving after a capture is documented Razorpay behaviour
        # and is the one transition we refuse. The order stays PAID.
        return False

    order.status = "FAILED"
    order.updated_at = _now()
    db.flush()
    emit(
        db,
        correlation_id=order.correlation_id,
        event_type="ORDER_FAILED",
        actor="platform",
        payload={
            "order_id": order.id,
            "razorpay_order_id": order.razorpay_order_id,
            "razorpay_payment_id": _razorpay_payment_id(payment),
            "reason": (
                _as_str(envelope.payment_entity.get("error_description"))
                or envelope.event_type
            ),
            "source": "WEBHOOK",
        },
    )
    return True
