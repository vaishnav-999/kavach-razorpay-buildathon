"""The demo control panel (BUILD_SPEC §15).

Five actions, each of which changes real state and then says exactly what it
changed, plus the §16.5 injection scenario. Nothing here simulates an outcome:
`drift-price` really moves a price in `products`, `revoke-mandate` really
revokes, and `replay-webhook` really re-POSTs the stored bytes and the stored
signature of a webhook Razorpay actually sent. The demo panel is a set of
levers on the running system, not a narration of one.

This module is the assembler. Every route is gated by `require_demo_mode`
(`demo_base.py`), and the actions themselves live one concern to a file:

| Module | Routes | Makes this fail |
|---|---|---|
| `demo_merchant.py` | drift-price, deplete-stock | CV-003, CV-002 |
| `demo_authority.py` | revoke-mandate | MG-002 |
| `demo_webhook.py` | replay-webhook | nothing - proves the dedup |
| `demo_reset.py` | reset | nothing - restores the seed |
| `demo_injection.py` | force-poisoned-cart | MG-005 |
| `demo_evidence.py` | injection (read-only) | nothing - it is the evidence |

Every action emits `DEMO_ACTION_TRIGGERED` with the parameters it used and the
result it produced. The reset **keeps `audit_events`**: the append-only claim
(invariant 8) would be hollow if a demo button truncated the table.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.platform.demo_authority import router as authority_router
from app.platform.demo_base import require_demo_mode
from app.platform.demo_evidence import router as evidence_router
from app.platform.demo_injection import router as injection_router
from app.platform.demo_merchant import router as merchant_router
from app.platform.demo_reset import router as reset_router
from app.platform.demo_webhook import router as webhook_router

router = APIRouter(
    prefix="/api/demo",
    tags=["demo"],
    dependencies=[Depends(require_demo_mode)],
)

# §15, in the order the panel presents them.
router.include_router(merchant_router)
router.include_router(authority_router)
router.include_router(webhook_router)
router.include_router(reset_router)

# §16.5 - the injection scenario. Same gate, same prefix.
router.include_router(injection_router)
router.include_router(evidence_router)
