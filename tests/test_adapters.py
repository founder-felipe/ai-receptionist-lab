"""Tests for adapter ABCs, mock implementations, and the factory."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from adapters.notify_ghl import GHLNotify
from adapters.notify_mock import MockNotify
from adapters.sms_mock import MockSMS
from adapters.store_json import JSONStore


pytestmark = pytest.mark.asyncio


async def test_mock_sms_send() -> None:
    sms = MockSMS()
    result = await sms.send(to="+61491570006", body="hello")

    assert result["status"] == "sent"
    assert result["to"] == "+61491570006"
    assert result["body"] == "hello"
    assert result["message_sid"].startswith("MOCK-")
    assert MockSMS.sent_messages[-1] == result


async def test_mock_notify() -> None:
    notify = MockNotify()
    result = await notify.notify(
        lead={"name": "Sam", "phone": "+61491570006"},
        venue={"name": "Northbridge Barbers"},
    )

    assert result == {"status": "delivered", "channel": "mock"}
    assert len(MockNotify.notifications) == 1
    record = MockNotify.notifications[0]
    assert record["lead"]["name"] == "Sam"
    assert record["venue"]["name"] == "Northbridge Barbers"


async def test_json_store_roundtrip(tmp_leads_dir: Path) -> None:
    store = JSONStore()
    ids: list[str] = []
    for service in ("Fade", "Beard Trim", "Hot Towel"):
        lead_id = await store.save_lead(
            {"name": f"caller-{service}", "service": service}
        )
        ids.append(lead_id)

    # Each saved lead must have its injected id + created_at.
    all_leads = await store.list_leads(limit=10)
    assert len(all_leads) == 3
    for lead in all_leads:
        assert "id" in lead
        assert "created_at" in lead

    # list_leads(2) returns the most recent two, newest first.
    recent = await store.list_leads(limit=2)
    assert len(recent) == 2
    assert recent[0]["service"] == "Hot Towel"
    assert recent[1]["service"] == "Beard Trim"


def _reload_factory() -> Any:
    """Re-import the factory module so it picks up patched settings."""
    import adapters as adapters_pkg

    return importlib.reload(adapters_pkg)


async def test_factory_mock_mode(stub_settings: Any) -> None:
    stub_settings(NOTIFY_ADAPTER="", MODE="mock")
    factory = _reload_factory()

    notifiers = factory.get_notify()
    assert len(notifiers) == 1
    assert isinstance(notifiers[0], MockNotify)
    assert notifiers[0].name == "mock"


async def test_factory_explicit_mock(stub_settings: Any) -> None:
    stub_settings(NOTIFY_ADAPTER="mock", MODE="mock")
    factory = _reload_factory()

    notifiers = factory.get_notify()
    assert len(notifiers) == 1
    assert isinstance(notifiers[0], MockNotify)


async def test_factory_unknown_token_raises(stub_settings: Any) -> None:
    stub_settings(NOTIFY_ADAPTER="bogus", MODE="mock")
    factory = _reload_factory()

    with pytest.raises(ValueError, match="bogus"):
        factory.get_notify()


async def test_factory_mock_auto_injected(
    stub_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In MOCK mode, ``mock`` is auto-injected ahead of ``ghl``."""
    settings = stub_settings(
        SMS_ADAPTER="mock",
        NOTIFY_ADAPTER="ghl",
        GHL_API_KEY="test",
        GHL_LOCATION_ID="test",
    )
    # The real ``settings.mode`` property returns LIVE when GHL creds are
    # present, which would suppress the mock auto-injection branch.
    # Force-override the property on the class so ``_is_mock_mode``
    # picks up MOCK regardless of GHL creds.
    monkeypatch.setattr(type(settings), "mode", "MOCK", raising=False)

    factory = _reload_factory()

    notifiers = factory.get_notify()
    assert len(notifiers) == 2
    assert isinstance(notifiers[0], MockNotify)
    assert isinstance(notifiers[1], GHLNotify)
    assert notifiers[0].name == "mock"
    assert notifiers[1].name == "ghl"


async def test_factory_sms_mock(stub_settings: Any) -> None:
    stub_settings(SMS_ADAPTER="mock")
    factory = _reload_factory()

    sms = factory.get_sms()
    assert isinstance(sms, MockSMS)
    assert sms.name == "mock"


async def test_factory_store_json(stub_settings: Any, tmp_path: Path) -> None:
    stub_settings(STORE_ADAPTER="json", STORE_PATH=str(tmp_path / "leads.json"))
    factory = _reload_factory()

    store = factory.get_store()
    assert isinstance(store, JSONStore)
    assert store.name == "json"


async def test_factory_store_sqlite_not_implemented(stub_settings: Any) -> None:
    stub_settings(STORE_ADAPTER="sqlite")
    factory = _reload_factory()

    with pytest.raises(NotImplementedError):
        factory.get_store()
