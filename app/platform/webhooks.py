"""Razorpay webhook ingestion (BUILD_SPEC §12.5).

The webhook signature is HMAC-SHA256 over the **raw request bytes**, keyed by
`RAZORPAY_WEBHOOK_SECRET`. It is not the checkout signature in
`app/platform/payments.py` — that one hashes `order_id|payment_id` under
`RAZORPAY_KEY_SECRET` — and neither is our Ed25519 scheme. Three schemes, never
interchangeable.

This module owns authenticity and storage: the HMAC, the dedup, and the
`webhook_events` row. `app/platform/webhook_dispatch.py` owns what a verified
event then means for an order. Together they are "the webhook handler" of
invariant 4.

Two rules shape everything below.

* **200 for every business outcome**, duplicates included. Non-2xx only for an
  invalid signature. Razorpay retries a non-2xx on exponential backoff for 24
  hours, so a 500 on a business edge case becomes a day-long retry storm.
* **State is monotonic toward `PAID`.** A `payment.failed` arriving after a
  `payment.captured` is documented Razorpay behaviour and must never un-pay an
  order.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import NoReturn

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import emit
from app.config import settings
from app.db import get_db
from app.errors import INTERNAL_ERROR, KavachError
from app.ids import new_correlation_id, new_webhook_event_id
from app.models import Order, WebhookEvent
from app.platform.webhook_dispatch import (
    apply_event,
    correlation_id_for,
    find_order,
    parse_envelope,
)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WebhookAck(BaseModel):
    """What we hand back. Razorpay ignores it; the demo panel and tests do not."""

    webhook_event_id: str
    razorpay_event_id: str
    event_type: str
    signature_valid: bool
    was_duplicate: bool
    order_id: str | None = None
    order_status: str | None = None
    state_changed: bool


# -- signature -------------------------------------------------------------


def expected_webhook_signature(raw: bytes) -> str:
    """HMAC-SHA256 over the raw body bytes, keyed by `RAZORPAY_WEBHOOK_SECRET`.

    `raw` must be exactly the bytes Razorpay sent. Re-serialising the parsed
    JSON changes key order and separators, and the digest with them; test 16
    exists to prove this handler never does that.
    """
    return hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"), raw, hashlib.sha256
    ).hexdigest()


# -- storage and dedup -----------------------------------------------------


def _insert_event(db: Session, event: WebhookEvent) -> bool:
    """Insert inside a SAVEPOINT. Returns False if the event id is already taken.

    This is the dedup, and it is the database's UNIQUE constraint doing the
    work. Never check-then-insert: the gap between the check and the insert is
    exactly what a retry storm drives two workers through.
    """
    savepoint = db.begin_nested()
    try:
        db.add(event)
        db.flush()
    except IntegrityError:
        # The savepoint rollback expunges the pending row; the outer
        # transaction is untouched and still usable.
        savepoint.rollback()
        return False
    savepoint.commit()
    return True


# -- the handler -----------------------------------------------------------


def handle_webhook(
    db: Session, *, raw: bytes, signature: str, event_id: str
) -> WebhookAck:
    """§12.5, steps 3 to 7. Raises `KavachError` only for an invalid signature."""
    envelope = parse_envelope(raw)
    raw_body = raw.decode("utf-8", errors="replace")

    expected = expected_webhook_signature(raw)
    valid = hmac.compare_digest(
        expected.encode("utf-8"), (signature or "").encode("utf-8")
    )
    if not valid:
        _reject(
            db,
            event_id=event_id,
            event_type=envelope.event_type,
            raw_body=raw_body,
            signature=signature,
        )

    order = find_order(db, envelope.razorpay_order_id)
    event = WebhookEvent(
        id=new_webhook_event_id(),
        # Razorpay always sends x-razorpay-event-id. An absent one stores as ""
        # and collides with any earlier absent one, sending it down the
        # duplicate path — no state change, which is the safe direction.
        razorpay_event_id=event_id,
        event_type=envelope.event_type,
        order_id=order.id if order is not None else None,
        razorpay_order_id=envelope.razorpay_order_id,
        razorpay_payment_id=envelope.razorpay_payment_id,
        signature_valid=True,
        was_duplicate=False,
        # Stored on every row: the §15 replay demo re-POSTs exactly these two.
        raw_body=raw_body,
        raw_signature=signature or "",
    )

    if not _insert_event(db, event):
        return _duplicate(db, event_id=event_id)

    emit(
        db,
        correlation_id=correlation_id_for(order),
        event_type="WEBHOOK_RECEIVED",
        actor="razorpay",
        payload={
            "webhook_event_id": event.id,
            "razorpay_event_id": event_id,
            "event_type": envelope.event_type,
            "order_id": event.order_id,
            "razorpay_order_id": envelope.razorpay_order_id,
            "razorpay_payment_id": envelope.razorpay_payment_id,
            "signature_valid": True,
        },
    )

    state_changed = apply_event(db, envelope=envelope, order=order)

    event.processed_at = _now()
    db.commit()

    return WebhookAck(
        webhook_event_id=event.id,
        razorpay_event_id=event_id,
        event_type=envelope.event_type,
        signature_valid=True,
        was_duplicate=False,
        order_id=event.order_id,
        order_status=order.status if order is not None else None,
        state_changed=state_changed,
    )


def _duplicate(db: Session, *, event_id: str) -> WebhookAck:
    """A re-delivery. Mark the original, change nothing else, return 200."""
    existing = db.scalar(
        select(WebhookEvent).where(WebhookEvent.razorpay_event_id == event_id)
    )
    if existing is None:
        # The IntegrityError was not the dedup constraint. Do not paper over it.
        raise KavachError(
            INTERNAL_ERROR,
            "The webhook event could not be stored.",
            detail={"razorpay_event_id": event_id},
            status_code=500,
        )

    existing.was_duplicate = True
    db.flush()

    order = db.get(Order, existing.order_id) if existing.order_id else None
    emit(
        db,
        correlation_id=correlation_id_for(order),
        event_type="WEBHOOK_DUPLICATE_IGNORED",
        actor="razorpay",
        payload={
            "webhook_event_id": existing.id,
            "razorpay_event_id": event_id,
            "event_type": existing.event_type,
            "order_id": existing.order_id,
        },
    )
    db.commit()

    return WebhookAck(
        webhook_event_id=existing.id,
        razorpay_event_id=event_id,
        event_type=existing.event_type,
        signature_valid=existing.signature_valid,
        was_duplicate=True,
        order_id=existing.order_id,
        order_status=order.status if order is not None else None,
        state_changed=False,
    )


def _reject(
    db: Session, *, event_id: str, event_type: str, raw_body: str, signature: str
) -> NoReturn:
    """Store the forgery, emit, and raise a 400. No order or payment is touched."""
    row_id = new_webhook_event_id()
    event = WebhookEvent(
        id=row_id,
        razorpay_event_id=event_id,
        event_type=event_type,
        order_id=None,
        razorpay_order_id=None,
        razorpay_payment_id=None,
        signature_valid=False,
        was_duplicate=False,
        raw_body=raw_body,
        raw_signature=signature or "",
    )
    if not _insert_event(db, event):
        # An unsigned body claiming an event id we already hold must not
        # overwrite that row, be mistaken for it, or turn into a 500 — a
        # forgery is not allowed to destroy evidence or provoke a retry storm.
        # The claimed id is preserved verbatim in the audit event below.
        event.razorpay_event_id = f"{event_id}#unverified_{row_id}"
        _insert_event(db, event)

    emit(
        db,
        correlation_id=new_correlation_id(),
        event_type="WEBHOOK_SIGNATURE_INVALID",
        actor="razorpay",
        payload={
            "razorpay_event_id": event_id,
            "event_type": event_type,
            "reason": "signature_missing" if not signature else "signature_mismatch",
        },
    )
    db.commit()

    raise KavachError(
        "WEBHOOK_SIGNATURE_INVALID",
        "The webhook signature did not verify against the raw request body.",
        detail={"webhook_event_id": row_id, "razorpay_event_id": event_id},
    )


# -- endpoint --------------------------------------------------------------


@router.post("/razorpay", response_model=WebhookAck)
async def post_razorpay_webhook(
    request: Request, db: Session = Depends(get_db)
) -> WebhookAck:
    """§12.5. Async, because step 1 is `await request.body()` — raw bytes first.

    Nothing parses the body before the signature is computed over it. The
    endpoint takes no request model for exactly that reason: a Pydantic body
    would hand us a re-serialisation, and the digest would never match.
    """
    raw = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    event_id = request.headers.get("x-razorpay-event-id", "")
    return handle_webhook(db, raw=raw, signature=signature, event_id=event_id)
