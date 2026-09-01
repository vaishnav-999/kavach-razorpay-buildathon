"""M0: the twelve tables of BUILD_SPEC §5.2

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NOW = sa.text("now()")


def upgrade() -> None:
    # 1. merchants
    op.create_table(
        "merchants",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("legal_name", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("transactable", sa.Boolean(), nullable=False),
        sa.Column("public_key_hex", sa.Text(), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )

    # 2. products
    op.create_table(
        "products",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("merchant_id", sa.Text(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("unit_price_paise", sa.BigInteger(), nullable=False),
        sa.Column("stock_qty", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id", "sku", name="uq_products_merchant_sku"),
    )

    # 3. carts
    op.create_table(
        "carts",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("merchant_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # 4. cart_items
    op.create_table(
        "cart_items",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("cart_id", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("unit_price_paise_snapshot", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(["cart_id"], ["carts.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # 5. quotes
    op.create_table(
        "quotes",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("cart_id", sa.Text(), nullable=False),
        sa.Column("merchant_id", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("line_items", postgresql.JSONB(), nullable=False),
        sa.Column("total_paise", sa.BigInteger(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("signing_payload", postgresql.JSONB(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(["cart_id"], ["carts.id"]),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # 6. mandates
    op.create_table(
        "mandates",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.Column("user_email", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("max_amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("cumulative_cap_paise", sa.BigInteger(), nullable=False),
        sa.Column("max_transactions", sa.Integer(), nullable=False),
        sa.Column("allowed_merchant_ids", postgresql.JSONB(), nullable=False),
        sa.Column("allowed_categories", postgresql.JSONB(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prompt_playback", sa.Text(), nullable=False),
        # Null while PROPOSED. A proposed mandate carries no authority.
        sa.Column("signing_payload", postgresql.JSONB(), nullable=True),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # 7. guard_decisions
    op.create_table(
        "guard_decisions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("mandate_id", sa.Text(), nullable=True),
        sa.Column("quote_id", sa.Text(), nullable=True),
        sa.Column("merchant_id", sa.Text(), nullable=True),
        sa.Column("requested_total_paise", sa.BigInteger(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("failed_rule_id", sa.Text(), nullable=True),
        sa.Column("block_code", sa.Text(), nullable=True),
        sa.Column("rules", postgresql.JSONB(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["mandate_id"], ["mandates.id"]),
        sa.ForeignKeyConstraint(["quote_id"], ["quotes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # 8. orders — guard_decision_id NOT NULL is invariant 2 in the schema.
    op.create_table(
        "orders",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("merchant_id", sa.Text(), nullable=False),
        sa.Column("quote_id", sa.Text(), nullable=False),
        sa.Column("mandate_id", sa.Text(), nullable=False),
        sa.Column("guard_decision_id", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("razorpay_order_id", sa.Text(), nullable=True),
        sa.Column("receipt", sa.Text(), nullable=True),
        sa.Column("line_items", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.ForeignKeyConstraint(["quote_id"], ["quotes.id"]),
        sa.ForeignKeyConstraint(["mandate_id"], ["mandates.id"]),
        sa.ForeignKeyConstraint(["guard_decision_id"], ["guard_decisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )

    # 9. payments
    op.create_table(
        "payments",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("order_id", sa.Text(), nullable=False),
        sa.Column("razorpay_payment_id", sa.Text(), nullable=True),
        sa.Column("razorpay_order_id", sa.Text(), nullable=True),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("signature_verified", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # 10. webhook_events — razorpay_event_id UNIQUE is the dedup mechanism.
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("razorpay_event_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("order_id", sa.Text(), nullable=True),
        sa.Column("razorpay_order_id", sa.Text(), nullable=True),
        sa.Column("razorpay_payment_id", sa.Text(), nullable=True),
        sa.Column("signature_valid", sa.Boolean(), nullable=False),
        sa.Column("was_duplicate", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("raw_body", sa.Text(), nullable=False),
        sa.Column("raw_signature", sa.Text(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("razorpay_event_id"),
    )

    # 11. agent_sessions
    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("user_email", sa.Text(), nullable=False),
        sa.Column("intent", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("llm_provider", sa.Text(), nullable=True),
        sa.Column("llm_model", sa.Text(), nullable=True),
        sa.Column("cassette_name", sa.Text(), nullable=True),
        sa.Column("tool_call_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("submit_attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # 12. audit_events — append-only, BIGSERIAL seq for strict ordering.
    op.create_table(
        "audit_events",
        sa.Column("seq", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("seq"),
        sa.UniqueConstraint("id"),
    )
    op.create_index(
        "ix_audit_events_correlation_seq", "audit_events", ["correlation_id", "seq"]
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_correlation_seq", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("agent_sessions")
    op.drop_table("webhook_events")
    op.drop_table("payments")
    op.drop_table("orders")
    op.drop_table("guard_decisions")
    op.drop_table("mandates")
    op.drop_table("quotes")
    op.drop_table("cart_items")
    op.drop_table("carts")
    op.drop_table("products")
    op.drop_table("merchants")
