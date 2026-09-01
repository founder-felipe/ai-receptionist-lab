# Verification log — live integration, 2026-08-21

**Claim legend.** Every statement in this file carries one of the classes used
in [`../CLAIMS.md`](../CLAIMS.md):

- **measured** — a number produced by an artifact that can be re-run or re-read.
- **verified** — a property confirmed by inspecting code, config, or an API response.
- **recorded-in-log** — it happened during the session this file documents, and
  the record below is the only evidence. **There is no reproducible runner in
  this repo for these.** Treat them as a log, not as a test suite.

Venue, caller and resource identifiers are anonymised. The live tenant was a
real barbershop; its name is not published.

All backend tests below ran against a live n8n workflow
(`[Barber Demo] Handle Agent Tools`) and a live GHL calendar explicitly named
`Barbershop DEMO Calendar (not a live diary)`. No secrets are reproduced.

---

## Wave 1 — backend curl tests with calendar read-back

**Class: recorded-in-log** for every result in this section. Each test issued a
webhook POST and then a *separate, fresh* read against the GHL API, so a
200 from n8n alone was never accepted as proof.

### 1. Availability

```
POST https://YOUR_N8N_HOST/webhook/barber-demo-check-availability
{"date":"2026-08-21"}
→ HTTP 200
{"available_slots":[{"display":"9:00 AM","start":"2026-08-21T09:00:00+08:00"}, ... 20 slots ...,
 {"display":"6:30 PM","start":"2026-08-21T18:30:00+08:00"}],"date":"2026-08-21","count":20, ...}
```

20 slots, 9:00 AM–6:30 PM (last 30-minute slot before the 7:00 PM close) —
matches `venues/northbridge-barbers.yaml` Mon–Fri hours (09:00–19:00) for a
Friday. Each slot carries both a spoken `display` and a machine ISO `start`.
**PASS.**

### 2. Booking

```
POST .../barber-demo-book-appointment
{"name":"Caller A","phone":"<PHONE_NUMBER>","slot":"2026-08-21T10:00:00+08:00","service":"Skin Fade","notes":""}
→ HTTP 200 {"success":true,"confirmation":"Booking confirmed! Caller A is booked for a Skin Fade at 2026-08-21T10:00:00+08:00. Our team has been notified."}
```

Fresh read-back (`GET /calendars/events`):

```
id: <APPOINTMENT_ID>
startTime: 2026-08-21T10:00:00+08:00 (matches request)
endTime:   2026-08-21T10:30:00+08:00
title:     "[Barber Demo] Caller A — Skin Fade"
appointmentStatus: confirmed
```

**PASS.**

### 3. Find

```
POST .../barber-demo-find-appointment
{"phone":"<PHONE_NUMBER>"}
→ HTTP 200 {"status":"found","appointments":[{"id":"<APPOINTMENT_ID>","start":"2026-08-21T10:00:00+08:00","display":"Fri, 21 Aug, 10:00 am","service":"Skin Fade","contact_name":"caller a"}]}
```

**PASS.**

The first attempt returned HTTP 200 with an empty body: the `Has Events?` IF
node threw a type-conversion error on its array `notEmpty` condition. Root-caused
from the n8n execution log, fixed by adding `singleValue: true` to that
condition's operator, redeployed, retested green. (Unary array/string operators
need this when a workflow is created through the raw REST API, which skips
n8n's normal auto-sanitisation.)

### 4. Cancel

```
POST .../barber-demo-cancel-appointment
{"appointmentId":"<APPOINTMENT_ID>","phone":"<PHONE_NUMBER>"}
→ HTTP 200 {"success":true,"confirmation":"Done -- your Skin Fade has been cancelled."}
```

Fresh read-back (`GET /calendars/events/appointments/{id}`):

```
appointmentStatus: cancelled
deleted: false
id: <APPOINTMENT_ID>   (unchanged — event preserved, audit trail intact)
```

**PASS.** Cancellation never deletes the event. **Class: verified** — this is a
property of the workflow's cancel branch, readable in
`n8n/barber-demo-handle-agent-tools.json`, not only a log entry.

### 5. Reschedule

Run before cancel, on the same event, so both paths were exercised against one
appointment:

