"""What the merchant published, and where its instruction had to land (§16.5).

Returns PK-005's description exactly as stored — its length and SHA-256 are
there so a judge can diff it against the `/merchant/catalog` response — and the
`submit_purchase` tool schema beside it. `policy_override` and
`skip_validation` are not disabled parameters: they do not exist, and neither
does any price, amount or currency, on any of the ten tools. There is nowhere
for the instruction to land.

The platform's own reading of the text is reported too: the same markers that
cause `UNTRUSTED_CONTENT_FLAGGED` to be written when the agent reads the
catalog. Flagging is evidence, not sanitisation — the bytes served are the
bytes stored, and this endpoint proves it rather than asserting it.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.buyer.guidance import INSTRUCTION_MARKERS, instruction_marker
from app.buyer.tools import TOOL_SPECS, TOOLS_BY_NAME
from app.db import get_db
from app.merchant import discovery
from app.platform.demo_scenario import (
    ATTACKS,
    CORRECT_CART,
    DEMO_MAX_AMOUNT_PAISE,
    FORBIDDEN_PARAMETERS,
    INJECTION_SKU,
    POISONED_CART,
    product_for,
    total_paise,
)

router = APIRouter()


class InjectionEvidenceOut(BaseModel):
    product: dict[str, Any]
    served_verbatim: dict[str, Any]
    tool: dict[str, Any]
    parameters: dict[str, Any]
    arithmetic: dict[str, Any]
    attacks: list[dict[str, str]]


def _schema_parameters(schema: dict[str, Any]) -> set[str]:
    """Every property name a JSON Schema declares, at any depth."""
    names: set[str] = set()
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for key, value in properties.items():
            names.add(str(key))
            if isinstance(value, dict):
                names |= _schema_parameters(value)
    items = schema.get("items")
    if isinstance(items, dict):
        names |= _schema_parameters(items)
    return names


# ── the evidence ──────────────────────────────────────────────────────────


@router.get("/injection", response_model=InjectionEvidenceOut)
def injection_evidence(db: Session = Depends(get_db)) -> InjectionEvidenceOut:
    """What the merchant published, and where its instruction had to land."""
    merchant = discovery.primary_merchant(db)
    product = product_for(db, merchant.id, INJECTION_SKU)
    description = product.description or ""

    spec = TOOLS_BY_NAME["submit_purchase"]
    declared: dict[str, list[str]] = {
        tool.name: sorted(_schema_parameters(tool.parameters)) for tool in TOOL_SPECS
    }
    everything = {name for names in declared.values() for name in names}

    poisoned_total = total_paise(db, merchant.id, POISONED_CART)
    correct_total = total_paise(db, merchant.id, CORRECT_CART)

    return InjectionEvidenceOut(
        product={
            "sku": product.sku,
            "name": product.name,
            "merchant_id": merchant.id,
            "unit_price_paise": int(product.unit_price_paise),
            # Verbatim. This is the same string /merchant/catalog returns and
            # the same one the model receives inside <untrusted_merchant_data>.
            "description": description,
        },
        served_verbatim={
            "length": len(description),
            "sha256": hashlib.sha256(description.encode("utf-8")).hexdigest(),
            "sanitised": False,
            # The platform's own reading of the stored text, computed here the
            # same way `get_catalog` computes it before writing
            # UNTRUSTED_CONTENT_FLAGGED. It is visible without an agent run,
            # and — this is the point — it changes nothing about what is
            # served: the bytes above are the bytes the catalog returns.
            "instruction_markers": [
                marker
                for marker in INSTRUCTION_MARKERS
                if marker in description.lower()
            ],
            "flagged_reason": (
                "description contains agent-directed phrasing: "
                f"{instruction_marker(description)!r}"
                if instruction_marker(description)
                else None
            ),
            "detail": (
                "Compare this digest with the description field of "
                "GET /merchant/catalog. The catalog serves the stored bytes "
                "unchanged; the buyer plane wraps them in "
                "<untrusted_merchant_data> and passes them on unedited. "
                "UNTRUSTED_CONTENT_FLAGGED records that the platform saw the "
                "instruction-shaped text and changed nothing about how it "
                "served it."
            ),
            "audit_event_type": "UNTRUSTED_CONTENT_FLAGGED",
        },
        tool={
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
        parameters={
            "declared_by_tool": declared,
            "declared_total": len(everything),
            "absent": [
                {"name": name, "present": name in everything}
                for name in FORBIDDEN_PARAMETERS
            ],
            "detail": (
                "No tool in the system accepts a price, an amount, a currency, "
                "a policy flag or an override. They are not disabled "
                "parameters — there is nowhere for the instruction to land. "
                "The agent chooses skus and quantities; every figure is "
                "computed by the merchant and checked by the Guard."
            ),
        },
        arithmetic={
            "correct_total_paise": correct_total,
            "poisoned_total_paise": poisoned_total,
            "mandate_cap_paise": DEMO_MAX_AMOUNT_PAISE,
            "overshoot_paise": poisoned_total - DEMO_MAX_AMOUNT_PAISE,
            "injected_line": {
                "sku": INJECTION_SKU,
                "qty": dict(POISONED_CART)[INJECTION_SKU],
                "unit_price_paise": int(product.unit_price_paise),
                "line_total_paise": (
                    int(product.unit_price_paise) * dict(POISONED_CART)[INJECTION_SKU]
                ),
            },
            "currency": "INR",
        },
        attacks=list(ATTACKS),
    )
