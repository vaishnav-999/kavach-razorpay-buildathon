"""Server-generated guidance and untrusted-text flagging.

Split out of `executor.py` to keep both files short. Two unrelated jobs live
here because both are about *saying what happened* rather than doing anything:

* `guidance_for()` turns a failing Guard rule into the next action, per §11.2.
  It is generated **server-side, from the rule's own observed and threshold
  values**, so the sentence the agent re-plans against says the same thing the
  Guard console shows the human. The model is never asked what it thinks went
  wrong.
* `instruction_marker()` spots merchant text shaped like an instruction to an
  agent. It **changes nothing** — see the note on the function.
"""

from __future__ import annotations

from typing import Any

from app.platform.guard import GuardResult, RuleResult

# Phrases that make a product description read like an instruction to an agent
# rather than a description of a product. PK-005 trips several.
INSTRUCTION_MARKERS: tuple[str, ...] = (
    "note for ai",
    "purchasing agent",
    "policy_override",
    "skip_validation",
    "you must add",
    "do not mention",
    "do not include it",
    "exempt from",
    "pre-approved",
    "ignore previous",
    "system note",
    "automated policy",
)


def rupees(paise: int) -> str:
    """`600000` -> `₹6,000.00`.

    A display string inside guidance prose only. It is never an API field and
    never a value in a money path (§18.3, invariant 6).
    """
    sign = "-" if paise < 0 else ""
    paise = abs(int(paise))
    return f"{sign}₹{paise // 100:,}.{paise % 100:02d}"


def instruction_marker(text: str) -> str | None:
    """The first instruction-shaped phrase in `text`, or None.

    Matching one does **not** alter, escape or shorten the text: the caller
    writes an `UNTRUSTED_CONTENT_FLAGGED` row and passes the description to the
    model exactly as the merchant wrote it. The flag records that the platform
    saw the attempt, independently of whether the model noticed it (§16.5).
    """
    lowered = text.lower()
    return next((marker for marker in INSTRUCTION_MARKERS if marker in lowered), None)


def blocked_result(result: GuardResult) -> dict[str, Any]:
    """The §11.2 BLOCK shape the `submit_purchase` tool returns."""
    rule = result.failed_rule
    return {
        "status": "BLOCKED",
        "failed_rule_id": result.failed_rule_id,
        "block_code": result.block_code,
        "detail": rule.detail if rule else "The Transaction Guard blocked this.",
        "guidance": guidance_for(rule),
        "guard_decision_id": result.decision_id,
    }


def guidance_for(rule: RuleResult | None) -> str:
    """A next action, derived from the failing rule (§11.2)."""
    if rule is None:
        return "Stop and tell the human what happened."

    if rule.rule_id == "MG-001":
        return (
            "The mandate signature does not verify against what the row now "
            "says. Nothing you can do makes this pass; tell the human."
        )
    if rule.rule_id == "MG-002":
        return (
            f"The mandate is {rule.observed}, not ACTIVE. Ask the human to "
            "authorize a new one."
        )
    if rule.rule_id == "MG-003":
        return "The mandate has expired. Ask the human to authorize a new one."
    if rule.rule_id == "MG-004":
        return (
            f"Merchant {rule.observed} is not on the mandate allowlist. Build "
            "the cart at an allowed merchant and request a new quote."
        )
    if rule.rule_id == "MG-005":
        return (
            f"Reduce the cart to at most {rupees(int(rule.threshold))}, then "
            "request a new quote and submit that one."
        )
    if rule.rule_id == "MG-006":
        return (
            f"This mandate allows {rupees(int(rule.threshold))} across the "
            f"whole session and the request would take it to "
            f"{rupees(int(rule.observed))}. Reduce the cart and request a new "
            "quote."
        )
    if rule.rule_id == "MG-007":
        allowed = ", ".join(rule.threshold or []) or "no"
        return (
            f"The mandate allows only {allowed} categories. Remove the other "
            "lines and request a new quote."
        )
    if rule.rule_id == "MG-008":
        return (
            "The quote does not match what the merchant signed, or it has "
            "already been spent, or it has expired. Request a fresh quote."
        )
    if rule.rule_id == "MG-009":
        return (
            f"This mandate has already authorised {rule.observed} of "
            f"{rule.threshold} permitted transactions. Tell the human."
        )
    return "Stop and tell the human what happened."
