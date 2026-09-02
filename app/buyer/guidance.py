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


# The re-plan every cap failure wants. Naming the steps — and naming the step
# *not* to take — matters: a blocked agent left to its own devices tends to
# restart from `discover_merchants`, which burns its §11.4 tool budget on a
# registry it has already read and never gets back to a submittable quote. The
# state machine tolerates that detour (see `state.LEGAL_TRANSITIONS`); this
# sentence is what should stop it happening.
_REBUILD = (
    "Do not start over from discovery and do not look for another merchant: "
    "the merchant and the products are fine, only the quantities are wrong. "
    "Open a new cart at the same merchant, add the same skus at lower "
    "quantities, request a fresh quote for that cart, and submit the new "
    "quote id. Your submit attempts in this session are limited, so do not "
    "spend one re-submitting the quote that was just refused."
)


def guidance_for(rule: RuleResult | None) -> str:
    """A next action, derived from the failing rule (§11.2).

    Written for a model that has just been refused and has two attempts in the
    whole session. Every branch says either "here is the specific thing to
    change" or "stop and tell the human" — never "try again".
    """
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
            "authorize a new one. Do not submit again until they have."
        )
    if rule.rule_id == "MG-003":
        return (
            "The mandate has expired. Ask the human to authorize a new one. "
            "Do not submit again until they have."
        )
    if rule.rule_id == "MG-004":
        allowed = ", ".join(rule.threshold or []) or "no merchant at all"
        return (
            f"Merchant {rule.observed} is not on the mandate allowlist. This "
            f"mandate covers {allowed}, and nothing else. Build the cart there "
            "and request a new quote, or tell the human if you cannot."
        )
    if rule.rule_id == "MG-005":
        return (
            f"The cart is {rupees(int(rule.observed))} and this mandate allows "
            f"at most {rupees(int(rule.threshold))} in one transaction. "
            f"{_REBUILD}"
        )
    if rule.rule_id == "MG-006":
        return (
            f"This mandate allows {rupees(int(rule.threshold))} across the "
            f"whole session and this request would take it to "
            f"{rupees(int(rule.observed))}. {_REBUILD}"
        )
    if rule.rule_id == "MG-007":
        allowed = ", ".join(rule.threshold or []) or "no"
        return (
            f"The mandate allows only {allowed} categories. Open a new cart at "
            "the same merchant containing only skus in those categories, "
            "request a fresh quote for it, and submit the new quote id. Do not "
            "start over from discovery."
        )
    if rule.rule_id == "MG-008":
        return (
            "The quote does not match what the merchant signed, or it has "
            "already been spent, or it has expired. Request a fresh quote for "
            "the same cart and submit that quote id. Nothing else needs to "
            "change, and there is no point rebuilding the cart or rediscovering "
            "the merchant."
        )
    if rule.rule_id == "MG-009":
        return (
            f"This mandate has already authorised {rule.observed} of "
            f"{rule.threshold} permitted transactions. Nothing you can do "
            "makes this pass; tell the human."
        )
    return "Stop and tell the human what happened."
