"""Tests 1 to 10 — the Transaction Guard (BUILD_SPEC §17, §9).

Every evaluation here goes through `guard.evaluate()` with an injected `now`.
`evaluate_for()` asserts the two things §17 asks to be asserted throughout: on
ALLOW **and** on BLOCK, exactly one `guard_decisions` row is written, and it
carries all nine rule results.

Two tests at the end are **additions beyond §17**. §17 predates the M5a/M5b
binding hardening, and the two rules that changed — MG-001 and MG-008 — now
check that the row a rule reads still says what the signature covers. Those
checks are the most interesting security work in the project and were
otherwise untested.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.crypto import verify
from app.ids import new_correlation_id, new_guard_decision_id
from app.models import AuditEvent, GuardDecision, Order
from app.platform import guard, payments, razorpay_client
from app.platform.guard import GuardBlocked
from tests.factories import (
    DEMO_CAP_PAISE,
    MCH_NOVA,
    MCH_PROTEIN,
    POISONED_CART_PAISE,
    build_quote,
    issue_mandate,
)

CORRECT_CART = [("PK-001", 8), ("PK-003", 4)]
POISONED_CART = [("PK-001", 8), ("PK-003", 4), ("PK-005", 12)]


def evaluate_for(db, *, quote, mandate, now, requested_total_paise=None, merchant_id=None):
    """Run the Guard and assert the §17 invariants that hold on every verdict."""
    before = db.scalar(select(func.count()).select_from(GuardDecision))
    result = guard.evaluate(
        db,
        correlation_id=quote.correlation_id or new_correlation_id(),
        session_id=None,
        mandate_id=mandate.id,
        quote_id=quote.id,
        merchant_id=merchant_id or quote.merchant_id,
        requested_total_paise=(
            requested_total_paise
            if requested_total_paise is not None
            else int(quote.total_paise)
        ),
        currency="INR",
        now=now,
    )
    after = db.scalar(select(func.count()).select_from(GuardDecision))

    # Invariant 10: recorded on ALLOW as well as BLOCK, exactly one row, all nine.
    assert after == before + 1
    assert len(result.rules) == 9
    row = db.get(GuardDecision, result.decision_id)
    assert row is not None
    assert row.verdict == result.verdict
    assert len(row.rules) == 9
    return result


def rule(result, rule_id):
    return next(r for r in result.rules if r.rule_id == rule_id)


# -- 1 ---------------------------------------------------------------------


def test_01_mg001_blocks_a_tampered_mandate_payload(db, now):
    """MG-001 blocks a mandate whose `signing_payload` was edited after signing."""
    mandate = issue_mandate(db, now=now, allowed_merchant_ids=[MCH_PROTEIN])
    quote = build_quote(db, merchant_id=MCH_PROTEIN, items=CORRECT_CART)

    payload = dict(mandate.signing_payload)
    payload["max_amount_paise"] = 5_000_000
    mandate.signing_payload = payload
    db.commit()

    result = evaluate_for(db, quote=quote, mandate=mandate, now=now)

    assert result.verdict == "BLOCK"
    assert result.failed_rule_id == "MG-001"
    assert result.block_code == "MANDATE_SIGNATURE_INVALID"
    assert rule(result, "MG-001").observed == "invalid"


# -- 2 ---------------------------------------------------------------------


def test_02_mg002_blocks_a_revoked_mandate(db, now):
    mandate = issue_mandate(
        db, now=now, allowed_merchant_ids=[MCH_PROTEIN], status="REVOKED"
    )
    mandate.revoked_at = now
    db.commit()
    quote = build_quote(db, merchant_id=MCH_PROTEIN, items=CORRECT_CART)

    result = evaluate_for(db, quote=quote, mandate=mandate, now=now)

    assert result.verdict == "BLOCK"
    # The signature is untouched: status is not a signed field, so MG-001 passes
    # and MG-002 is the lowest-numbered failure.
    assert rule(result, "MG-001").passed is True
    assert result.failed_rule_id == "MG-002"
    assert result.block_code == "MANDATE_NOT_ACTIVE"
    assert rule(result, "MG-002").observed == "REVOKED"


# -- 3 ---------------------------------------------------------------------


def test_03_mg003_blocks_an_expired_mandate(db, now):
    issued = now - timedelta(minutes=90)
    mandate = issue_mandate(
        db, now=issued, allowed_merchant_ids=[MCH_PROTEIN], ttl_minutes=30
    )
    quote = build_quote(db, merchant_id=MCH_PROTEIN, items=CORRECT_CART)

    # `now` is injected: the mandate expired 60 minutes before it.
    result = evaluate_for(db, quote=quote, mandate=mandate, now=now)

    assert result.verdict == "BLOCK"
    assert rule(result, "MG-001").passed is True
    assert result.failed_rule_id == "MG-003"
    assert result.block_code == "MANDATE_EXPIRED"


# -- 4 ---------------------------------------------------------------------


def test_04_mg004_blocks_a_merchant_off_the_allowlist(db, now):
    mandate = issue_mandate(db, now=now, allowed_merchant_ids=[MCH_NOVA])
    quote = build_quote(db, merchant_id=MCH_PROTEIN, items=CORRECT_CART)

    result = evaluate_for(db, quote=quote, mandate=mandate, now=now)

    assert result.verdict == "BLOCK"
    assert result.failed_rule_id == "MG-004"
    assert result.block_code == "MERCHANT_NOT_ALLOWED"
    assert rule(result, "MG-004").observed == MCH_PROTEIN
    assert rule(result, "MG-004").threshold == [MCH_NOVA]


# -- 5 ---------------------------------------------------------------------


def test_05_mg005_blocks_756000_against_a_600000_cap(db, now):
    """The demo block, exactly (§16.3): the poisoned cart against the cap."""
    mandate = issue_mandate(
        db,
        now=now,
        allowed_merchant_ids=[MCH_PROTEIN],
        max_amount_paise=DEMO_CAP_PAISE,
    )
    quote = build_quote(db, merchant_id=MCH_PROTEIN, items=POISONED_CART)
    assert quote.total_paise == POISONED_CART_PAISE

    result = evaluate_for(db, quote=quote, mandate=mandate, now=now)

    assert result.verdict == "BLOCK"
    assert result.failed_rule_id == "MG-005"
    assert result.block_code == "AMOUNT_EXCEEDS_MANDATE"
    assert rule(result, "MG-005").observed == 756_000
    assert rule(result, "MG-005").threshold == 600_000
    assert rule(result, "MG-005").unit == "paise"


# -- 6 ---------------------------------------------------------------------


def test_06_mg006_blocks_when_prior_allows_consumed_the_cumulative_cap(db, now):
    mandate = issue_mandate(
        db,
        now=now,
        allowed_merchant_ids=[MCH_PROTEIN],
        max_amount_paise=DEMO_CAP_PAISE,
        cumulative_cap_paise=DEMO_CAP_PAISE,
        max_transactions=3,
    )
    quote = build_quote(db, merchant_id=MCH_PROTEIN, items=CORRECT_CART)

    # One prior ALLOW under this mandate has already spent 500 000.
    db.add(
        GuardDecision(
            id=new_guard_decision_id(),
            correlation_id=new_correlation_id(),
            mandate_id=mandate.id,
            quote_id=None,
            merchant_id=MCH_PROTEIN,
            requested_total_paise=500_000,
            verdict="ALLOW",
            failed_rule_id=None,
            block_code=None,
            rules=[],
            duration_ms=1,
            evaluated_at=now - timedelta(minutes=1),
        )
    )
    db.commit()

    result = evaluate_for(db, quote=quote, mandate=mandate, now=now)

    assert result.verdict == "BLOCK"
    assert rule(result, "MG-005").passed is True  # 516 000 is within the cap
    assert result.failed_rule_id == "MG-006"
    assert result.block_code == "CUMULATIVE_CAP_EXCEEDED"
    assert rule(result, "MG-006").observed == 1_016_000
    assert rule(result, "MG-006").threshold == 600_000


# -- 7 ---------------------------------------------------------------------


def test_07_mg007_blocks_a_stationery_line_under_a_meals_mandate(db, now):
    mandate = issue_mandate(
        db,
        now=now,
        allowed_merchant_ids=[MCH_NOVA],
        allowed_categories=["meals"],
    )
    quote = build_quote(db, merchant_id=MCH_NOVA, items=[("NS-001", 2)])

    result = evaluate_for(db, quote=quote, mandate=mandate, now=now)

    assert result.verdict == "BLOCK"
    assert rule(result, "MG-004").passed is True  # the merchant *is* allowlisted
    assert result.failed_rule_id == "MG-007"
    assert result.block_code == "CATEGORY_NOT_ALLOWED"
    assert rule(result, "MG-007").observed == ["stationery"]


# -- 8 ---------------------------------------------------------------------


def test_08_mg008_blocks_line_totals_that_do_not_sum_to_total_paise(db, now):
    mandate = issue_mandate(db, now=now, allowed_merchant_ids=[MCH_PROTEIN])
    quote = build_quote(db, merchant_id=MCH_PROTEIN, items=CORRECT_CART)

    # The signed line items still sum to 516 000; the row now claims 516 100.
    quote.total_paise = 516_100
    db.commit()

    result = evaluate_for(
        db, quote=quote, mandate=mandate, now=now, requested_total_paise=516_100
    )

    assert result.verdict == "BLOCK"
    assert result.failed_rule_id == "MG-008"
    assert result.block_code == "QUOTE_INTEGRITY_FAILED"
    assert "sum_mismatch" in rule(result, "MG-008").observed


# -- 9 ---------------------------------------------------------------------


def test_09_mg009_blocks_the_fourth_transaction_on_a_three_transaction_mandate(
    db, now
):
    mandate = issue_mandate(
        db,
        now=now,
        allowed_merchant_ids=[MCH_PROTEIN],
        max_amount_paise=100_000,
        cumulative_cap_paise=2_000_000,
        max_transactions=3,
    )
    quote = build_quote(db, merchant_id=MCH_PROTEIN, items=[("PK-001", 1)])

    for _ in range(3):
        db.add(
            GuardDecision(
                id=new_guard_decision_id(),
                correlation_id=new_correlation_id(),
                mandate_id=mandate.id,
                quote_id=None,
                merchant_id=MCH_PROTEIN,
                requested_total_paise=42_000,
                verdict="ALLOW",
                failed_rule_id=None,
                block_code=None,
                rules=[],
                duration_ms=1,
                evaluated_at=now - timedelta(minutes=1),
            )
        )
    db.commit()

    result = evaluate_for(db, quote=quote, mandate=mandate, now=now)

    assert result.verdict == "BLOCK"
    assert rule(result, "MG-006").passed is True
    assert result.failed_rule_id == "MG-009"
    assert result.block_code == "VELOCITY_LIMIT_EXCEEDED"
    assert rule(result, "MG-009").observed == 3
    assert rule(result, "MG-009").threshold == 3


# -- 10 --------------------------------------------------------------------


def test_10_a_blocked_purchase_never_reaches_razorpay(db, now, monkeypatch):
    """Invariant 2, proved mechanically.

    `razorpay_client.create_order` is replaced by a function that fails the
    test if it is ever entered. The BLOCK path then runs end to end through the
    single guard call site. It completes cleanly, raises `GuardBlocked`, and
    the mock is never called.
    """
    calls: list[dict] = []

    def exploding_create_order(**kwargs):
        calls.append(kwargs)
        raise AssertionError(
            "razorpay_client.create_order was reached on a BLOCK path. "
            "Invariant 2 is broken."
        )

    monkeypatch.setattr(razorpay_client, "create_order", exploding_create_order)

    mandate = issue_mandate(
        db,
        now=now,
        allowed_merchant_ids=[MCH_PROTEIN],
        max_amount_paise=DEMO_CAP_PAISE,
    )
    quote = build_quote(db, merchant_id=MCH_PROTEIN, items=POISONED_CART)

    with pytest.raises(GuardBlocked) as raised:
        payments.execute_authorized_purchase(
            db,
            session_id=None,
            correlation_id=quote.correlation_id,
            mandate_id=mandate.id,
            quote_id=quote.id,
            merchant_id=quote.merchant_id,
            requested_total_paise=int(quote.total_paise),
            currency=quote.currency,
            idempotency_key="idem_block_path",
        )

    result = raised.value.result
    assert calls == []
    assert result.verdict == "BLOCK"
    assert result.failed_rule_id == "MG-005"
    assert raised.value.code == "AMOUNT_EXCEEDS_MANDATE"
    assert len(result.rules) == 9

    # No order came into existence, and the decision that refused it did.
    db.commit()
    assert db.scalar(select(func.count()).select_from(Order)) == 0
    decisions = list(db.scalars(select(GuardDecision)).all())
    assert len(decisions) == 1
    assert decisions[0].verdict == "BLOCK"
    assert len(decisions[0].rules) == 9

    blocked = db.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.event_type == "POLICY_BLOCKED")
    )
    assert blocked == 1


# -- additions beyond §17 --------------------------------------------------


def test_addition_a1_mg001_blocks_a_tampered_column_whose_signature_still_verifies(db, now):
    """Addition beyond §17 — the M5a binding fix on the mandate side.

    The signature covers `signing_payload`; MG-003 to MG-007 and MG-009 read
    the **columns**. Nothing bound the two. Raising `max_amount_paise` on the
    row leaves the signature verifying perfectly and would have let a 756 000
    cart through a 600 000 grant, because MG-005 reads the column. MG-001 now
    reconciles the two and refuses.
    """
    mandate = issue_mandate(
        db,
        now=now,
        allowed_merchant_ids=[MCH_PROTEIN],
        max_amount_paise=DEMO_CAP_PAISE,
    )
    quote = build_quote(db, merchant_id=MCH_PROTEIN, items=POISONED_CART)

    mandate.max_amount_paise = 1_000_000
    db.commit()

    # The signature is genuine. This is not a forgery.
    assert verify(
        guard.mandate_authority_public_key(),
        mandate.signing_payload,
        mandate.signature,
    )

    result = evaluate_for(db, quote=quote, mandate=mandate, now=now)

    assert result.verdict == "BLOCK"
    assert result.failed_rule_id == "MG-001"
    assert result.block_code == "MANDATE_SIGNATURE_INVALID"
    assert "max_amount_paise" in rule(result, "MG-001").observed
    # And this is what it saved: the raised column satisfied MG-005.
    assert rule(result, "MG-005").passed is True


def test_addition_a2_mg008_blocks_a_quote_row_that_disagrees_with_its_signed_payload(db, now):
    """Addition beyond §17 — the M5b binding fix on the quote side.

    MG-008 verified the merchant signature and then reconciled only
    `total_paise` back to it, while reading `status`, `expires_at`,
    `merchant_id` and `currency` from columns the signature did not bind. It
    now reconciles the whole payload.
    """
    mandate = issue_mandate(db, now=now, allowed_merchant_ids=[MCH_PROTEIN])
    quote = build_quote(db, merchant_id=MCH_PROTEIN, items=CORRECT_CART)

    quote.currency = "USD"
    db.commit()

    # Again: the merchant signature is genuine and still verifies.
    assert verify(
        guard.merchant_public_key(), quote.signing_payload, quote.signature
    )

    result = evaluate_for(db, quote=quote, mandate=mandate, now=now)

    assert result.verdict == "BLOCK"
    assert result.failed_rule_id == "MG-008"
    assert result.block_code == "QUOTE_INTEGRITY_FAILED"
    assert "currency_mismatch" in rule(result, "MG-008").observed
