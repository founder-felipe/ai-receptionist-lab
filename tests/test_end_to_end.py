"""End-to-end FastAPI flow test.

Wires the real FastAPI app (mock adapters via factory), monkeypatches the
Gemini factory to return a StubReceptionist, and walks the full state
machine via HTTP. Asserts terminal state, SMS outbox, owner-notify log,
and JSON leads file all line up.

SSE stream itself isn't exercised — TestClient SSE is finicky and the
indirect side-effects (adapter calls triggered by published events) are
the load-bearing contract.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import state_handler
from adapters.notify_mock import MockNotify
from adapters.sms_mock import MockSMS


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_leads_dir: Path) -> Any:
    """Boot the FastAPI app with Stub Gemini + tmp JSON store."""
    # Force the agent factory in main.py to use Stub regardless of env.
    import main
    from agents.receptionist_agent import StubReceptionist

    monkeypatch.setattr(
        main, "_agent_factory", lambda venue: StubReceptionist(venue=venue)
    )

    # Force MOCK mode so /reset succeeds.
    from config import settings

    monkeypatch.setattr(settings, "SMS_ADAPTER", "mock", raising=False)
    monkeypatch.setattr(settings, "STORE_ADAPTER", "json", raising=False)
    monkeypatch.setattr(settings, "NOTIFY_ADAPTER", "mock", raising=False)

    # Point the store at the tmp file (already done by tmp_leads_dir for
    # JSONStore.DEFAULT_PATH and settings.STORE_PATH).
    leads_path = tmp_leads_dir / "leads.json"
    monkeypatch.setattr(settings, "STORE_PATH", str(leads_path), raising=False)

    # TestClient triggers lifespan on __enter__.
    with TestClient(main.app) as c:
        yield c

    # Belt-and-braces: clear in-memory state between tests.
    state_handler.reset_all()


def test_full_journey(client: TestClient, tmp_leads_dir: Path) -> None:
    """POST /ring + four /turn calls walks the state machine to CLOSE."""
    # 1. Ring
    r = client.post("/ring")
    assert r.status_code == 200, r.text
    body = r.json()
    call_id = body["call_id"]
    assert call_id
    # After GREETING auto-advances we expect to be in CAPTURE_INTENT.
    assert body["state"] == "CAPTURE_INTENT"
    assert body["reply"]

    # 2. Four caller turns through to CLOSE.
    utterances = ["a fade", "saturday 2pm", "Sam", "0491570110"]
    final = None
    for utt in utterances:
        r = client.post("/turn", json={"call_id": call_id, "utterance": utt})
        assert r.status_code == 200, r.text
        final = r.json()

    assert final is not None
    assert final["state"] == "CLOSE"
    assert final["reply"]

    # 3. Side-effects fired exactly once each.
    assert len(MockSMS.sent_messages) == 1
    sms = MockSMS.sent_messages[0]
    assert "example.com/book/northbridge-barbers" in sms["body"]

    assert len(MockNotify.notifications) == 1

    # 4. JSON store has the lead.
    leads_path = tmp_leads_dir / "leads.json"
    assert leads_path.exists()
    leads = json.loads(leads_path.read_text(encoding="utf-8"))
    assert len(leads) == 1
    lead = leads[0]
    assert lead["name"] == "Sam"
    assert lead["service"] == "Fade"
    assert lead["link_sent"] is True
    assert lead["phone"]


def test_leads_endpoint(client: TestClient, tmp_leads_dir: Path) -> None:
    """GET /leads echoes the saved lead."""
    r = client.post("/ring")
    call_id = r.json()["call_id"]
    for utt in ["a fade", "saturday 2pm", "Sam", "0491570110"]:
        client.post("/turn", json={"call_id": call_id, "utterance": utt})

    r = client.get("/leads")
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["name"] == "Sam"


def test_reset_endpoint(client: TestClient, tmp_leads_dir: Path) -> None:
    """POST /reset wipes in-memory + leads file in MOCK mode."""
    # Seed: run a full conversation first.
    r = client.post("/ring")
    call_id = r.json()["call_id"]
    for utt in ["a fade", "saturday 2pm", "Sam", "0491570110"]:
        client.post("/turn", json={"call_id": call_id, "utterance": utt})

    assert len(MockSMS.sent_messages) == 1

    r = client.post("/reset")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"

    # In-memory conversations cleared.
    assert state_handler.conversations == {}
    # Mock outboxes cleared.
    assert MockSMS.sent_messages == []
    assert MockNotify.notifications == []
    # Leads file emptied.
    leads_path = tmp_leads_dir / "leads.json"
    assert json.loads(leads_path.read_text(encoding="utf-8")) == []
