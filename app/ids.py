"""Prefixed identifier generation (BUILD_SPEC §5.1).

`{prefix}_{12 chars}`, the 12 characters drawn with `secrets.choice`.
"""

from __future__ import annotations

import secrets

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"

# §5.1 — the full prefix table.
PREFIXES: dict[str, str] = {
    "merchant": "mch",
    "product": "prd",
    "cart": "crt",
    "cart_item": "cit",
    "quote": "qte",
    "mandate": "mnd",
    "guard_decision": "gdc",
    "order": "ord",
    "payment": "pay",
    "webhook_event": "whk",
    "agent_session": "ses",
    "audit_event": "evt",
    "correlation": "cor",
}


def new_id(prefix: str) -> str:
    return f"{prefix}_{''.join(secrets.choice(ALPHABET) for _ in range(12))}"


def new_merchant_id() -> str:
    return new_id(PREFIXES["merchant"])


def new_product_id() -> str:
    return new_id(PREFIXES["product"])


def new_cart_id() -> str:
    return new_id(PREFIXES["cart"])


def new_cart_item_id() -> str:
    return new_id(PREFIXES["cart_item"])


def new_quote_id() -> str:
    return new_id(PREFIXES["quote"])


def new_mandate_id() -> str:
    return new_id(PREFIXES["mandate"])


def new_guard_decision_id() -> str:
    return new_id(PREFIXES["guard_decision"])


def new_order_id() -> str:
    return new_id(PREFIXES["order"])


def new_payment_id() -> str:
    return new_id(PREFIXES["payment"])


def new_webhook_event_id() -> str:
    return new_id(PREFIXES["webhook_event"])


def new_session_id() -> str:
    return new_id(PREFIXES["agent_session"])


def new_audit_event_id() -> str:
    return new_id(PREFIXES["audit_event"])


def new_correlation_id() -> str:
    return new_id(PREFIXES["correlation"])