```
POST .../barber-demo-reschedule-appointment
{"appointmentId":"<APPOINTMENT_ID>","new_slot":"2026-08-21T14:00:00+08:00","phone":"<PHONE_NUMBER>"}
→ HTTP 200 {"success":true,"confirmation":"Done -- your Skin Fade has been moved to 2026-08-21T14:00:00+08:00."}
```

Fresh read-back: `startTime: 2026-08-21T14:00:00+08:00` (matches request),
`endTime: 2026-08-21T14:30:00+08:00`, `rescheduledAt` populated. **PASS.**

### Negative-path tests

| Test | Request | Result |
|---|---|---|
| Invalid booking input | `book_appointment` missing phone/slot/service | HTTP 400 `{"error":"missing required fields: name, phone, slot, service"}` — PASS |
| Find not_found | `find_appointment` with an unseeded phone | HTTP 200 `{"status":"not_found","appointments":[]}` — PASS |
| Cancel identity mismatch | `cancel_appointment` with a valid appointment ID but the wrong phone | HTTP 403 `{"error":"identity_mismatch","message":"I could not verify that against the name and phone on file, so I will have the team call you back to sort it out."}` — PASS |

The identity-mismatch case matters most: it confirms the **server-side** phone
match blocks a mismatched request, rather than relying on the agent's
conversational check. **Class: verified** — the check is in the workflow.

---

## Wave 2 — agent, simulated suite, real conversations

Widget URL returned HTTP 200. A live `GET` confirmed
`platform_settings.privacy.record_voice: false` — no audio captured at any
point, transcript-only. **Class: verified** (read from the live API response).

### Simulated-conversation suite — recorded-in-log, not a test suite

7 scenarios via `POST /v1/convai/agents/{id}/simulate-conversation`, all
passed. This is a **dry run**: it does not touch the real backend, confirmed by
the n8n execution log showing no new executions during any simulate call.

**There is no runner in this repo that reproduces these.** The claim class is
`recorded-in-log` and it must not be described as an eval suite.

1. **Happy booking** — service → date → `check_availability` → slot pick → name → phone → `book_appointment`, called with the exact ISO `start` from availability, never the spoken "nine AM".
2. **Cancel via find** — covered by scenario 6, which exercises the full find → verify → confirm → cancel path.
3. **Reschedule via find + availability** — `reschedule_appointment` called with the exact ISO `new_slot`, never "2pm".
4. **"Are you an AI?"** — the caller acknowledged the opening line's AI disclosure; the disclosure was already present verbatim in `first_message`.
5. **Not found** — `find_appointment` mocked to return `not_found`; the agent apologised and offered a fresh booking rather than claiming to have found one.
6. **Multiple appointments** — two appointments mocked; the agent read both back, disambiguated ("the Beard Trim one"), verified the caller name, read back the exact appointment, required an explicit "yes", then cancelled the correct `appointmentId`.
7. **Off-topic deflection** — a complaint/refund request; the agent offered a human callback and captured name + phone rather than attempting an answer.

**ISO regression assertion passed in every scenario that called a
booking-mutating tool** (`book_appointment`, `reschedule_appointment`):
`slot` / `new_slot` always carried the machine ISO value, never spoken display
text.

### Real (non-simulated) conversations

ElevenLabs has no scripted headless "widget test" API, and browser automation
was unavailable in the implementing session. The tests instead drove the same
WebSocket protocol the widget itself uses
(`wss://api.elevenlabs.io/v1/convai/conversation?agent_id=...`) in text mode.
That exercises the real LLM, the real webhook backend, and real calendar
writes — equal-or-stronger evidence than a browser click-through, and it is how
both bugs below were found.

#### Bug 1 — the agent had no idea what day it was

**Class: verified** (the fix is present in the repo and greppable).

The first real conversation asked to book "tomorrow". The agent resolved it to
**2025-01-16** — a date in the past — because the prompt gave it no grounding
for "today". `check_availability` correctly returned zero slots for that past
date; the backend was right and the agent was wrong. A real, harmless,
past-dated booking was created before this was caught by a fresh calendar query
outside the test's date window.

**Fixed** by adding ElevenLabs' built-in `{{system__time}}` dynamic variable to
the prompt, with an explicit instruction to resolve every relative day
reference against it. The live agent was PATCHed and the same conversation
re-run: "tomorrow" resolved correctly and the booking landed on the right date,
confirmed by a fresh read.

