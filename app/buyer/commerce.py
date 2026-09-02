"""Tools 1 to 7 (BUILD_SPEC §11.2) — everything that talks to the merchant.

Every function here reaches the merchant through `client.py`, over HTTP, with
an API key header (invariant 3). Nothing in this file imports `app.merchant`,
and test 23 walks the AST to prove it.

Merchant-written strings — names, descriptions — are wrapped in
`<untrusted_merchant_data>` and passed on **verbatim**. Nothing here escapes,
strips or shortens one.
"""

from __future__ import annotations

from typing import Any

from app.buyer.executor import REQUIRED_CAPABILITIES, ToolContext, ToolOutcome
from app.buyer.guidance import instruction_marker
from app.buyer.prompts import wrap_untrusted


def _rejection(merchant: dict[str, Any], missing: list[str]) -> dict[str, Any]:
    """The MERCHANT_REJECTED payload (§13.1).

    Built from the capability list the merchant published, never from anything
    the model said about it. The rejection is architectural: a merchant without
    `checkout.submit` cannot be transacted with however persuasive its catalog
    text is.
    """
    return {
        "merchant_id": merchant.get("id"),
        "slug": merchant.get("slug"),
        "name": merchant.get("name"),
        "category": merchant.get("category"),
        "missing_capabilities": missing,
        "reason": "profile does not advertise " + ", ".join(missing),
    }


# -- 1 ---------------------------------------------------------------------


