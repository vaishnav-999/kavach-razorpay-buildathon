"""`get_provider()` — the only way to obtain an LLM (BUILD_SPEC §11.7, §11.10).

There is **no default and no silent live fallback**. An unset or unrecognised
`LLM_PROVIDER` raises, here and at startup in `app/config.py`. Test 26 asserts
it: a system that quietly reaches for a live model when its configuration is
missing is a system that spends money nobody asked it to spend.

The four adapters beside this module are the only files in the project that
import an LLM SDK (invariant 11, test 22), and they are imported lazily below
so that a cassette run never so much as loads one.
"""

from __future__ import annotations

from app.buyer.llm.base import (
    LLMProvider,
    LLMResponse,
    LLMUnavailable,
    Message,
    ToolCall,
    ToolSpec,
)
from app.config import settings

__all__ = [
    "LLMProvider",
    "LLMProviderNotConfigured",
    "LLMResponse",
    "LLMUnavailable",
    "Message",
    "ToolCall",
    "ToolSpec",
    "get_provider",
]

KNOWN_PROVIDERS = ("gemini", "anthropic", "cassette")
DEFAULT_CASSETTE = "happy_path"


class LLMProviderNotConfigured(RuntimeError):
    """`LLM_PROVIDER` is unset or is not one of the three known providers."""


def _clean(value: str | None) -> str:
    # `.env` comments survive some deployment paths; strip one if present.
    return (value or "").split("#", 1)[0].strip().lower()


def get_provider(*, cassette: str | None = None) -> LLMProvider:
    """Build the provider named by `LLM_PROVIDER`, honouring `CASSETTE_MODE`.

    `cassette` is the name from the session body (§11.6) and is ignored unless
    a cassette is actually being used.
    """
    provider_name = _clean(settings.LLM_PROVIDER)
    mode = _clean(settings.CASSETTE_MODE) or "off"
    directory = settings.CASSETTE_DIR
    name = cassette or DEFAULT_CASSETTE

    if not provider_name:
        raise LLMProviderNotConfigured(
            "LLM_PROVIDER is unset. Set it to one of "
            f"{' | '.join(KNOWN_PROVIDERS)}. There is no default and no silent "
            "live fallback."
        )
    if provider_name not in KNOWN_PROVIDERS:
        raise LLMProviderNotConfigured(
            f"LLM_PROVIDER={provider_name!r} is not one of "
            f"{' | '.join(KNOWN_PROVIDERS)}."
        )

    # `cassette` as a provider means replay, whatever CASSETTE_MODE says: there
    # is no live model behind it to fall back to.
    if provider_name == "cassette" or mode == "replay":
        from app.buyer.llm.cassette import CassettePlayer

        return CassettePlayer(directory=directory, cassette=name)

    live = _live_provider(provider_name)

    if mode == "record":
        from app.buyer.llm.cassette import CassetteRecorder

        return CassetteRecorder(live, directory=directory, cassette=name)

    return live


def _live_provider(provider_name: str) -> LLMProvider:
    if provider_name == "gemini":
        if not settings.GEMINI_API_KEY.strip():
            raise LLMProviderNotConfigured("LLM_PROVIDER=gemini requires GEMINI_API_KEY.")
        if not settings.GEMINI_MODEL.strip():
            raise LLMProviderNotConfigured("LLM_PROVIDER=gemini requires GEMINI_MODEL.")
        from app.buyer.llm.gemini import GeminiProvider

        return GeminiProvider(
            api_key=settings.GEMINI_API_KEY.strip(),
            model=settings.GEMINI_MODEL.strip(),
        )

    if not settings.ANTHROPIC_API_KEY.strip():
        raise LLMProviderNotConfigured(
            "LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY."
        )
    from app.buyer.llm.anthropic import AnthropicProvider

    return AnthropicProvider(
        api_key=settings.ANTHROPIC_API_KEY.strip(),
        model=settings.ANTHROPIC_MODEL.strip(),
    )
