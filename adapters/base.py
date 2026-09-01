"""Adapter abstract base classes for SMS, owner notification, and lead storage.

These ABCs are the seam between the FastAPI/state-machine core and any
concrete integration (mock UI card, Twilio, GoHighLevel, SMTP, JSON file,
SQLite, etc.). Concrete adapters live in sibling modules and are wired up
by the factory in ``adapters/__init__.py``.

``mypy --strict`` is enforced on this module; keep type hints exact.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class SMSAdapter(ABC):
    """Send an SMS to a caller (mock card, Twilio, etc.)."""

    name: str

    @abstractmethod
    async def send(self, to: str, body: str) -> dict[str, Any]:
        """Send ``body`` to ``to``. Returns provider response dict."""
        ...


class NotifyAdapter(ABC):
    """Notify the venue owner that a new lead has come in."""

    name: str

    @abstractmethod
    async def notify(self, lead: dict[str, Any], venue: dict[str, Any]) -> dict[str, Any]:
        """Notify the owner of ``venue`` about ``lead``. Returns provider response dict."""
        ...


class StoreAdapter(ABC):
    """Persist captured leads."""

    name: str

    @abstractmethod
    async def save_lead(self, lead: dict[str, Any]) -> str:
        """Persist ``lead``. Returns the lead id (generated if absent)."""
        ...

    @abstractmethod
    async def list_leads(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent ``limit`` leads, newest first."""
        ...
