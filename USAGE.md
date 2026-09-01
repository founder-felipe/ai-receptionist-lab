# USAGE — AI Receptionist Demo

Field guide: plain English about what this is, four ways to drive it, and the open loose ends.

For the quickstart, architecture and claim classes, see [`README.md`](./README.md) and [`CLAIMS.md`](./CLAIMS.md).

---

## 1. What this demo is

**The idea.** An AI receptionist answers a venue's missed and after-hours calls, captures the booking enquiry, sends the venue's own booking link by SMS, and notifies the owner — without asking the venue to change booking systems. This repo is a runnable demonstration of that flow.

**Who it's for.** Small appointment-based venues — cafes, restaurants, barbers, salons — losing bookings because the phone rings out while staff are with a customer, or after close.

**What it visually proves in ~15 seconds.** Click "Ring the venue", and a single browser page plays out the entire journey:

1. Phone icon pulses (missed call coming in).
2. Transcript card streams the receptionist conversation.
3. SMS card slides in showing the booking link addressed to the caller.
4. Owner-notification card lights up (mock card in MOCK; real GHL + email in LIVE).
5. Leads table appends a row with a green dot.

**What it is NOT.**

- Not a provisioned phone product — there is no telephony number on the inbound leg by default.
- Not multi-tenant — one venue at a time via `VENUE_CONFIG_PATH`.
- Not a calendar booking system — it sends a booking link, it doesn't reserve a slot.
- Not production-hardened — no auth, no rate limiting, single-process SSE bus.

The point is to make the experience legible end to end in one screen, with no credentials in the way. The live voice + calendar path is documented separately in [`docs/runbook-live-pilot.md`](./docs/runbook-live-pilot.md).

---

## 2. Practical guide — how to use it

### Use case A — Record a screen capture

The fastest path, and how `docs/demo.gif` was made. Mock mode is **deterministic**: every take ends in CLOSE with all four panels lit and one green-dot lead row.

```bash
make install     # once
make demo
```

Then in a browser:

1. Open <http://localhost:8000/demo>.
2. Browser zoom to 110% so the panels read well on mobile-viewed video.
3. Scenario dropdown → pick **"Happy path — Saturday fade"**.
4. Click **Ring the venue**.
5. Click **Play scenario** — the script runs on a 1.2-second cadence.
6. Record the screen (OBS, QuickTime, or a headless Playwright `recordVideo` context). 1080p at 30fps is plenty.
7. Hit **Reset demo** between takes. Resets the conversation + truncates `data/leads.json`.

Two more scenarios live in `static/scenarios.json` ("Indecisive caller", "Out of scope question").

### Use case B — Drive it by hand for a specific venue

Same boot, different driving. Use the **free-type input** under the transcript instead of "Play scenario", so the conversation matches a particular venue.

Setup once:

1. Copy `venues/northbridge-barbers.yaml` to e.g. `venues/<their-slug>.yaml`.
2. Edit: `name`, `phone`, `booking_link`, `services` (their actual services list), `hours`.
3. Either: `export VENUE_CONFIG_PATH=venues/<their-slug>.yaml` before `make demo`, or set it in `.env`.

During the call:

- Each panel maps to one step: the receptionist holding the caller and capturing the enquiry; the SMS the caller would receive with the booking link; the notification landing in the venue's inbox or CRM; the lead logged for the record.
- Type caller utterances that sound like that venue's actual callers.

### Use case C — Live mode against real integrations

The same FastAPI process runs, with the mock adapters swapped for live ones. Nothing here has ever been run against a venue's real customers.

Prereqs:

1. **Gemini live.** Set `GEMINI_API_KEY` in `.env`.
2. **GHL live.** Set in `.env`:
   ```env
   NOTIFY_ADAPTER=mock,ghl,email
   GHL_API_KEY=<your private-integration key>
   GHL_LOCATION_ID=<the sub-account id>
   GHL_PIPELINE_ID=<the pipeline id>
   GHL_STAGE_ID=<the first stage id>
   ```
3. **SMTP live.** Pick a sending mailbox; set `SMTP_HOST / SMTP_USER / SMTP_PASS / SMTP_FROM / SMTP_TO_OWNER` in `.env`.
4. **External URL.** Open a tunnel:
   ```bash
   <your tunnel tool> http 8000
   ```
   Note the `https://YOUR_PUBLIC_TUNNEL` URL; set `PUBLIC_BASE_URL` in `.env`.

Then plumb the telephony side:

5. **n8n.** Import `n8n/barber-demo-handle-agent-tools.json` into <https://YOUR_N8N_HOST>. It lands inactive. Attach your own credentials to the placeholdered credential slots in the n8n credential store, paste your `PUBLIC_BASE_URL` where the workflow calls back into this app, then activate. Full wiring steps: [`docs/runbook-live-pilot.md`](./docs/runbook-live-pilot.md).
6. **ElevenLabs agent.** Create your own agent from `agents/elevenlabs/barber-agent-config.json`. Its tool URLs ship as placeholders — PATCH the agent to point them at your live `PUBLIC_BASE_URL` before testing, and include `prompt.tools` in the PATCH or the five tool `description` fields will stay stale.
7. **Phone leg (optional).** Provision a telephony number, wire it to the voice agent, and point the provider's no-answer status callback at the n8n `/webhook/receptionist-missed-call` endpoint. A real missed call then drives the whole flow.

Smoke test sequence after wiring:

