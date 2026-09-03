"""Tests 17 to 20 — the merchant plane (§17, §7.7, §7.8, §10).

Two tests at the end are **additions beyond §17**, covering the M5a binding
fix: a Guard ALLOW is an authorisation for one submission, not a bearer token.
`scripts/demo_decision_binding.py` mounts both attacks over HTTP and shows the
409; these assert the same refusals in the suite.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.crypto import verify
from app.errors import KavachError
from app.merchant import service as merchant_service
from app.models import CartItem, Order, Product
from app.platform import guard, payments
from tests.factories import (
    DEMO_CAP_PAISE,
    MCH_PROTEIN,
    MERCHANT_HEADERS,
    build_quote,
    issue_mandate,
    refetch,
)

CORRECT_CART = [("PK-001", 8), ("PK-003", 4)]


def product(db, sku: str) -> Product:
    return db.scalar(
        select(Product).where(
            Product.merchant_id == MCH_PROTEIN, Product.sku == sku
        )
    )


def allow_and_submit(db, *, mandate, quote, idempotency_key: str):
    return payments.execute_authorized_purchase(
        db,
        session_id=None,
        correlation_id=quote.correlation_id,
        mandate_id=mandate.id,
        quote_id=quote.id,
        merchant_id=quote.merchant_id,
        requested_total_paise=int(quote.total_paise),
        currency=quote.currency,
        idempotency_key=idempotency_key,
    )


# -- 17 --------------------------------------------------------------------


def test_17_the_quote_ignores_the_cart_price_snapshot(db):
    """§7.7. Every figure is re-read from `products` inside the transaction.

    `cart_items.unit_price_paise_snapshot` exists so the UI can show what the
    agent thought the price was. A caller that rewrites it changes nothing.
    """
    cart = merchant_service.create_cart(
        db, merchant_id=MCH_PROTEIN, session_id=None, correlation_id=None
    )
    merchant_service.add_cart_item(db, cart.id, sku="PK-001", qty=8)
    merchant_service.add_cart_item(db, cart.id, sku="PK-003", qty=4)

    for item in db.scalars(select(CartItem).where(CartItem.cart_id == cart.id)).all():
        item.unit_price_paise_snapshot = 1
    db.commit()

    quote = merchant_service.create_quote(db, cart.id)

    assert quote.total_paise == 516_000
    signed = {li["sku"]: li for li in quote.signing_payload["line_items"]}
    assert signed["PK-001"]["unit_price_paise"] == 42_000
    assert signed["PK-003"]["unit_price_paise"] == 45_000
    assert signed["PK-001"]["line_total_paise"] == 336_000
    assert signed["PK-003"]["line_total_paise"] == 180_000


# -- 18 --------------------------------------------------------------------


def test_18_the_quote_signature_verifies_and_one_paise_breaks_it(db):
    quote = build_quote(db, merchant_id=MCH_PROTEIN, items=CORRECT_CART)
    public_key = guard.merchant_public_key()

    assert verify(public_key, quote.signing_payload, quote.signature) is True

    tampered = dict(quote.signing_payload)
    tampered["total_paise"] = int(tampered["total_paise"]) + 1
    assert verify(public_key, tampered, quote.signature) is False


# -- 19 --------------------------------------------------------------------


def test_19_cv003_rejects_a_submit_after_a_price_drift(db, razorpay, now):
    """T-07. CV-003 re-reads prices and requires an exact match, not a tolerance."""
    mandate = issue_mandate(
        db,
        now=now,
        allowed_merchant_ids=[MCH_PROTEIN],
        max_amount_paise=DEMO_CAP_PAISE,
    )
    quote = build_quote(db, merchant_id=MCH_PROTEIN, items=CORRECT_CART)

    # The merchant raises PK-003 after signing: 45000 -> 49500.
    pk003 = product(db, "PK-003")
    pk003.unit_price_paise = 49_500
    db.commit()

    with pytest.raises(KavachError) as raised:
        allow_and_submit(db, mandate=mandate, quote=quote, idempotency_key="idem_drift")

    error = raised.value
    assert error.code == "MERCHANT_PRICE_DRIFT"
    assert error.status_code == 409
    assert error.detail["failed_check_id"] == "CV-003"

    # The Guard said ALLOW; the merchant refused. No order, no Razorpay call.
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Order)) == 0
    assert razorpay.count == 0


# -- 20 --------------------------------------------------------------------


def test_20_a_duplicate_idempotency_key_replays_the_original_order(
    db, client, razorpay, now
):
    """T-09. One key, one order, one Razorpay order — however many times it arrives."""
    mandate = issue_mandate(
        db,
        now=now,
        allowed_merchant_ids=[MCH_PROTEIN],
        max_amount_paise=DEMO_CAP_PAISE,
    )
    quote = build_quote(db, merchant_id=MCH_PROTEIN, items=CORRECT_CART)
    key = "idem_replay_001"

    first = allow_and_submit(db, mandate=mandate, quote=quote, idempotency_key=key)
    assert first.guard.verdict == "ALLOW"
    assert len(first.guard.rules) == 9
    assert razorpay.count == 1
    db.commit()

    body = {
        "quote_id": quote.id,
        "mandate_signature": mandate.signature,
        "mandate_signing_payload": mandate.signing_payload,
        "guard_decision_id": first.guard.decision_id,
        "correlation_id": first.order.correlation_id,
    }
    replay = client.post(
        "/merchant/checkout/submit",
        json=body,
        headers={**MERCHANT_HEADERS, "Idempotency-Key": key},
    )

    assert replay.status_code == 200
    assert replay.headers.get("X-Idempotent-Replay") == "true"
    assert replay.json()["order_id"] == first.order.id
    assert replay.json()["razorpay_order_id"] == first.order.razorpay_order_id

    # Only one Razorpay order was ever created, and only one order row exists.
    assert razorpay.count == 1
    assert db.scalar(select(func.count()).select_from(Order)) == 1
    assert refetch(db, Order, first.order.id).status == "PENDING_PAYMENT"


# -- additions beyond §17 --------------------------------------------------


def test_addition_a3_an_allow_presented_with_a_different_quote_is_refused(
    db, razorpay, now
):
    """Addition beyond §17 — the M5a binding fix, quote side.

    The merchant used to check only `verdict == ALLOW`. Any past ALLOW was
    therefore a bearer token: a decision reached about a 516 000 cart could be
    attached to an independently valid 756 000 cart and be charged the larger
    amount, because MG-005, MG-006 and MG-009 were evaluated against a total
    that is not the one being charged. The validator would not catch it —
    CV-001 to CV-004 never look at the amount against the mandate.
    """
    mandate = issue_mandate(
        db,
        now=now,
        allowed_merchant_ids=[MCH_PROTEIN],
        max_amount_paise=DEMO_CAP_PAISE,
    )
    authorised = build_quote(db, merchant_id=MCH_PROTEIN, items=CORRECT_CART)
    result = allow_and_submit(
        db, mandate=mandate, quote=authorised, idempotency_key="idem_bind_quote_ok"
    )
    assert result.guard.verdict == "ALLOW"
    calls_before = razorpay.count

    # A different, independently valid quote, presented under the same ALLOW.
    other = build_quote(db, merchant_id=MCH_PROTEIN, items=[("PK-005", 12)])

    with pytest.raises(KavachError) as raised:
        merchant_service.submit_checkout(
            db,
            quote_id=other.id,
            mandate_signing_payload=mandate.signing_payload,
            mandate_signature=mandate.signature,
            guard_decision_id=result.guard.decision_id,
            idempotency_key="idem_bind_quote_attack",
            correlation_id=other.correlation_id,
        )

    error = raised.value
    assert error.code == "MERCHANT_MANDATE_INVALID"
    assert error.status_code == 409
    assert error.detail["field"] == "quote_id"

    db.rollback()
    assert razorpay.count == calls_before
    assert db.scalar(select(func.count()).select_from(Order)) == 1


def test_addition_a4_an_allow_presented_with_a_different_mandate_is_refused(
    db, razorpay, now
):
    """Addition beyond §17 — the M5a binding fix, mandate side.

    CV-001 verifies whatever mandate it is handed on that mandate's own terms;
    it has no way to know which mandate the Guard actually evaluated. Without
    this comparison an ALLOW reached against one mandate's caps could be spent
    under another's name, and MG-006 and MG-009 — which count prior ALLOWs per
    mandate — would be counting against a mandate that is not paying.
    """
    authorising = issue_mandate(
        db,
        now=now,
        allowed_merchant_ids=[MCH_PROTEIN],
        max_amount_paise=DEMO_CAP_PAISE,
    )
    other_mandate = issue_mandate(
        db,
        now=now,
        allowed_merchant_ids=[MCH_PROTEIN],
        max_amount_paise=DEMO_CAP_PAISE,
    )
    quote = build_quote(db, merchant_id=MCH_PROTEIN, items=CORRECT_CART)
    result = allow_and_submit(
        db, mandate=authorising, quote=quote, idempotency_key="idem_bind_mandate_ok"
    )
    assert result.guard.verdict == "ALLOW"
    calls_before = razorpay.count

    with pytest.raises(KavachError) as raised:
        merchant_service.submit_checkout(
            db,
            quote_id=quote.id,
            mandate_signing_payload=other_mandate.signing_payload,
            mandate_signature=other_mandate.signature,
            guard_decision_id=result.guard.decision_id,
            idempotency_key="idem_bind_mandate_attack",
            correlation_id=quote.correlation_id,
        )

    error = raised.value
    assert error.code == "MERCHANT_MANDATE_INVALID"
    assert error.status_code == 409
    assert error.detail["field"] == "mandate_id"

    db.rollback()
    assert razorpay.count == calls_before
    assert db.scalar(select(func.count()).select_from(Order)) == 1
