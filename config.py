"""Env loader + adapter wiring for the AI receptionist demo.

Single source of truth for env-driven settings. Reads `.env` via
pydantic-settings, exposes a module-level `settings` singleton, and provides
helpers for derived values (mock vs live mode, parsed notify adapter list).
"""
from __future__ import annotations

from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Allowed tokens for adapter selector env vars. Keep in sync with README.md.
_VALID_SMS = {"mock", "twilio"}
_VALID_NOTIFY = {"mock", "ghl", "email"}
_VALID_STORE = {"json", "sqlite"}


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # --- Gemini ---
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # --- Venue ---
    VENUE_CONFIG_PATH: str = "venues/northbridge-barbers.yaml"

    # --- Adapter selectors ---
    SMS_ADAPTER: str = "mock"
    NOTIFY_ADAPTER: str = "mock"
    STORE_ADAPTER: str = "json"
    STORE_PATH: str = "data/leads.json"

    # --- GHL (live owner-notify) ---
    GHL_API_KEY: str = ""
    GHL_LOCATION_ID: str = ""
    GHL_PIPELINE_ID: str = ""
    GHL_STAGE_ID: str = ""

    # --- SMTP (live owner-notify) ---
    SMTP_HOST: str = ""
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    SMTP_FROM: str = ""
    SMTP_TO_OWNER: str = ""

    # --- n8n / ElevenLabs tunnel ---
    PUBLIC_BASE_URL: str = ""

    # --- Validators (fail fast on typos in selector envs) ---

    @field_validator("SMS_ADAPTER")
    @classmethod
    def _check_sms(cls, v: str) -> str:
        token = v.strip().lower()
        if token not in _VALID_SMS:
            raise ValueError(
                f"SMS_ADAPTER={v!r} not in {sorted(_VALID_SMS)}"
            )
        return token

    @field_validator("STORE_ADAPTER")
    @classmethod
    def _check_store(cls, v: str) -> str:
        token = v.strip().lower()
        if token not in _VALID_STORE:
            raise ValueError(
                f"STORE_ADAPTER={v!r} not in {sorted(_VALID_STORE)}"
            )
        return token

    @field_validator("NOTIFY_ADAPTER")
    @classmethod
    def _check_notify(cls, v: str) -> str:
        tokens = [t.strip().lower() for t in v.split(",") if t.strip()]
        unknown = [t for t in tokens if t not in _VALID_NOTIFY]
        if unknown:
            raise ValueError(
                f"NOTIFY_ADAPTER unknown tokens {unknown}; "
                f"valid: {sorted(_VALID_NOTIFY)}"
            )
        return ",".join(tokens) if tokens else "mock"

    # --- Derived helpers ---

    @property
    def mode(self) -> Literal["MOCK", "LIVE"]:
        """LIVE if any live adapter creds are present, else MOCK.

        Live indicators: a non-mock SMS_ADAPTER, OR `ghl`/`email` in
        NOTIFY_ADAPTER with their respective creds present.
        """
        if self.SMS_ADAPTER != "mock":
            return "LIVE"
        notify_tokens = {
            t.strip() for t in self.NOTIFY_ADAPTER.split(",") if t.strip()
        }
        if "ghl" in notify_tokens and self.GHL_API_KEY and self.GHL_LOCATION_ID:
            return "LIVE"
        if "email" in notify_tokens and self.SMTP_HOST and self.SMTP_USER:
            return "LIVE"
        return "MOCK"

    def notify_adapters_list(self) -> list[str]:
        """Parse NOTIFY_ADAPTER comma-sep list.

        In MOCK mode `mock` is always present (UI card depends on it); the
        helper injects it if the env var omits it. Unknown tokens already
        rejected at construction by the validator.
        """
        tokens = [
            t.strip().lower()
            for t in self.NOTIFY_ADAPTER.split(",")
            if t.strip()
        ]
        if self.mode == "MOCK" and "mock" not in tokens:
            tokens.insert(0, "mock")
        # Preserve order of first appearance, dedupe.
        seen: set[str] = set()
        ordered: list[str] = []
        for t in tokens:
            if t not in seen:
                seen.add(t)
                ordered.append(t)
        return ordered


# Module-level singleton. Importers do `from config import settings`.
settings = Settings()
