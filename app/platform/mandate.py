"""The Mandate Authority (BUILD_SPEC §8).

A mandate is a signed, bounded, revocable grant of purchasing authority from a
human to an agent. It is the artifact the whole project is about.

Two verbs, and the difference between them is the point:

* **propose** is the agent saying *"here is the authority I think I need."* It
  writes `status=PROPOSED` with `signature=NULL`. It grants nothing. MG-001
  fails on an unsigned mandate by construction, so a compromised agent that
  skips the human and submits against its own proposal is stopped by the same
  rule that catches a forged signature.
* **issue** is the human pressing Authorize. Only `issue` signs.

Every numeric limit is clamped here, server-side (§8.3), and `prompt_playback`
is generated from the **clamped** fields — never from model output. The
sentence the human consents to and the numbers the Guard enforces are derived
from the same values and signed together, so consent and enforcement cannot
drift apart.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import emit
from app.config import settings
from app.crypto import public_key_hex, sign
from app.db import get_db
from app.errors import KavachError
from app.ids import new_correlation_id, new_mandate_id
from app.models import Mandate, Merchant
from app.schemas import MandateOut

router = APIRouter(prefix="/api/mandates", tags=["mandate"])

MANDATE_PAYLOAD_TYPE = "kavach.mandate.v1"
CURRENCY = "INR"

# §8.3 — the ceilings. Not the user's to raise.
MAX_AMOUNT_PAISE_CEILING = 1_000_000  # ₹10,000
CUMULATIVE_CAP_PAISE_CEILING = 2_000_000  # ₹20,000
MAX_TRANSACTIONS_CEILING = 3
TTL_MINUTES_CEILING = 60

# `mandates` has no ttl column, so a re-issue that does not name one falls back
# to this. Well inside the §8.3 ceiling.
DEFAULT_TTL_MINUTES = 30

# The authorization card is read by a human in a timezone, not in UTC (§8.4).
IST = timezone(timedelta(hours=5, minutes=30))


def _now() -> datetime:
    # Whole seconds, so the timestamp stored in `mandates` is identical to the
    # one inside the signed payload. Timezone-aware UTC, always.
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rupees(paise: int) -> str:
    """`600000` -> `₹6,000.00`. A display string, never an API field (§18.3)."""
    return f"₹{paise // 100:,}.{paise % 100:02d}"


# ── clamps (§8.3) ─────────────────────────────────────────────────────────


class Limits(BaseModel):
    """The clamped, signable limits. Nothing else is ever signed."""

    currency: str = CURRENCY
    max_amount_paise: int
    cumulative_cap_paise: int
    max_transactions: int
    ttl_minutes: int
    allowed_merchant_ids: list[str]
    allowed_categories: list[str]


def clamp(
    *,
    currency: str,
    max_amount_paise: int,
    cumulative_cap_paise: int,
    max_transactions: int,
    ttl_minutes: int,
    allowed_merchant_ids: list[str],
    allowed_categories: list[str],
) -> tuple[Limits, list[dict[str, Any]]]:
    """Apply every §8.3 clamp. Returns the limits and what was capped.

    Clamping is silent capping, not rejection: a request for ₹50,000 becomes
    ₹10,000. A user cannot be talked into granting more than the system
    permits, because the ceiling is not theirs to raise — and neither is it the
    agent's, which is the case that matters.
    """
    if (currency or "").upper() != CURRENCY:
        raise KavachError(
            "VALIDATION_ERROR",
            f"currency must be {CURRENCY}; this build is single-currency.",
            detail={"observed": currency, "threshold": CURRENCY},
            status_code=422,
        )
    if not allowed_merchant_ids:
        raise KavachError(
            "VALIDATION_ERROR",
            "allowed_merchant_ids must be non-empty. An unbounded merchant "
            "allowlist is not a bound.",
            status_code=422,
        )
    if not allowed_categories:
        raise KavachError(
            "VALIDATION_ERROR",
            "allowed_categories must be non-empty. An unbounded category "
            "allowlist is not a bound.",
            status_code=422,
        )

    clamps: list[dict[str, Any]] = []

    def cap(field: str, requested: int, ceiling: int) -> int:
        if requested > ceiling:
            clamps.append(
                {"field": field, "requested": requested, "granted": ceiling}
            )
            return ceiling
        return requested

    max_amount = cap("max_amount_paise", max_amount_paise, MAX_AMOUNT_PAISE_CEILING)
    cumulative = cap(
        "cumulative_cap_paise", cumulative_cap_paise, CUMULATIVE_CAP_PAISE_CEILING
    )
    # A cumulative cap below the per-transaction cap would authorise a
    # transaction the mandate could not pay for. Raise it to meet the floor.
    if cumulative < max_amount:
        clamps.append(
            {
                "field": "cumulative_cap_paise",
                "requested": cumulative_cap_paise,
                "granted": max_amount,
            }
        )
        cumulative = max_amount
    transactions = cap("max_transactions", max_transactions, MAX_TRANSACTIONS_CEILING)
    ttl = cap("ttl_minutes", ttl_minutes, TTL_MINUTES_CEILING)

    return (
        Limits(
            currency=CURRENCY,
            max_amount_paise=max_amount,
            cumulative_cap_paise=cumulative,
            max_transactions=transactions,
            ttl_minutes=ttl,
            allowed_merchant_ids=list(allowed_merchant_ids),
            allowed_categories=list(allowed_categories),
        ),
        clamps,
    )


# ── prompt_playback (§8.4) ────────────────────────────────────────────────


def _merchant_names(db: Session, merchant_ids: list[str]) -> str:
    names: list[str] = []
    for merchant_id in merchant_ids:
        merchant = db.get(Merchant, merchant_id)
        # Merchant text is untrusted, so it is length-bounded before it enters
        # a sentence a human will read. It never enters a system prompt.
        names.append(merchant.name[:60] if merchant else merchant_id)
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def build_prompt_playback(
    db: Session, limits: Limits, expires_at: datetime
) -> str:
    """The sentence the human actually consents to (§8.4).

    Generated server-side from the clamped numeric fields — the same fields
    that are signed and that the Guard reads. Never from model output.
    """
    local = expires_at.astimezone(IST)
    when = f"{local:%H:%M} IST on {local.day} {local:%B %Y}"
    return (
        f"Authorize the agent to spend up to "
        f"{_rupees(limits.max_amount_paise)} in a single transaction, "
        f"{_rupees(limits.cumulative_cap_paise)} in total across at most "
        f"{limits.max_transactions} transaction"
        f"{'' if limits.max_transactions == 1 else 's'}, with "
        f"{_merchant_names(db, limits.allowed_merchant_ids)} only, on "
        f"{', '.join(limits.allowed_categories)} only, expiring at {when}."
    )


# ── signing payload (§6.4) ────────────────────────────────────────────────


def build_signing_payload(
    *,
    mandate_id: str,
    session_id: str | None,
    user_email: str,
    limits: Limits,
    issued_at: datetime,
    expires_at: datetime,
) -> dict[str, Any]:
    """Exactly the §6.4 keys, in the order §6.4 lists them."""
    return {
        "typ": MANDATE_PAYLOAD_TYPE,
        "mandate_id": mandate_id,
        "session_id": session_id,
        "user_email": user_email,
        "currency": limits.currency,
        "max_amount_paise": limits.max_amount_paise,
        "cumulative_cap_paise": limits.cumulative_cap_paise,
        "max_transactions": limits.max_transactions,
        "allowed_merchant_ids": limits.allowed_merchant_ids,
        "allowed_categories": limits.allowed_categories,
        "issued_at": _iso_z(issued_at),
        "expires_at": _iso_z(expires_at),
    }


def mandate_authority_public_key() -> str:
    """The Mandate Authority's Ed25519 public key, hex (§6.5)."""
    return public_key_hex(settings.MANDATE_SIGNING_SEED)


