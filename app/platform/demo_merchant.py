"""Two levers that make the merchant's own validator refuse (BUILD_SPEC §15).

Neither touches the Guard. They move a price and a stock level in `products`,
and the §10 checkout validator catches the consequence at submit — CV-003 by
recomputing the total from current prices, CV-002 by re-reading stock under a
row lock. The Guard protects the buyer from their own agent; these two show
that the merchant is protecting itself, separately, at the same moment.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Quote
from app.platform.demo_base import (
    DemoActionOut,
    DemoChange,
    DemoIn,
    correlation_for,
    product_row,
    record_action,
)

router = APIRouter()

# §15 — the product this panel moves, and the figures it moves it to.
DRIFT_SKU = "PK-003"
DRIFT_TO_PAISE = 49_500
DEPLETE_SKU = "PK-003"
DEPLETE_TO_QTY = 3
# The happy-path cart asks for four of them; three is one short on purpose.
DEPLETE_CART_QTY = 4


# ── 1. drift price (CV-003) ───────────────────────────────────────────────


@router.post("/drift-price", response_model=DemoActionOut)
def drift_price(
    body: DemoIn | None = None, db: Session = Depends(get_db)
) -> DemoActionOut:
    """§15 — PK-003 45000 → 49500 paise.

    A quote signed at the old price is now signed at a price the merchant no
    longer charges. CV-003 re-reads `products` at submit, recomputes the total
    and requires it to equal `quotes.total_paise` exactly, so the submission is
    refused with `MERCHANT_PRICE_DRIFT` and nothing is created.
    """
    correlation_id = correlation_for(body)
    product = product_row(db, DRIFT_SKU)
    before = int(product.unit_price_paise)

    product.unit_price_paise = DRIFT_TO_PAISE
    db.flush()

    # How many signed quotes this just invalidated. `line_items` is JSON and
    # the filter differs by database, so the rows are read and counted here
    # rather than in a dialect-specific WHERE clause.
    stale = sum(
        1
        for quote in db.scalars(select(Quote).where(Quote.status == "ACTIVE"))
        if any(line.get("sku") == DRIFT_SKU for line in (quote.line_items or []))
    )

    result = {
        "sku": product.sku,
        "unit_price_paise_before": before,
        "unit_price_paise_after": DRIFT_TO_PAISE,
        "stale_active_quotes": stale,
    }
    record_action(
        db,
        action="drift-price",
        correlation_id=correlation_id,
        session_id=body.session_id if body else None,
        params={"sku": DRIFT_SKU, "unit_price_paise": DRIFT_TO_PAISE},
        result=result,
    )

    return DemoActionOut(
        action="drift-price",
        summary=(
            f"{product.name} ({product.sku}) repriced {before} → "
            f"{DRIFT_TO_PAISE} paise. Any quote already signed at the old "
            "price is now stale."
        ),
        triggers=(
            "CV-003 price_unchanged → MERCHANT_PRICE_DRIFT at "
            "POST /merchant/checkout/submit. No stock is decremented and no "
            "order is created."
        ),
        changed=[
            DemoChange(
                target=f"products.{product.sku}",
                field="unit_price_paise",
                before=before,
                after=DRIFT_TO_PAISE,
                unit="paise",
            )
        ],
        correlation_id=correlation_id,
        detail=result,
    )


# ── 2. deplete stock (CV-002) ─────────────────────────────────────────────


@router.post("/deplete-stock", response_model=DemoActionOut)
def deplete_stock(
    body: DemoIn | None = None, db: Session = Depends(get_db)
) -> DemoActionOut:
    """§15 — PK-003 `stock_qty → 3`, one short of the happy-path cart's four.

    CV-002 re-reads stock under `SELECT ... FOR UPDATE` at submit, so the
    shortfall is caught with the row locked rather than in a window between a
    check and a decrement.
    """
    correlation_id = correlation_for(body)
    product = product_row(db, DEPLETE_SKU)
    before = int(product.stock_qty)

    product.stock_qty = DEPLETE_TO_QTY
    db.flush()

    result = {
        "sku": product.sku,
        "stock_qty_before": before,
        "stock_qty_after": DEPLETE_TO_QTY,
        "cart_qty": DEPLETE_CART_QTY,
    }
    record_action(
        db,
        action="deplete-stock",
        correlation_id=correlation_id,
        session_id=body.session_id if body else None,
        params={"sku": DEPLETE_SKU, "stock_qty": DEPLETE_TO_QTY},
        result=result,
    )

    return DemoActionOut(
        action="deplete-stock",
        summary=(
            f"{product.name} ({product.sku}) stock {before} → "
            f"{DEPLETE_TO_QTY} units. The happy-path cart asks for "
            f"{DEPLETE_CART_QTY}."
        ),
        triggers=(
            "CV-002 stock_available → MERCHANT_OUT_OF_STOCK at "
            "POST /merchant/checkout/submit, checked under a row lock."
        ),
        changed=[
            DemoChange(
                target=f"products.{product.sku}",
                field="stock_qty",
                before=before,
                after=DEPLETE_TO_QTY,
                unit="units",
            )
        ],
        correlation_id=correlation_id,
        detail=result,
    )
