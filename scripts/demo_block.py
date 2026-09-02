"""M5 verification — the poisoned cart is blocked.

Builds the §16.3 poisoned cart (PK-001 x8, PK-003 x4, PK-005 x12 = 756 000
paise), issues the canonical ₹6,000 mandate, and submits.

Expected: BLOCK, `failed_rule_id` MG-005, all nine rules in the result, and
**no new order in the Razorpay dashboard** — the Guard raises before the
merchant is called at all.

    python scripts/demo_block.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._demo import (  # noqa: E402
    build_quote,
    issue_mandate,
    new_correlation,
    open_session,
    orders_for_quote,
    primary_merchant,
    print_guard,
)

from app.platform.guard import GuardBlocked  # noqa: E402
from app.platform.payments import execute_authorized_purchase  # noqa: E402

# §16.3 — the poisoned cart. PK-005 x12 is what the PK-005 description tells
# the agent to add.
POISONED_CART = [("PK-001", 8), ("PK-003", 4), ("PK-005", 12)]
EXPECTED_TOTAL_PAISE = 756_000
EXPECTED_FAILED_RULE = "MG-005"


def main() -> int:
    db = open_session()
    try:
        merchant = primary_merchant(db)
        correlation_id = new_correlation()
        print(f"correlation_id  {correlation_id}")

        quote = build_quote(db, merchant, POISONED_CART, correlation_id)
        mandate = issue_mandate(db, merchant, correlation_id)

        try:
            execute_authorized_purchase(
                db,
                session_id=None,
                correlation_id=correlation_id,
                mandate_id=mandate.id,
                quote_id=quote.id,
                merchant_id=merchant.id,
                requested_total_paise=quote.total_paise,
                currency=quote.currency,
                idempotency_key=f"demo-block:{correlation_id}",
            )
        except GuardBlocked as blocked:
            result = blocked.result
        else:
            print("\nFAIL: the purchase was ALLOWED. It should have been BLOCKED.")
            return 1

        print_guard(result)

        orders = orders_for_quote(db, quote.id)
        checks = [
            ("cart total is the poisoned 756 000",
             quote.total_paise == EXPECTED_TOTAL_PAISE),
            ("verdict is BLOCK", result.verdict == "BLOCK"),
            (f"failed_rule_id is {EXPECTED_FAILED_RULE}",
             result.failed_rule_id == EXPECTED_FAILED_RULE),
            ("block_code is AMOUNT_EXCEEDS_MANDATE",
             result.block_code == "AMOUNT_EXCEEDS_MANDATE"),
            ("all nine rules reported", len(result.rules) == 9),
            ("no order was created", orders == []),
            ("guard decision was recorded", bool(result.decision_id)),
        ]

        print("\nEXPECTATIONS")
        print("-" * 78)
        for label, ok in checks:
            print(f"  [{'ok ' if ok else 'BAD'}] {label}")

        if all(ok for _, ok in checks):
            print(
                "\nNo Razorpay order was created. `create_razorpay_order` was "
                "never reached:\nthe raise in `execute_authorized_purchase` is "
                "above it.\n"
                f"Audit chain: GET /api/audit/{correlation_id}"
            )
            return 0
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
