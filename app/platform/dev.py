"""The §12.3 checkout page.

M3 put a `POST /api/dev/test-checkout` endpoint here that minted a `PROPOSED`
mandate and a `DEV_SCAFFOLD` guard decision so it could create an order before
the Transaction Guard existed. **M5 deleted it.** `execute_authorized_purchase()`
in `app/platform/payments.py` is now the only route to `create_razorpay_order()`,
and it is reachable only below a real ALLOW.

What is left is the page that *pays* an order the Guard already authorised: the
Razorpay checkout modal from §12.3, plus the server-side verification call that
turns a browser's word into a `PAID` order only after the HMAC re-derives
against the order id in our own database. M7 replaces it with the §14 frontend.

The `DEV_SCAFFOLD` rows M3 wrote are left in place: orders point at them by a
`NOT NULL` foreign key, and `guard_decisions` is the record of what was decided.
The verdict string is neither `ALLOW` nor `BLOCK`, so no code that tests for
`ALLOW` matches one.
"""

from __future__ import annotations

import html
import json

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.errors import KavachError
from app.platform import payments

page_router = APIRouter(prefix="/dev", tags=["checkout page"])


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kavach checkout</title>
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
  <h1>Kavach — checkout</h1>
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
    """§12.3 — pay an order the Transaction Guard already authorised."""
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