def discover_merchants(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """§7.3 — the registry, with each merchant's missing capabilities named."""
    merchants = (ctx.client.registry() or {}).get("merchants") or []

    entries: list[dict[str, Any]] = []
    for merchant in merchants:
        capabilities = list(merchant.get("capabilities") or [])
        missing = [c for c in REQUIRED_CAPABILITIES if c not in capabilities]
        merchant_id = str(merchant.get("id") or "")
        entries.append(
            {
                "merchant_id": merchant_id,
                "slug": merchant.get("slug"),
                # Merchant-written. Wrapped, not trusted.
                "name": wrap_untrusted(
                    str(merchant.get("name") or ""),
                    source="registry",
                    merchant_id=merchant_id,
                ),
                "category": merchant.get("category"),
                "capabilities": capabilities,
                "transactable": bool(merchant.get("transactable")),
                "missing_capabilities": missing,
            }
        )
        if missing:
            ctx.emit("MERCHANT_REJECTED", "agent", _rejection(merchant, missing))

    ctx.emit(
        "MERCHANT_DISCOVERED",
        "agent",
        {
            "count": len(entries),
            "merchants": [
                {
                    "merchant_id": e["merchant_id"],
                    "slug": e["slug"],
                    "category": e["category"],
                    "transactable": e["transactable"],
                    "capabilities": e["capabilities"],
                }
                for e in entries
            ],
        },
    )
    return ToolOutcome(
        result={
            "merchants": entries,
            "note": (
                "A merchant is transactable end to end only if it advertises "
                "both quote.signed and checkout.submit."
            ),
        },
        next_state="DISCOVERING",
    )


# -- 2 ---------------------------------------------------------------------


def get_merchant_profile(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """§7.2 — one published profile."""
    profile = ctx.client.profile(str(args.get("slug") or ""))
    merchant = profile.get("merchant") or {}
    merchant_id = str(merchant.get("id") or "")
    capabilities = list(profile.get("capabilities") or [])
    missing = [c for c in REQUIRED_CAPABILITIES if c not in capabilities]

    if missing:
        ctx.emit("MERCHANT_REJECTED", "agent", _rejection(merchant, missing))

    return ToolOutcome(
        result={
            "merchant_id": merchant_id,
            "slug": merchant.get("slug"),
            "name": wrap_untrusted(
                str(merchant.get("name") or ""),
                source="profile",
                merchant_id=merchant_id,
            ),
            "category": merchant.get("category"),
            "capabilities": capabilities,
            "missing_capabilities": missing,
            "transactable_end_to_end": not missing,
            "signing": profile.get("signing"),
            "limits": profile.get("limits"),
        },
        next_state="EVALUATING",
    )


# -- 3 ---------------------------------------------------------------------


def get_catalog(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """§7.4 — products, with descriptions passed on exactly as stored.

    PK-005's description carries a real prompt injection and it reaches the
    model unmodified. That is the point: a model that resists it proves nothing
    about the architecture, and a model that falls for it and is blocked anyway
    proves everything (§16.5, §11.9(A9)).
    """
    merchant_id = str(args.get("merchant_id") or "")
    products = (ctx.client.catalog(merchant_id) or {}).get("products") or []

    entries: list[dict[str, Any]] = []
    for product in products:
        sku = str(product.get("sku") or "")
        description = str(product.get("description") or "")
        _flag_instruction_shaped(ctx, merchant_id=merchant_id, sku=sku, text=description)
        entries.append(
            {
                "sku": sku,
                "category": product.get("category"),
                "unit_price_paise": product.get("unit_price_paise"),
                "stock_qty": product.get("stock_qty"),
                "active": product.get("active"),
                "name": wrap_untrusted(
                    str(product.get("name") or ""),
                    source="catalog",
                    merchant_id=merchant_id,
                ),
                "description": wrap_untrusted(
                    description, source="catalog", merchant_id=merchant_id
                ),
            }
        )

    ctx.emit(
        "CATALOG_FETCHED",
        "agent",
        {
            "merchant_id": merchant_id,
            "product_count": len(entries),
            "skus": [e["sku"] for e in entries],
        },
    )
    return ToolOutcome(
        result={"merchant_id": merchant_id, "products": entries},
        next_state="EVALUATING",
    )


def _flag_instruction_shaped(
    ctx: ToolContext, *, merchant_id: str, sku: str, text: str
) -> None:
    """Record that merchant text reads like an instruction (§13.1).

    This changes **nothing**. The description still reaches the model exactly
    as stored; all this does is put the platform's own observation into the
    audit trail, so the record shows the attack was visible to the system
    whether or not the model noticed it.
    """
    hit = instruction_marker(text)
    if hit is None:
        return
    at = text.lower().find(hit)
    ctx.emit(
        "UNTRUSTED_CONTENT_FLAGGED",
        "platform",
        {
            "merchant_id": merchant_id,
            "sku": sku,
            "field": "description",
            "reason": f"description contains agent-directed phrasing: {hit!r}",
            # Evidence, not sanitisation. `emit()` caps this at 1000 characters.
            "excerpt": text[max(0, at - 120) : at + 700],
        },
    )


# -- 4 ---------------------------------------------------------------------


def check_availability(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """§7.5 — read-only. Decides nothing, so it moves no state."""
    items = [
        {"sku": str(i.get("sku") or ""), "qty": int(i.get("qty") or 0)}
        for i in (args.get("items") or [])
    ]
    availability = ctx.client.availability(str(args.get("merchant_id") or ""), items)
    return ToolOutcome(result=availability, next_state=None)


# -- 5 ---------------------------------------------------------------------


def create_cart(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """§7.6 — open a cart. CART_CREATED is emitted by the merchant plane, on
    this session's correlation id, so the chain stays one story (§13.3)."""
    cart = ctx.client.create_cart(
        str(args.get("merchant_id") or ""),
        session_id=ctx.session.id,
        correlation_id=ctx.correlation_id,
    )
    return ToolOutcome(
        result={
            "cart_id": cart.get("id"),
            "merchant_id": cart.get("merchant_id"),
            "status": cart.get("status"),
            "items": cart.get("items") or [],
        },
        next_state="CART_BUILDING",
    )


# -- 6 ---------------------------------------------------------------------


def add_to_cart(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """§7.6 — add one line."""
    cart = ctx.client.add_to_cart(
        str(args.get("cart_id") or ""),
        sku=str(args.get("sku") or ""),
        qty=int(args.get("qty") or 0),
    )
    return ToolOutcome(
        result={
            "cart_id": cart.get("id"),
            "status": cart.get("status"),
            "items": [
                {"sku": i.get("sku"), "qty": i.get("qty")}
                for i in (cart.get("items") or [])
            ],
            "note": (
                "Figures the cart shows are a snapshot. Only request_quote "
                "produces a signed one."
            ),
        },
        next_state="CART_BUILDING",
    )


# -- 7 ---------------------------------------------------------------------


def request_quote(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """§7.7 — the merchant computes and signs. CHECKOUT_QUOTED is emitted there."""
    quote = ctx.client.request_quote(str(args.get("cart_id") or ""))
    return ToolOutcome(
        result={
            "quote_id": quote.get("quote_id"),
            "merchant_id": quote.get("merchant_id"),
            "currency": quote.get("currency"),
            "line_items": quote.get("line_items"),
            "total_paise": quote.get("total_paise"),
            "expires_at": quote.get("expires_at"),
            "status": quote.get("status"),
            "signature": quote.get("signature"),
            "note": (
                "These figures carry the merchant's signature. They are the "
                "only ones this system will act on."
            ),
        },
        next_state="QUOTED",
    )
