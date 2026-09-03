"""Tests 11 to 16 — checkout verification and webhooks (§17, §12.4, §12.5).

Two signature schemes appear here and they are never interchangeable. The
checkout response signature is HMAC-SHA256 over `order_id|payment_id` keyed by
`RAZORPAY_KEY_SECRET`. The webhook signature is HMAC-SHA256 over the **raw
request bytes** keyed by `RAZORPAY_WEBHOOK_SECRET`. Test 16 exists to prove the
handler really does use the raw bytes.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from sqlalchemy import func, select

from app.config import settings
from app.models import AuditEvent, Order, Payment, WebhookEvent
from app.platform import payments
from tests.factories import (
    DEMO_CAP_PAISE,
    MCH_PROTEIN,
    build_quote,
    issue_mandate,
    refetch,
)

CORRECT_CART = [("PK-001", 8), ("PK-003", 4)]


def make_order(db, now) -> Order:
    """An order in `PENDING_PAYMENT` with a Razorpay order id, via the real path."""
    mandate = issue_mandate(
        db,
        now=now,
        allowed_merchant_ids=[MCH_PROTEIN],
        max_amount_paise=DEMO_CAP_PAISE,
    )
    quote = build_quote(db, merchant_id=MCH_PROTEIN, items=CORRECT_CART)
    result = payments.execute_authorized_purchase(
        db,
        session_id=None,
        correlation_id=quote.correlation_id,
        mandate_id=mandate.id,
        quote_id=quote.id,
        merchant_id=quote.merchant_id,
        requested_total_paise=int(quote.total_paise),
        currency=quote.currency,
        idempotency_key=f"idem_{quote.id}",
    )
    assert result.guard.verdict == "ALLOW"
    assert len(result.guard.rules) == 9
    assert result.order.status == "PENDING_PAYMENT"
    return result.order


def webhook_body(*, event: str, razorpay_order_id: str, payment_id: str) -> dict:
    return {
        "entity": "event",
        "event": event,
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "entity": "payment",
                    "amount": 516_000,
                    "currency": "INR",
                    "status": "captured" if event != "payment.failed" else "failed",
                    "order_id": razorpay_order_id,
                    "method": "netbanking",
                }
            }
        },
    }


def sign_raw(raw: bytes) -> str:
    return hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"), raw, hashlib.sha256
    ).hexdigest()


def post_webhook(client, raw: bytes, *, signature: str, event_id: str):
    return client.post(
        "/api/webhooks/razorpay",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": signature,
            "x-razorpay-event-id": event_id,
        },
    )


# -- 11 --------------------------------------------------------------------


def test_11_valid_checkout_signature_pays_the_order(db, client, razorpay, now):
    order = make_order(db, now)
    payment_id = "pay_TESTCHECKOUT01"
    signature = payments.expected_checkout_signature(
        order.razorpay_order_id, payment_id
    )

    response = client.post(
        "/api/payments/verify",
        json={
            "order_id": order.id,
            "razorpay_payment_id": payment_id,
            "razorpay_order_id": order.razorpay_order_id,
            "razorpay_signature": signature,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["order_status"] == "PAID"
    assert body["signature_verified"] is True
    assert body["payment"]["status"] == "CAPTURED"

    stored = refetch(db, Order, order.id)
    assert stored.status == "PAID"
    payment = db.scalar(select(Payment).where(Payment.order_id == order.id))
    assert payment.status == "CAPTURED"
    assert payment.signature_verified is True
    assert payment.source == "CHECKOUT"


# -- 12 --------------------------------------------------------------------


def test_12_invalid_checkout_signature_writes_no_payment_status(
    db, client, razorpay, now
):
    """T-06: a forged checkout callback posted from the browser.

    The HMAC is computed server-side over the `razorpay_order_id` in **our**
    database, so a signature the attacker computed over their own order id
    cannot be made to verify.
    """
    order = make_order(db, now)
    forged_order_id = "order_ATTACKERCHOSE"
    payment_id = "pay_TESTCHECKOUT02"
    # Correctly signed - but over the attacker's order id, not ours.
    signature = payments.expected_checkout_signature(forged_order_id, payment_id)

    response = client.post(
        "/api/payments/verify",
        json={
            "order_id": order.id,
            "razorpay_payment_id": payment_id,
            "razorpay_order_id": forged_order_id,
            "razorpay_signature": signature,
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PAYMENT_SIGNATURE_INVALID"

    stored = refetch(db, Order, order.id)
    assert stored.status == "PENDING_PAYMENT"
    assert db.scalar(select(func.count()).select_from(Payment)) == 0


# -- 13 --------------------------------------------------------------------


def test_13_the_same_event_id_twice_produces_one_state_transition(
    db, client, razorpay, now
):
    """T-04. The dedup is a UNIQUE constraint and a caught IntegrityError."""
    order = make_order(db, now)
    raw = json.dumps(
        webhook_body(
            event="payment.captured",
            razorpay_order_id=order.razorpay_order_id,
            payment_id="pay_TESTWEBHOOK01",
        )
    ).encode("utf-8")
    signature = sign_raw(raw)

    first = post_webhook(client, raw, signature=signature, event_id="evt_dedup_001")
    second = post_webhook(client, raw, signature=signature, event_id="evt_dedup_001")

    assert first.status_code == 200
    assert first.json()["was_duplicate"] is False
    assert first.json()["state_changed"] is True

    # A duplicate is a business outcome, and every business outcome is a 200.
    assert second.status_code == 200
    assert second.json()["was_duplicate"] is True
    assert second.json()["state_changed"] is False

    stored = refetch(db, Order, order.id)
    assert stored.status == "PAID"
    assert db.scalar(select(func.count()).select_from(WebhookEvent)) == 1
    completed = db.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.event_type == "ORDER_COMPLETED")
    )
    assert completed == 1


# -- 14 --------------------------------------------------------------------


def test_14_invalid_webhook_signature_changes_nothing(db, client, razorpay, now):
    order = make_order(db, now)
    raw = json.dumps(
        webhook_body(
            event="payment.captured",
            razorpay_order_id=order.razorpay_order_id,
            payment_id="pay_TESTWEBHOOK02",
        )
    ).encode("utf-8")

    response = post_webhook(
        client, raw, signature="deadbeef" * 8, event_id="evt_forged_001"
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "WEBHOOK_SIGNATURE_INVALID"

    stored = refetch(db, Order, order.id)
    assert stored.status == "PENDING_PAYMENT"
    assert db.scalar(select(func.count()).select_from(Payment)) == 0
    # The forgery is kept as evidence, marked as what it is.
    event = db.scalar(select(WebhookEvent))
    assert event is not None
    assert event.signature_valid is False


# -- 15 --------------------------------------------------------------------


def test_15_a_late_payment_failed_does_not_un_pay_a_paid_order(
    db, client, razorpay, now
):
    """T-19. Documented Razorpay behaviour; transitions are monotonic to PAID."""
    order = make_order(db, now)
    payment_id = "pay_TESTWEBHOOK03"

    captured = json.dumps(
        webhook_body(
            event="payment.captured",
            razorpay_order_id=order.razorpay_order_id,
            payment_id=payment_id,
        )
    ).encode("utf-8")
    post_webhook(
        client, captured, signature=sign_raw(captured), event_id="evt_captured_001"
    )
    assert refetch(db, Order, order.id).status == "PAID"

    failed = json.dumps(
        webhook_body(
            event="payment.failed",
            razorpay_order_id=order.razorpay_order_id,
            payment_id=payment_id,
        )
    ).encode("utf-8")
    response = post_webhook(
        client, failed, signature=sign_raw(failed), event_id="evt_failed_001"
    )

    assert response.status_code == 200
    assert response.json()["state_changed"] is False

    stored = refetch(db, Order, order.id)
    assert stored.status == "PAID"
    payment = db.scalar(select(Payment).where(Payment.order_id == order.id))
    assert payment.status == "CAPTURED"


# -- 16 --------------------------------------------------------------------


def test_16_a_re_serialised_body_fails_verification(db, client, razorpay, now):
    """T-05, the mechanism. The digest is over the bytes Razorpay sent.

    The body below is sent with indentation. A signature computed over the
    re-serialised compact form of the same JSON is rejected; a signature
    computed over the bytes as sent is accepted. The only difference is
    whitespace, which is exactly the difference a handler that parses before it
    verifies would lose.
    """
    order = make_order(db, now)
    body = webhook_body(
        event="payment.captured",
        razorpay_order_id=order.razorpay_order_id,
        payment_id="pay_TESTWEBHOOK04",
    )
    raw = json.dumps(body, indent=2).encode("utf-8")
    re_serialised = json.dumps(json.loads(raw), separators=(",", ":")).encode("utf-8")
    assert raw != re_serialised

    rejected = post_webhook(
        client, raw, signature=sign_raw(re_serialised), event_id="evt_reserialised_1"
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "WEBHOOK_SIGNATURE_INVALID"
    assert refetch(db, Order, order.id).status == "PENDING_PAYMENT"

    accepted = post_webhook(
        client, raw, signature=sign_raw(raw), event_id="evt_reserialised_2"
    )
    assert accepted.status_code == 200
    assert accepted.json()["signature_valid"] is True
    assert refetch(db, Order, order.id).status == "PAID"
