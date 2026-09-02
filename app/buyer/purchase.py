"""Tools 8 to 10 (BUILD_SPEC §11.2) — authority, purchase and reporting.

These three do not talk to the merchant. `propose_mandate` goes to the Mandate
Authority, `submit_purchase` goes to the platform's single Guard call site, and
`report_finding` writes to the audit trail.

Neither of the first two accepts a sum. The agent names a quote; the limits and
the amount are read server-side from the row the merchant signed. A fully
compromised model cannot propose more authority than the quote it is proposing
to buy, and cannot ask to be charged a figure the merchant did not sign.
"""

from __future__ import annotations

from typing import Any

from app.buyer.executor import ToolContext, ToolOutcome
from app.buyer.guidance import blocked_result
from app.platform import mandate as mandate_authority
from app.platform import payments
from app.platform.guard import GuardBlocked


# -- 8 ---------------------------------------------------------------------


def propose_mandate(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """§8.2 propose — writes a PROPOSED row. **Grants nothing.**

    The row has no signature, so MG-001 fails against it by construction: a
    compromised agent that skips the human and submits against its own proposal
    is stopped by the same rule that catches a forged signature.
    """
    quote_id = str(args.get("quote_id") or "")
    justification = str(args.get("justification") or "")

    mandate, quote = mandate_authority.propose_for_quote(
        ctx.db,
        quote_id=quote_id,
        user_email=ctx.session.user_email,
        session_id=ctx.session.id,
        correlation_id=ctx.correlation_id,
    )

    card = {
        "mandate_id": mandate.id,
        "status": mandate.status,
        "quote_id": quote.id,
        "currency": mandate.currency,
        "max_amount_paise": mandate.max_amount_paise,
        "cumulative_cap_paise": mandate.cumulative_cap_paise,
        "max_transactions": mandate.max_transactions,
        "allowed_merchant_ids": list(mandate.allowed_merchant_ids or []),
        "allowed_categories": list(mandate.allowed_categories or []),
        # Generated server-side from the clamped fields (§8.4), never from
        # model output. The agent's own words are shown beside it, labelled as
        # the agent's, so the human can tell the two apart.
        "prompt_playback": mandate.prompt_playback,
        "agent_justification": justification,
    }

    return ToolOutcome(
        result={
            **card,
            "note": (
                "PROPOSED carries no signature and therefore no authority. "
                "Nothing can be bought until the human authorizes it, and the "
                "human may grant less than this."
            ),
        },
        next_state="AWAITING_AUTHORIZATION",
        awaiting_authorization=card,
    )


# -- 9 ---------------------------------------------------------------------


def submit_purchase(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """§11.2 — the only guard-gated tool.

    Everything below the Guard's `raise` in `execute_authorized_purchase()` is
    unreachable on BLOCK: no merchant submit, no Razorpay order. Test 10 proves
    that mechanically.
    """
    idempotency_key = str(args.get("idempotency_key") or "")
    if not idempotency_key:
        return ToolOutcome(
            result={
                "status": "ERROR",
                "code": "IDEMPOTENCY_KEY_REQUIRED",
                "detail": "submit_purchase needs an idempotency_key you chose.",
            }
        )

    try:
        purchase = payments.execute_purchase_for_quote(
            ctx.db,
            session_id=ctx.session.id,
            correlation_id=ctx.correlation_id,
            quote_id=str(args.get("quote_id") or ""),
            mandate_id=str(args.get("mandate_id") or ""),
            idempotency_key=idempotency_key,
        )
    except GuardBlocked as blocked:
        # The verdict and all nine rule results are already in
        # `guard_decisions` and POLICY_BLOCKED (§9.4, invariant 10).
        return ToolOutcome(
            result=blocked_result(blocked.result), next_state="BLOCKED"
        )

    order = purchase.order
    return ToolOutcome(
        result={
            "status": "ORDER_CREATED",
            "guard_verdict": purchase.guard.verdict,
            "guard_decision_id": purchase.guard.decision_id,
            "order_id": order.id,
            "razorpay_order_id": order.razorpay_order_id,
            "order_status": order.status,
            "amount_paise": order.amount_paise,
            "currency": order.currency,
            "replayed": purchase.replayed,
            "note": (
                "An order exists and is awaiting payment. The human pays it. "
                "Do not describe this as a completed payment."
            ),
        },
        next_state="ORDER_CREATED",
    )


# -- 10 --------------------------------------------------------------------


def report_finding(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """§11.2 — how the agent surfaces an embedded instruction it noticed.

    The excerpt is merchant text and is stored as evidence, capped at 1000
    characters by `emit()` (§13.2). Reporting moves no state: noticing an
    attack is not a step in a purchase.
    """
    ctx.emit(
        "AGENT_FINDING_REPORTED",
        "agent",
        {
            "merchant_id": str(args.get("merchant_id") or ""),
            "sku": str(args.get("sku") or "") or None,
            "excerpt": str(args.get("excerpt") or ""),
            "summary": str(args.get("summary") or ""),
            "severity": str(args.get("severity") or "medium"),
        },
    )
    return ToolOutcome(
        result={
            "status": "RECORDED",
            "detail": (
                "The finding is in the audit trail under this correlation id "
                "and is visible to the human."
            ),
        },
        next_state=None,
    )
