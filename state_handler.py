"""Conversation state machine for the AI receptionist demo.

A ``Conversation`` is a single in-progress call. ``advance`` is the only
mutator: callers (FastAPI routes, tests) feed it the latest caller
utterance plus a receptionist agent (Gemini or stub) and receive the
next state, the agent's spoken reply, and a list of SSE events to emit.

Storage is process-local — a module-level dict keyed by ``call_id``.
That's deliberate; this is a single-process local demo, not a service.

State table (see README.md § Conversation state machine for the canonical
version):

```
GREETING        → CAPTURE_INTENT     (auto, no caller utterance)
CAPTURE_INTENT  → CAPTURE_DATETIME   when `service` matches venue.services
CAPTURE_DATETIME → CAPTURE_CONTACT   when `requested_time` parses + in hours
CAPTURE_CONTACT → SEND_LINK          when name + phone captured (phone E.164)
SEND_LINK       → CLOSE              after `sms_sent` event emitted
CLOSE           → (terminal)         `lead_saved` + `owner_notified` events
```

Two reprompts on any CAPTURE_* state → fall through to CLOSE with
``needs_followup=True`` and a ``handoff`` event.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Protocol

from agents.extractors import (
    match_service,
    normalise_datetime,
    normalise_phone,
    within_hours,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------

class ConversationState(Enum):
    GREETING = auto()
    CAPTURE_INTENT = auto()
    CAPTURE_DATETIME = auto()
    CAPTURE_CONTACT = auto()
    SEND_LINK = auto()
    CLOSE = auto()


# Capture states are the ones subject to the 2-reprompt handoff rule.
_CAPTURE_STATES = {
    ConversationState.CAPTURE_INTENT,
    ConversationState.CAPTURE_DATETIME,
    ConversationState.CAPTURE_CONTACT,
}


# ---------------------------------------------------------------------------
# Conversation record
# ---------------------------------------------------------------------------

@dataclass
class Conversation:
    """In-memory record for a single live call."""

    call_id: str
    state: ConversationState
    venue: dict[str, Any]
    history: list[dict[str, str]] = field(default_factory=list)
    extracted: dict[str, Any] = field(default_factory=dict)
    reprompts: int = 0


# Module-level store. Keyed by call_id. Cleared by ``reset_all``.
conversations: dict[str, Conversation] = {}


def new_conversation(venue: dict[str, Any]) -> Conversation:
    """Create + register a fresh conversation in GREETING."""
    call_id = uuid.uuid4().hex
    convo = Conversation(
        call_id=call_id,
        state=ConversationState.GREETING,
        venue=dict(venue),
    )
    conversations[call_id] = convo
    return convo


def reset_all() -> None:
    """Wipe all in-memory conversations. Powers ``POST /reset``."""
    conversations.clear()


# ---------------------------------------------------------------------------
# Agent protocol (structural typing — works for Gemini + Stub)
# ---------------------------------------------------------------------------

class _AgentLike(Protocol):
    async def generate_reply(
        self,
        conversation: Conversation,
        last_utterance: str | None,
    ) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# advance()
# ---------------------------------------------------------------------------

async def advance(
    conversation: Conversation,
    utterance: str | None,
    agent: _AgentLike,
) -> tuple[ConversationState, str, list[dict[str, Any]]]:
    """Drive one turn of the conversation.

    Returns ``(next_state, agent_reply, events)`` where ``events`` is a
    list of ``{"type": str, "data": dict}`` payloads ready to be pushed
    onto the SSE bus.
    """
    events: list[dict[str, Any]] = []

    # Record caller utterance (if any) before asking the agent.
    if utterance:
        conversation.history.append({"role": "user", "content": utterance})
        events.append(
            {"type": "transcript_line", "data": {"role": "user", "content": utterance}}
        )

    # Ask the agent for this turn's reply + any captured fields.
    result = await agent.generate_reply(conversation, utterance)
    reply: str = (result.get("reply") or "").strip()
    new_fields: dict[str, Any] = dict(result.get("extracted") or {})

    # ------------------------------------------------------------------
    # Merge captured fields with light normalisation per field.
    # ------------------------------------------------------------------
    _merge_extracted(conversation, new_fields)

    # Record assistant reply.
    if reply:
        conversation.history.append({"role": "assistant", "content": reply})
        events.append(
            {"type": "transcript_line", "data": {"role": "assistant", "content": reply}}
        )

    # If we're already terminal, don't re-emit side-effect events.
    if conversation.state == ConversationState.CLOSE:
        return conversation.state, reply, events

    # ------------------------------------------------------------------
    # State transitions.
    # ------------------------------------------------------------------
    next_state = _next_state(conversation)

    # Handoff fallthrough: too many reprompts in a capture state.
    if (
        next_state == conversation.state
        and conversation.state in _CAPTURE_STATES
    ):
        conversation.reprompts += 1
        if conversation.reprompts >= 2:
            return _handoff(conversation, events)
    else:
        # Successful advance — reset reprompt counter.
        conversation.reprompts = 0

    conversation.state = next_state

    # ------------------------------------------------------------------
    # Side effects on entering certain states.
    # ------------------------------------------------------------------
    if conversation.state == ConversationState.SEND_LINK:
        reply, events = _do_send_link(conversation, reply, events)
        # SEND_LINK is transient — immediately advance to CLOSE.
        conversation.state = ConversationState.CLOSE
        events.extend(_do_close(conversation))

    elif conversation.state == ConversationState.CLOSE:
        # Direct entry into CLOSE (e.g. from a state machine that skipped
        # SEND_LINK for some reason). Emit close events idempotently.
        events.extend(_do_close(conversation))

    return conversation.state, reply, events


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _merge_extracted(convo: Conversation, new_fields: dict[str, Any]) -> None:
    """Normalise + merge fields the agent surfaced this turn."""
    venue = convo.venue
    tz = venue.get("timezone", "Australia/Perth")
    services = list(venue.get("services", []) or [])
    hours = venue.get("hours", {}) or {}

    for key, raw in new_fields.items():
        if raw is None:
            continue
        if key == "phone":
            normalised = normalise_phone(str(raw))
            if normalised:
                convo.extracted["phone"] = normalised
        elif key == "service":
            matched = match_service(str(raw), services)
            if matched:
                convo.extracted["service"] = matched
        elif key == "requested_time":
            parsed = normalise_datetime(str(raw), tz=tz)
            if parsed is None:
                continue
            if hours and not within_hours(parsed, hours):
                # Don't accept — leave the field unset so we re-prompt.
                # State machine will see missing field and stay put.
                convo.extracted["_requested_time_rejected"] = parsed.isoformat()
                continue
            convo.extracted["requested_time"] = parsed.isoformat()
            convo.extracted.pop("_requested_time_rejected", None)
        elif key in {"name", "notes"}:
            convo.extracted[key] = str(raw).strip()
        else:
            # Unknown field — keep raw, no normalisation.
            convo.extracted[key] = raw


def _next_state(convo: Conversation) -> ConversationState:
    """Compute desired next state from current state + extracted fields."""
    e = convo.extracted
    current = convo.state

    if current == ConversationState.GREETING:
        return ConversationState.CAPTURE_INTENT

    if current == ConversationState.CAPTURE_INTENT:
        return (
            ConversationState.CAPTURE_DATETIME
            if e.get("service")
            else ConversationState.CAPTURE_INTENT
        )

    if current == ConversationState.CAPTURE_DATETIME:
        return (
            ConversationState.CAPTURE_CONTACT
            if e.get("requested_time")
            else ConversationState.CAPTURE_DATETIME
        )

    if current == ConversationState.CAPTURE_CONTACT:
        if e.get("name") and e.get("phone"):
            return ConversationState.SEND_LINK
        return ConversationState.CAPTURE_CONTACT

    if current == ConversationState.SEND_LINK:
        return ConversationState.CLOSE

    return ConversationState.CLOSE


def _do_send_link(
    convo: Conversation,
    reply: str,
    events: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Emit the SMS send event."""
    name = convo.extracted.get("name", "there")
    service = convo.extracted.get("service", "your appointment")
    booking_link = convo.venue.get("booking_link", "")
    body = f"Hi {name} — book your {service} at {booking_link}"
    events.append(
        {
            "type": "sms_sent",
            "data": {
                "to": convo.extracted.get("phone"),
                "body": body,
            },
        }
    )
    convo.extracted["link_sent"] = True
    convo.extracted["sms_body"] = body
    return reply, events


