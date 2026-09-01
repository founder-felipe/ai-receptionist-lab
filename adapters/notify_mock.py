"""Mock owner-notification adapter — always-on UI card."""

from __future__ import annotations

from typing import ClassVar

from adapters.base import NotifyAdapter


class MockNotify(NotifyAdapter):
    """In-memory owner notifier. Records every notification."""

    name: ClassVar[str] = "mock"  # type: ignore[misc]

    #: Module-level log. ``main.py`` later wires this to the SSE bus.
    notifications: ClassVar[list[dict]] = []

    async def notify(self, lead: dict, venue: dict) -> dict:
        result = {
            "status": "delivered",
            "channel": "mock",
            "lead": lead,
            "venue": venue,
        }
        type(self).notifications.append(result)
        return {"status": "delivered", "channel": "mock"}
