"""M5 verification — an ALLOW decision does not transfer between quotes.

An `ALLOW` from the Transaction Guard is an authorisation for one quote, one
amount, at one merchant. If the merchant accepted any past `ALLOW` id, a
decision would be a bearer token: CV-001 to CV-004 never compare the amount to
the mandate, so a second, independently valid cart would sail through on an
authorisation that was never about it.

This script mounts exactly that attack.

1. The correct §16.3 cart (516 000) is authorised and paid for — a genuine
   ALLOW, a genuine order, a genuine Razorpay order.
2. A second cart is built: the poisoned 756 000 cart from `demo_block.py`. It
   is independently valid — ACTIVE quote, in stock, no price drift, and the
   same signed mandate verifies against it — so every one of CV-001 to CV-004
   passes.
3. Cart 2 is submitted over HTTP carrying cart 1's `guard_decision_id`.

Expected: 409 MERCHANT_MANDATE_INVALID naming `quote_id`, and no order for the
second quote. The Guard was never asked about 756 000, so 756 000 cannot be
charged.

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
        print(f"  decision   {purchase.guard.decision_id}  "
              f"{purchase.guard.verdict}")
        print(f"  order      {purchase.order.id}  "
              f"{purchase.order.razorpay_order_id}")

        # ── 2. a second cart the Guard was never asked about ──────────────
        quote_b = build_quote(db, merchant, STOLEN_CART, new_correlation())

        heading("STEP 2 — a different cart, independently valid")
        print(f"  quote      {quote_b.id}  {rupees(quote_b.total_paise)}")
        print(f"  status     {quote_b.status}, in stock, no price drift")
        print(f"  mandate    the same signed mandate verifies against it "
              f"(CV-001 passes)")

        # ── 3. submit cart 2 carrying cart 1's authorisation ──────────────
        body = {
            "quote_id": quote_b.id,
            "mandate": {"id": mandate.id},
            "mandate_signature": mandate.signature,
            "mandate_signing_payload": mandate.signing_payload,
            # The stolen authorisation. It says ALLOW, and it is about a
            # different quote for a smaller amount.
            "guard_decision_id": purchase.guard.decision_id,
            "correlation_id": correlation_id,
        }
        with TestClient(app) as client:
            response = client.post(
                "/merchant/checkout/submit",
                json=body,
                headers={
                    "X-Merchant-API-Key": settings.MERCHANT_API_KEY,
                    "Idempotency-Key": f"binding-b:{correlation_id}",
                },
            )

        payload = response.json()
        error = payload.get("error", {})
        detail = error.get("detail", {})

        heading("STEP 3 — submit cart 2 with cart 1's decision")
        print(f"  status     {response.status_code}")
        print(f"  code       {error.get('code')}")
        print(f"  field      {detail.get('field')}")
        print(f"  observed   {detail.get('observed')!r}")
        print(f"  expected   {detail.get('expected')!r}")
        print(f"  message    {error.get('message')}")

        db.expire_all()
        orders_b = orders_for_quote(db, quote_b.id)

        checks = [
            ("the setup purchase really was ALLOWED",
             purchase.guard.verdict == "ALLOW"),
            ("cart 2 is the larger 756 000 cart",
             quote_b.total_paise == 756_000
             and quote_b.total_paise > quote_a.total_paise),
            ("the submission was refused with 409", response.status_code == 409),
            ("code is MERCHANT_MANDATE_INVALID",
             error.get("code") == "MERCHANT_MANDATE_INVALID"),
            ("the mismatched field is named", detail.get("field") == "quote_id"),
            ("observed is the decision's quote",
             detail.get("observed") == quote_a.id),
            ("expected is the submitted quote",
             detail.get("expected") == quote_b.id),
            ("no order was created for cart 2", orders_b == []),
            ("cart 2's quote was not consumed", quote_b.status == "ACTIVE"),
            ("cart 1's order is untouched",
             len(orders_for_quote(db, quote_a.id)) == 1),
        ]

        print("\nEXPECTATIONS")
        print("-" * 78)
        for label, ok in checks:
            print(f"  [{'ok ' if ok else 'BAD'}] {label}")

        if all(ok for _, ok in checks):
            print(
                f"\nThe Guard was asked about {rupees(quote_a.total_paise)} and "
                f"never about {rupees(quote_b.total_paise)},\nso "
                f"{rupees(quote_b.total_paise)} could not be charged. An "
                "authorisation is not a bearer token."
            )
            return 0
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