def _build_lead(convo: Conversation) -> dict[str, Any]:
    """Snapshot a lead dict for store/notify side-effects."""
    e = convo.extracted
    return {
        "call_id": convo.call_id,
        "venue": convo.venue.get("name"),
        "name": e.get("name"),
        "phone": e.get("phone"),
        "service": e.get("service"),
        "requested_time": e.get("requested_time"),
        "notes": e.get("notes"),
        "link_sent": bool(e.get("link_sent")),
        "needs_followup": bool(e.get("needs_followup")),
    }


def _do_close(convo: Conversation) -> list[dict[str, Any]]:
    """Emit lead_saved + owner_notified events for terminal state."""
    lead = _build_lead(convo)
    return [
        {"type": "lead_saved", "data": {"lead": lead}},
        {"type": "owner_notified", "data": {"lead": lead, "venue": convo.venue}},
    ]


def _handoff(
    convo: Conversation,
    events: list[dict[str, Any]],
) -> tuple[ConversationState, str, list[dict[str, Any]]]:
    """Override agent reply, jump to CLOSE, emit handoff event."""
    reply = "Sorry I'm having trouble — I'll have the team call you back."
    # Replace any agent reply we already pushed into history this turn.
    if convo.history and convo.history[-1]["role"] == "assistant":
        convo.history[-1] = {"role": "assistant", "content": reply}
    else:
        convo.history.append({"role": "assistant", "content": reply})

    # Replace the last transcript_line event (the agent's reply, if any).
    events = [
        ev for ev in events
        if not (ev["type"] == "transcript_line" and ev["data"].get("role") == "assistant")
    ]
    events.append(
        {"type": "transcript_line", "data": {"role": "assistant", "content": reply}}
    )

    convo.extracted["needs_followup"] = True
    convo.state = ConversationState.CLOSE
    lead = _build_lead(convo)
    events.append({"type": "handoff", "data": {"lead": lead}})
    events.extend(_do_close(convo))
    return convo.state, reply, events
