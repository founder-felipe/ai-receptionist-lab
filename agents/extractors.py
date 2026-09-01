"""Field extractors / normalisers used by the state machine.

These helpers clean values produced by Gemini's ``capture_field`` calls
before they're stored on the conversation or persisted as a lead.

Pure functions only — no I/O, no global state. Easy to unit-test.
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import dateparser
import phonenumbers


# ---------------------------------------------------------------------------
# Phone
# ---------------------------------------------------------------------------

def normalise_phone(raw: str | None, region: str = "AU") -> str | None:
    """Return E.164-formatted phone string, or ``None`` if invalid.

    Accepts free-form caller input ("0491 570 006", "+61 491 570 006",
    "0400-000-000"). Uses ``phonenumbers`` with ``region`` as the default
    country code when no ``+`` prefix is present.
    """
    if not raw or not isinstance(raw, str):
        return None
    try:
        parsed = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(
        parsed, phonenumbers.PhoneNumberFormat.E164
    )


# ---------------------------------------------------------------------------
# Datetime
# ---------------------------------------------------------------------------

def normalise_datetime(
    raw: str | None,
    tz: str = "Australia/Perth",
) -> datetime | None:
    """Parse free-form datetime ("Saturday 2pm", "tomorrow at 10") to aware dt.

    Uses ``dateparser`` with ``RELATIVE_BASE = now()`` in ``tz`` and
    ``PREFER_DATES_FROM=future`` so "Saturday" always points at the next
    upcoming Saturday rather than the last one.

    Returns timezone-aware datetime in ``tz``, or ``None`` if unparseable.
    """
    if not raw or not isinstance(raw, str):
        return None

    zone = ZoneInfo(tz)
    base = datetime.now(zone)

    parsed = dateparser.parse(
        raw,
        settings={
            "RELATIVE_BASE": base,
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": tz,
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if parsed is None:
        return None
    # Ensure tz-aware in the requested zone.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    else:
        parsed = parsed.astimezone(zone)
    return parsed


# ---------------------------------------------------------------------------
# Service matching
# ---------------------------------------------------------------------------

def match_service(raw: str | None, services: list[str]) -> str | None:
    """Case-insensitive substring match against the venue's service list.

    Returns the canonical service name from ``services`` (preserving its
    capitalisation), or ``None`` if no service matches.

    Match rules:
    - exact case-insensitive equality wins first
    - then: canonical service name is a substring of caller utterance
      ("I'd love a fade" → matches "Fade")
    - then: caller utterance is a substring of a canonical service
      ("skin" → matches "Skin Fade")
    """
    if not raw or not isinstance(raw, str) or not services:
        return None
    needle = raw.strip().lower()
    if not needle:
        return None

    # Pass 1: exact equality.
    for svc in services:
        if svc.lower() == needle:
            return svc
    # Pass 2: canonical service appears in caller utterance.
    for svc in services:
        if svc.lower() in needle:
            return svc
    # Pass 3: caller utterance appears in canonical service.
    for svc in services:
        if needle in svc.lower():
            return svc
    return None


# ---------------------------------------------------------------------------
# Venue hours
# ---------------------------------------------------------------------------

_DAY_KEYS = {
    0: ("mon", "mon-fri", "weekdays"),
    1: ("tue", "mon-fri", "weekdays"),
    2: ("wed", "mon-fri", "weekdays"),
    3: ("thu", "mon-fri", "weekdays"),
    4: ("fri", "mon-fri", "weekdays"),
    5: ("sat", "weekend"),
    6: ("sun", "weekend"),
}


def _parse_time_range(raw: Any) -> tuple[time, time] | None:
    """Parse "09:00-19:00" → (time(9,0), time(19,0))."""
    if not isinstance(raw, str):
        return None
    s = raw.strip().lower()
    if s in {"closed", "", "none"}:
        return None
    if "-" not in s:
        return None
    a, b = s.split("-", 1)
    try:
        ah, am = (int(x) for x in a.strip().split(":", 1))
        bh, bm = (int(x) for x in b.strip().split(":", 1))
    except ValueError:
        return None
    return time(ah, am), time(bh, bm)


def within_hours(when: datetime, hours: dict[str, Any]) -> bool:
    """True if ``when`` falls inside the venue's opening hours.

    ``hours`` is the dict loaded from the venue YAML — e.g.
    ``{"mon-fri": "09:00-19:00", "sat": "08:00-17:00", "sun": "closed"}``.

    Lookup order per day-of-week: specific day key (``mon``, ``tue``…),
    then grouped keys (``mon-fri``, ``weekdays``, ``weekend``). First
    match wins. Missing day → closed.
    """
    if not hours:
        return False
    keys = _DAY_KEYS.get(when.weekday(), ())
    raw_range: Any = None
    for k in keys:
        if k in hours:
            raw_range = hours[k]
            break
    if raw_range is None:
        return False
    parsed = _parse_time_range(raw_range)
    if parsed is None:
        return False
    open_t, close_t = parsed
    t = when.time().replace(second=0, microsecond=0)
    return open_t <= t <= close_t
