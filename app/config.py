"""Application settings (BUILD_SPEC §4).

Every variable in §4.1 is read here and every rule in §4.2 is applied at import
time. The module fails loudly at startup rather than at the first request.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")

LLMProviderName = Literal["gemini", "anthropic", "cassette"]
CassetteMode = Literal["off", "record", "replay"]


class ConfigError(RuntimeError):
    """Raised when the environment is not fit to start the application."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Values injected by a deployment platform (Render's dashboard, docker
        # --env-file) keep their surrounding whitespace, where python-dotenv
        # strips it. Strip here so local and deployed runs validate identically.
        str_strip_whitespace=True,
    )

    # ── Core ──────────────────────────────────────────────────────────────
    DATABASE_URL: str
    APP_BASE_URL: str = "http://localhost:8000"
    DEMO_MODE: bool = True
    LOG_LEVEL: str = "INFO"

    # ── Razorpay (test mode only) ─────────────────────────────────────────
    RAZORPAY_KEY_ID: str
    RAZORPAY_KEY_SECRET: str
    RAZORPAY_WEBHOOK_SECRET: str

    # ── Signing seeds (Ed25519, 32 bytes hex) ─────────────────────────────
    MANDATE_SIGNING_SEED: str
    MERCHANT_SIGNING_SEED: str

    # ── Merchant plane auth ───────────────────────────────────────────────
    MERCHANT_API_KEY: str

    # ── LLM provider ──────────────────────────────────────────────────────
    LLM_PROVIDER: str = ""
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = ""
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"

    # ── Cassette record/replay ────────────────────────────────────────────
    CASSETTE_MODE: str = "off"
    CASSETTE_DIR: str = Field(default="cassettes")

    # §4.2 — Render emits postgres://; SQLAlchemy 2.x requires postgresql://.
    @field_validator("DATABASE_URL")
    @classmethod
    def _normalise_database_url(cls, v: str) -> str:
        if v.startswith("postgres://"):
            return "postgresql://" + v[len("postgres://") :]
        return v

    # §4.2 — refuse to start on a live key. A live key is an accident.
    @field_validator("RAZORPAY_KEY_ID")
    @classmethod
    def _test_key_only(cls, v: str) -> str:
        if not v.startswith("rzp_test_"):
            raise ValueError(
                "RAZORPAY_KEY_ID must begin with 'rzp_test_'. "
                "Kavach refuses to start with a live Razorpay key."
            )
        return v

    @field_validator("MANDATE_SIGNING_SEED", "MERCHANT_SIGNING_SEED")
    @classmethod
    def _seed_is_hex64(cls, v: str, info: ValidationInfo) -> str:
        if not _HEX64.match(v or ""):
            raise ValueError(
                f"{info.field_name} must be exactly 64 hex characters "
                '(32 random bytes). Generate with: python3 -c "import os;'
                'print(os.urandom(32).hex())"'
            )
        return v.lower()

    @field_validator("CASSETTE_MODE")
    @classmethod
    def _known_cassette_mode(cls, v: str) -> str:
        # Tolerate a trailing inline comment carried over from .env.example.
        v = v.split("#", 1)[0].strip().lower()
        if v not in ("off", "record", "replay"):
            raise ValueError(
                f"CASSETTE_MODE must be one of off | record | replay, got {v!r}"
            )
        return v

    @field_validator("LLM_PROVIDER")
    @classmethod
    def _known_llm_provider(cls, v: str) -> str:
        # §4.2 — unset or unknown raises at startup. No silent live fallback.
        v = v.split("#", 1)[0].strip().lower()
        if v not in ("gemini", "anthropic", "cassette"):
            raise ValueError(
                "LLM_PROVIDER must be set to one of gemini | anthropic | cassette. "
                f"Got {v!r}. There is no default and no silent live fallback."
            )
        return v

    @model_validator(mode="after")
    def _cross_field_rules(self) -> "Settings":
        if self.MANDATE_SIGNING_SEED == self.MERCHANT_SIGNING_SEED:
            raise ValueError(
                "MANDATE_SIGNING_SEED and MERCHANT_SIGNING_SEED must be different. "
                "The Mandate Authority and the merchant are separate signers."
            )

        if self.LLM_PROVIDER == "gemini":
            if not self.GEMINI_API_KEY.strip():
                raise ValueError("LLM_PROVIDER=gemini requires GEMINI_API_KEY.")
            if not self.GEMINI_MODEL.strip():
                raise ValueError("LLM_PROVIDER=gemini requires GEMINI_MODEL.")
        elif self.LLM_PROVIDER == "anthropic":
            if not self.ANTHROPIC_API_KEY.strip():
                raise ValueError("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY.")
        # cassette requires neither.

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and validate settings once. Raises ConfigError on any problem."""
    try:
        return Settings()  # type: ignore[call-arg]
    except Exception as exc:  # pragma: no cover - startup failure path
        raise ConfigError(f"Invalid Kavach configuration:\n{exc}") from exc


settings = get_settings()
