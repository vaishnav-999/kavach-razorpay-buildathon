"""Order creation, checkout verification and reconciliation (BUILD_SPEC §12).

Three things live here:

* **§12.2** `create_razorpay_order()` — the one function that turns a signed
  quote into a Razorpay order. In M5 it is called from
  `execute_authorized_purchase()`, immediately below the single
  `guard.evaluate()` call site, and it is reachable only on ALLOW.
* **§12.4** `verify_checkout_signature()` — HMAC-SHA256 over
  `razorpay_order_id|razorpay_payment_id`, keyed by `RAZORPAY_KEY_SECRET`,
  using **our** stored order id. One of only two places that may write
  `payments.status`; the other is the webhook handler.
* **§12.6** `reconcile()` — a read-only comparison against Razorpay's record.

Razorpay's HMAC schemes are not our Ed25519 scheme, and the checkout signature
is not the webhook signature. `app/crypto.py` is deliberately not imported here.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any, NoReturn

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import emit
from app.config import settings
from app.db import get_db
from app.errors import KavachError
from app.ids import new_correlation_id, new_order_id, new_payment_id
from app.models import Order, Payment, Quote
from app.platform import razorpay_client
from app.schemas import PaymentOut

router = APIRouter(prefix="/api/payments", tags=["payments"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_order(db: Session, order_id: str) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise KavachError(
            "ORDER_NOT_FOUND",
            f"No order with id {order_id}.",
            detail={"order_id": order_id},
        )
    return order


# -- 12.2 order creation ---------------------------------------------------


def create_razorpay_order(
    db: Session,
    *,
    quote: Quote,
    mandate_id: str,
    guard_decision_id: str,
    idempotency_key: str,
    session_id: str | None = None,
) -> Order:
    """Create our `orders` row and the Razorpay order behind it (§12.2).

    `guard_decision_id` is a required argument because `orders.guard_decision_id`
    is `NOT NULL`: there is no way to reach this function without naming the
    decision that permitted the purchase.

    A repeated `idempotency_key` returns the original order and creates no
    second Razorpay order.
    """
    existing = db.scalar(select(Order).where(Order.idempotency_key == idempotency_key))
    if existing is not None:
        return existing

    order_id = new_order_id()
    correlation_id = quote.correlation_id or new_correlation_id()

    order = Order(
        id=order_id,
        merchant_id=quote.merchant_id,
        quote_id=quote.id,
        mandate_id=mandate_id,
        guard_decision_id=guard_decision_id,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        amount_paise=quote.total_paise,
        currency=quote.currency,
        status="CREATED",
        # §12.2 - the receipt is our own order id: 16 characters against
        # Razorpay's 40-character limit.
        receipt=order_id,
        line_items=quote.line_items,
    )
    db.add(order)
    db.flush()

    emit(
        db,
        correlation_id=correlation_id,
        session_id=session_id,
        event_type="ORDER_CREATED",
        actor="merchant",
        payload={
            "order_id": order.id,
            "merchant_id": order.merchant_id,
            "quote_id": order.quote_id,
            "mandate_id": order.mandate_id,
            "guard_decision_id": order.guard_decision_id,
            "idempotency_key": order.idempotency_key,
            "amount_paise": order.amount_paise,
            "currency": order.currency,
            "status": order.status,
            "line_items": order.line_items,
        },
    )

    try:
        created = razorpay_client.create_order(
            amount_paise=order.amount_paise,
            currency=order.currency,
            receipt=order.receipt or order.id,
            # §12.2 - how a judge cross-references the Razorpay dashboard
            # against our audit trail.
            notes={
                "kavach_order_id": order.id,
                "correlation_id": correlation_id,
                "mandate_id": mandate_id,
                "guard_decision_id": guard_decision_id,
            },
        )
    except razorpay_client.RazorpayError as exc:
        # Nothing half-created survives: the order row and its audit event go
        # with the rollback, so an order never exists without a Razorpay order.
        db.rollback()
        raise KavachError(
            "RAZORPAY_ERROR",
            "Razorpay would not create the order.",
            correlation_id=correlation_id,
            detail={"status_code": exc.status_code, "razorpay": exc.body},
        ) from exc

    # Razorpay echoes the amount and currency it recorded. If either disagrees
    # with ours the two systems are already out of step, and no payment should
    # be collected against this order.
    if created.get("amount") != order.amount_paise or (
        created.get("currency") != order.currency
    ):
        db.rollback()
        raise KavachError(
            "RAZORPAY_ERROR",
            "Razorpay recorded an amount or currency we did not send.",
            correlation_id=correlation_id,
            detail={
                "sent_amount_paise": order.amount_paise,
                "razorpay_amount": created.get("amount"),
                "sent_currency": order.currency,
                "razorpay_currency": created.get("currency"),
            },
        )

    order.razorpay_order_id = created["id"]
    order.status = "PENDING_PAYMENT"
    order.updated_at = _now()
    db.flush()

    emit(
        db,
        correlation_id=correlation_id,
        session_id=session_id,
        event_type="RAZORPAY_ORDER_CREATED",
        actor="platform",
        payload={
            "order_id": order.id,
            "razorpay_order_id": order.razorpay_order_id,
            "amount_paise": order.amount_paise,
            "currency": order.currency,
            "receipt": order.receipt,
        },
    )
    db.commit()
    return order


# -- 12.4 checkout signature verification ----------------------------------


def expected_checkout_signature(
    razorpay_order_id: str, razorpay_payment_id: str
) -> str:
    """HMAC-SHA256 of `order_id|payment_id`, keyed by `RAZORPAY_KEY_SECRET`.

    This is the checkout scheme. The webhook scheme hashes the raw request body
    under a different secret (§12.5). The two are never interchangeable.
    """
    message = f"{razorpay_order_id}|{razorpay_payment_id}"
    return hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_checkout_signature(
    db: Session,
    *,
    order_id: str,
    razorpay_payment_id: str,
    razorpay_order_id: str,
    razorpay_signature: str,
) -> Payment:
    """§12.4. Raises `KavachError('PAYMENT_SIGNATURE_INVALID')` on a bad signature.

    The browser's `razorpay_order_id` is accepted as an argument and then
    ignored for the purposes of the HMAC: the message is built from
    `order.razorpay_order_id`, read from our own database. Signing over the
    posted order id would let an attacker sign a message we would then accept.
    """
    order = get_order(db, order_id)

    stored_razorpay_order_id = order.razorpay_order_id
    if not stored_razorpay_order_id:
        _reject(
            db,
            order=order,
            razorpay_payment_id=razorpay_payment_id,
            reason="order_has_no_razorpay_order_id",
        )

    expected = expected_checkout_signature(
        stored_razorpay_order_id, razorpay_payment_id
    )
    valid = hmac.compare_digest(
        expected.encode("utf-8"), (razorpay_signature or "").encode("utf-8")
    )

    if not valid:
        _reject(
            db,
            order=order,
            razorpay_payment_id=razorpay_payment_id,
            reason=(
                "signature_mismatch"
                if stored_razorpay_order_id == razorpay_order_id
                else "signature_mismatch_and_posted_order_id_differs"
            ),
        )

    payment = db.scalar(
        select(Payment).where(
            Payment.order_id == order.id,
            Payment.razorpay_payment_id == razorpay_payment_id,
        )
    )
    if payment is None:
        payment = Payment(
            id=new_payment_id(),
            order_id=order.id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_order_id=stored_razorpay_order_id,
            amount_paise=order.amount_paise,
            currency=order.currency,
            status="CAPTURED",
            signature_verified=True,
            source="CHECKOUT",
            # The browser's claim, kept as evidence. It is never read back as a
            # status: the status above was set because the HMAC verified.
            raw_payload={
                "razorpay_payment_id": razorpay_payment_id,
                "razorpay_order_id": razorpay_order_id,
            },
        )
        db.add(payment)
    else:
        payment.status = "CAPTURED"
        payment.signature_verified = True
        payment.source = "CHECKOUT"
        payment.updated_at = _now()

    order.status = "PAID"
    order.updated_at = _now()
    db.flush()

    emit(
        db,
        correlation_id=order.correlation_id,
        event_type="PAYMENT_VERIFIED",
        actor="platform",
        payload={
            "order_id": order.id,
            "payment_id": payment.id,
            "razorpay_order_id": stored_razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "amount_paise": payment.amount_paise,
            "currency": payment.currency,
            "method": payment.method,
            "status": payment.status,
            "signature_verified": True,
        },
    )
    db.commit()
    return payment


def _reject(
    db: Session, *, order: Order, razorpay_payment_id: str, reason: str
) -> NoReturn:
    """Record the rejection and raise. Writes no payment status and no order status."""
    emit(
        db,
        correlation_id=order.correlation_id,
        event_type="PAYMENT_SIGNATURE_INVALID",
        actor="platform",
        payload={
            "order_id": order.id,
            "razorpay_order_id": order.razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "reason": reason,
        },
    )
    # The order stays PENDING_PAYMENT. Only the audit row is committed.
    db.commit()
    raise KavachError(
        "PAYMENT_SIGNATURE_INVALID",
        "The checkout signature did not verify against our order.",
        correlation_id=order.correlation_id,
        detail={"order_id": order.id, "reason": reason},
    )


# -- 12.6 reconciliation ---------------------------------------------------


def reconcile(db: Session, order_id: str) -> dict[str, Any]:
    """Compare Razorpay's record with ours. Read-only: writes no payment status."""
    order = get_order(db, order_id)
    if not order.razorpay_order_id:
        raise KavachError(
            "ORDER_NOT_FOUND",
            f"Order {order.id} has no Razorpay order to reconcile against.",
            correlation_id=order.correlation_id,
            detail={"order_id": order.id},
        )

    try:
        response = razorpay_client.fetch_order_payments(order.razorpay_order_id)
    except razorpay_client.RazorpayError as exc:
        raise KavachError(
            "RAZORPAY_ERROR",
            "Razorpay would not return the payments for this order.",
            correlation_id=order.correlation_id,
            detail={"status_code": exc.status_code, "razorpay": exc.body},
        ) from exc

    items = response.get("items") or []
    captured = [item for item in items if item.get("status") == "captured"]
    captured_amount_paise = sum(int(item.get("amount") or 0) for item in captured)

    discrepancies: list[dict[str, Any]] = []
    if order.status == "PAID" and not captured:
        discrepancies.append(
            {
                "code": "PAID_WITHOUT_CAPTURED_PAYMENT",
                "detail": (
                    "We hold this order as PAID; Razorpay reports no captured payment."
                ),
            }
        )
    if order.status != "PAID" and captured:
        discrepancies.append(
            {
                "code": "CAPTURED_BUT_NOT_PAID",
                "detail": (
                    "Razorpay reports a captured payment; we hold the order at "
                    f"{order.status}."
                ),
            }
        )
    if captured and captured_amount_paise != order.amount_paise:
        discrepancies.append(
            {
                "code": "AMOUNT_MISMATCH",
                "detail": "The captured amount differs from the order amount.",
                "observed": captured_amount_paise,
                "threshold": order.amount_paise,
                "unit": "paise",
            }
        )

    emit(
        db,
        correlation_id=order.correlation_id,
        event_type="RECONCILIATION_PERFORMED",
        actor="platform",
        payload={
            "order_id": order.id,
            "razorpay_order_id": order.razorpay_order_id,
            "before_status": order.status,
            # A comparison, not a correction. §12.6 never writes payment status,
            # so before and after are the same status by construction.
            "after_status": order.status,
            "changed": False,
        },
    )
    db.commit()

    return {
        "order_id": order.id,
        "our_status": order.status,
        "our_amount_paise": order.amount_paise,
        "razorpay": {
            "payment_count": len(items),
            "captured_count": len(captured),
            "captured_amount_paise": captured_amount_paise,
        },
        "reconciled": not discrepancies,
        "discrepancies": discrepancies,
    }


