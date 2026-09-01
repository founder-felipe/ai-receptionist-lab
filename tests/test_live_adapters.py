"""Smoke tests for live adapters — no network, no real creds.

Each test asserts the construction-time validation: missing creds raise
``RuntimeError`` (or ``NotImplementedError`` for the Twilio stub) before
any I/O happens.
"""

from __future__ import annotations

from typing import Any

import pytest


def test_ghl_requires_creds(stub_settings: Any) -> None:
    stub_settings(GHL_API_KEY="", GHL_LOCATION_ID="")
    from adapters.notify_ghl import GHLNotify

    with pytest.raises(RuntimeError, match="GHL_API_KEY"):
        GHLNotify()


def test_email_requires_creds(stub_settings: Any) -> None:
    stub_settings(
        SMTP_HOST="",
        SMTP_USER="",
        SMTP_PASS="",
        SMTP_FROM="",
        SMTP_TO_OWNER="",
    )
    from adapters.notify_email import EmailNotify

    with pytest.raises(RuntimeError) as excinfo:
        EmailNotify()
    message = str(excinfo.value)
    assert "SMTP_HOST" in message
    assert "SMTP_USER" in message


def test_twilio_stub_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIVE_TWILIO", raising=False)
    from adapters.sms_twilio import TwilioSMS

    with pytest.raises(NotImplementedError, match="SMS_ADAPTER=mock"):
        TwilioSMS()
