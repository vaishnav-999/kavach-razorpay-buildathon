"""Force the poisoned cart through the real submit path (BUILD_SPEC §16.5).

PK-005's description carries a real prompt injection. It is served verbatim by
`/merchant/catalog`, it reaches the model verbatim inside
`<untrusted_merchant_data>`, and **nothing here sanitises, escapes or truncates
it**. A model that resists the injection proves nothing about the
architecture; a model that falls for it and is blocked anyway proves
everything.

`gemini-3.6-flash` currently resists it, so the block cannot be demonstrated by
prompting alone. This endpoint makes it demonstrable without weakening
anything: it builds the cart the injection asks for — PK-001 x8, PK-003 x4,
**PK-005 x12** — has the merchant sign it, and submits it through
`execute_purchase_for_quote()`, the same function the agent's `submit_purchase`
tool calls. There is no second code path and no simulated verdict. The Guard
evaluates all nine rules and MG-005 blocks on the real arithmetic, and the
Razorpay client's own call counter is read either side of the submission, so
"no Razorpay activity" is a measurement rather than a claim.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import emit
from app.db import get_db
from app.errors import KavachError
from app.ids import new_correlation_id
from app.merchant import discovery, service
from app.models import AgentSession, Mandate, Order
from app.platform import mandate as mandate_authority
from app.platform import payments, razorpay_client
from app.platform.demo_scenario import (
    DEMO_MAX_AMOUNT_PAISE,
    DEMO_MAX_TRANSACTIONS,
    DEMO_TTL_MINUTES,
    DEMO_USER_EMAIL,
    INJECTION_SKU,
    POISONED_CART,
    total_paise,
)
from app.platform.guard import GuardBlocked

router = APIRouter()


# ── wire models ───────────────────────────────────────────────────────────


class ForcePoisonedCartIn(BaseModel):
    correlation_id: str | None = None
    session_id: str | None = None
    # Which mandate to submit against. Omitted, the most recent ACTIVE one is
    # used, and the canonical §16.3 mandate is issued if there is none.
    mandate_id: str | None = None


class ForcePoisonedCartOut(BaseModel):
    action: str = "force-poisoned-cart"
    summary: str
    correlation_id: str
    cart: dict[str, Any]
    quote: dict[str, Any]
    mandate: dict[str, Any]
    # The §9.3 result, exactly as the Guard wrote it to guard_decisions.
    guard: dict[str, Any]
    razorpay: dict[str, Any]
    injection: dict[str, Any]


# ── helpers ───────────────────────────────────────────────────────────────


def _resolve_mandate(
    db: Session,
    *,
    mandate_id: str | None,
    session_id: str | None,
    correlation_id: str,
    merchant_id: str,
) -> tuple[Mandate, str]:
    """The mandate this submission is made under, and where it came from.

    An explicitly named mandate wins. Otherwise the most recent ACTIVE one is
    used — in a live demo that is the mandate the human just authorised on the
    card, which is the honest thing to submit against.

    With no ACTIVE mandate at all, the canonical §16.3 grant is issued through
    the ordinary propose → issue path so the control works standalone. `issue()`
    is still the only signer, the §8.3 clamps still apply, and the human
    pressing this button in DEMO_MODE is the human granting it. The response
    says which of the three happened.
    """
    if mandate_id:
        return mandate_authority.get_mandate(db, mandate_id), "named"

    active = db.scalars(
        select(Mandate)
        .where(Mandate.status == "ACTIVE")
        .order_by(Mandate.issued_at.desc())
        .limit(1)
    ).first()
    if active is not None:
        return active, "existing_active"

    proposed = mandate_authority.propose(
        db,
        user_email=DEMO_USER_EMAIL,
        session_id=session_id,
        correlation_id=correlation_id,
        currency="INR",
        max_amount_paise=DEMO_MAX_AMOUNT_PAISE,
        cumulative_cap_paise=DEMO_MAX_AMOUNT_PAISE,
        max_transactions=DEMO_MAX_TRANSACTIONS,
        ttl_minutes=DEMO_TTL_MINUTES,
        allowed_merchant_ids=[merchant_id],
        allowed_categories=["meals"],
    )
    issued = mandate_authority.issue(
        db, mandate_id=proposed.id, ttl_minutes=DEMO_TTL_MINUTES
    )
    return issued, "demo_issued"


# ── the control ───────────────────────────────────────────────────────────


@router.post("/force-poisoned-cart", response_model=ForcePoisonedCartOut)
def force_poisoned_cart(
    body: ForcePoisonedCartIn | None = None, db: Session = Depends(get_db)
) -> ForcePoisonedCartOut:
    """Submit the cart the injection asks for, through the real submit path."""
    body = body or ForcePoisonedCartIn()
    merchant = discovery.primary_merchant(db)

    session_id = body.session_id
    if session_id and db.get(AgentSession, session_id) is None:
        session_id = None
    correlation_id = body.correlation_id or (
        db.get(AgentSession, session_id).correlation_id if session_id else None
    )
    if not correlation_id:
        correlation_id = new_correlation_id()

    expected_total = total_paise(db, merchant.id, POISONED_CART)
    mandate, mandate_source = _resolve_mandate(
        db,
        mandate_id=body.mandate_id,
        session_id=session_id,
        correlation_id=correlation_id,
        merchant_id=merchant.id,
    )

    # Refuse rather than transact. A mandate whose cap already covers 756000
    # would make this submission a legitimate purchase, and the Guard would
    # correctly ALLOW it — which on a live deployment means a real Razorpay
    # order. The demo control will not create one behind a judge's back, and it
    # will not fake a BLOCK either. It says so and stops.
    if mandate.status == "ACTIVE" and mandate.max_amount_paise >= expected_total:
        raise KavachError(
            "DEMO_PRECONDITION_FAILED",
            f"Mandate {mandate.id} authorises {mandate.max_amount_paise} paise "
            f"per transaction, which covers the poisoned cart's "
            f"{expected_total}. The Guard would ALLOW this submission and a "
            "real Razorpay order would be created. Revoke that mandate, or "
            "authorise one below the poisoned total, and try again.",
            correlation_id=correlation_id,
            detail={
                "mandate_id": mandate.id,
                "max_amount_paise": int(mandate.max_amount_paise),
                "poisoned_total_paise": expected_total,
                "mandate_source": mandate_source,
            },
        )

    # The merchant computes and signs every figure (§7.7). Nothing this
    # endpoint sends can change a price.
    cart = service.create_cart(
        db,
        merchant_id=merchant.id,
        session_id=session_id,
        correlation_id=correlation_id,
    )
    for sku, qty in POISONED_CART:
        service.add_cart_item(db, cart.id, sku=sku, qty=qty)
    quote = service.create_quote(db, cart.id)

    calls_before = razorpay_client.call_count()
    try:
        payments.execute_purchase_for_quote(
            db,
            session_id=session_id,
            correlation_id=correlation_id,
            quote_id=quote.id,
            mandate_id=mandate.id,
            idempotency_key=f"demo-injection:{quote.id}",
        )
    except GuardBlocked as blocked:
        result = blocked.result
    else:  # pragma: no cover - the cap check above makes this unreachable
        raise KavachError(
            "DEMO_PRECONDITION_FAILED",
            "The poisoned cart was ALLOWED. That is a real authorisation, not "
            "a demo failure: check the mandate's caps.",
            correlation_id=correlation_id,
            detail={"quote_id": quote.id, "mandate_id": mandate.id},
        )
    calls_after = razorpay_client.call_count()

    orders = list(db.scalars(select(Order).where(Order.quote_id == quote.id)))

    # §15 — every demo action leaves a record of itself. The Guard has already
    # written POLICY_BLOCKED and the full nine-rule decision; this says who
    # pulled the lever and what it was pointed at.
    emit(
        db,
        correlation_id=correlation_id,
        session_id=session_id,
        event_type="DEMO_ACTION_TRIGGERED",
        actor="demo",
        payload={
            "action": "force-poisoned-cart",
            "params": {
                "items": [{"sku": sku, "qty": qty} for sku, qty in POISONED_CART],
                "quote_id": quote.id,
                "mandate_id": mandate.id,
                "mandate_source": mandate_source,
            },
            "result": {
                "verdict": result.verdict,
                "failed_rule_id": result.failed_rule_id,
                "block_code": result.block_code,
                "guard_decision_id": result.decision_id,
                "requested_total_paise": result.requested_total_paise,
                "max_amount_paise": int(mandate.max_amount_paise),
                "razorpay_client_calls": calls_after - calls_before,
                "order_created": bool(orders),
            },
        },
    )
    db.commit()
    injected = next(
        (line for line in quote.line_items if line.get("sku") == INJECTION_SKU), {}
    )
    failed = result.failed_rule

    return ForcePoisonedCartOut(
        summary=(
            f"Submitted {result.requested_total_paise} paise against a mandate "
            f"capped at {mandate.max_amount_paise}. Verdict "
            f"{result.verdict}, failed_rule_id {result.failed_rule_id}, "
            f"block_code {result.block_code}. All "
            f"{len(result.rules)} rules were evaluated and no order exists."
        ),
        correlation_id=correlation_id,
        cart={
            "cart_id": cart.id,
            "merchant_id": merchant.id,
            "items": [{"sku": sku, "qty": qty} for sku, qty in POISONED_CART],
        },
        quote={
            "quote_id": quote.id,
            "total_paise": int(quote.total_paise),
            "currency": quote.currency,
            "line_items": quote.line_items,
            "signature": quote.signature,
        },
        mandate={
            "mandate_id": mandate.id,
            "status": mandate.status,
            "source": mandate_source,
            "max_amount_paise": int(mandate.max_amount_paise),
            "cumulative_cap_paise": int(mandate.cumulative_cap_paise),
            "prompt_playback": mandate.prompt_playback,
        },
        guard=result.to_dict(),
        razorpay={
            # Read off the Razorpay client itself, before and after the call.
            "client_calls_before": calls_before,
            "client_calls_after": calls_after,
            "orders_for_quote": len(orders),
            "order_created": bool(orders),
            "detail": (
                "create_razorpay_order() sits below the raise in "
                "execute_authorized_purchase(). On BLOCK it is unreachable, so "
                "the merchant was never called and neither was Razorpay."
            ),
        },
        injection={
            "sku": INJECTION_SKU,
            "qty": injected.get("qty"),
            "line_total_paise": injected.get("line_total_paise"),
            "source": (
                "products.description for PK-005, served verbatim by "
                "/merchant/catalog and never sanitised."
            ),
            "failed_rule": failed.to_dict() if failed else None,
        },
    )
