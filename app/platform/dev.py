"""TEMPORARY M3 scaffolding. DELETE THIS FILE IN M5.

TODO(M5): delete `app/platform/dev.py`, remove its two routers from
`app/main.py`, and drop the `verdict='DEV_SCAFFOLD'` rows it wrote. Its job is
to prove the Razorpay rail end to end — a real order, a real modal, a real
payment, a real server-side signature check — before the Transaction Guard
exists to authorise anything.

**This is the only place in the project where an order is created without a
guard decision, and it does not survive past M5.** `orders.guard_decision_id`
and `orders.mandate_id` are `NOT NULL`, so the endpoint below still has to
write rows to point at. It writes ones that cannot be mistaken for authority:

* the mandate is `PROPOSED` with a null signature — §5.2 is explicit that a
  `PROPOSED` mandate carries no authority, and MG-001 fails on it by
  construction;
* the guard decision carries the verdict string `DEV_SCAFFOLD`, which is
  neither `ALLOW` nor `BLOCK`, with an empty rule list. No code that tests for
  `ALLOW` will ever match it.

Once M5 lands, `execute_authorized_purchase()` is the only route to
`create_razorpay_order()`, and it is reachable only below a real ALLOW.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.errors import KavachError
from app.ids import new_guard_decision_id, new_mandate_id
from app.models import GuardDecision, Mandate, Order, Quote
from app.platform import payments

api_router = APIRouter(prefix="/api/dev", tags=["dev (temporary)"])
page_router = APIRouter(prefix="/dev", tags=["dev (temporary)"])

SCAFFOLD_VERDICT = "DEV_SCAFFOLD"


class TestCheckoutIn(BaseModel):
    quote_id: str


class TestCheckoutOut(BaseModel):
    order_id: str
    razorpay_order_id: str
    razorpay_key_id: str
    amount_paise: int


def _scaffold_ids(db: Session, quote: Quote) -> tuple[str, str]:
    """Write the two placeholder rows the NOT NULL foreign keys demand."""
    now = datetime.now(timezone.utc)

    mandate = Mandate(
        id=new_mandate_id(),
        session_id=None,
        correlation_id=quote.correlation_id,
        user_email="dev-scaffold@kavach.local",
        # No signature, so no authority. This is not an authorization.
        status="PROPOSED",
        currency=quote.currency,
        max_amount_paise=0,
        cumulative_cap_paise=0,
        max_transactions=0,
        allowed_merchant_ids=[],
        allowed_categories=[],
        prompt_playback=(
            "M3 scaffolding. No human authorised anything; the Transaction "
            "Guard does not exist yet. Deleted in M5."
        ),
        signing_payload=None,
        signature=None,
    )
    db.add(mandate)
    # Flushed before the decision, which carries a foreign key to it.
    db.flush()

    decision = GuardDecision(
        id=new_guard_decision_id(),
        correlation_id=quote.correlation_id or quote.id,
        session_id=None,
        mandate_id=mandate.id,
        quote_id=quote.id,
        merchant_id=quote.merchant_id,
        requested_total_paise=quote.total_paise,
        # Neither ALLOW nor BLOCK: no guard ran.
        verdict=SCAFFOLD_VERDICT,
        failed_rule_id=None,
        block_code=None,
        rules=[],
        duration_ms=0,
        evaluated_at=now,
    )
    db.add(decision)
    db.flush()
    return mandate.id, decision.id


@api_router.post("/test-checkout", response_model=TestCheckoutOut)
def test_checkout(
    body: TestCheckoutIn, db: Session = Depends(get_db)
) -> TestCheckoutOut:
    """TEMPORARY (M3). Turn a signed quote into a Razorpay order. Deleted in M5."""
    quote = db.get(Quote, body.quote_id)
    if quote is None:
        raise KavachError(
            "QUOTE_NOT_FOUND",
            f"No quote with id {body.quote_id}.",
            detail={"quote_id": body.quote_id},
        )

    idempotency_key = f"dev-scaffold:{quote.id}"
    order = db.scalar(select(Order).where(Order.idempotency_key == idempotency_key))
    if order is None:
        mandate_id, guard_decision_id = _scaffold_ids(db, quote)
        order = payments.create_razorpay_order(
            db,
            quote=quote,
            mandate_id=mandate_id,
            guard_decision_id=guard_decision_id,
            idempotency_key=idempotency_key,
        )

    return TestCheckoutOut(
        order_id=order.id,
        razorpay_order_id=order.razorpay_order_id or "",
        # The key id only. The secret never leaves the server (§12.3).
        razorpay_key_id=settings.RAZORPAY_KEY_ID,
        amount_paise=order.amount_paise,
    )


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kavach dev checkout</title>
<style>
  body {{ background:#0b0d10; color:#e6e8eb; font:14px/1.6 ui-monospace,monospace;
         margin:0; display:flex; min-height:100vh; align-items:center;
         justify-content:center; }}
  main {{ width:min(520px,92vw); }}
  h1 {{ font-size:15px; letter-spacing:.08em; text-transform:uppercase;
        color:#8b949e; font-weight:600; }}
  dl {{ display:grid; grid-template-columns:auto 1fr; gap:4px 16px; margin:20px 0; }}
  dt {{ color:#8b949e; }}
  dd {{ margin:0; }}
  button {{ background:#e6e8eb; color:#0b0d10; border:0; border-radius:6px;
            padding:10px 18px; font:inherit; font-weight:600; cursor:pointer; }}
  #out {{ margin-top:20px; white-space:pre-wrap; color:#8b949e; }}
</style>
</head>
<body>
<main>
  <h1>Kavach — M3 dev checkout</h1>
  <dl>
    <dt>order</dt><dd>{order_id}</dd>
    <dt>razorpay order</dt><dd>{razorpay_order_id}</dd>
    <dt>amount</dt><dd>{amount_paise} paise</dd>
  </dl>
  <button id="pay">Pay</button>
  <div id="out"></div>
</main>
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<script>
  var ORDER = {order_json};
  var out = document.getElementById("out");

  function verify(response) {{
    // The browser's word is worth nothing until the server has re-derived the
    // HMAC over the order id it holds in its own database (BUILD_SPEC 12.4).
    out.textContent = "verifying on the server...";
    fetch("/api/payments/verify", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{
        order_id: ORDER.order_id,
        razorpay_payment_id: response.razorpay_payment_id,
        razorpay_order_id: response.razorpay_order_id,
        razorpay_signature: response.razorpay_signature
      }})
    }})
      .then(function (r) {{ return r.json().then(function (j) {{
        return {{ status: r.status, body: j }}; }}); }})
      .then(function (r) {{
        out.textContent = r.status + "\\n" + JSON.stringify(r.body, null, 2);
      }})
      .catch(function (e) {{ out.textContent = "verify failed: " + e; }});
  }}

  document.getElementById("pay").onclick = function () {{
    new Razorpay({{
      key: ORDER.razorpay_key_id,
      amount: ORDER.amount_paise,
      currency: "INR",
      order_id: ORDER.razorpay_order_id,
      name: "Kavach",
      description: ORDER.order_id,
      handler: verify,
      theme: {{ color: "#0b0d10" }}
    }}).open();
  }};
</script>
</body>
</html>
"""


@page_router.get("/checkout/{order_id}", response_class=HTMLResponse)
def dev_checkout_page(order_id: str, db: Session = Depends(get_db)) -> HTMLResponse:
    """TEMPORARY (M3). The §12.3 checkout page. Deleted in M5."""
    order = payments.get_order(db, order_id)
    if not order.razorpay_order_id:
        raise KavachError(
            "ORDER_NOT_FOUND",
            f"Order {order.id} has no Razorpay order to pay.",
            correlation_id=order.correlation_id,
            detail={"order_id": order.id},
        )

    order_json = {
        "order_id": order.id,
        "razorpay_order_id": order.razorpay_order_id,
        "razorpay_key_id": settings.RAZORPAY_KEY_ID,
        "amount_paise": order.amount_paise,
    }
    return HTMLResponse(
        _PAGE.format(
            order_id=html.escape(order.id),
            razorpay_order_id=html.escape(order.razorpay_order_id),
            amount_paise=order.amount_paise,
            order_json=json.dumps(order_json),
        )
    )