def get_mandate(db: Session, mandate_id: str) -> Mandate:
    mandate = db.get(Mandate, mandate_id)
    if mandate is None:
        raise KavachError(
            "MANDATE_NOT_FOUND",
            f"No mandate with id {mandate_id}.",
            detail={"mandate_id": mandate_id},
        )
    return mandate


# ── propose (§8.2) ────────────────────────────────────────────────────────


def propose(
    db: Session,
    *,
    user_email: str,
    session_id: str | None,
    correlation_id: str | None,
    currency: str,
    max_amount_paise: int,
    cumulative_cap_paise: int,
    max_transactions: int,
    ttl_minutes: int,
    allowed_merchant_ids: list[str],
    allowed_categories: list[str],
) -> Mandate:
    """Write a `PROPOSED` mandate. No signature, therefore no authority."""
    limits, clamps = clamp(
        currency=currency,
        max_amount_paise=max_amount_paise,
        cumulative_cap_paise=cumulative_cap_paise,
        max_transactions=max_transactions,
        ttl_minutes=ttl_minutes,
        allowed_merchant_ids=allowed_merchant_ids,
        allowed_categories=allowed_categories,
    )
    correlation = correlation_id or new_correlation_id()

    # The proposal is clamped too, so the card the human reads shows what they
    # would actually be granting rather than what the agent asked for. §8.3
    # applies the clamps at issue; applying them here as well means a PROPOSED
    # row can never hold a number above the ceiling, and the Guard reading
    # those columns can never report a cap the system would not honour.
    provisional_expiry = _now() + timedelta(minutes=limits.ttl_minutes)

    mandate = Mandate(
        id=new_mandate_id(),
        session_id=session_id,
        correlation_id=correlation,
        user_email=user_email,
        status="PROPOSED",
        currency=limits.currency,
        max_amount_paise=limits.max_amount_paise,
        cumulative_cap_paise=limits.cumulative_cap_paise,
        max_transactions=limits.max_transactions,
        allowed_merchant_ids=limits.allowed_merchant_ids,
        allowed_categories=limits.allowed_categories,
        # §5.2 — null while PROPOSED, all four of them.
        issued_at=None,
        expires_at=None,
        revoked_at=None,
        prompt_playback=build_prompt_playback(db, limits, provisional_expiry),
        signing_payload=None,
        signature=None,
    )
    db.add(mandate)
    db.flush()

    emit(
        db,
        correlation_id=correlation,
        session_id=session_id,
        event_type="MANDATE_PROPOSED",
        actor="agent",
        payload={
            "mandate_id": mandate.id,
            "currency": mandate.currency,
            "max_amount_paise": mandate.max_amount_paise,
            "cumulative_cap_paise": mandate.cumulative_cap_paise,
            "max_transactions": mandate.max_transactions,
            "allowed_merchant_ids": mandate.allowed_merchant_ids,
            "allowed_categories": mandate.allowed_categories,
            "expires_at": provisional_expiry,
            "prompt_playback": mandate.prompt_playback,
            # What the agent asked for versus what it would actually get.
            "clamps_applied": {"any": bool(clamps), "fields": clamps},
        },
    )
    db.commit()
    return mandate


