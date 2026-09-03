"""Domain builders and the §16 constants the tests assert against.

Imported by the test modules, never by `conftest.py`. That ordering matters:
`conftest.py` pins the environment before anything from `app` is imported, and
pytest always loads it before it loads a test module, so by the time this file
is imported `app.config` has already been built against the throwaway database.

`build_quote` and `issue_mandate` go through the real signing code rather than
inserting hand-written rows, so a mandate a test hands the Guard is verified by
MG-001 exactly as a mandate a human authorised would be.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.config import settings
from app.crypto import sign
from app.ids import new_mandate_id
from app.merchant import seed as seed_module
from app.merchant import service as merchant_service
from app.models import Mandate, Quote
from app.platform import mandate as mandate_module

# The §16 fixed seed ids, so a test can name a merchant without a lookup.
MCH_PROTEIN = seed_module.MCH_PROTEIN
MCH_NOVA = seed_module.MCH_NOVA
MCH_SAFFRON = seed_module.MCH_SAFFRON

# §16.3 - the canonical demo arithmetic.
CORRECT_CART_PAISE = 516_000
POISONED_CART_PAISE = 756_000
DEMO_CAP_PAISE = 600_000

# The merchant plane rejects an unauthenticated call (§7). Tests that reach it
# over HTTP send this; it matches the key `conftest.py` pins.
MERCHANT_HEADERS = {"X-Merchant-API-Key": "kavach_test_merchant_key"}


def refetch(session, model, pk):
    """Re-read a row written by another session (the app's, under TestClient)."""
    session.commit()
    session.expire_all()
    return session.get(model, pk)


def build_quote(
    session, *, merchant_id: str, items: list[tuple[str, int]], session_id=None
) -> Quote:
    """A genuinely signed quote, through the real §7.6/§7.7 path."""
    cart = merchant_service.create_cart(
        session, merchant_id=merchant_id, session_id=session_id, correlation_id=None
    )
    for sku, qty in items:
        merchant_service.add_cart_item(session, cart.id, sku=sku, qty=qty)
    return merchant_service.create_quote(session, cart.id)


def issue_mandate(
    session,
    *,
    now: datetime,
    allowed_merchant_ids: list[str],
    allowed_categories: list[str] | None = None,
    max_amount_paise: int = DEMO_CAP_PAISE,
    cumulative_cap_paise: int | None = None,
    max_transactions: int = 3,
    ttl_minutes: int = 30,
    status: str = "ACTIVE",
    user_email: str = "priya@example.com",
    correlation_id: str | None = None,
) -> Mandate:
    """An ACTIVE, genuinely signed mandate whose columns match its payload.

    `mandate.issue()` reads the wall clock for `issued_at` and `expires_at`,
    which a test asserting on an expiry boundary cannot use. This builds the
    same §6.4 payload with the same signer and an injected `now`, so MG-001
    passes on it exactly as it does on a mandate a human authorised.
    """
    limits = mandate_module.Limits(
        currency="INR",
        max_amount_paise=max_amount_paise,
        cumulative_cap_paise=(
            cumulative_cap_paise
            if cumulative_cap_paise is not None
            else max(max_amount_paise, 2_000_000)
        ),
        max_transactions=max_transactions,
        ttl_minutes=ttl_minutes,
        allowed_merchant_ids=list(allowed_merchant_ids),
        allowed_categories=list(allowed_categories or ["meals"]),
    )
    mandate_id = new_mandate_id()
    issued_at = now.replace(microsecond=0)
    expires_at = issued_at + timedelta(minutes=ttl_minutes)
    payload = mandate_module.build_signing_payload(
        mandate_id=mandate_id,
        session_id=None,
        user_email=user_email,
        limits=limits,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    row = Mandate(
        id=mandate_id,
        session_id=None,
        correlation_id=correlation_id,
        user_email=user_email,
        status=status,
        currency=limits.currency,
        max_amount_paise=limits.max_amount_paise,
        cumulative_cap_paise=limits.cumulative_cap_paise,
        max_transactions=limits.max_transactions,
        allowed_merchant_ids=limits.allowed_merchant_ids,
        allowed_categories=limits.allowed_categories,
        issued_at=issued_at,
        expires_at=expires_at,
        revoked_at=None,
        prompt_playback=mandate_module.build_prompt_playback(
            session, limits, expires_at
        ),
        signing_payload=payload,
        signature=sign(settings.MANDATE_SIGNING_SEED, payload),
    )
    session.add(row)
    session.commit()
    return row
