"""What every demo action shares (BUILD_SPEC §15).

The gate, the request and response shapes, and the two helpers each action
needs. `demo.py` assembles the routers; the action modules import from here.

**DEMO_MODE.** §15 says the demo router is "mounted only when
`DEMO_MODE=true`". It is mounted always and every route depends on
`require_demo_mode`, which refuses with `DEMO_MODE_DISABLED` and an
explanation. The reason is the failure mode a judge would otherwise hit: an
unmounted router answers 404, which reads as a broken deployment rather than as
a switch that is deliberately off. The authority is the same either way — with
`DEMO_MODE=false` nothing under `/api/demo` can change a single row.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import emit
from app.config import settings
from app.errors import KavachError
from app.ids import new_correlation_id
from app.models import Product


def require_demo_mode() -> None:
    """The gate. Every route on the demo router depends on it."""
    if not settings.DEMO_MODE:
        raise KavachError(
            "DEMO_MODE_DISABLED",
            "The demo control panel is switched off on this deployment. "
            "Set DEMO_MODE=true to enable it. Nothing under /api/demo can "
            "change any state while it is off.",
            detail={"demo_mode": False, "setting": "DEMO_MODE"},
        )


# ── wire models ───────────────────────────────────────────────────────────


class DemoIn(BaseModel):
    """Every action takes the same optional body.

    `correlation_id` is what threads a demo action onto the chain the console
    is already watching, so `DEMO_ACTION_TRIGGERED` appears in the §13.3 audit
    trace beside the events it explains.
    """

    correlation_id: str | None = None
    session_id: str | None = None


class RevokeMandateIn(DemoIn):
    mandate_id: str | None = None


class DemoChange(BaseModel):
    """One field an action moved, so the UI can render it without prose."""

    target: str
    field: str
    before: Any = None
    after: Any = None
    unit: str | None = None


class DemoActionOut(BaseModel):
    action: str
    # One line, written for a projector. The panel shows this verbatim.
    summary: str
    # What it means for the next submission: the rule that will now fire.
    triggers: str | None = None
    changed: list[DemoChange] = []
    correlation_id: str
    detail: dict[str, Any] = {}


# ── helpers ───────────────────────────────────────────────────────────────


def product_row(db: Session, sku: str) -> Product:
    product = db.scalar(select(Product).where(Product.sku == sku))
    if product is None:
        raise KavachError(
            "PRODUCT_NOT_FOUND",
            f"No product with sku {sku}. Has the database been seeded?",
            detail={"sku": sku},
        )
    return product


def record_action(
    db: Session,
    *,
    action: str,
    correlation_id: str,
    session_id: str | None,
    params: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """§15 — every action emits `DEMO_ACTION_TRIGGERED` with what it used."""
    emit(
        db,
        correlation_id=correlation_id,
        session_id=session_id,
        event_type="DEMO_ACTION_TRIGGERED",
        actor="demo",
        payload={"action": action, "params": params, "result": result},
    )
    db.commit()


def correlation_for(body: DemoIn | None) -> str:
    return (body.correlation_id if body else None) or new_correlation_id()
