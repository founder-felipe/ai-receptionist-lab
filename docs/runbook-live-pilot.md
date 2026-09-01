# Live-pilot runbook

How to take this repo from the zero-credential mock demo to a **single-venue
live pilot**: an ElevenLabs voice agent, five n8n webhook tools, and a
GoHighLevel (GHL) calendar as the booking backend.

This is the runbook that was actually followed for the one live integration
recorded in [`verification-2026-08-21.md`](./verification-2026-08-21.md),
generalised. Venue, persona, phone numbers and all provider IDs here are
placeholders — fill them in for your own tenant.

> **Scope.** Nothing in this runbook is required to run the demo. `make demo`
> works with zero credentials. This file only matters if you want to wire the
> voice + calendar path.

---

## 0. Secrets policy

Never paste `.env` values (`ELEVENLABS_API_KEY`, `GEMINI_API_KEY`,
`GHL_API_KEY`, `N8N_API_KEY`, `TWILIO_*`) into this file or any other tracked
file. Resource **IDs** (agent, calendar, workflow, location) are not secrets,
but this repo keeps them as placeholders anyway so a fork never inherits
someone else's tenant.

---

## 1. IDs and URLs — fill these in

| Item | Value | Notes |
|---|---|---|
| GHL calendar name | `Barbershop DEMO Calendar (not a live diary)` | Label it visibly as a demo calendar so nobody mistakes it for the venue's real diary. |
| GHL calendar ID | `<CALENDAR_ID>` | |
| GHL location ID | `<LOCATION_ID>` | |
| GHL credential (in n8n) | `<CREDENTIAL_ID>` | Stored in the n8n credential store, never in the exported workflow JSON. |
| n8n workflow name | `[Barber Demo] Handle Agent Tools` | |
| n8n workflow ID | `<WORKFLOW_ID>` | |
| ElevenLabs agent name | `Barber Demo — Northbridge Barbers preview` | |
| ElevenLabs agent ID | `<AGENT_ID>` | |
| Widget URL | `https://elevenlabs.io/app/talk-to?agent_id=<AGENT_ID>` | Expect HTTP 200 once the agent exists. |
| Telephony number (optional) | `<PHONE_NUMBER>` | Only needed for the phone leg; the widget path works without it. |

Agent privacy settings to verify with a live `GET` after creation:

- `platform_settings.privacy.record_voice: false` — this is the load-bearing
  setting: no audio is captured at all, so the deployment is transcript-only.
  The other privacy fields (`delete_audio`, `retention_days`) only govern
  audio that was recorded in the first place, so they are moot here — do not
  cite them as the privacy guarantee.
- `platform_settings.guardrails` — the reference config in
  `agents/elevenlabs/barber-agent-config.json` enables ten toggles: `focus`,
  `prompt_injection`, `synthetic_voice` (`trigger_action: end_call`), and the
  seven `content` moderation categories (`sexual`, `violence`, `harassment`,
  `self_harm`, `profanity`, `religion_or_politics`,
  `medical_and_legal_information`), each `threshold: "medium"` with
  `trigger_action: end_call`. These default to **off/unconfigured** in the
  ElevenLabs API, so they must be set explicitly.

---

## 2. Webhook contract

Import [`../n8n/barber-demo-handle-agent-tools.json`](../n8n/barber-demo-handle-agent-tools.json)
into your n8n instance. It lands inactive; attach your own GHL credential to
the placeholdered credential slots, then activate.

Base: `https://YOUR_N8N_HOST/webhook/`

| Agent tool | Path | Body |
|---|---|---|
| `check_availability` | `barber-demo-check-availability` | `{date}` |
| `book_appointment` | `barber-demo-book-appointment` | `{name, phone, slot, service, notes}` |
| `find_appointment` | `barber-demo-find-appointment` | `{phone}` |
| `cancel_appointment` | `barber-demo-cancel-appointment` | `{appointmentId, phone}` |
| `reschedule_appointment` | `barber-demo-reschedule-appointment` | `{appointmentId, new_slot, phone}` |

Curl template (these are public webhook URLs — no secret needed):

```bash
curl -s -X POST https://YOUR_N8N_HOST/webhook/barber-demo-check-availability \
  -H "Content-Type: application/json" -d '{"date":"2026-08-22"}'
```

Two contract rules the workflow enforces, both worth re-checking after any edit:

- **Machine time, not spoken time.** `check_availability` returns each slot as
  both a spoken `display` string and a machine `start` ISO timestamp.
  `book_appointment` / `reschedule_appointment` must receive the ISO value.
- **Cancel never deletes.** The cancel path sets
  `appointmentStatus: cancelled` and leaves the event in place
  (`deleted: false`), so the audit trail survives.

### Retry hardening

All 15 `httpRequest` nodes that call the GHL API carry
`retryOnFail: true, maxTries: 3, waitBetweenTries: 400` (ms). This is not
decoration — see the transient-401 bug in the verification log. Verify after
any workflow edit:

