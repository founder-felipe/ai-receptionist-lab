"""Shared pytest fixtures for the AI receptionist demo test suite."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

# Ensure repo root is on sys.path so ``import adapters`` works when pytest
# is invoked from anywhere.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_stub_settings(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> Any:
    """Install a stub ``config`` module exposing ``settings`` for tests.

    Keeps these tests runnable in isolation; the stub is overridden the
    moment the real ``config`` module is importable on sys.path.
    """
    try:
        import config as real_config  # type: ignore[import-not-found]
    except ImportError:
        real_config = None

    if real_config is not None and hasattr(real_config, "settings"):
        # Override settings attributes in place for the duration of the test.
        # Skip keys that aren't pydantic fields (e.g. computed properties like
        # `mode`, or test-only knobs that map to nothing on the real model).
        real = real_config.settings
        model_fields = getattr(type(real), "model_fields", {})
        for key, value in overrides.items():
            if key not in model_fields:
                continue
            monkeypatch.setattr(real, key, value, raising=False)
        return real

    module = types.ModuleType("config")
    defaults = {
        "SMS_ADAPTER": "mock",
        "NOTIFY_ADAPTER": "",
        "STORE_ADAPTER": "json",
        "MODE": "mock",
        "STORE_PATH": None,
    }
    defaults.update(overrides)
    settings = types.SimpleNamespace(**defaults)
    module.settings = settings  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "config", module)
    return settings


@pytest.fixture
def stub_settings(monkeypatch: pytest.MonkeyPatch):
    """Factory fixture: ``stub_settings(NOTIFY_ADAPTER="ghl")`` etc."""

    def _factory(**overrides: Any) -> Any:
        return _install_stub_settings(monkeypatch, **overrides)

    return _factory


@pytest.fixture
def tmp_leads_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``JSONStore`` writes to a tmp_path-based file."""
    from adapters.store_json import JSONStore

    leads_path = tmp_path / "leads.json"
    monkeypatch.setattr(JSONStore, "DEFAULT_PATH", leads_path)
    _install_stub_settings(monkeypatch, STORE_PATH=str(leads_path))
    return tmp_path


@pytest.fixture
def mock_gemini() -> Any:
    """Stub Gemini brain returning canned replies keyed by state.

    Deterministic stand-in for the model call, so the state machine can be
    exercised without credentials or network access.
    """

    class _StubGemini:
        responses: dict[str, dict] = {
            "GREETING": {
                "reply": "Northbridge Barbers, this is Sage — how can I help?",
                "extracted": {},
                "next_state": "CAPTURE_INTENT",
            },
            "CAPTURE_INTENT": {
                "reply": "Got it — a fade. When works for you?",
                "extracted": {"service": "Fade"},
                "next_state": "CAPTURE_DATETIME",
            },
            "CAPTURE_DATETIME": {
                "reply": "Saturday 2pm — locked in. Can I grab your name and number?",
                "extracted": {"requested_time": "2026-05-30T14:00:00+08:00"},
                "next_state": "CAPTURE_CONTACT",
            },
            "CAPTURE_CONTACT": {
                "reply": "Thanks Sam — sending the booking link now.",
                "extracted": {"name": "Sam", "phone": "+61491570006"},
                "next_state": "SEND_LINK",
            },
        }

        def generate_reply(self, conversation: Any) -> dict:
            state = getattr(conversation, "state", "GREETING")
            return self.responses.get(
                state,
                {"reply": "", "extracted": {}, "next_state": "CLOSE"},
            )

    return _StubGemini()


@pytest.fixture(autouse=True)
def clean_mock_adapters() -> Any:
    """Clear MockSMS / MockNotify class-level state between tests."""
    from adapters.notify_mock import MockNotify
    from adapters.sms_mock import MockSMS

    MockSMS.sent_messages.clear()
    MockNotify.notifications.clear()
    yield
    MockSMS.sent_messages.clear()
    MockNotify.notifications.clear()
