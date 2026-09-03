"""Withdraw authority (BUILD_SPEC §15).

Revocation is immediate and needs no cooperation from the agent: the Guard
reads `mandates.status` on every submission, so authority that has been
withdrawn cannot be spent even by a model that still holds the mandate id.
MG-001 keeps passing — the signature is genuine — and MG-002 fails, which is
exactly the distinction the console is there to show.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import KavachError
from app.models import Mandate
from app.platform import mandate as mandate_authority
from app.platform.demo_base import (
    DemoActionOut,
    DemoChange,
    RevokeMandateIn,
    correlation_for,
    record_action,
)

router = APIRouter()


# ── 3. revoke mandate (MG-002) ────────────────────────────────────────────


@router.post("/revoke-mandate", response_model=DemoActionOut)
def revoke_mandate(
    body: RevokeMandateIn | None = None, db: Session = Depends(get_db)
) -> DemoActionOut:
    """§15 — revoke the active mandate. MG-002 fails from here on.

    Revocation is immediate and needs no cooperation from the agent: the Guard
    reads `mandates.status` on every submission, so authority that has been
    withdrawn cannot be spent even by a model that still holds the mandate id.
    """
    mandate_id = body.mandate_id if body else None
    correlation_id = correlation_for(body)

    if mandate_id:
        mandate = mandate_authority.get_mandate(db, mandate_id)
    else:
        mandate = db.scalars(
            select(Mandate)
            .where(Mandate.status == "ACTIVE")
            .order_by(Mandate.issued_at.desc())
            .limit(1)
        ).first()

    if mandate is None:
        raise KavachError(
            "DEMO_PRECONDITION_FAILED",
            "There is no ACTIVE mandate to revoke. Run the agent as far as "
            "the authorization card and press Authorize first.",
            correlation_id=correlation_id,
            detail={"looked_for": "mandates.status = ACTIVE"},
        )

    before = mandate.status
    # The real revoke path (§8.1). It emits MANDATE_REVOKED on the mandate's
    # own correlation id, so the chain the console is watching sees it too.
    revoked = mandate_authority.revoke(db, mandate.id, reason="demo_panel")

    result = {
        "mandate_id": revoked.id,
        "status_before": before,
        "status_after": revoked.status,
        "max_amount_paise": int(revoked.max_amount_paise),
        "correlation_id": revoked.correlation_id,
    }
    record_action(
        db,
        action="revoke-mandate",
        correlation_id=correlation_id,
        session_id=(body.session_id if body else None) or revoked.session_id,
        params={"mandate_id": revoked.id},
        result=result,
    )

    return DemoActionOut(
        action="revoke-mandate",
        summary=(
            f"Mandate {revoked.id} is {revoked.status}. It was authorised for "
            f"{revoked.max_amount_paise} paise per transaction."
        ),
        triggers=(
            "MG-002 mandate_active → MANDATE_NOT_ACTIVE on the next "
            "submit_purchase. MG-001 still passes: the signature is genuine, "
            "the authority behind it is gone."
        ),
        changed=[
            DemoChange(
                target=f"mandates.{revoked.id}",
                field="status",
                before=before,
                after=revoked.status,
            )
        ],
        correlation_id=correlation_id,
        detail=result,
    )
