"""The read-only merchant surface (BUILD_SPEC §7.1 – §7.5).

Profile, registry, catalog and availability. Nothing here changes state, and
nothing here makes a promise: an availability answer at time T is explicitly
not a commitment at time T+1, which is why stock is enforced under a row lock
at submit and nowhere else.

The transactional half — carts, quotes, orders — lives in `service.py`.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import KavachError
from app.models import Merchant, Product

# §7.1 limits, advertised in the profile and enforced in `service.py`.
QUOTE_TTL_SECONDS = 900
MAX_LINE_ITEMS = 20
MAX_QTY_PER_LINE = 100

CURRENCY = "INR"
QUOTE_PAYLOAD_TYPE = "kavach.quote.v1"
PROFILE_VERSION = "kavach-merchant-profile/0.1"
PRIMARY_MERCHANT_SLUG = "protein-kitchen"

# §21 — we are inspired by UCP and we say so in exactly these words. We do not
# claim to implement it, here or anywhere else.
INSPIRED_BY = (
    "Universal Commerce Protocol (public drafts). This endpoint is not a UCP "
    "implementation; see BUILD_SPEC §21."
)


# ── merchants ─────────────────────────────────────────────────────────────


def get_merchant(db: Session, merchant_id: str) -> Merchant:
    merchant = db.get(Merchant, merchant_id)
    if merchant is None:
        raise KavachError(
            "MERCHANT_NOT_FOUND",
            f"No merchant with id {merchant_id}.",
            detail={"merchant_id": merchant_id},
        )
    return merchant


def get_merchant_by_slug(db: Session, slug: str) -> Merchant:
    merchant = db.scalar(select(Merchant).where(Merchant.slug == slug))
    if merchant is None:
        raise KavachError(
            "MERCHANT_NOT_FOUND",
            f"No merchant with slug {slug}.",
            detail={"slug": slug},
        )
    return merchant


def primary_merchant(db: Session) -> Merchant:
    return get_merchant_by_slug(db, PRIMARY_MERCHANT_SLUG)


def profile_url(slug: str) -> str:
    return f"{settings.APP_BASE_URL.rstrip('/')}/.well-known/ucp/{slug}"


def build_profile(merchant: Merchant) -> dict:
    """The §7.1 merchant profile.

    `capabilities` comes straight from the row. A non-transactable merchant
    advertises a shorter list, and that shorter list is the whole mechanism by
    which the agent rejects it — the rejection is architectural, not a matter
    of the agent having been persuaded.
    """
    return {
        "profile_version": PROFILE_VERSION,
        "inspired_by": INSPIRED_BY,
        "merchant": {
            "id": merchant.id,
            "slug": merchant.slug,
            "name": merchant.name,
            "legal_name": merchant.legal_name,
            "category": merchant.category,
        },
        "currency": CURRENCY,
        "capabilities": list(merchant.capabilities),
        "signing": {
            "algorithm": "ed25519",
            "public_key_hex": merchant.public_key_hex,
            "payload_type": QUOTE_PAYLOAD_TYPE,
        },
        "auth": {"scheme": "api_key", "header": "X-Merchant-API-Key"},
        "endpoints": {
            "catalog": "/merchant/catalog",
            "availability": "/merchant/availability",
            "carts": "/merchant/carts",
            "quote": "/merchant/checkout/quote",
            "submit": "/merchant/checkout/submit",
            "order": "/merchant/orders/{order_id}",
        },
        "limits": {
            "quote_ttl_seconds": QUOTE_TTL_SECONDS,
            "max_line_items": MAX_LINE_ITEMS,
            "max_qty_per_line": MAX_QTY_PER_LINE,
        },
    }


def registry(db: Session) -> list[dict]:
    """§7.3 — what `discover_merchants` reads."""
    merchants = db.scalars(select(Merchant).order_by(Merchant.slug)).all()
    return [
        {
            "id": m.id,
            "slug": m.slug,
            "name": m.name,
            "category": m.category,
            "transactable": m.transactable,
            "capabilities": list(m.capabilities),
            "profile_url": profile_url(m.slug),
        }
        for m in merchants
    ]


# ── catalog & availability ────────────────────────────────────────────────


def catalog(db: Session, merchant_id: str) -> list[Product]:
    """§7.4 — products for a merchant.

    Descriptions are returned exactly as stored, PK-005 included. Sanitising
    here would be defending the wrong layer, and would defeat the entire
    demonstration: the injection has to reach the model for the Guard blocking
    the purchase to prove anything at all.
    """
    get_merchant(db, merchant_id)
    return list(
        db.scalars(
            select(Product)
            .where(Product.merchant_id == merchant_id)
            .order_by(Product.sku)
        ).all()
    )


def product_by_sku(db: Session, merchant_id: str, sku: str) -> Product:
    product = db.scalar(
        select(Product).where(Product.merchant_id == merchant_id, Product.sku == sku)
    )
    if product is None:
        raise KavachError(
            "PRODUCT_NOT_FOUND",
            f"No product {sku} at merchant {merchant_id}.",
            detail={"merchant_id": merchant_id, "sku": sku},
        )
    return product


def availability(db: Session, merchant_id: str, items: list[dict]) -> list[dict]:
    """§7.5 — read-only. No reservation is made and none is implied.

    Availability at time T is not a promise at time T+1, which is exactly why
    CV-002 re-checks stock under a row lock at submit.
    """
    get_merchant(db, merchant_id)
    out: list[dict] = []
    for item in items:
        product = product_by_sku(db, merchant_id, item["sku"])
        requested = item["qty"]
        available_qty = product.stock_qty if product.active else 0
        out.append(
            {
                "sku": product.sku,
                "requested_qty": requested,
                "available_qty": available_qty,
                "available": available_qty >= requested,
                "unit_price_paise": product.unit_price_paise,
            }
        )
    return out
