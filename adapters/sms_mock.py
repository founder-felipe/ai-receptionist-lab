"""Mock SMS adapter — always-on UI-card sender for demo recordings."""

from __future__ import annotations

from typing import ClassVar
from uuid import uuid4

from adapters.base import SMSAdapter


class MockSMS(SMSAdapter):
    """In-memory SMS sender. Records every send for the demo UI + tests."""

    name: ClassVar[str] = "mock"  # type: ignore[misc]

    #: Module-level outbox. ``main.py`` later wires this to the SSE bus.
    sent_messages: ClassVar[list[dict]] = []

    async def send(self, to: str, body: str) -> dict:
        message = {
            "message_sid": f"MOCK-{uuid4()}",
            "status": "sent",
            "to": to,
            "body": body,
        }
        type(self).sent_messages.append(message)
        return message
