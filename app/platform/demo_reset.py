"""Restore the seed and clear the transaction tables (BUILD_SPEC §15).

`audit_events` is kept, deliberately, and so is `agent_sessions`: the
append-only claim (invariant 8) would be hollow if a demo button truncated the
table, and every previous run stays retrievable by correlation id after a
reset. PK-005's description is restored from the same §16.5 constant the seed
uses — verbatim, injection included. A reset never sanitises it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.merchant.seed import PRODUCTS as SEED_PRODUCTS
from app.models import (
    AuditEvent,
    Cart,
    CartItem,
    GuardDecision,
    Mandate,
    Order,
    Payment,
    Product,
    Quote,
    WebhookEvent,
)
from app.platform.demo_base import (
    DemoActionOut,
    DemoChange,
    DemoIn,
    correlation_for,
    record_action,
)

router = APIRouter()


# ── 5. reset (§15) ────────────────────────────────────────────────────────

# Children before parents. `orders` names a `guard_decisions` row by a NOT NULL
# foreign key, so decisions can only go after the orders that point at them.
_CLEARED: tuple[tuple[str, type], ...] = (
    ("payments", Payment),
    ("webhook_events", WebhookEvent),
    ("orders", Order),
    ("guard_decisions", GuardDecision),
    ("quotes", Quote),
    ("cart_items", CartItem),
    ("carts", Cart),
    ("mandates", Mandate),
)


@router.post("/reset", response_model=DemoActionOut)
def reset(body: DemoIn | None = None, db: Session = Depends(get_db)) -> DemoActionOut:
    """§15 — restore seed prices and stock, clear the transaction tables.

    `audit_events` is kept, deliberately. The append-only claim would be
    hollow if a demo button truncated the table, so a reset leaves the whole
    history of every previous run in place and retrievable by correlation id.
    `agent_sessions` is kept for the same reason: the events reference it.
    """
    correlation_id = correlation_for(body)

    restored: list[DemoChange] = []
    for seed in SEED_PRODUCTS:
        product = db.get(Product, seed["id"])
        if product is None:
            continue
        if int(product.unit_price_paise) != int(seed["unit_price_paise"]):
            restored.append(
                DemoChange(
                    target=f"products.{product.sku}",
                    field="unit_price_paise",
                    before=int(product.unit_price_paise),
                    after=int(seed["unit_price_paise"]),
                    unit="paise",
                )
            )
        if int(product.stock_qty) != int(seed["stock_qty"]):
            restored.append(
                DemoChange(
                    target=f"products.{product.sku}",
                    field="stock_qty",
                    before=int(product.stock_qty),
                    after=int(seed["stock_qty"]),
                    unit="units",
                )
            )
        product.unit_price_paise = int(seed["unit_price_paise"])
        product.stock_qty = int(seed["stock_qty"])
        product.active = True
        # The PK-005 description is restored from the same §16.5 constant the
        # seed uses — verbatim, injection included. A reset never sanitises it.
        product.description = seed["description"]

    cleared: dict[str, int] = {}
    for name, model in _CLEARED:
        cleared[name] = int(db.execute(delete(model)).rowcount or 0)
    db.flush()

    kept = int(db.scalar(select(func.count()).select_from(AuditEvent)) or 0)

    result = {
        "cleared": cleared,
        "products_restored": len(SEED_PRODUCTS),
        "fields_restored": len(restored),
        "audit_events_kept": kept,
    }
    record_action(
        db,
        action="reset",
        correlation_id=correlation_id,
        session_id=body.session_id if body else None,
        params={"scope": "seed prices, stock, and the transaction tables"},
        result=result,
    )

    total = sum(cleared.values())
    return DemoActionOut(
        action="reset",
        summary=(
            f"Seed prices and stock restored across {len(SEED_PRODUCTS)} "
            f"products; {total} rows cleared across "
            f"{len(_CLEARED)} tables. {kept} audit events kept."
        ),
        triggers=(
            "audit_events is append-only and is never truncated here, so "
            "every previous run stays retrievable at GET /api/audit/{id}."
        ),
        changed=restored,
        correlation_id=correlation_id,
        detail=result,
    )
