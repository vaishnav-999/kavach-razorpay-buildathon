"""Shared plumbing for the two M5 verification scripts.

Both scripts drive the real code paths in-process — the same functions the
agent's `submit_purchase` tool will call in M6 — against the same database the
server uses. Nothing here is a stub and nothing here is a shortcut: the quote
is signed by the merchant, the mandate is signed by the Mandate Authority, and
the purchase goes through the single `guard.evaluate()` call site in
`app/platform/payments.py`.

Run either script with the server's `.env` in place:

    python scripts/demo_block.py
    python scripts/demo_allow.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# So `python scripts/demo_block.py` works without an install step.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `prompt_playback` contains a rupee sign and a Windows console defaults to
# cp1252. Print in UTF-8 rather than mangling the sentence the human consents to.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy.orm import Session  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.ids import new_correlation_id  # noqa: E402
from app.merchant import discovery, service  # noqa: E402
from app.models import Merchant, Order, Quote  # noqa: E402
from app.platform import mandate as mandate_service  # noqa: E402
from app.platform.guard import GuardResult, rupees  # noqa: E402

USER_EMAIL = "priya@example.com"

# §16.3 — the canonical demo mandate.
MANDATE_MAX_AMOUNT_PAISE = 600_000
MANDATE_CUMULATIVE_CAP_PAISE = 600_000
MANDATE_MAX_TRANSACTIONS = 1
MANDATE_TTL_MINUTES = 30

RULE = "-" * 78


def open_session() -> Session:
    return SessionLocal()


def heading(text: str) -> None:
    print(f"\n{text}\n{RULE}")


def build_quote(
    db: Session, merchant: Merchant, items: list[tuple[str, int]], correlation_id: str
) -> Quote:
    """Cart -> line items -> a merchant-signed quote (§7.6, §7.7)."""
    cart = service.create_cart(
        db, merchant_id=merchant.id, session_id=None, correlation_id=correlation_id
    )
    for sku, qty in items:
        service.add_cart_item(db, cart.id, sku=sku, qty=qty)
    quote = service.create_quote(db, cart.id)

    heading("QUOTE (signed by the merchant)")
    for line in quote.line_items:
        print(
            f"  {line['sku']:<8} x{line['qty']:<3} "
            f"{rupees(line['unit_price_paise']):>12} = "
            f"{rupees(line['line_total_paise']):>12}"
        )
    print(f"  {'total':<12} {rupees(quote.total_paise):>27}")
    print(f"  quote_id  {quote.id}")
    return quote


def issue_mandate(db: Session, merchant: Merchant, correlation_id: str):
    """Propose, then issue. Only the issue signs (§8.2)."""
    proposed = mandate_service.propose(
        db,
        user_email=USER_EMAIL,
        session_id=None,
        correlation_id=correlation_id,
        currency="INR",
        max_amount_paise=MANDATE_MAX_AMOUNT_PAISE,
        cumulative_cap_paise=MANDATE_CUMULATIVE_CAP_PAISE,
        max_transactions=MANDATE_MAX_TRANSACTIONS,
        ttl_minutes=MANDATE_TTL_MINUTES,
        allowed_merchant_ids=[merchant.id],
        allowed_categories=["meals"],
    )
    heading("MANDATE")
    print(f"  proposed  {proposed.id}  status={proposed.status}  "
          f"signature={proposed.signature!r}")

    active = mandate_service.issue(
        db, mandate_id=proposed.id, ttl_minutes=MANDATE_TTL_MINUTES
    )
    print(f"  issued    {active.id}  status={active.status}  "
          f"signature={active.signature[:16]}...")
    print(f"\n  {active.prompt_playback}")
    return active


def print_guard(result: GuardResult) -> None:
    heading(f"TRANSACTION GUARD — {result.verdict}")
    for rule in result.rules:
        mark = "PASS" if rule.passed else "FAIL"
        print(f"  [{mark}] {rule.rule_id}  {rule.name}")
        print(f"         observed={rule.observed!r}  threshold={rule.threshold!r}  "
              f"unit={rule.unit}")
        print(f"         {rule.detail}")
    print(RULE)
    print(f"  decision_id     {result.decision_id}")
    print(f"  verdict         {result.verdict}")
    print(f"  failed_rule_id  {result.failed_rule_id}")
    print(f"  block_code      {result.block_code}")
    print(f"  duration_ms     {result.duration_ms}")
    print(f"  rules reported  {len(result.rules)} of 9")


def orders_for_quote(db: Session, quote_id: str) -> list[Order]:
    return list(db.query(Order).filter(Order.quote_id == quote_id).all())


def primary_merchant(db: Session) -> Merchant:
    return discovery.primary_merchant(db)


def new_correlation() -> str:
    return new_correlation_id()
