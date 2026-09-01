"""Adapter factory — wires env config to concrete SMS/Notify/Store adapters.

Imports of concrete adapters are deferred to the factory functions so that
optional third-party packages (e.g. ``twilio``) are only imported when the
operator opts in via ``SMS_ADAPTER=twilio`` etc.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adapters.base import NotifyAdapter, SMSAdapter, StoreAdapter


def _settings():  # type: ignore[no-untyped-def]
    """Return the project settings singleton (deferred import)."""
    from config import settings  # type: ignore[import-not-found]

    return settings


def _is_mock_mode(settings) -> bool:  # type: ignore[no-untyped-def]
    """Best-effort detector for MOCK mode.

    A run is "mock mode" when no real Gemini key is set and the operator
    hasn't explicitly opted into live adapters. ``config.py`` may expose
    a ``MODE`` / ``mock_mode`` attribute; fall back to a heuristic.
    """
    for attr in ("MODE", "mode"):
        value = getattr(settings, attr, None)
        if isinstance(value, str):
            return value.lower() == "mock"
    for attr in ("MOCK_MODE", "mock_mode", "is_mock"):
        value = getattr(settings, attr, None)
        if isinstance(value, bool):
            return value
    sms = str(getattr(settings, "SMS_ADAPTER", "mock") or "mock").lower()
    return sms == "mock"


def get_sms() -> "SMSAdapter":
    """Return the configured SMS adapter (single instance)."""
    settings = _settings()
    choice = str(getattr(settings, "SMS_ADAPTER", "mock") or "mock").lower()
    if choice == "mock":
        from adapters.sms_mock import MockSMS

        return MockSMS()
    if choice == "twilio":
        # Deferred import: only require the ``twilio`` package when live.
        from adapters.sms_twilio import TwilioSMS  # type: ignore[import-not-found]

        return TwilioSMS()
    raise ValueError(f"Unknown SMS_ADAPTER: {choice!r}")


def get_notify() -> list["NotifyAdapter"]:
    """Return a list of configured notify adapters.

    ``NOTIFY_ADAPTER`` is a comma-separated env var (e.g. ``"mock,ghl,email"``).
    Unknown tokens raise ``ValueError`` so typos fail fast at startup. In
    MOCK mode the ``mock`` channel is auto-injected if omitted, so the UI
    card always renders.
    """
    settings = _settings()
    raw = str(getattr(settings, "NOTIFY_ADAPTER", "") or "")
    tokens = [t.strip().lower() for t in raw.split(",") if t.strip()]

    if _is_mock_mode(settings) and "mock" not in tokens:
        tokens.insert(0, "mock")

    if not tokens:
        tokens = ["mock"]

    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            ordered.append(token)

    adapters: list[NotifyAdapter] = []
    for token in ordered:
        if token == "mock":
            from adapters.notify_mock import MockNotify

            adapters.append(MockNotify())
        elif token == "ghl":
            from adapters.notify_ghl import GHLNotify  # type: ignore[import-not-found]

            adapters.append(GHLNotify())
        elif token == "email":
            from adapters.notify_email import EmailNotify  # type: ignore[import-not-found]

            adapters.append(EmailNotify())
        else:
            raise ValueError(f"Unknown NOTIFY_ADAPTER token: {token!r}")
    return adapters


def get_store() -> "StoreAdapter":
    """Return the configured lead-store adapter."""
    settings = _settings()
    choice = str(getattr(settings, "STORE_ADAPTER", "json") or "json").lower()
    if choice == "json":
        from adapters.store_json import JSONStore

        path = getattr(settings, "STORE_PATH", None)
        return JSONStore(path) if path else JSONStore()
    if choice == "sqlite":
        raise NotImplementedError("sqlite store deferred to v2")
    raise ValueError(f"Unknown STORE_ADAPTER: {choice!r}")


__all__ = ["get_sms", "get_notify", "get_store"]
