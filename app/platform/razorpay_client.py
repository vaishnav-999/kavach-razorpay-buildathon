"""Razorpay HTTP client (BUILD_SPEC §12.1).

Three functions, three endpoints, nothing else. **This module may be imported
only from within `app/platform/`** — invariant 1, checked by test 22. The LLM
has no route to this code: the buyer plane cannot import it, and every call
here is made by platform code that has already been past the Guard.

The key secret is used for HTTP Basic auth and for nothing else. It is never
logged, never returned, and never placed in an exception message: `httpx`
carries credentials in a header it does not include in `repr()`, and the error
raised below carries only the status code and the response body Razorpay sent
back.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

BASE_URL = "https://api.razorpay.com/v1"
TIMEOUT_SECONDS = 15.0

log = logging.getLogger("kavach.razorpay")

# Every outbound Razorpay request increments this, and nothing decrements it.
#
# It exists for one claim: the §15 injection control reports the counter before
# and immediately after a BLOCKED submission, so "no Razorpay activity" is a
# reading taken from the client itself rather than an assertion in prose. It is
# a process-local integer, never persisted and never read by any decision.
_call_count = 0


def call_count() -> int:
    """How many HTTP requests this process has made to Razorpay."""
    return _call_count


class RazorpayError(Exception):
    """A non-2xx from Razorpay, or a transport failure reaching it.

    Carries the status code and the response body (§12.1). `status_code` is
    None when the request never got a response at all.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.body = body


def _auth() -> tuple[str, str]:
    return (settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)


def _request(method: str, path: str, *, json: dict | None = None) -> dict:
    global _call_count
    _call_count += 1
    url = f"{BASE_URL}{path}"
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
            response = client.request(method, url, json=json, auth=_auth())
    except httpx.HTTPError as exc:
        # str(exc) is httpx's own message plus the URL. The URL carries no
        # credentials — auth goes in the Authorization header.
        log.warning("razorpay transport failure: %s %s", method, path)
        raise RazorpayError(
            f"Could not reach Razorpay: {exc}",
            status_code=None,
            body=None,
        ) from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text}

    if response.status_code >= 400:
        log.warning(
            "razorpay error: %s %s -> %s", method, path, response.status_code
        )
        raise RazorpayError(
            f"Razorpay returned {response.status_code} for {method} {path}.",
            status_code=response.status_code,
            body=payload,
        )

    if not isinstance(payload, dict):
        raise RazorpayError(
            f"Razorpay returned a non-object body for {method} {path}.",
            status_code=response.status_code,
            body=payload,
        )
    return payload


def create_order(
    *, amount_paise: int, currency: str, receipt: str, notes: dict
) -> dict:
    """`POST /v1/orders` — §12.2.

    `amount_paise` is the smallest currency subunit, as an integer. ₹5,160 is
    516000. `receipt` is our `ord_...` id, well inside Razorpay's 40-character
    limit.
    """
    return _request(
        "POST",
        "/orders",
        json={
            "amount": amount_paise,
            "currency": currency,
            "receipt": receipt,
            "payment_capture": 1,
            "notes": notes,
        },
    )


def fetch_order_payments(razorpay_order_id: str) -> dict:
    """`GET /v1/orders/{order_id}/payments` — the payments Razorpay has for an order."""
    return _request("GET", f"/orders/{razorpay_order_id}/payments")


def fetch_payment(razorpay_payment_id: str) -> dict:
    """`GET /v1/payments/{payment_id}` — one payment, as Razorpay sees it."""
    return _request("GET", f"/payments/{razorpay_payment_id}")
