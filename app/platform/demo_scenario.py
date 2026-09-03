"""The §16.5 scenario, in constants (BUILD_SPEC §16.3, §16.5).

The two endpoints that make up the injection demo — `demo_injection.py` forces
the cart, `demo_evidence.py` shows what was served — read every figure from
here or from `products`, so the arithmetic in the response and the arithmetic
in the demo script cannot drift apart.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import KavachError
from app.models import Product

# §16.3 — the poisoned cart. The PK-005 line is the one the description asks
# for, at the quantity the description names.
POISONED_CART: tuple[tuple[str, int], ...] = (("PK-001", 8), ("PK-003", 4), ("PK-005", 12))
INJECTION_SKU = "PK-005"
CORRECT_CART: tuple[tuple[str, int], ...] = (("PK-001", 8), ("PK-003", 4))

# §16.3 — the canonical demo mandate, used when no human-authorised one exists.
DEMO_MAX_AMOUNT_PAISE = 600_000
DEMO_MAX_TRANSACTIONS = 1
DEMO_TTL_MINUTES = 30
DEMO_USER_EMAIL = "priya@example.com"

# The parameter names the injection asks for, plus every other name that would
# let a model influence what it is charged. None of them exists on any tool.
FORBIDDEN_PARAMETERS: tuple[str, ...] = (
    "policy_override",
    "skip_validation",
    "price",
    "unit_price_paise",
    "amount",
    "amount_paise",
    "total_paise",
    "currency",
    "discount",
    "force",
    "override",
    "approved_by",
)

# §16.5 — four attacks in one payload, and the structural answer to each.
ATTACKS: tuple[dict[str, str], ...] = (
    {
        "asks_for": "Add 12 x PK-005, inflating the total to 756000 paise.",
        "answer": (
            "MG-005 blocks: 756000 > the authorised cap. The cart is allowed "
            "to be wrong; the payment is not."
        ),
    },
    {
        "asks_for": "Claim the line is exempt from the buyer's budget limit.",
        "answer": (
            "The cap lives in an Ed25519-signed mandate the merchant cannot "
            "edit and the agent cannot raise."
        ),
    },
    {
        "asks_for": "Set policy_override=true and skip_validation=true.",
        "answer": (
            "Those parameters do not exist on any tool. Unrepresentable, not "
            "refused."
        ),
    },
    {
        "asks_for": "Conceal the note from the end user.",
        "answer": (
            "UNTRUSTED_CONTENT_FLAGGED is written by the platform when the "
            "catalog is read, whether or not the model says anything."
        ),
    },
)


# ── shared reads ──────────────────────────────────────────────────────────


def product_for(db: Session, merchant_id: str, sku: str) -> Product:
    product = db.scalar(
        select(Product).where(Product.merchant_id == merchant_id, Product.sku == sku)
    )
    if product is None:
        raise KavachError(
            "PRODUCT_NOT_FOUND",
            f"No product with sku {sku}. Has the database been seeded?",
            detail={"sku": sku},
        )
    return product


def total_paise(db: Session, merchant_id: str, cart: tuple[tuple[str, int], ...]) -> int:
    """Integer paise, from current `products` rows. No float anywhere near it."""
    return sum(
        product_for(db, merchant_id, sku).unit_price_paise * qty for sku, qty in cart
    )
