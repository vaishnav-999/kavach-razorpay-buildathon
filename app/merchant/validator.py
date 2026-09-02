"""The Checkout Validator (BUILD_SPEC §10).

The Transaction Guard protects the **buyer** from their own agent. This
protects the **merchant** from a stale or forged submission. Both must pass,
and they are deliberately separate: in the real world they belong to different
companies, and the design has to survive that.

The asymmetry is the point. CV-001 verifies the mandate with **nothing but the
Mandate Authority's public key**. It does not read `mandates`, it does not read
`guard_decisions`, and it does not call the platform back. A real merchant
would have access to none of those, so neither does this one.

Like the Guard, all four checks run and all four report — the failing one is
the lowest-numbered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.crypto import public_key_hex, verify
from app.models import Product, Quote

MANDATE_PAYLOAD_TYPE = "kavach.mandate.v1"

# §10 — check id to name and error code, verbatim.
CHECK_NAMES: dict[str, str] = {
    "CV-001": "mandate_verified",
    "CV-002": "stock_available",
    "CV-003": "price_unchanged",
    "CV-004": "quote_fresh",
}

CHECK_CODES: dict[str, str] = {
    "CV-001": "MERCHANT_MANDATE_INVALID",
    "CV-002": "MERCHANT_OUT_OF_STOCK",
    "CV-003": "MERCHANT_PRICE_DRIFT",
    "CV-004": "MERCHANT_QUOTE_STALE",
}


def mandate_authority_public_key() -> str:
    """The Mandate Authority's Ed25519 public key (§6.5).

    In a deployment where the merchant really is a different company this is
    fetched once from `GET /api/mandates/public-key` and cached. Here it is
    derived from configuration, and **only the public half is ever used** — no
    merchant code signs a mandate, and no merchant code reads a mandate row.
    """
    return public_key_hex(settings.MANDATE_SIGNING_SEED)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_iso_z(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _iso_z(value: datetime) -> str:
    return _aware(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rupees(paise: int) -> str:
    sign = "-" if paise < 0 else ""
    paise = abs(int(paise))
    return f"{sign}Rs {paise // 100:,}.{paise % 100:02d}"


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    passed: bool
    observed: Any
    threshold: Any
    unit: str
    detail: str

    @property
    def name(self) -> str:
        return CHECK_NAMES[self.check_id]

    @property
    def code(self) -> str | None:
        return None if self.passed else CHECK_CODES[self.check_id]

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "name": self.name,
            "passed": self.passed,
            "observed": self.observed,
            "threshold": self.threshold,
            "unit": self.unit,
            "detail": self.detail,
            "code": self.code,
        }


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    failed_check_id: str | None
    code: str | None
    detail: str | None
    checks: tuple[CheckResult, ...]
    # The rows CV-002 locked, keyed by sku. Handed back so the caller can
    # decrement under the same lock rather than re-reading and racing.
    locked_products: dict[str, Product] = field(default_factory=dict)

    def to_list(self) -> list[dict[str, Any]]:
        return [check.to_dict() for check in self.checks]


# ── the four checks ───────────────────────────────────────────────────────


def _cv001(
    *,
    mandate_signing_payload: dict | None,
    mandate_signature: str | None,
    merchant_id: str,
    now: datetime,
) -> CheckResult:
    """Ed25519 verify with the Mandate Authority public key, and nothing else."""
    failures: list[str] = []
    payload = mandate_signing_payload or {}

    if not payload or not mandate_signature:
        failures.append("mandate_absent")
    elif not verify(mandate_authority_public_key(), payload, mandate_signature):
        failures.append("signature_invalid")

    if payload.get("typ") != MANDATE_PAYLOAD_TYPE:
        failures.append("wrong_payload_type")

    allowed = payload.get("allowed_merchant_ids") or []
    if merchant_id not in allowed:
        failures.append("merchant_not_allowed")

    expires_at = _parse_iso_z(payload.get("expires_at"))
    if expires_at is None:
        failures.append("no_expiry")
    elif not (_aware(now) < expires_at):
        failures.append("mandate_expired")

    if not failures:
        # `expires_at` is non-None here: a missing or unparseable expiry is
        # itself a failure above.
        assert expires_at is not None
        return CheckResult(
            "CV-001", True, "verified", "verified", "ed25519",
            f"Mandate {payload.get('mandate_id')} verifies against the Mandate "
            f"Authority public key, names this merchant, and is valid until "
            f"{_iso_z(expires_at)}.",
        )
    return CheckResult(
        "CV-001", False, failures, "verified", "ed25519",
        "The submitted mandate was not accepted: " + ", ".join(failures) + ".",
    )


def _lock_products(db: Session, quote: Quote) -> dict[str, Product]:
    """`SELECT ... FOR UPDATE` on every product in the quote (§7.8 step 3).

    Ordered by id so concurrent submissions take the row locks in the same
    sequence and cannot deadlock against each other.
    """
    skus = [str(li.get("sku")) for li in (quote.signing_payload or {}).get(
        "line_items", []
    )]
    if not skus:
        return {}
    rows = db.scalars(
        select(Product)
        .where(Product.merchant_id == quote.merchant_id, Product.sku.in_(skus))
        .order_by(Product.id)
        .with_for_update()
    ).all()
    return {row.sku: row for row in rows}


def _cv002(quote: Quote, products: dict[str, Product]) -> CheckResult:
    shortfalls: list[dict[str, Any]] = []
    for line in (quote.signing_payload or {}).get("line_items", []):
        sku = str(line.get("sku"))
        qty = int(line.get("qty", 0))
        product = products.get(sku)
        if product is None or not product.active:
            shortfalls.append({"sku": sku, "requested": qty, "available": 0})
        elif product.stock_qty < qty:
            shortfalls.append(
                {"sku": sku, "requested": qty, "available": product.stock_qty}
            )

    if not shortfalls:
        return CheckResult(
            "CV-002", True,
            {p.sku: p.stock_qty for p in products.values()},
            {
                str(li.get("sku")): int(li.get("qty", 0))
                for li in (quote.signing_payload or {}).get("line_items", [])
            },
            "units",
            "Every line item is in stock under a row lock held for this "
            "submission.",
        )
    return CheckResult(
        "CV-002", False, shortfalls, "requested_qty", "units",
        "Insufficient stock: "
        + ", ".join(
            f"{s['sku']} needs {s['requested']}, {s['available']} on hand"
            for s in shortfalls
        )
        + ".",
    )


def _cv003(quote: Quote, products: dict[str, Product]) -> CheckResult:
    """The price-drift catcher.

    Current prices are re-read and the total recomputed. It has to reproduce
    the quote's `total_paise` **exactly** — not approximately, and not within a
    tolerance. A merchant that raised a price after signing does not get to
    charge the new one against the old signature.
    """
    recomputed = 0
    drifted: list[dict[str, Any]] = []
    for line in (quote.signing_payload or {}).get("line_items", []):
        sku = str(line.get("sku"))
        qty = int(line.get("qty", 0))
        quoted_unit = int(line.get("unit_price_paise", 0))
        product = products.get(sku)
        if product is None:
            drifted.append({"sku": sku, "quoted": quoted_unit, "current": None})
            continue
        if product.unit_price_paise != quoted_unit:
            drifted.append(
                {
                    "sku": sku,
                    "quoted": quoted_unit,
                    "current": product.unit_price_paise,
                }
            )
        recomputed += product.unit_price_paise * qty

    passed = not drifted and recomputed == int(quote.total_paise)
    if passed:
        return CheckResult(
            "CV-003", True, recomputed, int(quote.total_paise), "paise",
            f"Current prices still reproduce the signed total of "
            f"{_rupees(quote.total_paise)} exactly.",
        )
    return CheckResult(
        "CV-003", False, recomputed, int(quote.total_paise), "paise",
        f"Prices have moved since the quote was signed: current prices total "
        f"{_rupees(recomputed)} against the signed "
        f"{_rupees(quote.total_paise)}"
        + (
            " ("
            + ", ".join(
                f"{d['sku']} {d['quoted']} -> {d['current']}" for d in drifted
            )
            + ")"
            if drifted
            else ""
        )
        + ".",
    )


def _cv004(quote: Quote, now: datetime) -> CheckResult:
    expires_at = _aware(quote.expires_at)
    fresh = quote.status == "ACTIVE" and _aware(now) < expires_at
    if fresh:
        seconds = int((expires_at - _aware(now)).total_seconds())
        return CheckResult(
            "CV-004", True, quote.status, "ACTIVE", "status",
            f"Quote {quote.id} is ACTIVE with {seconds} s left before it "
            "expires.",
        )
    reason = (
        f"status is {quote.status}"
        if quote.status != "ACTIVE"
        else f"it expired at {_iso_z(expires_at)}"
    )
    return CheckResult(
        "CV-004", False, quote.status, "ACTIVE", "status",
        f"Quote {quote.id} cannot be spent: {reason}. A CONSUMED quote cannot "
        "be spent twice.",
    )


# ── the validator ─────────────────────────────────────────────────────────


def validate_checkout(
    db: Session,
    *,
    quote: Quote,
    mandate_signing_payload: dict | None,
    mandate_signature: str | None,
    now: datetime,
) -> ValidationResult:
    """Run CV-001 to CV-004 (§10). All four run; all four report.

    `now` is injected for the same reason the Guard injects it: a validator
    that reads the clock cannot be tested against a boundary.
    """
    products = _lock_products(db, quote)

    checks = (
        _cv001(
            mandate_signing_payload=mandate_signing_payload,
            mandate_signature=mandate_signature,
            merchant_id=quote.merchant_id,
            now=now,
        ),
        _cv002(quote, products),
        _cv003(quote, products),
        _cv004(quote, now),
    )

    failed = next((check for check in checks if not check.passed), None)
    return ValidationResult(
        passed=failed is None,
        failed_check_id=failed.check_id if failed else None,
        code=failed.code if failed else None,
        detail=failed.detail if failed else None,
        checks=checks,
        locked_products=products,
    )
