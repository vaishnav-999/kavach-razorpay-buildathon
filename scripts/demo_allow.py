"""M5 verification — the correct cart goes through.

Builds the §16.3 correct cart (PK-001 x8, PK-003 x4 = 516 000 paise), issues
the same canonical ₹6,000 mandate, and submits.

Expected: ALLOW on all nine rules, the four validator checks passing, a real
Razorpay order, and a checkout URL to pay it. This is the success path that
`/api/dev/test-checkout` used to provide before M5 deleted it — except that
this one goes through the Transaction Guard.

    python scripts/demo_allow.py
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
    primary_merchant,
    print_guard,
)

from app.config import settings  # noqa: E402
from app.platform.guard import GuardBlocked, rupees  # noqa: E402
from app.platform.payments import execute_authorized_purchase  # noqa: E402

# §16.3 — the cart the agent should have built.
CORRECT_CART = [("PK-001", 8), ("PK-003", 4)]
EXPECTED_TOTAL_PAISE = 516_000


def main() -> int:
    db = open_session()
    try:
        merchant = primary_merchant(db)
        correlation_id = new_correlation()
        print(f"correlation_id  {correlation_id}")

        quote = build_quote(db, merchant, CORRECT_CART, correlation_id)
        mandate = issue_mandate(db, merchant, correlation_id)

        try:
            purchase = execute_authorized_purchase(
                db,
                session_id=None,
                correlation_id=correlation_id,
                mandate_id=mandate.id,
                quote_id=quote.id,
                merchant_id=merchant.id,
                requested_total_paise=quote.total_paise,
                currency=quote.currency,
                idempotency_key=f"demo-allow:{correlation_id}",
            )
        except GuardBlocked as blocked:
            print_guard(blocked.result)
            print(
                "\nFAIL: the purchase was BLOCKED by "
                f"{blocked.result.failed_rule_id}. It should have been ALLOWED."
            )
            return 1

        print_guard(purchase.guard)
        order = purchase.order

        heading("ORDER")
        print(f"  order_id           {order.id}")
        print(f"  status             {order.status}")
        print(f"  amount             {rupees(order.amount_paise)}")
        print(f"  razorpay_order_id  {order.razorpay_order_id}")
        print(f"  guard_decision_id  {order.guard_decision_id}")
        print(f"  mandate_id         {order.mandate_id}")

        checks = [
            ("cart total is the correct 516 000",
             quote.total_paise == EXPECTED_TOTAL_PAISE),
            ("verdict is ALLOW", purchase.guard.verdict == "ALLOW"),
            ("all nine rules reported", len(purchase.guard.rules) == 9),
            ("no rule failed", purchase.guard.failed_rule_id is None),
            ("a Razorpay order exists", bool(order.razorpay_order_id)),
            ("order status is PENDING_PAYMENT", order.status == "PENDING_PAYMENT"),
            ("order names its guard decision",
             order.guard_decision_id == purchase.guard.decision_id),
            ("order amount equals the signed quote total",
             order.amount_paise == quote.total_paise),
        ]

        print("\nEXPECTATIONS")
        print("-" * 78)
        for label, ok in checks:
            print(f"  [{'ok ' if ok else 'BAD'}] {label}")

        if not all(ok for _, ok in checks):
            return 1

        print(
            f"\nPay it (server must be running):\n"
            f"  {settings.APP_BASE_URL.rstrip('/')}/dev/checkout/{order.id}\n"
            f"Then:\n"
            f"  GET /merchant/orders/{order.id}\n"
            f"  GET /api/audit/{correlation_id}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
