"""GoHighLevel owner-notification adapter.

Creates a Contact then an Opportunity in the configured GHL sub-account.
API reference: the GoHighLevel public REST API docs.

The module is safe to import without credentials: validation only happens
on construction so the factory in ``adapters/__init__.py`` can lazy-load
this module behind a token check.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

import httpx

from adapters.base import NotifyAdapter

_GHL_BASE = "https://services.leadconnectorhq.com"
_GHL_VERSION = "2021-07-28"
_TIMEOUT_SECONDS = 10.0


def _redact(text: str, secret: str) -> str:
    """Strip ``secret`` from ``text`` so errors don't leak the API key."""
    if not secret:
        return text
    return text.replace(secret, "***REDACTED***")


def _split_name(full_name: str) -> tuple[str, str | None]:
    """Split a full name into (first, last). ``last`` is ``None`` if absent."""
    cleaned = (full_name or "").strip()
    if not cleaned:
        return "", None
    parts = re.split(r"\s+", cleaned, maxsplit=1)
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1]


class GHLNotify(NotifyAdapter):
    """Notify the venue owner by writing the lead into GoHighLevel."""

    name: ClassVar[str] = "ghl"  # type: ignore[misc]

    def __init__(self) -> None:
        from config import settings  # local import: avoid hard dep at module load

        if not settings.GHL_API_KEY:
            raise RuntimeError("GHL_API_KEY required")
        if not settings.GHL_LOCATION_ID:
            raise RuntimeError("GHL_LOCATION_ID required")

        self._api_key: str = settings.GHL_API_KEY
        self._location_id: str = settings.GHL_LOCATION_ID
        self._default_pipeline_id: str = settings.GHL_PIPELINE_ID or ""
        self._default_stage_id: str = settings.GHL_STAGE_ID or ""
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """Lazy-init the AsyncClient. Per-instance — never share across loops."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)
        return self._client

    async def aclose(self) -> None:
        """Close the underlying client. Called from W3.A's lifespan hook."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Version": _GHL_VERSION,
            "Location-Id": self._location_id,
            "Content-Type": "application/json",
        }

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._get_client()
        try:
            response = await client.post(
                f"{_GHL_BASE}{path}",
                headers=self._headers(),
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"GHL request to {path} failed: {_redact(str(exc), self._api_key)}"
            ) from None
        if response.status_code >= 400:
            body = _redact(response.text, self._api_key)
            raise RuntimeError(
                f"GHL {path} returned {response.status_code}: {body}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"GHL {path} returned non-JSON body: "
                f"{_redact(response.text, self._api_key)}"
            ) from exc
        if not isinstance(data, dict):
            raise RuntimeError(
                f"GHL {path} returned non-object JSON: {type(data).__name__}"
            )
        return data

    async def notify(
        self, lead: dict[str, Any], venue: dict[str, Any]
    ) -> dict[str, Any]:
        first, last = _split_name(str(lead.get("name", "")))

        contact_payload: dict[str, Any] = {
            "firstName": first,
            "phone": str(lead.get("phone", "")),
            "locationId": self._location_id,
            "source": "AI Receptionist Demo",
            "tags": ["ai-receptionist", str(venue.get("name", ""))],
        }
        if last:
            contact_payload["lastName"] = last
        if lead.get("email"):
            contact_payload["email"] = str(lead["email"])

        contact_response = await self._post("/contacts/", contact_payload)
        contact_block = contact_response.get("contact") or contact_response
        contact_id = (
            contact_block.get("id")
            if isinstance(contact_block, dict)
            else None
        )
        if not contact_id:
            raise RuntimeError("GHL contact create returned no id")

        owner = venue.get("owner") or {}
        pipeline_id = (
            self._default_pipeline_id
            or (owner.get("ghl_pipeline_id") if isinstance(owner, dict) else None)
            or ""
        )
        stage_id = (
            self._default_stage_id
            or (owner.get("ghl_stage_id") if isinstance(owner, dict) else None)
            or ""
        )

        opportunity_name = (
            f"{lead.get('name', 'Unknown')} — {lead.get('service', 'booking')}"
        )
        opportunity_payload: dict[str, Any] = {
            "pipelineId": pipeline_id,
            "pipelineStageId": stage_id,
            "contactId": contact_id,
            "name": opportunity_name,
            "status": "open",
            "monetaryValue": 0,
            "locationId": self._location_id,
        }

        opp_response = await self._post("/opportunities/", opportunity_payload)
        opp_block = opp_response.get("opportunity") or opp_response
        opportunity_id = (
            opp_block.get("id") if isinstance(opp_block, dict) else None
        )

        return {
            "status": "delivered",
            "channel": "ghl",
            "contact_id": contact_id,
            "opportunity_id": opportunity_id,
        }