# ── issue (§8.3, §8.5) ────────────────────────────────────────────────────


def issue(
    db: Session,
    *,
    mandate_id: str,
    max_amount_paise: int | None = None,
    cumulative_cap_paise: int | None = None,
    max_transactions: int | None = None,
    ttl_minutes: int | None = None,
    allowed_merchant_ids: list[str] | None = None,
    allowed_categories: list[str] | None = None,
) -> Mandate:
    """The human pressing Authorize. The only path that signs (§8.2)."""
    mandate = get_mandate(db, mandate_id)
    if mandate.status != "PROPOSED":
        raise KavachError(
            "VALIDATION_ERROR",
            f"Mandate {mandate.id} is {mandate.status}; only a PROPOSED "
            "mandate can be issued. Issuing again would reset limits that "
            "have already been spent against.",
            correlation_id=mandate.correlation_id,
            detail={"mandate_id": mandate.id, "status": mandate.status},
            status_code=409,
        )

    limits, clamps = clamp(
        currency=mandate.currency,
        max_amount_paise=(
            max_amount_paise if max_amount_paise is not None
            else mandate.max_amount_paise
        ),
        cumulative_cap_paise=(
            cumulative_cap_paise if cumulative_cap_paise is not None
            else mandate.cumulative_cap_paise
        ),
        max_transactions=(
            max_transactions if max_transactions is not None
            else mandate.max_transactions
        ),
        ttl_minutes=ttl_minutes if ttl_minutes is not None else DEFAULT_TTL_MINUTES,
        allowed_merchant_ids=(
            allowed_merchant_ids
            if allowed_merchant_ids is not None
            else list(mandate.allowed_merchant_ids or [])
        ),
        allowed_categories=(
            allowed_categories
            if allowed_categories is not None
            else list(mandate.allowed_categories or [])
        ),
    )

    issued_at = _now()
    expires_at = issued_at + timedelta(minutes=limits.ttl_minutes)

    mandate.currency = limits.currency
    mandate.max_amount_paise = limits.max_amount_paise
    mandate.cumulative_cap_paise = limits.cumulative_cap_paise
    mandate.max_transactions = limits.max_transactions
    mandate.allowed_merchant_ids = limits.allowed_merchant_ids
    mandate.allowed_categories = limits.allowed_categories
    mandate.issued_at = issued_at
    mandate.expires_at = expires_at
    # Regenerated from the clamped fields that are about to be signed, so the
    # sentence and the signature describe the same grant.
    mandate.prompt_playback = build_prompt_playback(db, limits, expires_at)

    payload = build_signing_payload(
        mandate_id=mandate.id,
        session_id=mandate.session_id,
        user_email=mandate.user_email,
        limits=limits,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    mandate.signing_payload = payload
    mandate.signature = sign(settings.MANDATE_SIGNING_SEED, payload)
    mandate.status = "ACTIVE"
    db.flush()

    emit(
        db,
        correlation_id=mandate.correlation_id or new_correlation_id(),
        session_id=mandate.session_id,
        event_type="AUTHORIZATION_GRANTED",
        actor="user",
        payload={
            "mandate_id": mandate.id,
            "user_email": mandate.user_email,
            "currency": mandate.currency,
            "max_amount_paise": mandate.max_amount_paise,
            "cumulative_cap_paise": mandate.cumulative_cap_paise,
            "max_transactions": mandate.max_transactions,
            "allowed_merchant_ids": mandate.allowed_merchant_ids,
            "allowed_categories": mandate.allowed_categories,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "signature": mandate.signature,
            # §8.5 — whether anything was clamped down from what was asked for.
            "clamps_applied": {"any": bool(clamps), "fields": clamps},
        },
    )
    db.commit()
    return mandate


# ── revoke ────────────────────────────────────────────────────────────────


def revoke(db: Session, mandate_id: str, *, reason: str | None = None) -> Mandate:
    """Set `REVOKED` and stamp `revoked_at`. MG-002 fails from then on."""
    mandate = get_mandate(db, mandate_id)
    if mandate.status == "REVOKED":
        # Idempotent: no second state change, no second audit row.
        return mandate

    mandate.status = "REVOKED"
    mandate.revoked_at = _now()
    db.flush()

    emit(
        db,
        correlation_id=mandate.correlation_id or new_correlation_id(),
        session_id=mandate.session_id,
        event_type="MANDATE_REVOKED",
        actor="user",
        payload={
            "mandate_id": mandate.id,
            "revoked_at": mandate.revoked_at,
            "reason": reason or "revoked_by_user",
        },
    )
    db.commit()
    return mandate


# ── endpoints (§8.1) ──────────────────────────────────────────────────────


class MandateProposeIn(BaseModel):
    user_email: str
    session_id: str | None = None
    correlation_id: str | None = None
    currency: str = CURRENCY
    max_amount_paise: int = Field(ge=1)
    cumulative_cap_paise: int = Field(ge=1)
    max_transactions: int = Field(ge=1)
    ttl_minutes: int = Field(ge=1)
    allowed_merchant_ids: list[str]
    allowed_categories: list[str]


class MandateIssueIn(BaseModel):
    mandate_id: str
    # Omitted fields fall back to the proposal. The human may lower a number;
    # the clamps mean they cannot raise one past the ceiling.
    max_amount_paise: int | None = Field(default=None, ge=1)
    cumulative_cap_paise: int | None = Field(default=None, ge=1)
    max_transactions: int | None = Field(default=None, ge=1)
    ttl_minutes: int | None = Field(default=None, ge=1)
    allowed_merchant_ids: list[str] | None = None
    allowed_categories: list[str] | None = None


class MandateRevokeIn(BaseModel):
    reason: str | None = None


class MandatePublicKeyOut(BaseModel):
    algorithm: str
    public_key_hex: str
    payload_type: str
    canonicalisation: str


@router.get("/public-key", response_model=MandatePublicKeyOut)
def get_public_key() -> MandatePublicKeyOut:
    """§8.1 — the Mandate Authority public key.

    Registered before `/{id}` so the literal path wins the match. This is the
    only thing a merchant needs in order to verify a mandate (§10, CV-001):
    no database access, no call back to us.
    """
    return MandatePublicKeyOut(
        algorithm="Ed25519",
        public_key_hex=mandate_authority_public_key(),
        payload_type=MANDATE_PAYLOAD_TYPE,
        canonicalisation="JSON, sorted keys, no whitespace, UTF-8",
    )


@router.post("/propose", response_model=MandateOut)
def post_propose(body: MandateProposeIn, db: Session = Depends(get_db)) -> MandateOut:
    """§8.1 — called by the agent. Creates a `PROPOSED` row. **No authority.**"""
    mandate = propose(
        db,
        user_email=body.user_email,
        session_id=body.session_id,
        correlation_id=body.correlation_id,
        currency=body.currency,
        max_amount_paise=body.max_amount_paise,
        cumulative_cap_paise=body.cumulative_cap_paise,
        max_transactions=body.max_transactions,
        ttl_minutes=body.ttl_minutes,
        allowed_merchant_ids=body.allowed_merchant_ids,
        allowed_categories=body.allowed_categories,
    )
    return MandateOut.model_validate(mandate)


@router.post("/issue", response_model=MandateOut)
def post_issue(body: MandateIssueIn, db: Session = Depends(get_db)) -> MandateOut:
    """§8.1 — called by the human. Clamps, signs, sets `ACTIVE`."""
    mandate = issue(
        db,
        mandate_id=body.mandate_id,
        max_amount_paise=body.max_amount_paise,
        cumulative_cap_paise=body.cumulative_cap_paise,
        max_transactions=body.max_transactions,
        ttl_minutes=body.ttl_minutes,
        allowed_merchant_ids=body.allowed_merchant_ids,
        allowed_categories=body.allowed_categories,
    )
    return MandateOut.model_validate(mandate)


@router.post("/{mandate_id}/revoke", response_model=MandateOut)
def post_revoke(
    mandate_id: str,
    body: MandateRevokeIn | None = None,
    db: Session = Depends(get_db),
) -> MandateOut:
    """§8.1 — human or demo panel. The Guard's MG-002 fails from here on."""
    mandate = revoke(db, mandate_id, reason=body.reason if body else None)
    return MandateOut.model_validate(mandate)


@router.get("/{mandate_id}", response_model=MandateOut)
def get_one(mandate_id: str, db: Session = Depends(get_db)) -> MandateOut:
    """§8.1 — mandate plus `signature` and `signing_payload` (§6.2)."""
    return MandateOut.model_validate(get_mandate(db, mandate_id))