- Run a scenario in the browser, confirm the GHL contact + opportunity appear in their sub-account.
- Confirm the email lands in the owner inbox.
- Test the voice widget (or the real phone leg if wired) and confirm the n8n execution log shows the right branches firing.

### Use case D — Develop further

```bash
make test     # 20 unit + e2e tests, no credentials needed
make lint     # ruff (whole repo) + mypy --strict on adapters/base.py
```

Where to extend:

- **New SMS / notify / store backend.** Drop a class in `adapters/` implementing the matching ABC, add its token to the `_VALID_*` set in `config.py`, register it in `adapters/__init__.py` factory. No other code changes.
- **New venue.** Copy `venues/northbridge-barbers.yaml`, edit, point `VENUE_CONFIG_PATH` at it.
- **New canned scenario.** Append to `static/scenarios.json`. Shows up in the dropdown on next page load.
- **New conversation state.** Add to `ConversationState` enum in `state_handler.py`, update `_next_state` transition logic, add row to `_CAPTURE_STATES` if it's reprompt-eligible. Update `prompts/receptionist_system.md` so the brain knows about the new capture target.
- **Brand tweak.** All visual tokens are in `static/brand.css` as CSS custom properties — change a few values, refresh.

The single source of truth for the receptionist persona is `prompts/receptionist_system.md`. Both the Gemini brain and the ElevenLabs agent are wired to it.

---

## 3. Loose ends — what still needs testing

Surfaced during the build but not closed. Each row is a discrete work item.

| Loose end | Why it matters | How to test |
|---|---|---|
| Live Gemini sometimes misses `capture_field("service", …)` on the opener, so handoff fires before the booking captures. | Derails a live run; mock mode is unaffected. | Run live with `GEMINI_API_KEY` set, ring, utter "I want a fade". Inspect `data/leads.json` — if `service` is `null` and `needs_followup` is `true`, you hit the bug. Mitigation candidates: strengthen `prompts/receptionist_system.md`; switch `tool_config` mode to `ANY` for the first capture turn; migrate `agents/receptionist_agent.py` from the deprecated `google.generativeai` to the new `google.genai` SDK. |
| Live CRM contact + opportunity create | Billable, and it creates a real CRM record. Never run end to end. | `NOTIFY_ADAPTER=mock,ghl` plus full credentials → run a scenario → open the sub-account and verify the contact and opportunity. Remove the test record afterwards. |
| Live SMTP email to the owner inbox | A real email, never sent. | `NOTIFY_ADAPTER=mock,ghl,email` plus full SMTP credentials → run a scenario → check the configured `SMTP_TO_OWNER` mailbox. |
| n8n workflow import | Confirms the export is importable, not just parseable. | `curl -X POST https://YOUR_N8N_HOST/api/v1/workflows -H "X-N8N-API-KEY: $N8N_API_KEY" --data @n8n/barber-demo-handle-agent-tools.json` → confirm 200 and that the workflow appears inactive in the UI. Do **not** activate until credentials and the public tunnel are wired. |
| Voice-agent tool URLs are placeholders | The agent cannot call back into the FastAPI app until they are set. | After `<your tunnel tool> http 8000`, PATCH the agent to swap the placeholder host for your live `PUBLIC_BASE_URL`. Then test the dashboard widget end to end. |
| HTMX SSE behaviour on page reload | A glitch mid-call would be visible to anyone driving the demo. | Reload `/demo` mid-call, verify the session is cleanly dropped and that clicking "Ring the venue" starts a fresh one. |
| Five-panel layout on a narrow viewport | The demo has never been opened on a phone. | Resize the browser to <600px wide, run a scenario, check the panels stack cleanly without overlap or clipping. |
| Reduced-motion users | Accessibility: animations should respect the OS preference. | Turn on the OS "reduce motion" setting, reload `/demo`, and verify the pulse and slide-in animations are suppressed. `brand.css` has a `@media (prefers-reduced-motion)` block; confirm it actually triggers. |
| Cross-browser SSE | Verified in Chromium (curl) only. | Open `/demo` in Safari + Firefox, run a scenario, confirm transcript + SMS cards still stream in. |
| Long-conversation stability | History grows per turn; only 4-turn paths have been tested. | Drive the caller off-topic for 5–10 turns before booking, and verify the 2-reprompt handoff still fires and the model does not drift or hallucinate captured fields. |
| Real phone number on the inbound leg | Out of scope for the default configuration; needed before any real deployment. | Provision a telephony number, wire its voice URL to the voice agent, point the no-answer status callback at the n8n `/webhook/receptionist-missed-call` endpoint, and dial in. |
| `data/leads.json` corruption under concurrent writes | Single-process is fine for the demo; multi-venue would not be. | Hit `/ring` and `/turn` from two browser tabs simultaneously. `JSONStore` uses atomic temp + rename, so it should hold, but it has never been verified under contention. |

When you close one, delete the row.

---

## Quick reference

| Want to… | Command / file |
|---|---|
| Boot the demo | `make demo` |
| Run tests | `make test` |
| Lint | `make lint` |
| Reset between takes | "Reset demo" button on `/demo`, or `POST /reset` |
| Inspect saved leads | `cat data/leads.json` or `GET /leads` |
| Change venue | `VENUE_CONFIG_PATH` env var |
| Add a scenario | edit `static/scenarios.json` |
| Tune the receptionist | edit `prompts/receptionist_system.md` |
| Wire a new adapter | `adapters/<your_adapter>.py` + `config.py` `_VALID_*` set + `adapters/__init__.py` factory |
