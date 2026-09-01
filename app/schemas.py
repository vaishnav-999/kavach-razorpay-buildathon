"""Pydantic v2 models for every entity in BUILD_SPEC §5.2.

Money is always an integer field named `*_paise`. No rupee-denominated field
crosses an API boundary anywhere in this system (§18.3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer


def _iso_z(value: datetime) -> str:
    # §18.3 — timestamps are ISO 8601 UTC with a Z suffix, everywhere.
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


UtcDatetime = Annotated[
    datetime, PlainSerializer(_iso_z, return_type=str, when_used="json")
]

# Enumerated string states, kept as Literals so a typo is a type error.
CartStatus = Literal["OPEN", "QUOTED", "CONSUMED", "ABANDONED"]
QuoteStatus = Literal["ACTIVE", "CONSUMED", "EXPIRED"]
MandateStatus = Literal["PROPOSED", "ACTIVE", "REVOKED", "EXPIRED", "EXHAUSTED"]
GuardVerdict = Literal["ALLOW", "BLOCK"]
OrderStatus = Literal["CREATED", "PENDING_PAYMENT", "PAID", "FAILED", "CANCELLED"]
PaymentStatus = Literal["CREATED", "AUTHORIZED", "CAPTURED", "FAILED"]
PaymentSource = Literal["CHECKOUT", "WEBHOOK"]
Actor = Literal["user", "agent", "platform", "merchant", "razorpay", "demo"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# 1. merchants
class MerchantOut(ORMModel):
    id: str
    slug: str
    name: str
    legal_name: str | None = None
    category: str
    transactable: bool
    public_key_hex: str
    capabilities: list[str]
    base_url: str | None = None
    created_at: UtcDatetime


# 2. products
class ProductOut(ORMModel):
    id: str
    merchant_id: str
    sku: str
    name: str
    # Untrusted merchant text. Served verbatim.
    description: str
    category: str
    unit_price_paise: int
    stock_qty: int
    active: bool
    updated_at: UtcDatetime


# 3. carts / 4. cart_items
class CartItemOut(ORMModel):
    id: str
    cart_id: str
    product_id: str
    sku: str
    qty: int
    unit_price_paise_snapshot: int
    created_at: UtcDatetime


class CartOut(ORMModel):
    id: str
    merchant_id: str
    session_id: str | None = None
    correlation_id: str | None = None
    status: CartStatus
    created_at: UtcDatetime
    items: list[CartItemOut] = Field(default_factory=list)


# 5. quotes
class QuoteLineItem(BaseModel):
    """A line item as displayed. The signing payload (§6.3) carries only sku,
    qty, unit_price_paise and line_total_paise."""

    sku: str
    name: str | None = None
    category: str | None = None
    qty: int
    unit_price_paise: int
    line_total_paise: int


class QuoteOut(ORMModel):
    id: str
    cart_id: str
    merchant_id: str
    correlation_id: str | None = None
    currency: str
    line_items: list[QuoteLineItem]
    total_paise: int
    issued_at: UtcDatetime
    expires_at: UtcDatetime
    status: QuoteStatus
    signing_payload: dict[str, Any]
    signature: str
    created_at: UtcDatetime


# 6. mandates
class MandateOut(ORMModel):
    id: str
    session_id: str | None = None
    correlation_id: str | None = None
    user_email: str
    status: MandateStatus
    currency: str
    max_amount_paise: int
    cumulative_cap_paise: int
    max_transactions: int
    allowed_merchant_ids: list[str]
    allowed_categories: list[str]
    issued_at: UtcDatetime | None = None
    expires_at: UtcDatetime | None = None
    revoked_at: UtcDatetime | None = None
    prompt_playback: str
    signing_payload: dict[str, Any] | None = None
    signature: str | None = None
    created_at: UtcDatetime


# 7. guard_decisions
class GuardRuleResult(BaseModel):
    """One of the nine rules (§9.3). Reported whether it passed or failed."""

    rule_id: str
    name: str
    passed: bool
    observed: Any
    threshold: Any
    unit: str
    detail: str
    block_code: str | None = None


class GuardDecisionOut(ORMModel):
    id: str
    correlation_id: str
    session_id: str | None = None
    mandate_id: str | None = None
    quote_id: str | None = None
    merchant_id: str | None = None
    requested_total_paise: int
    verdict: GuardVerdict
    failed_rule_id: str | None = None
    block_code: str | None = None
    rules: list[GuardRuleResult]
    duration_ms: int
    evaluated_at: UtcDatetime


# 8. orders
class OrderOut(ORMModel):
    id: str
    merchant_id: str
    quote_id: str
    mandate_id: str
    guard_decision_id: str
    correlation_id: str
    idempotency_key: str
    amount_paise: int
    currency: str
    status: OrderStatus
    razorpay_order_id: str | None = None
    receipt: str | None = None
    line_items: list[QuoteLineItem]
    created_at: UtcDatetime
    updated_at: UtcDatetime


# 9. payments
class PaymentOut(ORMModel):
    id: str
    order_id: str
    razorpay_payment_id: str | None = None
    razorpay_order_id: str | None = None
    amount_paise: int
    currency: str
    method: str | None = None
    status: PaymentStatus
    signature_verified: bool
    source: PaymentSource
    created_at: UtcDatetime
    updated_at: UtcDatetime


# 10. webhook_events
class WebhookEventOut(ORMModel):
    id: str
    razorpay_event_id: str
    event_type: str
    order_id: str | None = None
    razorpay_order_id: str | None = None
    razorpay_payment_id: str | None = None
    signature_valid: bool
    was_duplicate: bool
    processed_at: UtcDatetime | None = None
    created_at: UtcDatetime


# 11. agent_sessions
class AgentSessionOut(ORMModel):
    id: str
    correlation_id: str
    user_email: str
    intent: str | None = None
    state: str
    llm_provider: str | None = None
    llm_model: str | None = None
    cassette_name: str | None = None
    tool_call_count: int
    submit_attempt_count: int
    started_at: UtcDatetime
    ended_at: UtcDatetime | None = None
    terminal_reason: str | None = None


# 12. audit_events
class AuditEventOut(ORMModel):
    seq: int
    id: str
    correlation_id: str
    session_id: str | None = None
    event_type: str
    actor: Actor
    payload: dict[str, Any]
    created_at: UtcDatetime


class HealthOut(BaseModel):
    status: Literal["ok"]
