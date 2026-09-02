"""The transactional merchant surface (BUILD_SPEC §7.6, §7.7, §7.9).

Carts, quotes and order reads. The read-only half — profile, registry, catalog
and availability — lives in `discovery.py`.

`create_quote` is the load-bearing function in this module: it re-reads current
prices from `products` inside the transaction, recomputes every total with
integer arithmetic, and signs the result. Nothing a caller sends can influence
the price.

`submit_checkout` (§7.8) is the only path to an order. It is idempotent before
it is anything else, it runs the §10 validator before it touches stock, and it
cannot be reached without naming an `ALLOW` guard decision.
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
from app.merchant.validator import ValidationResult, validate_checkout
from app.models import Cart, CartItem, GuardDecision, Order, Payment, Product, Quote

# The merchant plane reaches the Razorpay rail only through the platform:
# `app/platform/razorpay_client.py` is importable from inside `app/platform/`
# and nowhere else (invariant 1). `payments.py` breaks the cycle by importing
# this module inside `execute_authorized_purchase()` rather than at module
# level.
from app.platform import payments


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


# ── submit (§7.8) ─────────────────────────────────────────────────────────


def _require_allow_decision(
    db: Session, guard_decision_id: str, quote: Quote, mandate_id: str
) -> GuardDecision:
    """Invariant 2 at the merchant boundary.

    §10's "must not query buyer or platform tables" is scoped to CV-001's
    mandate verification, and for good reason: a real merchant cannot see our
    tables. This is a different question — *did the platform actually
    authorise this?* — and it is the one property the whole project rests on,
    so it is checked here as well as at the call site.

    Two things are checked, and the second is what makes the first mean
    anything:

    1. the named decision exists and its verdict is `ALLOW`;
    2. that decision was reached about **this** submission — same quote, same
       mandate, same amount, same merchant.

    Without (2), any past `ALLOW` is a bearer token: a caller could attach a
    decision reached about a 516 000 cart to a fresh, independently valid
    756 000 cart and be charged the larger amount, because MG-005, MG-006 and
    MG-009 were evaluated against a total that is not the one being charged.
    The validator would not catch it — CV-001 to CV-004 never look at the
    amount against the mandate. Only this comparison does.

    The mandate is bound for the same reason. CV-001 verifies whatever mandate
    it is handed on that mandate's own terms; it has no way to know which one
    the Guard actually evaluated. Without this comparison an `ALLOW` reached
    against one mandate's caps could be spent under another's name, and
    MG-006 and MG-009 — which count prior ALLOWs per mandate — would be
    counting against a mandate that is not paying.
    """
    decision = db.get(GuardDecision, guard_decision_id)
    if decision is None or decision.verdict != "ALLOW":
        raise KavachError(
            "MERCHANT_MANDATE_INVALID",
            "A submission must name a Transaction Guard decision with verdict "
            "ALLOW. No order exists without one.",
            detail={
                "guard_decision_id": guard_decision_id,
                "observed": decision.verdict if decision else "no_decision",
                "threshold": "ALLOW",
            },
        )

    # An authorisation is for one quote, under one mandate, for one amount, at
    # one merchant. It does not transfer to another submission.
    for field, observed, expected in (
        ("quote_id", decision.quote_id, quote.id),
        ("mandate_id", decision.mandate_id, mandate_id),
        ("requested_total_paise", decision.requested_total_paise, quote.total_paise),
        ("merchant_id", decision.merchant_id, quote.merchant_id),
    ):
        if observed != expected:
            raise KavachError(
                "MERCHANT_MANDATE_INVALID",
                f"Guard decision {decision.id} was not reached about this "
                f"submission: its {field} is {observed!r}, this submission's "
                f"is {expected!r}. An authorisation does not transfer to "
                "another submission.",
                detail={
                    "guard_decision_id": decision.id,
                    "field": field,
                    "observed": observed,
                    "expected": expected,
                },
            )
    return decision


def submit_checkout(
    db: Session,
    *,
    quote_id: str,
    mandate_signing_payload: dict | None,
    mandate_signature: str | None,
    guard_decision_id: str,
    idempotency_key: str,
    correlation_id: str | None = None,
    session_id: str | None = None,
    now: datetime | None = None,
) -> tuple[Order, bool]:
    """§7.8. Returns `(order, replayed)`.

    Steps 3 to 6 of §7.8 are one transaction: the stock decrement, the order
    row, the Razorpay order and the CONSUMED marks either all happen or none
    do. `create_razorpay_order()` rolls back on a Razorpay failure, which takes
    the decrements and the audit rows with it — no phantom order, no leaked
    stock.
    """
    if not idempotency_key:
        raise KavachError(
            "IDEMPOTENCY_KEY_REQUIRED",
            "POST /merchant/checkout/submit requires an Idempotency-Key header.",
            correlation_id=correlation_id,
        )

    # 1. Idempotency FIRST — before the validator, before anything. A retry of
    #    a submission that already produced an order must not re-validate and
    #    must not create a second Razorpay order.
    existing = db.scalar(select(Order).where(Order.idempotency_key == idempotency_key))
    if existing is not None:
        return existing, True

    quote = db.get(Quote, quote_id)
    if quote is None:
        raise KavachError(
            "QUOTE_NOT_FOUND",
            f"No quote with id {quote_id}.",
            correlation_id=correlation_id,
            detail={"quote_id": quote_id},
        )

    now = now or datetime.now(timezone.utc)
    mandate_id = str((mandate_signing_payload or {}).get("mandate_id") or "")

    decision = _require_allow_decision(db, guard_decision_id, quote, mandate_id)

    # §13.3 — one correlation id, one story. The chain is only retrievable as a
    # whole if POLICY_APPROVED and everything after it thread onto the same id,
    # so the bound decision's id wins over whatever the caller sent.
    # `guard_decisions.correlation_id` is NOT NULL, so there is always one.
    correlation = decision.correlation_id

    # 2. The Checkout Validator. CV-002 takes the row locks §7.8 step 3 calls
    #    for, and hands them back so the decrement below happens under them.
    result: ValidationResult = validate_checkout(
        db,
        quote=quote,
        mandate_signing_payload=mandate_signing_payload,
        mandate_signature=mandate_signature,
        now=now,
    )

    if not result.passed:
        emit(
            db,
            correlation_id=correlation,
            session_id=session_id,
            event_type="CHECKOUT_REJECTED",
            actor="merchant",
            payload={
                "quote_id": quote.id,
                "merchant_id": quote.merchant_id,
                "mandate_id": mandate_id,
                "rule_id": result.failed_check_id,
                "code": result.code,
                "detail": result.detail,
            },
        )
        # Nothing was decremented and nothing was created; only the record of
        # the refusal is kept.
        db.commit()
        raise KavachError(
            result.code or "MERCHANT_MANDATE_INVALID",
            result.detail or "The merchant rejected this submission.",
            correlation_id=correlation,
            detail={
                "quote_id": quote.id,
                "failed_check_id": result.failed_check_id,
                "checks": result.to_list(),
            },
        )

    # 3. Decrement under the locks CV-002 already holds.
    for line in (quote.signing_payload or {}).get("line_items", []):
        product = result.locked_products[str(line.get("sku"))]
        product.stock_qty -= int(line.get("qty", 0))
        product.updated_at = datetime.now(timezone.utc)

    emit(
        db,
        correlation_id=correlation,
        session_id=session_id,
        event_type="CHECKOUT_VALIDATED",
        actor="merchant",
        payload={
            "quote_id": quote.id,
            "merchant_id": quote.merchant_id,
            "mandate_id": mandate_id,
            "total_paise": quote.total_paise,
            "checks": result.to_list(),
        },
    )

    # 4. Spend the quote and close the cart, inside the same transaction as
    #    the order. A quote is single-use and the row says so before the
    #    Razorpay call rather than after it.
    quote.status = "CONSUMED"
    cart = db.get(Cart, quote.cart_id)
    if cart is not None:
        cart.status = "CONSUMED"
    db.flush()

    # 5. The order row carrying `guard_decision_id`, then the Razorpay order.
    #    `create_razorpay_order` commits on success and rolls back everything
    #    above on a Razorpay failure.
    order = payments.create_razorpay_order(
        db,
        quote=quote,
        mandate_id=mandate_id,
        guard_decision_id=decision.id,
        idempotency_key=idempotency_key,
        session_id=session_id,
        correlation_id=correlation,
    )
    return order, False
