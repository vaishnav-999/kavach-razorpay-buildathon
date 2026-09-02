"""The Transaction Guard (BUILD_SPEC §9).

The core of the project. If you cut everything else, this survives.

`evaluate()` is a **pure function of database state**. It makes no LLM call, no
network call and no random choice, and it never reads the clock: `now` is
injected by the caller. Given the same rows and the same `now` it returns the
same verdict, which is what makes it testable and what makes an audit of it
mean something.

All nine rules run on every evaluation. There is no short-circuit, because a
guard that stops at the first failure cannot be audited — the console renders
every rule, passing and failing, with the observed value, the threshold, the
unit and a sentence a human can read under demo lighting.

`evaluate()` has exactly one call site: `execute_authorized_purchase()` in
`app/platform/payments.py` (§9.4). Test 21 asserts there is only one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import emit
from app.config import settings
from app.crypto import public_key_hex, verify
from app.errors import KavachError
from app.ids import new_guard_decision_id
from app.models import GuardDecision, Mandate, Quote

# The nine rules, in ID order. Names and block codes are §9.2 verbatim.
RULE_NAMES: dict[str, str] = {
    "MG-001": "mandate_signature_valid",
    "MG-002": "mandate_active",
    "MG-003": "mandate_not_expired",
    "MG-004": "merchant_allowlisted",
    "MG-005": "amount_within_cap",
    "MG-006": "cumulative_cap_respected",
    "MG-007": "categories_allowlisted",
    "MG-008": "quote_integrity",
    "MG-009": "transaction_velocity",
}

RULE_BLOCK_CODES: dict[str, str] = {
    "MG-001": "MANDATE_SIGNATURE_INVALID",
    "MG-002": "MANDATE_NOT_ACTIVE",
    "MG-003": "MANDATE_EXPIRED",
    "MG-004": "MERCHANT_NOT_ALLOWED",
    "MG-005": "AMOUNT_EXCEEDS_MANDATE",
    "MG-006": "CUMULATIVE_CAP_EXCEEDED",
    "MG-007": "CATEGORY_NOT_ALLOWED",
    "MG-008": "QUOTE_INTEGRITY_FAILED",
    "MG-009": "VELOCITY_LIMIT_EXCEEDED",
}


def rupees(paise: int) -> str:
    """`756000` -> `Rs 7,560.00`. Display only; never a value in a payload."""
    sign = "-" if paise < 0 else ""
    paise = abs(int(paise))
    return f"{sign}Rs {paise // 100:,}.{paise % 100:02d}"


def _iso_ms(value: datetime) -> str:
    # §9.3 — `2026-09-02T10:31:44.812Z`.
    value = _aware(value)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def _iso_z(value: datetime) -> str:
    return _aware(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _aware(value: datetime) -> datetime:
    """Coerce to timezone-aware UTC.

    Every timestamp column is `TIMESTAMPTZ` and every value this module
    produces is aware, but a driver that hands back a naive datetime would
    otherwise raise `TypeError` deep inside MG-003 and read as a Guard bug.
    Failing safe here keeps the comparison honest.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class RuleResult:
    """One of the nine rules. Reported whether it passed or failed (§9.1)."""

    rule_id: str
    passed: bool
    observed: Any
    threshold: Any
    unit: str
    detail: str

    @property
    def name(self) -> str:
        return RULE_NAMES[self.rule_id]

    @property
    def block_code(self) -> str | None:
        return None if self.passed else RULE_BLOCK_CODES[self.rule_id]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "passed": self.passed,
            "observed": self.observed,
            "threshold": self.threshold,
            "unit": self.unit,
            "detail": self.detail,
            "block_code": self.block_code,
        }


