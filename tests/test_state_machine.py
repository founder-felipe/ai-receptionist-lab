"""State machine tests — happy path, handoff, service matching, hours."""
from __future__ import annotations

from typing import Any

import pytest

from agents.receptionist_agent import StubReceptionist
from state_handler import (
    ConversationState,
    advance,
    new_conversation,
    reset_all,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

VENUE: dict[str, Any] = {
    "name": "Northbridge Barbers",
    "booking_link": "https://example.com/book/northbridge-barbers",
    "services": ["Fade", "Beard Trim"],
    "hours": {
        "mon-fri": "09:00-19:00",
        "sat": "08:00-17:00",
        "sun": "closed",
    },
    "timezone": "Australia/Perth",
}


@pytest.fixture(autouse=True)
def _clear_conversations() -> None:
    reset_all()


def _event_types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

async def test_happy_path() -> None:
    """Walk the full state machine; verify events + extracted at CLOSE."""
    agent = StubReceptionist(
        responses={
            "GREETING": {
                "reply": "Northbridge Barbers, this is Sage.",
                "extracted": {},
            },
            "CAPTURE_INTENT": {
                "reply": "A fade — got it. When works?",
                "extracted": {"service": "fade"},  # lowercase → normaliser maps to "Fade"
            },
            "CAPTURE_DATETIME": {
                "reply": "Locked in. Name and number?",
                # next Saturday at 2pm — inside hours.
                "extracted": {"requested_time": "Saturday 2pm"},
            },
            "CAPTURE_CONTACT": {
                "reply": "Thanks Sam — sending the link now.",
                "extracted": {"name": "Sam", "phone": "0491570006"},
            },
        },
        venue=VENUE,
    )

    convo = new_conversation(VENUE)

    # Turn 1: GREETING → CAPTURE_INTENT (no caller utterance yet).
    state, reply, events = await advance(convo, None, agent)
    assert state == ConversationState.CAPTURE_INTENT
    assert "transcript_line" in _event_types(events)
    assert reply

    # Turn 2: caller says "fade" → CAPTURE_INTENT → CAPTURE_DATETIME.
    state, reply, events = await advance(convo, "I want a fade", agent)
    assert state == ConversationState.CAPTURE_DATETIME
    assert convo.extracted["service"] == "Fade"

    # Turn 3: caller offers a time → CAPTURE_DATETIME → CAPTURE_CONTACT.
    state, reply, events = await advance(convo, "next Saturday at 2pm", agent)
    assert state == ConversationState.CAPTURE_CONTACT
    assert convo.extracted.get("requested_time")

    # Turn 4: caller gives name + phone → SEND_LINK (collapses straight to CLOSE).
    state, reply, events = await advance(convo, "Sam, 0491 570 006", agent)
    assert state == ConversationState.CLOSE

    types = _event_types(events)
    assert "sms_sent" in types
    assert "lead_saved" in types
    assert "owner_notified" in types

    # Final extracted dict has all 4 captured fields + lead bookkeeping.
    e = convo.extracted
    assert e["name"] == "Sam"
    assert e["phone"] == "+61491570006"
    assert e["service"] == "Fade"
    assert e["requested_time"]
    assert e["link_sent"] is True
    assert "needs_followup" not in e or e["needs_followup"] is False


# ---------------------------------------------------------------------------
# Handoff fallthrough
# ---------------------------------------------------------------------------

async def test_handoff_fallthrough() -> None:
    """Two failed reprompts on a capture state → CLOSE w/ needs_followup."""
    # Stub captures nothing at any state.
    silent = StubReceptionist(
        responses={
            "GREETING": {"reply": "Northbridge Barbers.", "extracted": {}},
            "CAPTURE_INTENT": {"reply": "What service?", "extracted": {}},
        },
        venue=VENUE,
    )

    convo = new_conversation(VENUE)

    # Turn 1: greeting → CAPTURE_INTENT.
    state, _, _ = await advance(convo, None, silent)
    assert state == ConversationState.CAPTURE_INTENT

    # Turn 2: still no service → reprompt 1, stays put.
    state, _, _ = await advance(convo, "uhh", silent)
    assert state == ConversationState.CAPTURE_INTENT
    assert convo.reprompts == 1

    # Turn 3: still nothing → reprompt 2 → handoff to CLOSE.
    state, reply, events = await advance(convo, "dunno", silent)
    assert state == ConversationState.CLOSE
    assert convo.extracted.get("needs_followup") is True
    assert "team call you back" in reply.lower()
    types = _event_types(events)
    assert "handoff" in types
    assert "lead_saved" in types


# ---------------------------------------------------------------------------
# Service matching
# ---------------------------------------------------------------------------

async def test_service_matching() -> None:
    """Lowercase 'fade' from the agent should resolve to canonical 'Fade'."""
    agent = StubReceptionist(
        responses={
            "GREETING": {"reply": "Hi.", "extracted": {}},
            "CAPTURE_INTENT": {
                "reply": "Got it.",
                "extracted": {"service": "fade"},
            },
        },
        venue=VENUE,
    )
    convo = new_conversation(VENUE)
    await advance(convo, None, agent)
    await advance(convo, "fade please", agent)
    assert convo.extracted["service"] == "Fade"
    assert convo.state == ConversationState.CAPTURE_DATETIME


# ---------------------------------------------------------------------------
# Datetime outside hours
# ---------------------------------------------------------------------------

async def test_datetime_outside_hours() -> None:
    """Sunday request at a venue closed Sunday → rejected, stays in DATETIME."""
    agent = StubReceptionist(
        responses={
            "GREETING": {"reply": "Hi.", "extracted": {}},
            "CAPTURE_INTENT": {
                "reply": "Sure.",
                "extracted": {"service": "Fade"},
            },
            "CAPTURE_DATETIME": {
                "reply": "Sundays we're closed — nearest available is Saturday.",
                "extracted": {"requested_time": "Sunday 2pm"},
            },
        },
        venue=VENUE,
    )
    convo = new_conversation(VENUE)
    await advance(convo, None, agent)
    await advance(convo, "fade", agent)

    state, reply, _ = await advance(convo, "Sunday 2pm", agent)
    assert state == ConversationState.CAPTURE_DATETIME
    assert "requested_time" not in convo.extracted
    assert convo.reprompts == 1
    assert "nearest" in reply.lower() or "closed" in reply.lower()
