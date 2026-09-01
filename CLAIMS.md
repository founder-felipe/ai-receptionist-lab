# CLAIMS

Every sentence in this repository that carries a number or asserts an outcome
maps to a row below. If a statement is not backed by a row here, it should not
be in the repo.

## Classes

| Class | Meaning |
|---|---|
| **measured** | A number produced by an artifact that exists and can be re-run or re-read. |
| **verified** | A property confirmed by inspecting code, config, or an API response. |
| **recorded-in-log** | It happened during a documented session, and the write-up is the only evidence. No reproducible runner exists in this repo. |
| **unknown** | Not known. Stated as unknown, or omitted. Never implied. |

## Rows

### RC-1 — the test suite runs with zero credentials

**Class: measured.** `make install && make lint && make test` passes on a
clean machine with no `.env`, no API keys, and no network-dependent fixtures.

- **20 test functions** (`grep -rc "def test_" tests/` → 20; pytest collects 20
  — there is no parametrisation in this suite, so the two numbers agree).
- Verified on **Python 3.11.15** (`20 passed`) and **Python 3.14.3**
  (`20 passed`), both with `ruff check .` and `mypy --strict` clean.
- CI runs the same three commands on Python 3.12, 3.13 and 3.14.

Re-derive:

```bash
grep -rc "def test_" tests/ | awk -F: '{s+=$2} END {print s}'
make install && make lint && make test
```

### RC-2 — one live integration, two real bugs

**Class: verified.** The voice agent and the n8n workflow were integrated once,
on 2026-08-21, against a real barbershop's live calendar (single venue,
anonymised in this repo). Running it live — rather than against mocks — surfaced
two genuine bugs that mocks did not catch:

1. **Date grounding.** The agent had no reference for "today" and resolved
   "tomorrow" to a date in the past. Fixed by injecting ElevenLabs'
   `{{system__time}}` dynamic variable into the prompt.
2. **Transient provider 401 on parallel fan-out.** One of five near-simultaneous
   calendar detail requests intermittently returned `401 "Command timed out"`,
   making `find_appointment` report `not_found` for an appointment that existed.
   Fixed with retry hardening on every calendar-calling HTTP node.

Both fixes are present in this repository, not just described:

```bash
grep -c 'system__time' agents/elevenlabs/barber-agent-config.json      # ≥ 1
python3 -c "
import json; d=json.load(open('n8n/barber-demo-handle-agent-tools.json'))
http=[n for n in d['nodes'] if n.get('type')=='n8n-nodes-base.httpRequest']
ok=[n for n in http if n['parameters']['options'].get('retryOnFail') is True
    and n['parameters']['options'].get('maxTries')==3
    and n['parameters']['options'].get('waitBetweenTries')]
print(len(ok),'/',len(http),'retry-hardened;',len(d['nodes']),'nodes')"
# 15 / 15 retry-hardened; 76 nodes
```

Evidence: [`docs/verification-2026-08-21.md`](./docs/verification-2026-08-21.md).

### RC-3 — 7 simulated conversation scenarios passed

**Class: recorded-in-log.** Seven scenarios were run through the provider's
`simulate-conversation` endpoint and all passed, including an assertion that
booking-mutating tool calls always carried the machine ISO timestamp and never
the spoken display text.

**This is a log, not a test suite.** There is no runner in this repository that
reproduces it, and it must not be described as an eval suite. See
[`docs/verification-2026-08-21.md`](./docs/verification-2026-08-21.md).

### RC-4 — production usage

**Class: unknown — stated, not implied.**

- **No production callers.** The live integration was an integration test
  against one venue's calendar. No member of the public ever reached the agent.
- **No pilots.** Zero.
- **No revenue, no ROI, no bookings-recovered figure.** Nothing of the kind was
  measured, and nothing of the kind is claimed anywhere in this repository.
- **No load, concurrency, or soak testing.**

### RC-5 — workflow structure

**Class: measured.** The exported n8n workflow contains **76 nodes** and **58
connection keys**, with all credential IDs, workflow IDs and webhook IDs
replaced by placeholders. Re-derive:

```bash
python3 -c "
import json; d=json.load(open('n8n/barber-demo-handle-agent-tools.json'))
print(len(d['nodes']),'nodes;',len(d['connections']),'connection keys')"
```

## Never claimed

No client name, venue name, city, or URL. No business outcome, revenue, ROI,
or time-saved figure. No production-scale or enterprise language. No test count
that has not been re-run in this tree. No "eval suite" wording for RC-3. No
"pilot" — there have been none.