@dataclass(frozen=True)
class GuardResult:
    """The §9.3 result shape."""

    verdict: str
    decision_id: str
    correlation_id: str
    mandate_id: str
    quote_id: str
    requested_total_paise: int
    currency: str
    evaluated_at: datetime
    duration_ms: int
    failed_rule_id: str | None
    block_code: str | None
    rules: tuple[RuleResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "decision_id": self.decision_id,
            "correlation_id": self.correlation_id,
            "mandate_id": self.mandate_id,
            "quote_id": self.quote_id,
            "requested_total_paise": self.requested_total_paise,
            "currency": self.currency,
            "evaluated_at": _iso_ms(self.evaluated_at),
            "duration_ms": self.duration_ms,
            "failed_rule_id": self.failed_rule_id,
            "block_code": self.block_code,
            "rules": [rule.to_dict() for rule in self.rules],
        }

    @property
    def failed_rule(self) -> RuleResult | None:
        return next((r for r in self.rules if r.rule_id == self.failed_rule_id), None)


class GuardBlocked(KavachError):
    """Raised at the single call site when the verdict is BLOCK (§9.4).

    Nothing below the `raise` in `execute_authorized_purchase()` runs: no
    merchant submit, no Razorpay order. Carries the full result so the console,
    the API response and the agent all see the same nine rules.
    """

    def __init__(self, result: GuardResult) -> None:
        rule = result.failed_rule
        super().__init__(
            result.block_code or "AMOUNT_EXCEEDS_MANDATE",
            rule.detail if rule else "The Transaction Guard blocked this purchase.",
            correlation_id=result.correlation_id,
            detail=result.to_dict(),
        )
        self.result = result


def mandate_authority_public_key() -> str:
    """The Mandate Authority's Ed25519 public key, hex (§6.5)."""
    return public_key_hex(settings.MANDATE_SIGNING_SEED)


def merchant_public_key() -> str:
    """The merchant signing key's public half, used by MG-008."""
    return public_key_hex(settings.MERCHANT_SIGNING_SEED)


# ── the nine rules ────────────────────────────────────────────────────────


def _payload_column_drift(mandate: Mandate) -> list[dict[str, Any]]:
    """Fields where the stored row disagrees with the payload that was signed.

    The signature proves the Mandate Authority issued *that payload*. It says
    nothing about the columns, and the columns are what MG-003 to MG-007 and
    MG-009 actually read. Without this comparison, anything able to write to
    `mandates` could raise a cap on a mandate whose signature still verifies,
    and every downstream rule would enforce the raised number.
    """
    payload = mandate.signing_payload or {}
    expires_at = _iso_z(mandate.expires_at) if mandate.expires_at else None
    pairs: tuple[tuple[str, Any, Any], ...] = (
        ("max_amount_paise",
         int(mandate.max_amount_paise), payload.get("max_amount_paise")),
        ("cumulative_cap_paise",
         int(mandate.cumulative_cap_paise), payload.get("cumulative_cap_paise")),
        ("max_transactions",
         int(mandate.max_transactions), payload.get("max_transactions")),
        ("allowed_merchant_ids",
         list(mandate.allowed_merchant_ids or []),
         payload.get("allowed_merchant_ids")),
        ("allowed_categories",
         list(mandate.allowed_categories or []),
         payload.get("allowed_categories")),
        ("expires_at", expires_at, payload.get("expires_at")),
        ("currency", mandate.currency, payload.get("currency")),
    )
    return [
        {"field": field, "column": column, "signed": signed}
        for field, column, signed in pairs
        if column != signed
    ]


def _mg001(mandate: Mandate | None, mandate_id: str) -> RuleResult:
    """Ed25519 verify of `mandates.signature` over `mandates.signing_payload`,
    and then a check that the row still says what the signature covers.

    A `PROPOSED` mandate has no signature and fails here by construction. That
    is not a special case: a compromised agent that skips the human and submits
    against its own proposal is stopped by the same rule that catches a forged
    signature.
    """
    if mandate is None:
        return RuleResult(
            "MG-001", False, "no_mandate", "valid", "ed25519",
            f"No mandate with id {mandate_id} exists, so there is no signature "
            "to verify.",
        )
    if not mandate.signature or not mandate.signing_payload:
        return RuleResult(
            "MG-001", False, "unsigned", "valid", "ed25519",
            f"Mandate {mandate.id} is {mandate.status} and carries no signature. "
            "Proposing authority is not granting it.",
        )
    if not verify(
        mandate_authority_public_key(), mandate.signing_payload, mandate.signature
    ):
        return RuleResult(
            "MG-001", False, "invalid", "valid", "ed25519",
            "The stored mandate signature does not verify against the Mandate "
            "Authority public key.",
        )

    drift = _payload_column_drift(mandate)
    if drift:
        return RuleResult(
            "MG-001", False, [d["field"] for d in drift], "valid", "ed25519",
            "The mandate signature verifies, but the stored mandate no longer "
            "matches what was signed: "
            + "; ".join(
                f"{d['field']} is {d['column']!r} in the row against "
                f"{d['signed']!r} in the signed payload"
                for d in drift
            )
            + ". The limits the other rules read are not the limits the human "
            "authorised.",
        )

    return RuleResult(
        "MG-001", True, "valid", "valid", "ed25519",
        "Mandate signature verified against the Mandate Authority public key, "
        "and every limit the other rules read matches the signed payload.",
    )


