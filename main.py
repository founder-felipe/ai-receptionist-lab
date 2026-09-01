"""FastAPI entry point — routes, lifespan, SSE bus.

Single-process demo server. Wires:
- the adapter factory (mock / live SMS, notify, store)
- the Gemini-driven receptionist agent (with Stub fallback)
- the per-call SSE bus (anyio memory streams keyed by ``call_id``)
- side-effect dispatch on state-machine events (sms / notify / store)

The Jinja templates and static assets live under ``templates/`` and
``static/`` (owned by W3.B); this module mounts them but does not write
to them.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import anyio
import yaml
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

import state_handler
from adapters import get_notify, get_sms, get_store
from agents.receptionist_agent import GeminiReceptionist, StubReceptionist
from config import settings
from state_handler import advance, new_conversation

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"
PROMPT_PATH = ROOT / "prompts" / "receptionist_system.md"
SCENARIOS_PATH = STATIC_DIR / "scenarios.json"

IDLE_TIMEOUT_SECONDS = 5 * 60  # tear down idle SSE buses after 5 min
SWEEPER_INTERVAL_SECONDS = 60


# ---------------------------------------------------------------------------
# SSE bus state (module-level, single-process demo)
# ---------------------------------------------------------------------------

# call_id -> send stream (publisher side)
event_buses: dict[str, MemoryObjectSendStream] = {}
# call_id -> receive stream (consumer side, handed to /events generator)
_event_receivers: dict[str, MemoryObjectReceiveStream] = {}
# call_id -> last-activity unix timestamp
_bus_last_activity: dict[str, float] = {}

# Global broadcast bus for `reset` events — every connected client clears UI.
_broadcast_send: MemoryObjectSendStream | None = None
_broadcast_recv: MemoryObjectReceiveStream | None = None


def _now() -> float:
    return time.time()


def _create_bus(call_id: str) -> None:
    send, recv = anyio.create_memory_object_stream(max_buffer_size=256)
    event_buses[call_id] = send
    _event_receivers[call_id] = recv
    _bus_last_activity[call_id] = _now()


async def _close_bus(call_id: str) -> None:
    send = event_buses.pop(call_id, None)
    recv = _event_receivers.pop(call_id, None)
    _bus_last_activity.pop(call_id, None)
    if send is not None:
        await send.aclose()
    if recv is not None:
        await recv.aclose()


async def _publish(call_id: str, event: dict[str, Any]) -> None:
    """Push an event onto a per-call bus (no-op if bus is gone)."""
    send = event_buses.get(call_id)
    if send is None:
        return
    try:
        await send.send(event)
        _bus_last_activity[call_id] = _now()
    except (anyio.BrokenResourceError, anyio.ClosedResourceError):
        # Client disconnected — drop the bus.
        await _close_bus(call_id)


async def _idle_sweeper() -> None:
    """Background task: close SSE buses idle > IDLE_TIMEOUT_SECONDS."""
    while True:
        try:
            await asyncio.sleep(SWEEPER_INTERVAL_SECONDS)
            cutoff = _now() - IDLE_TIMEOUT_SECONDS
            stale = [cid for cid, ts in _bus_last_activity.items() if ts < cutoff]
            for cid in stale:
                logger.info("sweeping idle SSE bus call_id=%s", cid)
                await _close_bus(cid)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("idle sweeper iteration failed")


# ---------------------------------------------------------------------------
# Venue loader + agent factory
# ---------------------------------------------------------------------------

def _load_venue(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    with open(p, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _load_system_prompt() -> str:
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text(encoding="utf-8")
    return "You are a warm, efficient receptionist."


def _build_agent(venue: dict[str, Any]):  # type: ignore[no-untyped-def]
    """Return a Gemini receptionist, or a Stub if no key / MOCK mode / init fails."""
    if settings.mode == "MOCK":
        logger.info("MOCK mode — using StubReceptionist (no Gemini API calls)")
        return StubReceptionist(venue=venue)
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        logger.warning("GEMINI_API_KEY not set — using StubReceptionist")
        return StubReceptionist(venue=venue)
    try:
        return GeminiReceptionist(
            api_key=api_key,
            system_prompt=_load_system_prompt(),
            venue=venue,
            model=settings.GEMINI_MODEL,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini init failed (%s) — falling back to Stub", exc)
        return StubReceptionist(venue=venue)


# Override hook for tests: monkeypatch this to substitute a Stub.
_agent_factory = _build_agent


def _redact(value: str) -> str:
    if not value:
        return "<unset>"
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}…{value[-2:]}"


def _scenario_count() -> int:
    if not SCENARIOS_PATH.exists():
        return 0
    try:
        data = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
        return len(data) if isinstance(data, list) else 0
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------------------
# Event side-effect dispatch
# ---------------------------------------------------------------------------

async def _dispatch_side_effects(
    app: FastAPI,
    call_id: str,
    events: list[dict[str, Any]],
) -> None:
    """For each state-machine event: publish it, then fire any adapter
    side-effects and publish their results as follow-up events.
    """
    sms = app.state.sms
    notifiers: list = app.state.notifiers
    store = app.state.store

    for event in events:
        await _publish(call_id, event)
        etype = event.get("type")
        data = event.get("data") or {}

        if etype == "sms_sent":
            to = data.get("to") or ""
            body = data.get("body") or ""
            try:
                result = await sms.send(to, body)
                await _publish(
                    call_id,
                    {
                        "type": "sms_result",
                        "data": {
                            "channel": getattr(sms, "name", "sms"),
                            "status": result.get("status", "sent"),
                            "message_sid": result.get("message_sid"),
                        },
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("SMS send failed")
                await _publish(
                    call_id,
                    {
                        "type": "sms_result",
                        "data": {
                            "channel": getattr(sms, "name", "sms"),
                            "status": "error",
                            "error": str(exc),
                        },
                    },
                )

        elif etype == "lead_saved":
            lead = data.get("lead") or {}
            try:
                lead_id = await store.save_lead(lead)
                await _publish(
                    call_id,
                    {
                        "type": "store_result",
                        "data": {
                            "channel": getattr(store, "name", "store"),
                            "status": "saved",
                            "lead_id": lead_id,
                        },
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("store save failed")
                await _publish(
                    call_id,
                    {
                        "type": "store_result",
                        "data": {
                            "channel": getattr(store, "name", "store"),
                            "status": "error",
                            "error": str(exc),
                        },
                    },
                )

        elif etype == "owner_notified":
            lead = data.get("lead") or {}
            venue = data.get("venue") or {}
            if notifiers:
                results = await asyncio.gather(
                    *(n.notify(lead, venue) for n in notifiers),
                    return_exceptions=True,
                )
                for n, result in zip(notifiers, results):
                    if isinstance(result, Exception):
                        await _publish(
                            call_id,
                            {
                                "type": "notify_result",
                                "data": {
                                    "channel": getattr(n, "name", "notify"),
                                    "status": "error",
                                    "error": str(result),
                                },
                            },
                        )
                    else:
                        payload = result if isinstance(result, dict) else {}
                        await _publish(
                            call_id,
                            {
                                "type": "notify_result",
                                "data": {
                                    "channel": getattr(n, "name", "notify"),
                                    "status": payload.get("status", "delivered"),
                                },
                            },
                        )

        elif etype == "CLOSE":
            await _close_bus(call_id)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Load venue
    venue = _load_venue(settings.VENUE_CONFIG_PATH)
    app.state.venue = venue

    # Adapters
    app.state.sms = get_sms()
    app.state.notifiers = get_notify()
    app.state.store = get_store()

    # Agent (Gemini or Stub)
    app.state.agent = _agent_factory(venue)

    # Global broadcast bus
    global _broadcast_send, _broadcast_recv
    _broadcast_send, _broadcast_recv = anyio.create_memory_object_stream(
        max_buffer_size=64
    )

    # Idle sweeper
    sweeper = asyncio.create_task(_idle_sweeper())
    app.state.sweeper_task = sweeper

    # Startup banner
    sms_name = getattr(app.state.sms, "name", type(app.state.sms).__name__)
    notify_names = [
        getattr(n, "name", type(n).__name__) for n in app.state.notifiers
    ]
    store_name = getattr(app.state.store, "name", type(app.state.store).__name__)
    logger.warning(
        "=" * 60 + "\n"
        f"  AI Receptionist Demo — mode={settings.mode}\n"
        f"  venue: {venue.get('name', '?')}\n"
        f"  agent: {type(app.state.agent).__name__}\n"
        f"  sms:   {sms_name}\n"
        f"  notify:{notify_names}\n"
        f"  store: {store_name} -> {settings.STORE_PATH}\n"
        f"  gemini_key: {_redact(settings.GEMINI_API_KEY)}\n"
        f"  ghl_key:    {_redact(settings.GHL_API_KEY)}\n"
        f"  smtp_host:  {settings.SMTP_HOST or '<unset>'}\n"
        + "=" * 60
    )

    try:
        yield
    finally:
        sweeper.cancel()
        try:
            await sweeper
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        # Close all live buses
        for cid in list(event_buses.keys()):
            await _close_bus(cid)
        if _broadcast_send is not None:
            await _broadcast_send.aclose()
        if _broadcast_recv is not None:
            await _broadcast_recv.aclose()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="The Rise AI — Receptionist Demo", lifespan=lifespan)

# Mount static files if the directory exists (W3.B owns it; tolerate absence).
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/demo")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "favicon.ico"))


@app.get("/demo")
async def demo(request: Request):  # type: ignore[no-untyped-def]
    return templates.TemplateResponse(
        request,
        "demo.html",
        {
            "venue": app.state.venue,
            "mode": settings.mode,
            "scenario_count": _scenario_count(),
        },
    )


@app.post("/ring")
async def ring() -> JSONResponse:
    """Start a new conversation; emit the GREETING line + advance once."""
    convo = new_conversation(app.state.venue)
    _create_bus(convo.call_id)

    # Kick off the GREETING turn so the bus already has content for any
    # subscriber that races in.
    state, reply, events = await advance(convo, None, app.state.agent)
    await _dispatch_side_effects(app, convo.call_id, events)

    return JSONResponse(
        {"call_id": convo.call_id, "state": state.name, "reply": reply}
    )


@app.post("/turn")
async def turn(request: Request) -> JSONResponse:
    payload = await request.json()
    call_id = payload.get("call_id")
    utterance = payload.get("utterance") or ""
    if not call_id:
        raise HTTPException(status_code=400, detail="call_id required")

    convo = state_handler.conversations.get(call_id)
    if convo is None:
        raise HTTPException(status_code=404, detail="unknown call_id")

    state, reply, events = await advance(convo, utterance, app.state.agent)
    await _dispatch_side_effects(app, call_id, events)

    return JSONResponse({"reply": reply, "state": state.name})


def _render_event_html(etype: str, data: dict[str, Any]) -> str | None:
    """Render an SSE event as the HTML fragment HTMX injects via hx-sse-swap.

    Returns None for non-UI events (sms_result, store_result, handoff, CLOSE)
    which are logged as JSON and ignored by the browser.
    """
    env = templates.env
    if etype == "transcript_line":
        return env.get_template("partials/transcript_line.html").render(
            role=data.get("role") or "",
            content=data.get("content") or "",
        )
    if etype == "sms_sent":
        return env.get_template("partials/sms_card.html").render(
            to=data.get("to") or "",
            body=data.get("body") or "",
        )
    if etype == "notify_result":
        return env.get_template("partials/notify_card.html").render(
            channel=data.get("channel") or "",
            status=data.get("status") or "sent",
        )
    if etype == "owner_notified":
        lead = data.get("lead") or {}
        name = lead.get("name") or "?"
        service = lead.get("service") or "?"
        return env.get_template("partials/notify_card.html").render(
            channel=f"owner — {name} / {service}",
            status="sent",
        )
    if etype == "lead_saved":
        lead = data.get("lead") or {}
        return env.get_template("partials/lead_row.html").render(
            name=lead.get("name"),
            service=lead.get("service"),
            requested_time=lead.get("requested_time"),
            phone=lead.get("phone"),
            link_sent=bool(lead.get("link_sent")),
            needs_followup=bool(lead.get("needs_followup")),
            created_at=lead.get("created_at") or "—",
        )
    return None


@app.get("/events")
async def events(call_id: str) -> EventSourceResponse:
    """SSE stream of events for a single call_id."""
    recv = _event_receivers.get(call_id)
    if recv is None:
        raise HTTPException(status_code=404, detail="unknown call_id")

    async def event_generator() -> AsyncIterator[dict[str, Any]]:
        try:
            async for event in recv:
                etype = event.get("type", "message")
                data = event.get("data", {})
                html = _render_event_html(etype, data)
                if html is not None:
                    yield {"event": etype, "data": html}
                else:
                    yield {"event": etype, "data": json.dumps(data, default=str)}
                if etype == "CLOSE":
                    break
        except (anyio.ClosedResourceError, anyio.EndOfStream):
            return
        finally:
            # Receiver is one-shot; tear down whichever side is left.
            await _close_bus(call_id)

    return EventSourceResponse(event_generator())


@app.get("/leads")
async def leads(limit: int = 50) -> JSONResponse:
    store = app.state.store
    rows = await store.list_leads(limit)
    return JSONResponse(rows)


@app.post("/reset")
async def reset() -> JSONResponse:
    """Clear in-memory + JSON-store state. MOCK + json-store only."""
    if settings.STORE_ADAPTER not in ("json",) or settings.mode != "MOCK":
        raise HTTPException(
            status_code=403,
            detail=f"reset refused (store={settings.STORE_ADAPTER}, mode={settings.mode})",
        )

    state_handler.reset_all()

    # Close all live SSE buses
    for cid in list(event_buses.keys()):
        await _close_bus(cid)

    # Reset mock adapter in-memory logs so test takes are clean.
    try:
        from adapters.notify_mock import MockNotify
        from adapters.sms_mock import MockSMS

        MockSMS.sent_messages.clear()
        MockNotify.notifications.clear()
    except Exception:  # noqa: BLE001
        pass

    # Truncate the JSON leads file.
    store_path = Path(settings.STORE_PATH)
    if not store_path.is_absolute():
        store_path = ROOT / store_path
    try:
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text("[]", encoding="utf-8")
    except OSError as exc:
        logger.warning("could not truncate %s: %s", store_path, exc)

    # Broadcast reset to any listening UI clients.
    if _broadcast_send is not None:
        try:
            await _broadcast_send.send({"type": "reset", "data": {}})
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):
            pass

    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Dev entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",  # noqa: S104
        port=int(os.environ.get("PORT", "8000")),
        reload=True,
    )
