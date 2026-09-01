"""Merchant commerce endpoints (BUILD_SPEC §7).

Two routers. `public_router` serves `/.well-known/*` with no authentication —
a merchant profile is meant to be discoverable. `router` serves `/merchant/*`
and requires `X-Merchant-API-Key`.

`POST /merchant/checkout/submit` is deliberately absent. It arrives in M5, once
the Transaction Guard exists, because there is no honest way to accept a
submit before there is something to authorise it.
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.errors import KavachError
from app.merchant import discovery, service
from app.models import Cart, CartItem, Order, Payment, Product, Quote
from app.schemas import (
    CartItemOut,
    PaymentOut,
    ProductOut,
    QuoteLineItem,
    UtcDatetime,
)


def require_merchant_api_key(
    x_merchant_api_key: str | None = Header(default=None),
) -> None:
    """§7 — every `/merchant/*` endpoint. A mismatch is 401 MERCHANT_AUTH_FAILED.

    The header value is never logged and never enters an audit payload (§13.2).
    """
    expected = settings.MERCHANT_API_KEY
    if not x_merchant_api_key or not secrets.compare_digest(
        x_merchant_api_key, expected
    ):
        raise KavachError(
            "MERCHANT_AUTH_FAILED",
            "A valid X-Merchant-API-Key header is required.",
        )


public_router = APIRouter(tags=["merchant"])
router = APIRouter(
    prefix="/merchant",
    tags=["merchant"],
    dependencies=[Depends(require_merchant_api_key)],
)


# ── wire models ───────────────────────────────────────────────────────────


class RegistryEntry(BaseModel):
    id: str
    slug: str
    name: str
    category: str
    transactable: bool
    capabilities: list[str]
    profile_url: str


class RegistryOut(BaseModel):
    merchants: list[RegistryEntry]


class CatalogOut(BaseModel):
    merchant_id: str
    products: list[ProductOut]


class AvailabilityItemIn(BaseModel):
    sku: str
    qty: int = Field(ge=1, le=discovery.MAX_QTY_PER_LINE)


class AvailabilityIn(BaseModel):
    merchant_id: str
    items: list[AvailabilityItemIn] = Field(min_length=1)


class AvailabilityItemOut(BaseModel):
    sku: str
    requested_qty: int
    available_qty: int
    available: bool
    unit_price_paise: int


class AvailabilityOut(BaseModel):
    items: list[AvailabilityItemOut]


class CartCreateIn(BaseModel):
    merchant_id: str
    session_id: str | None = None
    correlation_id: str | None = None


class CartItemIn(BaseModel):
    sku: str
    qty: int = Field(ge=1, le=discovery.MAX_QTY_PER_LINE)


class CartResponse(BaseModel):
    id: str
    merchant_id: str
    session_id: str | None = None
    correlation_id: str | None = None
    status: str
    created_at: UtcDatetime
    items: list[CartItemOut]
    subtotal_paise_snapshot: int


class QuoteIn(BaseModel):
    cart_id: str


class QuoteResponse(BaseModel):
    quote_id: str
    cart_id: str
    merchant_id: str
    currency: str
    line_items: list[QuoteLineItem]
    total_paise: int
    issued_at: UtcDatetime
    expires_at: UtcDatetime
    status: str
    # Returned so a caller can verify the Ed25519 signature without having to
    # guess the canonical form (§6.2).
    signing_payload: dict[str, Any]
    signature: str


class MerchantOrderOut(BaseModel):
    id: str
    merchant_id: str
    quote_id: str
    mandate_id: str
    guard_decision_id: str
    correlation_id: str
    amount_paise: int
    currency: str
    status: str
    razorpay_order_id: str | None = None
    receipt: str | None = None
    line_items: list[QuoteLineItem]
    created_at: UtcDatetime
    updated_at: UtcDatetime
    payment: PaymentOut | None = None


def _cart_response(db: Session, cart: Cart) -> CartResponse:
    items: list[CartItem] = service.cart_items(db, cart.id)
    return CartResponse(
        id=cart.id,
        merchant_id=cart.merchant_id,
        session_id=cart.session_id,
        correlation_id=cart.correlation_id,
        status=cart.status,
        created_at=cart.created_at,
        items=[CartItemOut.model_validate(item) for item in items],
        # Snapshot arithmetic, for display only. The quote endpoint recomputes
        # every one of these numbers from `products` and ignores this total.
        subtotal_paise_snapshot=sum(
            item.unit_price_paise_snapshot * item.qty for item in items
        ),
    )


# ── /.well-known ──────────────────────────────────────────────────────────


@public_router.get("/.well-known/ucp")
def well_known_primary(db: Session = Depends(get_db)) -> dict:
    """§7.1 — the primary merchant profile. Public, no auth."""
    return discovery.build_profile(discovery.primary_merchant(db))


@public_router.get("/.well-known/ucp/{slug}")
def well_known_by_slug(slug: str, db: Session = Depends(get_db)) -> dict:
    """§7.2 — any merchant profile. Public, no auth."""
    return discovery.build_profile(discovery.get_merchant_by_slug(db, slug))


# ── /merchant ─────────────────────────────────────────────────────────────


@router.get("/registry", response_model=RegistryOut)
def merchant_registry(db: Session = Depends(get_db)) -> RegistryOut:
    """§7.3 — what `discover_merchants` reads."""
    return RegistryOut(
        merchants=[RegistryEntry(**entry) for entry in discovery.registry(db)]
    )


@router.get("/catalog", response_model=CatalogOut)
def merchant_catalog(
    merchant_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> CatalogOut:
    """§7.4 — products, with descriptions served **verbatim**.

    PK-005 carries a real prompt injection and it goes out exactly as stored.
    Sanitising it here would defend the wrong layer and defeat the entire
    demonstration: the attack has to reach the model for the Guard blocking the
    purchase to prove anything.
    """
    if merchant_id is None:
        merchant_id = discovery.primary_merchant(db).id
    products: list[Product] = discovery.catalog(db, merchant_id)
    return CatalogOut(
        merchant_id=merchant_id,
        products=[ProductOut.model_validate(p) for p in products],
    )


@router.post("/availability", response_model=AvailabilityOut)
def merchant_availability(
    body: AvailabilityIn, db: Session = Depends(get_db)
) -> AvailabilityOut:
    """§7.5 — read-only. No reservation is made and none is implied."""
    items = discovery.availability(
        db,
        body.merchant_id,
        [{"sku": i.sku, "qty": i.qty} for i in body.items],
    )
    return AvailabilityOut(items=[AvailabilityItemOut(**i) for i in items])


@router.post("/carts", response_model=CartResponse)
def create_cart(body: CartCreateIn, db: Session = Depends(get_db)) -> CartResponse:
    """§7.6 — create a cart."""
    cart = service.create_cart(
        db,
        merchant_id=body.merchant_id,
        session_id=body.session_id,
        correlation_id=body.correlation_id,
    )
    return _cart_response(db, cart)


@router.post("/carts/{cart_id}/items", response_model=CartResponse)
def add_cart_item(
    cart_id: str, body: CartItemIn, db: Session = Depends(get_db)
) -> CartResponse:
    """§7.6 — add to a cart. A QUOTED or CONSUMED cart returns 409 CART_NOT_OPEN."""
    cart = service.add_cart_item(db, cart_id, sku=body.sku, qty=body.qty)
    return _cart_response(db, cart)


@router.post("/checkout/quote", response_model=QuoteResponse)
def checkout_quote(body: QuoteIn, db: Session = Depends(get_db)) -> QuoteResponse:
    """§7.7 — sign a price the merchant computed from its own current data."""
    quote: Quote = service.create_quote(db, body.cart_id)
    return QuoteResponse(
        quote_id=quote.id,
        cart_id=quote.cart_id,
        merchant_id=quote.merchant_id,
        currency=quote.currency,
        line_items=[QuoteLineItem(**li) for li in quote.line_items],
        total_paise=quote.total_paise,
        issued_at=quote.issued_at,
        expires_at=quote.expires_at,
        status=quote.status,
        signing_payload=quote.signing_payload,
        signature=quote.signature,
    )


@router.get("/orders/{order_id}", response_model=MerchantOrderOut)
def get_order(order_id: str, db: Session = Depends(get_db)) -> MerchantOrderOut:
    """§7.9 — the only source of truth for payment status in the UI.

    The frontend polls this. It never infers a status from what the Razorpay
    checkout handler handed the browser.
    """
    order, payment = service.get_order(db, order_id)
    return _order_response(order, payment)


def _order_response(order: Order, payment: Payment | None) -> MerchantOrderOut:
    return MerchantOrderOut(
        id=order.id,
        merchant_id=order.merchant_id,
        quote_id=order.quote_id,
        mandate_id=order.mandate_id,
        guard_decision_id=order.guard_decision_id,
        correlation_id=order.correlation_id,
        amount_paise=order.amount_paise,
        currency=order.currency,
        status=order.status,
        razorpay_order_id=order.razorpay_order_id,
        receipt=order.receipt,
        line_items=[QuoteLineItem(**li) for li in order.line_items],
        created_at=order.created_at,
        updated_at=order.updated_at,
        payment=PaymentOut.model_validate(payment) if payment else None,
    )