def _mg002(mandate: Mandate | None) -> RuleResult:
    observed = mandate.status if mandate else "no_mandate"
    passed = observed == "ACTIVE"
    if passed:
        detail = "Mandate status is ACTIVE."
    elif mandate is None:
        detail = "No mandate to check; a purchase needs an ACTIVE mandate."
    else:
        detail = (
            f"Mandate status is {observed}; only an ACTIVE mandate may "
            "authorise a purchase."
        )
    return RuleResult("MG-002", passed, observed, "ACTIVE", "status", detail)


def _mg003(mandate: Mandate | None, now: datetime) -> RuleResult:
    if mandate is None or mandate.expires_at is None:
        return RuleResult(
            "MG-003", False, _iso_z(now), "expires_at", "timestamp",
            "The mandate has no expiry, which means no authority was ever "
            "issued from it.",
        )
    expires_at = _aware(mandate.expires_at)
    now = _aware(now)
    passed = now < expires_at
    seconds = int(abs((expires_at - now).total_seconds()))
    detail = (
        f"Mandate expires at {_iso_z(expires_at)}, {seconds} s from now."
        if passed
        else f"Mandate expired at {_iso_z(expires_at)}, {seconds} s ago."
    )
    return RuleResult(
        "MG-003", passed, _iso_z(now), _iso_z(expires_at), "timestamp", detail
    )


def _mg004(mandate: Mandate | None, merchant_id: str) -> RuleResult:
    allowed = list(mandate.allowed_merchant_ids or []) if mandate else []
    passed = merchant_id in allowed
    if passed:
        detail = (
            f"Merchant {merchant_id} is on the mandate's allowlist of "
            f"{len(allowed)} merchant(s)."
        )
    else:
        detail = (
            f"Merchant {merchant_id} is not on the mandate's allowlist "
            f"{allowed or '[]'}."
        )
    return RuleResult("MG-004", passed, merchant_id, allowed, "merchant_id", detail)


def _mg005(mandate: Mandate | None, requested_total_paise: int) -> RuleResult:
    cap = mandate.max_amount_paise if mandate else 0
    passed = requested_total_paise <= cap
    gap = abs(requested_total_paise - cap)
    if passed:
        detail = (
            f"Cart total of {rupees(requested_total_paise)} is within the "
            f"authorised per-transaction cap of {rupees(cap)}, with "
            f"{rupees(gap)} of headroom."
        )
    else:
        detail = (
            f"Cart total of {rupees(requested_total_paise)} exceeds the "
            f"authorised per-transaction cap of {rupees(cap)} by {rupees(gap)}."
        )
    return RuleResult(
        "MG-005", passed, requested_total_paise, cap, "paise", detail
    )


def _mg006(
    mandate: Mandate | None, prior_total_paise: int, requested_total_paise: int
) -> RuleResult:
    cap = mandate.cumulative_cap_paise if mandate else 0
    observed = prior_total_paise + requested_total_paise
    passed = observed <= cap
    gap = abs(observed - cap)
    if passed:
        detail = (
            f"{rupees(prior_total_paise)} already authorised under this mandate "
            f"plus {rupees(requested_total_paise)} requested is "
            f"{rupees(observed)}, within the cumulative cap of {rupees(cap)}."
        )
    else:
        detail = (
            f"{rupees(prior_total_paise)} already authorised under this mandate "
            f"plus {rupees(requested_total_paise)} requested is "
            f"{rupees(observed)}, which exceeds the cumulative cap of "
            f"{rupees(cap)} by {rupees(gap)}."
        )
    return RuleResult("MG-006", passed, observed, cap, "paise", detail)


