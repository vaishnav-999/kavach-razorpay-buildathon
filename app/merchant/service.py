"""The transactional merchant surface (BUILD_SPEC §7.6, §7.7, §7.9).

Carts, quotes and order reads. The read-only half — profile, registry, catalog
and availability — lives in `discovery.py`.

`create_quote` is the load-bearing function in this module: it re-reads current
prices from `products` inside the transaction, recomputes every total with
integer arithmetic, and signs the result. Nothing a caller sends can influence
the price.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import emit
from app.config import settings
from app.crypto import sign
from app.errors import KavachError
from app.ids import new_cart_id, new_cart_item_id, new_correlation_id, new_quote_id
from app.merchant.discovery import (
    CURRENCY,
    MAX_LINE_ITEMS,
    MAX_QTY_PER_LINE,
    QUOTE_PAYLOAD_TYPE,
    QUOTE_TTL_SECONDS,
    get_merchant,
    product_by_sku,
)
from app.models import Cart, CartItem, Order, Payment, Product, Quote


def _now() -> datetime:
    # Whole seconds, so the timestamp stored in `quotes` is identical to the
    # one inside the signed payload. A signature over a value the database
    # rounded differently is a signature nobody can re-verify.
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso_z(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── carts ─────────────────────────────────────────────────────────────────


def get_cart(db: Session, cart_id: str) -> Cart:
    cart = db.get(Cart, cart_id)
    if cart is None:
        raise KavachError(
            "CART_NOT_FOUND",
            f"No cart with id {cart_id}.",
            detail={"cart_id": cart_id},
        )
    return cart


def cart_items(db: Session, cart_id: str) -> list[CartItem]:
    # Insertion order, so the signed line item order is reproducible.
    return list(
        db.scalars(
            select(CartItem)
            .where(CartItem.cart_id == cart_id)
            .order_by(CartItem.created_at, CartItem.id)
        ).all()
    )


def create_cart(
    db: Session,
    *,
    merchant_id: str,
    session_id: str | None,
    correlation_id: str | None,
) -> Cart:
    merchant = get_merchant(db, merchant_id)
    # A correlation id is minted here when the caller has none, so that a plain
    # curl session still produces a complete, retrievable audit chain.
    correlation = correlation_id or new_correlation_id()

    cart = Cart(
        id=new_cart_id(),
        merchant_id=merchant.id,
        session_id=session_id,
        correlation_id=correlation,
        status="OPEN",
    )
    db.add(cart)
    db.flush()

    emit(
        db,
        correlation_id=correlation,
        session_id=session_id,
        event_type="CART_CREATED",
        actor="agent",
        payload={"cart_id": cart.id, "merchant_id": merchant.id},
    )
    db.commit()
    return cart


def add_cart_item(db: Session, cart_id: str, *, sku: str, qty: int) -> Cart:
    cart = get_cart(db, cart_id)
    if cart.status != "OPEN":
        raise KavachError(
            "CART_NOT_OPEN",
            f"Cart {cart.id} is {cart.status}; items can only be added to an OPEN cart.",
            correlation_id=cart.correlation_id,
            detail={"cart_id": cart.id, "status": cart.status},
        )

    product = product_by_sku(db, cart.merchant_id, sku)
    lines = cart_items(db, cart.id)
    existing = next((line for line in lines if line.sku == sku), None)

    if existing is None and len(lines) >= MAX_LINE_ITEMS:
        raise KavachError(
            "VALIDATION_ERROR",
            f"A cart may hold at most {MAX_LINE_ITEMS} distinct line items.",
            correlation_id=cart.correlation_id,
            detail={"observed": len(lines), "threshold": MAX_LINE_ITEMS},
            status_code=422,
        )

    new_qty = (existing.qty if existing else 0) + qty
    if new_qty > MAX_QTY_PER_LINE:
        raise KavachError(
            "VALIDATION_ERROR",
            f"A line may hold at most {MAX_QTY_PER_LINE} units of one SKU.",
            correlation_id=cart.correlation_id,
            detail={"sku": sku, "observed": new_qty, "threshold": MAX_QTY_PER_LINE},
            status_code=422,
        )

    if existing is not None:
        existing.qty = new_qty
        # Display only, and refreshed so the UI shows what the agent last saw.
        existing.unit_price_paise_snapshot = product.unit_price_paise
        item = existing
    else:
        item = CartItem(
            id=new_cart_item_id(),
            cart_id=cart.id,
            product_id=product.id,
            sku=product.sku,
            qty=new_qty,
            unit_price_paise_snapshot=product.unit_price_paise,
        )
        db.add(item)
    db.flush()

    emit(
        db,
        correlation_id=cart.correlation_id or new_correlation_id(),
        session_id=cart.session_id,
        event_type="CART_ITEM_ADDED",
        actor="agent",
        payload={
            "cart_id": cart.id,
            "cart_item_id": item.id,
            "sku": item.sku,
            "qty": item.qty,
            "unit_price_paise_snapshot": item.unit_price_paise_snapshot,
            "line_count": len(cart_items(db, cart.id)),
        },
    )
    db.commit()
    return cart


# ── quote ─────────────────────────────────────────────────────────────────


def create_quote(db: Session, cart_id: str) -> Quote:
    """§7.7 — the most important endpoint in §7.

    Everything that determines the price is read from `products` here, inside
    this transaction. `cart_items.unit_price_paise_snapshot` is never consulted:
    it exists so the UI can show what the agent *thought* the price was, and a
    caller that tampers with it changes nothing.
    """
    cart = get_cart(db, cart_id)
    if cart.status != "OPEN":
        raise KavachError(
            "CART_NOT_OPEN",
            f"Cart {cart.id} is {cart.status}; only an OPEN cart can be quoted.",
            correlation_id=cart.correlation_id,
            detail={"cart_id": cart.id, "status": cart.status},
        )

    lines = cart_items(db, cart.id)
    if not lines:
        raise KavachError(
            "VALIDATION_ERROR",
            f"Cart {cart.id} is empty.",
            correlation_id=cart.correlation_id,
            detail={"cart_id": cart.id},
            status_code=422,
        )

    display_items: list[dict] = []
    signed_items: list[dict] = []
    total_paise = 0

    for line in lines:
        # Step 1: re-read the CURRENT price and stock. Not the snapshot.
        product = db.get(Product, line.product_id)
        if product is None or not product.active:
            raise KavachError(
                "PRODUCT_NOT_FOUND",
                f"Product {line.sku} is no longer available.",
                correlation_id=cart.correlation_id,
                detail={"cart_id": cart.id, "sku": line.sku},
            )

        # Steps 2 and 3: recompute server-side, integer paise throughout.
        unit_price_paise = product.unit_price_paise
        line_total_paise = unit_price_paise * line.qty
        total_paise += line_total_paise

        # The stock read above makes no reservation — §7.5 is explicit that
        # availability is not a promise. Stock is enforced by CV-002 under a
        # row lock at submit, which is the only place it can be enforced
        # honestly.

        display_items.append(
            {
                "sku": product.sku,
                "name": product.name,
                "category": product.category,
                "qty": line.qty,
                "unit_price_paise": unit_price_paise,
                "line_total_paise": line_total_paise,
            }
        )
        # §6.3 — the signed line item carries only these four keys. Product
        # names and descriptions are untrusted merchant text and do not enter a
        # cryptographic payload.
        signed_items.append(
            {
                "sku": product.sku,
                "qty": line.qty,
                "unit_price_paise": unit_price_paise,
                "line_total_paise": line_total_paise,
            }
        )

    quote_id = new_quote_id()
    issued_at = _now()
    expires_at = issued_at + timedelta(seconds=QUOTE_TTL_SECONDS)

    # Step 4: the §6.3 payload, exactly these keys.
    signing_payload = {
        "typ": QUOTE_PAYLOAD_TYPE,
        "quote_id": quote_id,
        "merchant_id": cart.merchant_id,
        "cart_id": cart.id,
        "currency": CURRENCY,
        "total_paise": total_paise,
        "line_items": signed_items,
        "issued_at": _iso_z(issued_at),
        "expires_at": _iso_z(expires_at),
    }
    # Step 5: sign it as the merchant.
    signature = sign(settings.MERCHANT_SIGNING_SEED, signing_payload)

    # Step 6: persist, and close the cart against further edits.
    quote = Quote(
        id=quote_id,
        cart_id=cart.id,
        merchant_id=cart.merchant_id,
        correlation_id=cart.correlation_id,
        currency=CURRENCY,
        line_items=display_items,
        total_paise=total_paise,
        issued_at=issued_at,
        expires_at=expires_at,
        status="ACTIVE",
        signing_payload=signing_payload,
        signature=signature,
    )
    db.add(quote)
    cart.status = "QUOTED"
    db.flush()

    # Step 7.
    emit(
        db,
        correlation_id=cart.correlation_id or new_correlation_id(),
        session_id=cart.session_id,
        event_type="CHECKOUT_QUOTED",
        actor="merchant",
        payload={
            "quote_id": quote.id,
            "cart_id": cart.id,
            "merchant_id": cart.merchant_id,
            "currency": CURRENCY,
            "total_paise": total_paise,
            # The signed line items, not the display ones: no merchant free
            # text enters the audit trail from here.
            "line_items": signed_items,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "signature": signature,
        },
    )
    db.commit()
    return quote


# ── orders ────────────────────────────────────────────────────────────────


def get_order(db: Session, order_id: str) -> tuple[Order, Payment | None]:
    """§7.9 — the only source of truth for payment status in the UI."""
    order = db.get(Order, order_id)
    if order is None:
        raise KavachError(
            "ORDER_NOT_FOUND",
            f"No order with id {order_id}.",
            detail={"order_id": order_id},
        )
    payment = db.scalar(
        select(Payment)
        .where(Payment.order_id == order.id)
        .order_by(Payment.created_at.desc())
        .limit(1)
    )
    return order, payment
