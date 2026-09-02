"""The buyer's only route to the merchant (BUILD_SPEC §11, invariant 3).

Every call in this module goes over HTTP, with an API key header, exactly as a
third-party agent would have to. **No file under `app/buyer/` imports from
`app.merchant`** — test 23 walks the AST and asserts it.

That fence is the difference between a demo and a claim. An in-process import
would let a compromised buyer plane call `create_quote()` directly and sign a
price nobody quoted; over HTTP it can only ask, and the merchant decides.

Base URL is `APP_BASE_URL`, so in this build the buyer calls back into the same
process through the network stack. That is deliberate — the boundary is real
even when the deployment is one container.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from app.config import settings

TIMEOUT_SECONDS = 20.0


class MerchantCallFailed(RuntimeError):
    """A non-2xx from the merchant plane, or a transport failure reaching it.

    Carries the §18.1 error envelope when the merchant sent one, so the tool
    result the model sees names the same code a human would see in the API.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code or "MERCHANT_CALL_FAILED"
        self.detail = detail

    def as_tool_result(self) -> dict[str, Any]:
        return {
            "status": "ERROR",
            "code": self.code,
            "detail": self.message,
        }


class MerchantClient:
    """A thin, synchronous httpx wrapper. One instance per agent turn."""

    def __init__(self, *, correlation_id: str | None = None) -> None:
        self.base_url = settings.APP_BASE_URL.rstrip("/")
        self.correlation_id = correlation_id

    # -- plumbing ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            # §7 — every /merchant/* endpoint requires this. It is never
            # logged and never enters an audit payload (§13.2).
            "X-Merchant-API-Key": settings.MERCHANT_API_KEY,
            # §18.3 — every call carries one, so a request can be traced
            # through the logs on its own.
            "X-Request-Id": f"req_{uuid.uuid4().hex[:12]}",
        }
        if self.correlation_id:
            headers["X-Correlation-Id"] = self.correlation_id
        return headers

    def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
                response = client.request(
                    method, url, params=params, json=json, headers=self._headers()
                )
        except httpx.HTTPError as exc:
            raise MerchantCallFailed(
                f"Could not reach the merchant at {path}: {exc}",
                code="MERCHANT_UNREACHABLE",
            ) from exc

        if response.status_code >= 400:
            body: Any
            try:
                body = response.json()
            except ValueError:
                body = {"error": {"message": response.text[:500]}}
            error = (body or {}).get("error") or {}
            raise MerchantCallFailed(
                error.get("message") or f"The merchant returned {response.status_code}.",
                status_code=response.status_code,
                code=error.get("code"),
                detail=error.get("detail"),
            )

        return response.json()

    # -- §7 endpoints -----------------------------------------------------

    def registry(self) -> dict[str, Any]:
        """§7.3 — every merchant, with its capabilities and `transactable`."""
        return self._call("GET", "/merchant/registry")

    def profile(self, slug: str) -> dict[str, Any]:
        """§7.2 — a merchant profile. Public; the key is sent anyway."""
        return self._call("GET", f"/.well-known/ucp/{slug}")

    def catalog(self, merchant_id: str) -> dict[str, Any]:
        """§7.4 — products, with descriptions exactly as the merchant wrote
        them. Nothing in this module inspects, escapes or shortens one."""
        return self._call("GET", "/merchant/catalog", params={"merchant_id": merchant_id})

    def availability(
        self, merchant_id: str, items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """§7.5 — read-only. No reservation is made and none is implied."""
        return self._call(
            "POST",
            "/merchant/availability",
            json={"merchant_id": merchant_id, "items": items},
        )

    def create_cart(
        self,
        merchant_id: str,
        *,
        session_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """§7.6 — open a cart."""
        return self._call(
            "POST",
            "/merchant/carts",
            json={
                "merchant_id": merchant_id,
                "session_id": session_id,
                "correlation_id": correlation_id or self.correlation_id,
            },
        )

    def add_to_cart(self, cart_id: str, *, sku: str, qty: int) -> dict[str, Any]:
        """§7.6 — add a line."""
        return self._call(
            "POST", f"/merchant/carts/{cart_id}/items", json={"sku": sku, "qty": qty}
        )

    def request_quote(self, cart_id: str) -> dict[str, Any]:
        """§7.7 — the merchant computes every figure from its own current data
        and signs the result. Nothing the buyer sends can influence it."""
        return self._call("POST", "/merchant/checkout/quote", json={"cart_id": cart_id})