def _mg007(mandate: Mandate | None, quote: Quote | None) -> RuleResult:
    allowed = list(mandate.allowed_categories or []) if mandate else []
    if quote is None:
        return RuleResult(
            "MG-007", False, "no_quote", allowed, "category",
            "There is no quote, so no line item category can be checked "
            "against the mandate's allowlist.",
        )
    observed = sorted({str(li.get("category")) for li in (quote.line_items or [])})
    offending = [c for c in observed if c not in allowed]
    passed = not offending
    if passed:
        detail = (
            f"Every line item is in {', '.join(observed) or 'no category'}, "
            "which the mandate allows."
        )
    else:
        detail = (
            f"Line items in category {', '.join(offending)} are outside the "
            f"mandate's allowed categories {allowed or '[]'}."
        )
    return RuleResult("MG-007", passed, observed, allowed, "category", detail)


def _mg008(
    quote: Quote | None,
    quote_id: str,
    merchant_id: str,
    requested_total_paise: int,
    now: datetime,
) -> RuleResult:
    """The rule that makes the whole chain trustworthy (§9.2).

    It is where "the number the agent asked us to charge" is forced to equal
    "the number the merchant signed".
    """
    if quote is None:
        return RuleResult(
            "MG-008", False, ["quote_missing"], "intact", "checks",
            f"No quote with id {quote_id} exists, so nothing binds the "
            "requested amount to a merchant signature.",
        )

    failures: list[str] = []
    payload = quote.signing_payload or {}

    if not quote.signature or not verify(
        merchant_public_key(), payload, quote.signature
    ):
        failures.append("signature_invalid")
    if quote.status != "ACTIVE":
        failures.append(f"quote_{quote.status.lower()}")
    if not (_aware(now) < _aware(quote.expires_at)):
        failures.append("quote_expired")
    if quote.merchant_id != merchant_id:
        failures.append("merchant_mismatch")

    line_items = payload.get("line_items") or []
    if any(
        int(li.get("line_total_paise", -1))
        != int(li.get("unit_price_paise", 0)) * int(li.get("qty", 0))
        for li in line_items
    ):
        failures.append("line_total_mismatch")
    if sum(int(li.get("line_total_paise", 0)) for li in line_items) != int(
        quote.total_paise
    ):
        failures.append("sum_mismatch")
    if int(quote.total_paise) != int(requested_total_paise):
        failures.append("requested_total_mismatch")

    passed = not failures
    if passed:
        detail = (
            f"Quote {quote.id} verifies against the merchant signing key, is "
            f"ACTIVE until {_iso_z(quote.expires_at)}, and its signed total of "
            f"{rupees(quote.total_paise)} is exactly the amount requested."
        )
    else:
        detail = (
            f"Quote {quote.id} failed integrity checks: {', '.join(failures)}. "
            f"Signed total {rupees(quote.total_paise)} against requested "
            f"{rupees(requested_total_paise)}."
        )
    return RuleResult(
        "MG-008", passed, failures if failures else "intact", "intact", "checks", detail
    )


def _mg009(mandate: Mandate | None, prior_count: int) -> RuleResult:
    limit = mandate.max_transactions if mandate else 0
    passed = prior_count < limit
    if passed:
        detail = (
            f"{prior_count} prior authorisation(s) under this mandate, below "
            f"the limit of {limit}."
        )
    else:
        detail = (
            f"{prior_count} prior authorisation(s) under this mandate has "
            f"reached the limit of {limit}."
        )
    return RuleResult("MG-009", passed, prior_count, limit, "transactions", detail)


# ── the contract ──────────────────────────────────────────────────────────


