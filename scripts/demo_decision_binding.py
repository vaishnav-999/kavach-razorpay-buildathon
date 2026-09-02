"""M5 verification — an ALLOW decision does not transfer.

An `ALLOW` from the Transaction Guard is an authorisation for one quote, under
one mandate, for one amount, at one merchant. If the merchant accepted any past
`ALLOW` id, a decision would be a bearer token: CV-001 to CV-004 never compare
the amount to the mandate, and CV-001 has no way to know which mandate the
Guard actually read.

This script mounts that attack twice.

**Case 1 — a different quote.** The correct §16.3 cart (516 000) is authorised
and bought, producing a genuine ALLOW. A second cart is built: the poisoned
756 000 cart from `demo_block.py`, independently valid — ACTIVE quote, in
stock, no price drift, the same signed mandate verifies against it — so all
four validator checks pass. It is submitted carrying the first cart's
`guard_decision_id`. Expected: 409 MERCHANT_MANDATE_INVALID naming `quote_id`,
no order.

**Case 2 — a different mandate.** The same ALLOW is resubmitted alongside M2, a
second mandate signed by the same Mandate Authority for the same merchant and
category, which CV-001 would accept on its own terms. Only the decision binding
can tell that the Guard read M1. Expected: 409 naming `mandate_id`, no order.

    python scripts/demo_decision_binding.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._demo import (  # noqa: E402
    build_quote,
    heading,
    issue_mandate,
    new_correlation,
    open_session,
    orders_for_quote,
    primary_merchant,
)

from app.config import settings  # noqa: E402
from app.platform.guard import GuardBlocked, rupees  # noqa: E402
from app.platform.payments import execute_authorized_purchase  # noqa: E402

# §16.3 — the cart that is authorised, and the cart that tries to ride on it.
AUTHORISED_CART = [("PK-001", 8), ("PK-003", 4)]  # 516 000
STOLEN_CART = [("PK-001", 8), ("PK-003", 4), ("PK-005", 12)]  # 756 000


def main() -> int:
    from fastapi.testclient import TestClient

    from app.main import app

    db = open_session()
    try:
        merchant = primary_merchant(db)
        correlation_id = new_correlation()
        print(f"correlation_id  {correlation_id}")

        # ── 1. a genuine ALLOW for the correct cart ───────────────────────
        quote_a = build_quote(db, merchant, AUTHORISED_CART, correlation_id)
        mandate = issue_mandate(db, merchant, correlation_id)

        try:
            purchase = execute_authorized_purchase(
                db,
                session_id=None,
                correlation_id=correlation_id,
                mandate_id=mandate.id,
                quote_id=quote_a.id,
                merchant_id=merchant.id,
                requested_total_paise=quote_a.total_paise,
                currency=quote_a.currency,
                idempotency_key=f"binding-a:{correlation_id}",
            )
        except GuardBlocked as blocked:
            print(f"\nFAIL: the setup purchase was BLOCKED by "
                  f"{blocked.result.failed_rule_id}. It should have been ALLOWED.")
            return 1

        heading("STEP 1 — a genuine authorisation")
        print(f"  quote      {quote_a.id}  {rupees(quote_a.total_paise)}")
        print(f"  mandate    {mandate.id}  (M1)")
        print(f"  decision   {purchase.guard.decision_id}  {purchase.guard.verdict}")
        print(f"  order      {purchase.order.id}  {purchase.order.razorpay_order_id}")

        def submit(quote_id: str, mandate_obj, key: str):
            """Submit over HTTP carrying the stolen authorisation."""
            body = {
                "quote_id": quote_id,
                "mandate": {"id": mandate_obj.id},
                "mandate_signature": mandate_obj.signature,
                "mandate_signing_payload": mandate_obj.signing_payload,
                "guard_decision_id": purchase.guard.decision_id,
                "correlation_id": correlation_id,
            }
            with TestClient(app) as client:
                response = client.post(
                    "/merchant/checkout/submit",
                    json=body,
                    headers={
                        "X-Merchant-API-Key": settings.MERCHANT_API_KEY,
                        "Idempotency-Key": key,
                    },
                )
            error = response.json().get("error", {})
            return response, error, error.get("detail", {})

        def report(title: str, response, error: dict, detail: dict) -> None:
            heading(title)
            print(f"  status     {response.status_code}")
            print(f"  code       {error.get('code')}")
            print(f"  field      {detail.get('field')}")
            print(f"  observed   {detail.get('observed')!r}")
            print(f"  expected   {detail.get('expected')!r}")
            print(f"  message    {error.get('message')}")

        # ── 2. a second cart the Guard was never asked about ──────────────
        quote_b = build_quote(db, merchant, STOLEN_CART, new_correlation())

        heading("STEP 2 — a different cart, independently valid")
        print(f"  quote      {quote_b.id}  {rupees(quote_b.total_paise)}")
        print(f"  status     {quote_b.status}, in stock, no price drift")
        print("  mandate    M1 verifies against it, so CV-001 passes")

        response, error, detail = submit(
            quote_b.id, mandate, f"binding-b:{correlation_id}"
        )
        report("STEP 3 — submit cart 2 with cart 1's decision", response, error, detail)

        db.expire_all()
        checks = [
            ("the setup purchase really was ALLOWED",
             purchase.guard.verdict == "ALLOW"),
            ("cart 2 is the larger 756 000 cart",
             quote_b.total_paise == 756_000
             and quote_b.total_paise > quote_a.total_paise),
            ("the submission was refused with 409", response.status_code == 409),
            ("code is MERCHANT_MANDATE_INVALID",
             error.get("code") == "MERCHANT_MANDATE_INVALID"),
            ("the mismatched field is quote_id", detail.get("field") == "quote_id"),
            ("observed is the decision's quote",
             detail.get("observed") == quote_a.id),
            ("expected is the submitted quote",
             detail.get("expected") == quote_b.id),
            ("no order was created for cart 2",
             orders_for_quote(db, quote_b.id) == []),
            ("cart 2's quote was not consumed", quote_b.status == "ACTIVE"),
            ("cart 1's order is untouched",
             len(orders_for_quote(db, quote_a.id)) == 1),
        ]

        print("\nEXPECTATIONS — case 1, a different quote")
        print("-" * 78)
        for label, ok in checks:
            print(f"  [{'ok ' if ok else 'BAD'}] {label}")

        # ── 3. the same attack on the mandate ─────────────────────────────
        # M2: independently valid. Same Mandate Authority signature, same
        # merchant, same category, unexpired — CV-001 accepts it on its own
        # terms and cannot tell it is not the mandate the Guard read.
        mandate_2 = issue_mandate(db, merchant, new_correlation())

        heading("STEP 4 — a second, independently valid mandate")
        print(f"  M1         {mandate.id}  the Guard evaluated this one")
        print(f"  M2         {mandate_2.id}  validly signed, CV-001 would accept it")

        # Cart 1's own quote, so quote_id, amount and merchant all match the
        # decision and `mandate_id` is the only field left to disagree.
        response_m, error_m, detail_m = submit(
            quote_a.id, mandate_2, f"binding-m:{correlation_id}"
        )
        report(
            "STEP 5 — submit cart 1's quote with M2 under cart 1's decision",
            response_m, error_m, detail_m,
        )

        db.expire_all()
        checks_m = [
            ("M2 is a different mandate from M1", mandate_2.id != mandate.id),
            ("M2 is ACTIVE and signed",
             mandate_2.status == "ACTIVE" and bool(mandate_2.signature)),
            ("the submission was refused with 409", response_m.status_code == 409),
            ("code is MERCHANT_MANDATE_INVALID",
             error_m.get("code") == "MERCHANT_MANDATE_INVALID"),
            ("the mismatched field is mandate_id",
             detail_m.get("field") == "mandate_id"),
            ("observed is the decision's mandate",
             detail_m.get("observed") == mandate.id),
            ("expected is the submitted mandate",
             detail_m.get("expected") == mandate_2.id),
            ("still only cart 1's original order exists",
             len(orders_for_quote(db, quote_a.id)) == 1),
        ]

        print("\nEXPECTATIONS — case 2, a different mandate")
        print("-" * 78)
        for label, ok in checks_m:
            print(f"  [{'ok ' if ok else 'BAD'}] {label}")

        if all(ok for _, ok in checks) and all(ok for _, ok in checks_m):
            print(
                f"\nThe Guard was asked about {rupees(quote_a.total_paise)} and "
                f"never about {rupees(quote_b.total_paise)}, so\n"
                f"{rupees(quote_b.total_paise)} could not be charged. It was "
                f"asked about M1 and never about M2,\nso M2 could not spend its "
                "authorisation. An authorisation is not a bearer token."
            )
            return 0
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
