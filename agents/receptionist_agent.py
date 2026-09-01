"""Gemini-driven receptionist + stub used by tests.

``GeminiReceptionist`` wraps ``google.generativeai`` with a single
function-call tool — ``capture_field(field_name, value)`` — that the
model invokes 0..N times per turn to surface structured fields. After
the function-calling round it produces the spoken reply.

``StubReceptionist`` exposes the same async ``generate_reply`` interface
and returns canned replies keyed by ``ConversationState``. Tests and the
end-to-end suite use it so no real API key is required.

Both return:

```python
{
    "reply": str,                           # what the receptionist says
    "extracted": dict[str, str],            # fields captured this turn
    "want_state": ConversationState | None, # optional explicit hint
}
```

The state machine in ``state_handler.advance`` owns the actual
transition decision — ``want_state`` is purely advisory.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from state_handler import Conversation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gemini-backed implementation
# ---------------------------------------------------------------------------

class GeminiReceptionist:
    """Production receptionist powered by Gemini function-calling."""

    def __init__(
        self,
        api_key: str,
        system_prompt: str,
        venue: dict[str, Any],
        model: str = "gemini-2.5-flash",
    ) -> None:
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")

        # Import lazily so tests that only use StubReceptionist don't
        # need google-generativeai importable.
        import google.generativeai as genai  # noqa: WPS433  (lazy import is intentional)

        self._genai = genai
        self._api_key = api_key
        self._model_name = model
        self.venue = venue
        self.system_prompt = self._render_prompt(system_prompt, venue)

        genai.configure(api_key=api_key)

        # Single tool declaration: capture_field(field_name, value).
        self._capture_field_decl = {
            "name": "capture_field",
            "description": (
                "Record a structured field the caller has provided. "
                "Call once per field captured this turn."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "field_name": {
                        "type": "string",
                        "enum": [
                            "name",
                            "phone",
                            "service",
                            "requested_time",
                            "notes",
                        ],
                    },
                    "value": {"type": "string"},
                },
                "required": ["field_name", "value"],
            },
        }

        self._model = genai.GenerativeModel(
            model_name=self._model_name,
            system_instruction=self.system_prompt,
            tools=[{"function_declarations": [self._capture_field_decl]}],
        )

    # -- prompt rendering ---------------------------------------------------

    @staticmethod
    def _render_prompt(template: str, venue: dict[str, Any]) -> str:
        """Substitute ``{venue_*}`` placeholders in the system prompt.

        Uses safe ``str.replace`` rather than ``str.format`` to avoid
        choking on stray ``{`` characters inside the markdown.
        """
        services = ", ".join(venue.get("services", []) or [])
        hours = venue.get("hours", {}) or {}
        hours_str = ", ".join(f"{k}: {v}" for k, v in hours.items())
        return (
            template
            .replace("{venue_name}", str(venue.get("name", "the venue")))
            .replace("{venue_services}", services)
            .replace("{venue_hours}", hours_str)
        )

    # -- main entry point ---------------------------------------------------

    async def generate_reply(
        self,
        conversation: "Conversation",
        last_utterance: str | None,
    ) -> dict[str, Any]:
        """Produce reply + extracted fields for one turn.

        On any error (no network, quota, malformed response) returns a
        graceful fallback reply and an empty extracted dict so the state
        machine can re-prompt rather than crash.
        """
        try:
            return await self._generate(conversation, last_utterance)
        except Exception as exc:  # noqa: BLE001 — bounded fallback path
            logger.warning("Gemini call failed: %s", exc, exc_info=True)
            return {
                "reply": "One moment please...",
                "extracted": {},
                "want_state": None,
            }

    async def _generate(
        self,
        conversation: "Conversation",
        last_utterance: str | None,
    ) -> dict[str, Any]:
        # Build chat history in Gemini's role/parts shape.
        history: list[dict[str, Any]] = []
        for entry in conversation.history:
            role = "user" if entry["role"] == "user" else "model"
            history.append({"role": role, "parts": [{"text": entry["content"]}]})

        chat = self._model.start_chat(history=history)
        prompt = last_utterance or "(begin the call with your opening line)"

        response = chat.send_message(
            prompt,
            tool_config={"function_calling_config": {"mode": "AUTO"}},
        )

        extracted: dict[str, str] = {}
        reply_text = ""

        # Walk function calls; respond with empty tool results then ask for
        # the final spoken reply. We keep it single-shot (one round trip
        # of function calls) for predictability; Gemini almost always
        # emits all calls in the first response anyway.
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", []) or []:
                fn = getattr(part, "function_call", None)
                if fn is not None and getattr(fn, "name", "") == "capture_field":
                    args = dict(getattr(fn, "args", {}) or {})
                    field = str(args.get("field_name", "")).strip()
                    value = str(args.get("value", "")).strip()
                    if field and value:
                        extracted[field] = value
                text = getattr(part, "text", None)
                if text:
                    reply_text += text

        # If we got no spoken text (function-only response, or empty), nudge
        # Gemini for a reply. This catches both the "extracted but silent"
        # and "no extraction and silent" cases that otherwise reprompt.
        if not reply_text:
            try:
                follow = chat.send_message("Please give a one-sentence spoken reply to the caller now.")
                reply_text = (getattr(follow, "text", "") or "").strip()
            except Exception:  # noqa: BLE001
                reply_text = ""

        if not reply_text:
            reply_text = "Sorry — could you say that again?"

        return {
            "reply": reply_text.strip(),
            "extracted": extracted,
            "want_state": None,
        }


# ---------------------------------------------------------------------------
# Test stub
# ---------------------------------------------------------------------------

class StubReceptionist:
    """Deterministic stand-in for tests / offline demos.

    Configure via ``responses`` (keyed by ``ConversationState.name``) or
    pass an explicit per-instance override at construction. Falls back to
    a sensible default per state.
    """

    DEFAULT_RESPONSES: dict[str, dict[str, Any]] = {
        "GREETING": {
            "reply": "Northbridge Barbers, this is Sage — how can I help?",
            "extracted": {},
        },
        "CAPTURE_INTENT": {
            "reply": "Got it — a fade. When works for you?",
            "extracted": {"service": "Fade"},
        },
        "CAPTURE_DATETIME": {
            "reply": "Saturday 2pm — locked in. Can I grab your name and number?",
            "extracted": {"requested_time": "Saturday 2pm"},
        },
        "CAPTURE_CONTACT": {
            "reply": "Thanks Sam — sending the booking link now.",
            "extracted": {"name": "Sam", "phone": "0491570110"},
        },
        "SEND_LINK": {
            "reply": "Link's on its way to your phone now.",
            "extracted": {},
        },
        "CLOSE": {
            "reply": "All sorted — see you then.",
            "extracted": {},
        },
    }

    def __init__(
        self,
        responses: dict[str, dict[str, Any]] | None = None,
        venue: dict[str, Any] | None = None,
    ) -> None:
        self.responses = responses if responses is not None else dict(self.DEFAULT_RESPONSES)
        self.venue = venue or {}
        self.calls: list[tuple[str, str | None]] = []

    async def generate_reply(
        self,
        conversation: "Conversation",
        last_utterance: str | None,
    ) -> dict[str, Any]:
        state_name = conversation.state.name
        self.calls.append((state_name, last_utterance))
        canned = self.responses.get(state_name, {"reply": "", "extracted": {}})
        return {
            "reply": canned.get("reply", ""),
            "extracted": dict(canned.get("extracted", {})),
            "want_state": canned.get("want_state"),
        }