def _prior_allows(db: Session, mandate_id: str) -> tuple[int, int]:
    """Count and paise total of prior ALLOW decisions for this mandate.

    Prior ALLOWs are counted whether or not the merchant went on to accept the
    submission. Authority is spent at the moment it is granted, not at the
    moment it succeeds.
    """
    rows = list(
        db.scalars(
            select(GuardDecision).where(
                GuardDecision.mandate_id == mandate_id,
                GuardDecision.verdict == "ALLOW",
            )
        ).all()
    )
    return len(rows), sum(int(r.requested_total_paise) for r in rows)


def evaluate(
    db: Session,
    *,
    correlation_id: str,
    session_id: str | None,
    mandate_id: str,
    quote_id: str,
    merchant_id: str,
    requested_total_paise: int,
    currency: str,
    now: datetime,  # injected, never read from the clock inside
) -> GuardResult:
    """Run all nine rules and record the decision (§9.1).

    Writes exactly one `guard_decisions` row per evaluation — on ALLOW as well
    as BLOCK — and commits it, so the record of the decision outlives anything
    that happens downstream of it.
    """
    started = time.perf_counter()

    mandate = db.get(Mandate, mandate_id)
    quote = db.get(Quote, quote_id)
    prior_count, prior_total_paise = _prior_allows(db, mandate_id)

    rules: tuple[RuleResult, ...] = (
        _mg001(mandate, mandate_id),
        _mg002(mandate),
        _mg003(mandate, now),
        _mg004(mandate, merchant_id),
        _mg005(mandate, requested_total_paise),
        _mg006(mandate, prior_total_paise, requested_total_paise),
        _mg007(mandate, quote),
        _mg008(quote, quote_id, merchant_id, requested_total_paise, now),
        _mg009(mandate, prior_count),
    )

    # Lowest-numbered failing rule. The rules are built in ID order, so the
    # first failure in the tuple is the lowest-numbered one.
    failed = next((rule for rule in rules if not rule.passed), None)
    verdict = "BLOCK" if failed is not None else "ALLOW"
    duration_ms = max(0, round((time.perf_counter() - started) * 1000))

    result = GuardResult(
        verdict=verdict,
        decision_id=new_guard_decision_id(),
        correlation_id=correlation_id,
        mandate_id=mandate_id,
        quote_id=quote_id,
        requested_total_paise=requested_total_paise,
        currency=currency,
        evaluated_at=_aware(now),
        duration_ms=duration_ms,
        failed_rule_id=failed.rule_id if failed else None,
        block_code=failed.block_code if failed else None,
        rules=rules,
    )

    db.add(
        GuardDecision(
            id=result.decision_id,
            correlation_id=correlation_id,
            session_id=session_id,
            # Null when the row does not exist: the foreign keys are real, and
            # a decision about a mandate that was never issued still has to be
            # recorded.
            mandate_id=mandate.id if mandate is not None else None,
            quote_id=quote.id if quote is not None else None,
            merchant_id=merchant_id,
            requested_total_paise=requested_total_paise,
            verdict=verdict,
            failed_rule_id=result.failed_rule_id,
            block_code=result.block_code,
            # All nine, always.
            rules=[rule.to_dict() for rule in rules],
            duration_ms=duration_ms,
            evaluated_at=result.evaluated_at,
        )
    )
    db.flush()

    payload: dict[str, Any] = {
        "guard_decision_id": result.decision_id,
        "verdict": verdict,
        "mandate_id": mandate_id,
        "quote_id": quote_id,
        "merchant_id": merchant_id,
        "requested_total_paise": requested_total_paise,
        "duration_ms": duration_ms,
        "rules": [rule.to_dict() for rule in rules],
    }
    if verdict == "BLOCK":
        payload["failed_rule_id"] = result.failed_rule_id
        payload["block_code"] = result.block_code

    emit(
        db,
        correlation_id=correlation_id,
        session_id=session_id,
        event_type="POLICY_BLOCKED" if verdict == "BLOCK" else "POLICY_APPROVED",
        actor="platform",
        payload=payload,
    )
    # Committed here so the decision survives independently of whatever the
    # merchant or Razorpay does next. A guard decision is a fact about what was
    # authorised, not a step in the purchase transaction.
    db.commit()
    return result
