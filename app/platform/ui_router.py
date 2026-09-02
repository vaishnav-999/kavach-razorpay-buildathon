"""What the browser is allowed to read.

Two endpoints the §14 console needs and no plane already offers it.

`GET /api/ui/config` hands the page the Razorpay **key id** and the demo flag.
It is a runtime read rather than a build-time constant because the frontend is
compiled once into a container image and the key belongs to the deployment, not
to the bundle. §4.3 permits exactly this: the key **secret** never reaches the
browser, only the key id.

`GET /api/ui/orders/{order_id}` is the §7.9 order view, read-only.

**Why it is not a direct call to `/merchant/orders/{id}`.** §7 requires
`X-Merchant-API-Key` on every `/merchant/*` endpoint, and that key is what
authenticates the buyer plane to the merchant as a known API client. Putting it
in a JavaScript bundle would hand every visitor the ability to open carts and
request quotes as the agent. So the browser gets an id-scoped read of one order
instead — strictly less authority than the key it replaces, and no secret
crosses the wire.

It is still the merchant plane's own answer: `service.get_order()` is the same
function `GET /merchant/orders/{id}` calls, so §7.9 remains the single source of
truth for payment status in the UI. Only the transport differs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db

router = APIRouter(prefix="/api/ui", tags=["ui"])


class UiConfigOut(BaseModel):
    # §12.3 — the key id is the only Razorpay credential a browser ever sees.
    razorpay_key_id: str
    demo_mode: bool


@router.get("/config", response_model=UiConfigOut)
def get_config() -> UiConfigOut:
    return UiConfigOut(
        razorpay_key_id=settings.RAZORPAY_KEY_ID,
        demo_mode=settings.DEMO_MODE,
    )


@router.get("/orders/{order_id}")
def get_order(order_id: str, db: Session = Depends(get_db)):
    """The §7.9 view of one order, for the page that has to poll it.

    Deferred import for the same reason `payments.py` defers its own: the
    merchant plane is a peer, and importing it at module scope would make the
    platform's import graph depend on it at startup.
    """
    from app.merchant.router import _order_response
    from app.merchant.service import get_order as merchant_get_order

    order, payment = merchant_get_order(db, order_id)
    return _order_response(order, payment)