The fix is in this repo:

```bash
grep -c 'system__time' agents/elevenlabs/barber-agent-config.json   # ≥ 1
```

#### Bug 2 — transient 401 on parallel fan-out

**Class: verified** (the fix is present in the repo and countable).

Re-running the full five-flow matrix with a throwaway caller identity surfaced
a `find_appointment` call returning `not_found` for a phone that had just
booked successfully and was confirmed present in the calendar.

Root cause, from the n8n execution log: `find_appointment`'s `Split Out Events`
node fans out one `GET /calendars/events/appointments/{id}` per event — five
near-simultaneous requests — and one of them, the exact event the test needed,
came back `401 "Command timed out"` from the provider's API. Not an auth
problem and not workflow logic: the other four parallel calls used the
identical credential and succeeded, and an immediate retry of the same
`find_appointment` succeeded.

**Fixed** by adding `retryOnFail: true, maxTries: 3, waitBetweenTries: 400` to
all 15 `httpRequest` nodes that call the calendar API. Redeployed, reactivated,
full five-flow matrix re-run fresh with read-backs — all green.

The fix is in this repo (see the verification snippet in
[`runbook-live-pilot.md` §2](./runbook-live-pilot.md#retry-hardening)):

```
15/15 http nodes retry-hardened; 76 nodes total
```

#### Final-gate conversations

Run fresh, after both fixes:

1. **Book** — Caller B, Kids Cut, "tomorrow", first morning slot. The agent
   resolved "tomorrow" to the correct date, offered real available slots, and
   booked. Fresh read: `startTime: 2026-08-21T09:00:00+08:00`,
   `appointmentStatus: confirmed`.
2. **Cancel** — Caller B, same appointment. The agent found it by phone,
   verified the name, read the appointment back, required an explicit "yes",
   and cancelled. Fresh read: `appointmentStatus: cancelled`,
   `deleted: false`.

Full transcripts were captured in the implementing session and are not
reproduced here. Every tool-call argument was inspected against the ISO and
identity rules above. **Class: recorded-in-log.**

---

## Honesty check

**Class: recorded-in-log.**

```
grep -icE "<the venue's existing booking product>" against the live agent prompt body
→ zero hits
```

Confirmed twice: once during authoring, which caught three mentions of the
product name in a first draft — present only to instruct the agent never to
claim the integration — and forced a rewrite to generic language so the name
never appears at all, spoken or not; and once again after the rewrite.

## Secret-leakage check

**Class: recorded-in-log.**

```
grep -rE 'sk_|Bearer [A-Za-z0-9]' across all new/modified files
→ clean, no matches
```

The public copy of this repository is additionally scanned with `gitleaks` and
an identifier denylist before every commit.

---

## Repository checks at the time of the live integration

**Class: measured, but superseded.** The implementing session recorded a
passing `make test` and `make lint` against the private working tree. Those
numbers are **not** carried over into this repository's claims — the counts in
[`../CLAIMS.md`](../CLAIMS.md) come from re-running the suite in *this* tree.
Re-run them yourself:

```bash
make install && make lint && make test
```

Structural facts about the exported workflow, re-checkable at any time:

```bash
python3 -c "
import json; d=json.load(open('n8n/barber-demo-handle-agent-tools.json'))
print(len(d['nodes']), 'nodes;', len(d['connections']), 'connection keys')"
# 76 nodes; 58 connection keys
```

---

## What was *not* proven

- **No production callers.** The live integration was a single-venue
  integration test, not a pilot. Nobody's real customers ever reached this
  agent.
- **No phone leg.** The widget and WebSocket paths were proven end-to-end. The
  telephony number and SMS gates were never exercised live.
- **No load, no concurrency, no soak.** Every test above is a single-caller
  path.
- **No business outcome.** No bookings-recovered figure, no revenue, no time
  saved. None was measured and none is claimed.

---

## Overall

**GO on the booking core**: availability, book, find, cancel and reschedule
all green with fresh calendar read-backs, plus real-conversation proof and
compliance proof. Two real bugs were found and fixed during verification —
neither would have surfaced without running the thing live against a real
calendar — and both were confirmed resolved by fresh re-tests rather than
patched and assumed.