# -- endpoints -------------------------------------------------------------


class PaymentVerifyIn(BaseModel):
    order_id: str
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str


class PaymentVerifyOut(BaseModel):
    order_id: str
    order_status: str
    signature_verified: bool
    payment: PaymentOut


class ReconcileRazorpayOut(BaseModel):
    payment_count: int
    captured_count: int
    captured_amount_paise: int


class ReconcileOut(BaseModel):
    order_id: str
    our_status: str
    our_amount_paise: int
    razorpay: ReconcileRazorpayOut
    reconciled: bool
    discrepancies: list[dict[str, Any]]


@router.post("/verify", response_model=PaymentVerifyOut)
def post_verify(
    body: PaymentVerifyIn, db: Session = Depends(get_db)
) -> PaymentVerifyOut:
    """§12.4 - the only endpoint that can turn a browser's word into a PAID order."""
    payment = verify_checkout_signature(
        db,
        order_id=body.order_id,
        razorpay_payment_id=body.razorpay_payment_id,
        razorpay_order_id=body.razorpay_order_id,
        razorpay_signature=body.razorpay_signature,
    )
    order = get_order(db, body.order_id)
    return PaymentVerifyOut(
        order_id=order.id,
        order_status=order.status,
        signature_verified=payment.signature_verified,
        payment=PaymentOut.model_validate(payment),
    )


@router.get("/{order_id}/reconcile", response_model=ReconcileOut)
def get_reconcile(order_id: str, db: Session = Depends(get_db)) -> ReconcileOut:
    """§12.6 - answers 'how do you know?' without asking anyone to take our word."""
    return ReconcileOut(**reconcile(db, order_id))