```bash
python3 -c "
import json
d = json.load(open('n8n/barber-demo-handle-agent-tools.json'))
http = [n for n in d['nodes'] if n.get('type') == 'n8n-nodes-base.httpRequest']
ok = [n for n in http
      if n['parameters']['options'].get('retryOnFail') is True
      and n['parameters']['options'].get('maxTries') == 3
      and n['parameters']['options'].get('waitBetweenTries')]
print(f'{len(ok)}/{len(http)} http nodes retry-hardened; {len(d[\"nodes\"])} nodes total')
"
# Expected: 15/15 http nodes retry-hardened; 76 nodes total
```

---

## 3. SMS gate

Default is **`sms_enabled=false`** on all three guarded SMS gates (book,
cancel, reschedule) inside the workflow. Both provider nodes (Twilio and GHL)
are additionally `disabled: true` as defence in depth. Nothing in this repo
sends an SMS until you deliberately flip both layers.

To enable, in order:

1. Provision a number with your telephony provider and import it into
   ElevenLabs.
2. Assign it to the agent; confirm with two independent reads — the agent's
   embedded `phone_numbers` array, and `GET /v1/convai/phone-numbers`.
3. Place one real test call to confirm the assignment end-to-end. API-level
   assignment is not proof that audio flows.
4. Add the SMS provider credential in n8n (a human enters credentials — never
   an agent), point the SMS node at the same number, flip `sms_enabled=true`
   on all three gates, and send one test SMS to your own phone.
5. Update the agent's spoken SMS line off its "not live yet" placeholder.
6. Re-run the GO/NO-GO checklist in §5 with the phone and SMS legs included.

Until step 4 the agent's spoken line stays: *"You'll get a text confirmation
once our SMS line is live."* The widget is the only end-to-end-tested path
until then.

---

## 4. Compliance checklist

Written for an Australian single-venue pilot. Adapt to your jurisdiction —
this is an engineering checklist, not legal advice.

- [ ] The first message discloses that the caller is talking to an AI, and
      says **"transcribed"**, never "recorded", unless audio really is
      retained.
- [ ] `record_voice: false` confirmed by a live `GET`, not by the config file.
- [ ] The agent prompt never claims an integration with the venue's existing
      booking software. Grep for it (§6) — a first draft named the product
      three times purely to instruct the agent *not* to claim it, which is
      exactly the kind of string that leaks into a spoken turn.
- [ ] Cancel uses `appointmentStatus: cancelled`, never `DELETE`.
- [ ] Caller identity handling is phone + name + explicit confirmation, and is
      labelled demo-grade in the prompt. It is **not** production caller
      authentication.
- [ ] Platform guardrails enabled and live-verified (§1).

---

## 5. GO/NO-GO checklist

- [ ] All 5 webhooks curl-green, each with a fresh read-back from the calendar
      API (not just an HTTP 200 from n8n).
- [ ] Widget URL returns 200.
- [ ] Simulated-conversation suite passes, including the ISO-not-spoken
      assertion.
- [ ] At least one **real** (non-simulated) conversation completes book +
      reschedule + cancel, each confirmed by a fresh calendar read.
- [ ] Calendar is visibly labelled as a demo calendar.
- [ ] Honesty grep clean (§6).
- [ ] Evidence written to a dated verification file.

Phone and SMS are upgrades, not gates — the widget path can be fully proven
without them.

---

## 6. Honesty grep

Two checks against the agent's live prompt body: one negative (never names
the venue's existing booking software), one positive (the intended persona
and business name actually landed, rather than a rename silently reverting).

```bash
PROMPT=$(python3 -c "import json; print(json.load(open('agents/elevenlabs/barber-agent-config.json'))['conversation_config']['agent']['prompt']['prompt'])")

echo "$PROMPT" | grep -icE "<their booking product name>"
# Expected: 0

echo "$PROMPT" | grep -c "Northbridge Barbers\|Sage"
# Expected: >0
```

Run the same greps against a fresh live `GET` of the agent, not only against
the file — a PATCH that omits `prompt.tools` will leave the five tool
`description` fields stale while `prompt.prompt` looks correct.

---

## 7. Known limits

- Single venue at a time (`VENUE_CONFIG_PATH`). No multi-tenancy.
- No inbound-call auth, rate limiting, or queueing. Single-process SSE bus.
- `find_appointment` fans out one detail request per calendar event; on a busy
  calendar that is a linear scan, retry-hardened but not paginated.
- The n8n Set node is pinned to `typeVersion` 3.4. Newer n8n cores accept 3.5;
  older installed node packages do not, and the failure surfaces as
  `Cannot read properties of undefined (reading 'execute')` at activation
  time.
- Unary array/string conditions in IF nodes need `singleValue: true` when the
  workflow is created through the raw REST API, which skips n8n's normal
  auto-sanitisation.
