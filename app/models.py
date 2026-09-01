"""The twelve tables of BUILD_SPEC §5.2.

All timestamps are TIMESTAMPTZ, stored in UTC. All money is BigInteger holding
integer paise — there is no float and no Decimal anywhere in the money path.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _now_col() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# 1. merchants
class Merchant(Base):
    __tablename__ = "merchants"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    legal_name: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    transactable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    public_key_hex: Mapped[str] = mapped_column(Text, nullable=False)
    capabilities: Mapped[list] = mapped_column(JSONB, nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_col()


# 2. products
class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("merchant_id", "sku", name="uq_products_merchant_sku"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(
        Text, ForeignKey("merchants.id"), nullable=False
    )
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Untrusted input. Served verbatim (§7.4). Never sanitised.
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    # §16.2 nutrition attributes. Nullable: stationery lines carry neither.
    protein_grams: Mapped[int | None] = mapped_column(Integer, nullable=True)
    diet: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit_price_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stock_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    updated_at: Mapped[datetime] = _now_col()


# 3. carts
class Cart(Base):
    __tablename__ = "carts"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(
        Text, ForeignKey("merchants.id"), nullable=False
    )
    session_id: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _now_col()


# 4. cart_items
class CartItem(Base):
    __tablename__ = "cart_items"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    cart_id: Mapped[str] = mapped_column(Text, ForeignKey("carts.id"), nullable=False)
    product_id: Mapped[str] = mapped_column(
        Text, ForeignKey("products.id"), nullable=False
    )
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    # Display only. The quote endpoint and the validator ignore it completely.
    unit_price_paise_snapshot: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = _now_col()


# 5. quotes
class Quote(Base):
    __tablename__ = "quotes"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    cart_id: Mapped[str] = mapped_column(Text, ForeignKey("carts.id"), nullable=False)
    merchant_id: Mapped[str] = mapped_column(
        Text, ForeignKey("merchants.id"), nullable=False
    )
    correlation_id: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    line_items: Mapped[list] = mapped_column(JSONB, nullable=False)
    total_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    signing_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _now_col()


# 6. mandates
class Mandate(Base):
    __tablename__ = "mandates"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(Text)
    user_email: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    max_amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cumulative_cap_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_transactions: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_merchant_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    allowed_categories: Mapped[list] = mapped_column(JSONB, nullable=False)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    prompt_playback: Mapped[str] = mapped_column(Text, nullable=False)
    # Both null while PROPOSED. A PROPOSED mandate carries no authority.
    signing_payload: Mapped[dict | None] = mapped_column(JSONB)
    signature: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_col()


# 7. guard_decisions
class GuardDecision(Base):
    __tablename__ = "guard_decisions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str | None] = mapped_column(Text)
    mandate_id: Mapped[str | None] = mapped_column(Text, ForeignKey("mandates.id"))
    quote_id: Mapped[str | None] = mapped_column(Text, ForeignKey("quotes.id"))
    merchant_id: Mapped[str | None] = mapped_column(Text)
    requested_total_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    failed_rule_id: Mapped[str | None] = mapped_column(Text)
    block_code: Mapped[str | None] = mapped_column(Text)
    # All nine rule results, always — on ALLOW as well as BLOCK.
    rules: Mapped[list] = mapped_column(JSONB, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


# 8. orders
class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(
        Text, ForeignKey("merchants.id"), nullable=False
    )
    quote_id: Mapped[str] = mapped_column(Text, ForeignKey("quotes.id"), nullable=False)
    mandate_id: Mapped[str] = mapped_column(
        Text, ForeignKey("mandates.id"), nullable=False
    )
    # Invariant 2, expressed in the schema: an order cannot exist without
    # naming the guard decision that permitted it.
    guard_decision_id: Mapped[str] = mapped_column(
        Text, ForeignKey("guard_decisions.id"), nullable=False
    )
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    razorpay_order_id: Mapped[str | None] = mapped_column(Text)
    receipt: Mapped[str | None] = mapped_column(Text)
    line_items: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col()


# 9. payments
class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    order_id: Mapped[str] = mapped_column(Text, ForeignKey("orders.id"), nullable=False)
    razorpay_payment_id: Mapped[str | None] = mapped_column(Text)
    razorpay_order_id: Mapped[str | None] = mapped_column(Text)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str | None] = mapped_column(Text)
    # Written from exactly two places: verify_checkout_signature() and the
    # webhook handler. Never from a frontend claim.
    status: Mapped[str] = mapped_column(Text, nullable=False)
    signature_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col()


# 10. webhook_events
class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    # Dedup key, from the x-razorpay-event-id header. The UNIQUE constraint is
    # the dedup: insert and catch IntegrityError, never check-then-insert.
    razorpay_event_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    order_id: Mapped[str | None] = mapped_column(Text, ForeignKey("orders.id"))
    razorpay_order_id: Mapped[str | None] = mapped_column(Text)
    razorpay_payment_id: Mapped[str | None] = mapped_column(Text)
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    was_duplicate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    raw_body: Mapped[str] = mapped_column(Text, nullable=False)
    raw_signature: Mapped[str] = mapped_column(Text, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now_col()


# 11. agent_sessions
class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_email: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    llm_provider: Mapped[str | None] = mapped_column(Text)
    llm_model: Mapped[str | None] = mapped_column(Text)
    cassette_name: Mapped[str | None] = mapped_column(Text)
    tool_call_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    submit_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    started_at: Mapped[datetime] = _now_col()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_reason: Mapped[str | None] = mapped_column(Text)


# 12. audit_events — append-only. Never UPDATE, never DELETE.
class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_correlation_seq", "correlation_id", "seq"),)

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _now_col()
