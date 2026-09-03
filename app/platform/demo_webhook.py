"""Replay a webhook Razorpay actually sent (BUILD_SPEC §15).

Genuine signature, genuine event id. Nothing is forged and nothing is
re-serialised: `webhook_events.raw_body` holds the exact bytes Razorpay sent,
so the HMAC verifies for the same reason it verified the first time, and the
`x-razorpay-event-id` collides with the UNIQUE constraint that is the dedup.
One state transition, not two, and a 200 for the duplicate — a non-2xx would
make Razorpay retry for 24 hours.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.errors import KavachError
from app.models import Order, WebhookEvent
from app.platform.demo_base import (
    DemoActionOut,
    DemoChange,
    DemoIn,
    correlation_for,
    record_action,
)

router = APIRouter()


# ── 4. replay webhook (dedup) ─────────────────────────────────────────────


@router.post("/replay-webhook", response_model=DemoActionOut)
def replay_webhook(
    body: DemoIn | None = None, db: Session = Depends(get_db)
) -> DemoActionOut:
    """§15 — re-POST the stored bytes and signature of the last webhook.

    Genuine signature, genuine event id. Nothing is forged and nothing is
    re-serialised: `webhook_events.raw_body` holds the exact bytes Razorpay
    sent, so the HMAC verifies for the same reason it verified the first time
    and the `x-razorpay-event-id` collides with the UNIQUE constraint that is
    the dedup. One state transition, not two.
    """
    correlation_id = correlation_for(body)
    event = db.scalars(
        select(WebhookEvent)
        .where(WebhookEvent.signature_valid.is_(True))
        .order_by(WebhookEvent.created_at.desc())
        .limit(1)
    ).first()

    if event is None:
        raise KavachError(
            "DEMO_PRECONDITION_FAILED",
            "No webhook has been received yet, so there is nothing to replay. "
            "Complete a payment first; Razorpay's captured event arrives at "
            "POST /api/webhooks/razorpay.",
            correlation_id=correlation_id,
            detail={"looked_for": "webhook_events.signature_valid = true"},
        )

    order = db.get(Order, event.order_id) if event.order_id else None
    status_before = order.status if order else None

    ack, transport, transport_detail = _resend(db, event)

    if order is not None:
        db.refresh(order)
    status_after = order.status if order else None

    result = {
        "razorpay_event_id": event.razorpay_event_id,
        "event_type": event.event_type,
        "transport": transport,
        "was_duplicate": bool(ack.get("was_duplicate")),
        "state_changed": bool(ack.get("state_changed")),
        "order_id": event.order_id,
        "order_status_before": status_before,
        "order_status_after": status_after,
    }
    record_action(
        db,
        action="replay-webhook",
        correlation_id=correlation_id,
        session_id=body.session_id if body else None,
        params={
            "razorpay_event_id": event.razorpay_event_id,
            "webhook_event_id": event.id,
        },
        result=result,
    )

    return DemoActionOut(
        action="replay-webhook",
        summary=(
            f"Re-sent {event.event_type} with event id "
            f"{event.razorpay_event_id}: accepted, "
            f"was_duplicate={bool(ack.get('was_duplicate'))}, "
            f"state_changed={bool(ack.get('state_changed'))}."
        ),
        triggers=(
            "WEBHOOK_DUPLICATE_IGNORED. The UNIQUE constraint on "
            "webhook_events.razorpay_event_id is the dedup — an insert that "
            "raises IntegrityError, never a check-then-insert. 200 is returned "
            "for the duplicate, because a non-2xx would make Razorpay retry "
            "for 24 hours."
        ),
        changed=[
            DemoChange(
                target=f"orders.{order.id}" if order else "orders",
                field="status",
                before=status_before,
                after=status_after,
            )
        ],
        correlation_id=correlation_id,
        detail={**result, "transport_detail": transport_detail},
    )


def _resend(db: Session, event: WebhookEvent) -> tuple[dict[str, Any], str, str]:
    """POST the stored bytes back to our own webhook endpoint.

    Over real HTTP when the deployment can reach itself, which is the honest
    version of "re-POSTs to `/api/webhooks/razorpay`". If that transport fails
    — a container that cannot resolve its own public hostname — the same
    handler is called in-process with the same bytes and the same headers, and
    the response says which of the two happened rather than hiding it.
    """
    raw = event.raw_body.encode("utf-8")
    headers = {
        "content-type": "application/json",
        "x-razorpay-signature": event.raw_signature,
        "x-razorpay-event-id": event.razorpay_event_id,
    }
    url = f"{settings.APP_BASE_URL.rstrip('/')}/api/webhooks/razorpay"

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(url, content=raw, headers=headers)
        if response.status_code == 200:
            return response.json(), "http", f"POST {url} → 200"
        detail = f"POST {url} → {response.status_code}"
    except httpx.HTTPError as exc:
        detail = f"POST {url} failed: {type(exc).__name__}"

    # Same function the endpoint calls, same bytes, same event id.
    from app.platform.webhooks import handle_webhook

    ack = handle_webhook(
        db,
        raw=raw,
        signature=event.raw_signature,
        event_id=event.razorpay_event_id,
    )
    return ack.model_dump(), "in_process", f"{detail}; replayed in process"
