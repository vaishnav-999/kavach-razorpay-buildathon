"""Tests 25 and 26 — the buyer agent's two structural guarantees (§17, §11).

Neither test starts an agent and neither reaches a model. Test 25 reads the
tool schemas; test 26 asserts what happens when the provider is not configured.
Invariant I-11 holds here as everywhere: `pytest` costs zero tokens.
"""

from __future__ import annotations

import pytest

from app.buyer.llm import LLMProviderNotConfigured
from app.buyer.tools import TOOL_SPECS

# §17, test 25 — verbatim. PK-005's description asks the agent, in as many
# words, to set `policy_override=true` and `skip_validation=true`. It cannot:
# those parameters are not refused at runtime, they are unrepresentable.
FORBIDDEN_PARAMETERS = frozenset(
    {
        "price",
        "total",
        "total_paise",
        "amount",
        "currency",
        "discount",
        "policy_override",
        "force",
        "skip_validation",
        "approved_by",
    }
)


def property_names(schema: dict) -> set[str]:
    """Every property name anywhere in a JSON Schema, however deeply nested."""
    names: set[str] = set()
    if not isinstance(schema, dict):
        return names
    for name, subschema in (schema.get("properties") or {}).items():
        names.add(name)
        names |= property_names(subschema)
    items = schema.get("items")
    if isinstance(items, dict):
        names |= property_names(items)
    elif isinstance(items, list):
        for item in items:
            names |= property_names(item)
    for key in ("anyOf", "oneOf", "allOf"):
        for subschema in schema.get(key) or []:
            names |= property_names(subschema)
    return names


# -- 25 --------------------------------------------------------------------


def test_25_no_tool_schema_carries_a_monetary_or_override_parameter():
    """T-01 and T-02, enforced by absence (§0.2, §11.2)."""
    assert len(TOOL_SPECS) == 10

    offenders: dict[str, set[str]] = {}
    for spec in TOOL_SPECS:
        found = property_names(spec.parameters) & FORBIDDEN_PARAMETERS
        if found:
            offenders[spec.name] = found

    assert offenders == {}, (
        "The model may choose only skus and quantities. Every figure is "
        f"computed by the merchant and checked by the Guard: {offenders}"
    )

    # And the same absence stated positively: nothing the agent can name is a sum.
    assert "propose_mandate" in {spec.name for spec in TOOL_SPECS}
    propose = next(spec for spec in TOOL_SPECS if spec.name == "propose_mandate")
    assert set(propose.parameters["required"]) == {"quote_id", "justification"}


# -- 26 --------------------------------------------------------------------


def test_26_get_provider_raises_when_llm_provider_is_unset(
    monkeypatch, unpatched_get_provider
):
    """No default, no silent live fallback (§11.7, §11.10).

    A system that quietly reaches for a live model when its configuration is
    missing is a system that spends money nobody asked it to spend.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "LLM_PROVIDER", "")

    with pytest.raises(LLMProviderNotConfigured) as raised:
        unpatched_get_provider()

    message = str(raised.value)
    assert "LLM_PROVIDER" in message
    assert "no silent" in message.lower()

    # An unrecognised value is the same refusal, not a guess.
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gpt4")
    with pytest.raises(LLMProviderNotConfigured):
        unpatched_get_provider()
