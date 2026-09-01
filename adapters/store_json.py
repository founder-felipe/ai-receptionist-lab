"""JSON-file lead store.

Writes to ``data/leads.json`` via aiofiles using a temp-file + atomic
rename so a crashed write never corrupts the existing log. Each lead is
stamped with a ``id`` (uuid4) and ``created_at`` (Australia/Perth ISO)
if the caller didn't supply them already.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import ClassVar
from uuid import uuid4
from zoneinfo import ZoneInfo

import aiofiles

from adapters.base import StoreAdapter

PERTH_TZ = ZoneInfo("Australia/Perth")


class JSONStore(StoreAdapter):
    """Append-only JSON-array lead store, safe for a single-process demo."""

    name: ClassVar[str] = "json"  # type: ignore[misc]

    #: Default sink path. Tests monkeypatch this to ``tmp_path``.
    DEFAULT_PATH: ClassVar[Path] = Path("data/leads.json")

    def __init__(self, path: Path | str | None = None) -> None:
        self.path: Path = Path(path) if path is not None else self.DEFAULT_PATH

    def _ensure_dir(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def _read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        async with aiofiles.open(self.path, "r", encoding="utf-8") as fh:
            raw = await fh.read()
        if not raw.strip():
            return []
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError(f"{self.path} is not a JSON array")
        return data

    async def _write_all(self, leads: list[dict]) -> None:
        self._ensure_dir()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        async with aiofiles.open(tmp, "w", encoding="utf-8") as fh:
            await fh.write(json.dumps(leads, indent=2, default=str))
        os.replace(tmp, self.path)

    async def save_lead(self, lead: dict) -> str:
        record = dict(lead)
        if "id" not in record:
            record["id"] = str(uuid4())
        if "created_at" not in record:
            record["created_at"] = datetime.now(tz=PERTH_TZ).isoformat()
        leads = await self._read_all()
        leads.append(record)
        await self._write_all(leads)
        return str(record["id"])

    async def list_leads(self, limit: int = 50) -> list[dict]:
        leads = await self._read_all()
        leads_sorted = sorted(
            leads,
            key=lambda lead: lead.get("created_at", ""),
            reverse=True,
        )
        return leads_sorted[:limit]
