# Voice Sales Agent

Production-grade **outbound voice AI sales agent**. Feed it an Excel sheet of
leads; it calls each one, runs a natural qualifying conversation, handles
objections, checks your real Google Calendar, and books appointments —
with reliability, compliance, and observability built in from the start.

> **Repo note:** this project lives in the `Autoextensionbot` repository for
> now (chosen during setup). Rename the repo on GitHub anytime — git remotes
> redirect automatically.

---

## ⚠️ Compliance — read this first (plain language, not legal advice)

Outbound cold calling is **regulated**. In the United States, the **TCPA**
(Telephone Consumer Protection Act) and a patchwork of **state laws** govern
automated and AI-assisted calls. Requirements vary by state and change over
time, and several states **require you to disclose that the caller is an AI**.

**You are responsible for having a lawful basis to call each number** —
including honoring the National and any state Do-Not-Call registries, getting
any consent the law requires, and respecting opt-outs immediately. This
software ships with safeguards on by default:

- `disclose_ai_identity: true` — the agent states it's an AI assistant.
- Do-Not-Call enforcement — a `do_not_call` lead is never dialed.
- Verbal opt-out handling — the agent flags DNC and the lead is never called again.
- A `consent_log` audit trail of every disposition and consent event.
- Calling-hours enforcement — no calls outside **8am–8pm in the lead's local time**.

These are **defaults, not legal compliance**. Configure the tool to match the
law where you and your leads are located, and consult a qualified attorney.

---

## Architecture (one paragraph)

Retell handles the audio path — telephony (via Twilio), speech-to-text,
turn-taking, barge-in, and ElevenLabs TTS. Two interchangeable "brains" sit
behind a config switch (`llm_mode`):

- **`retell_managed`** (default) — Retell's own LLM runs the conversation from
  the editable prompt; our FastAPI webhooks service the tool calls
  (`check_availability`, `book_appointment`, `mark_callback`, `flag_dnc`,
  `end_call`). Lowest latency.
- **`custom_claude`** — Claude (`claude-sonnet-4-5`) drives every turn over a
  custom-LLM WebSocket with full tool-calling. Maximum control.

A `VoiceProvider` interface abstracts Retell so Vapi can be swapped in without
touching call logic. State lives in SQLite (SQLAlchemy + Alembic), migrating
cleanly to Postgres. A FastAPI app serves webhooks, the optional LLM WebSocket,
and a single-page dashboard.

```
Lead <-> Twilio <-> Retell (STT - turn-taking - barge-in - ElevenLabs TTS)
                      |                          ^
              webhooks|             llm_mode=    | retell_managed: tool webhooks
                      v             custom_claude:| custom_claude: full turn
              FastAPI backend ───────────────────┘
                ├─ brain: Claude tool-calling  ─┐
                ├─ tools: calendar / db          ├─► Google Calendar
                └─ DB (idempotent) ──────────────┘   SQLite → Postgres
```

---

## Prerequisites

- Python **3.11+**
- Accounts/keys: **Anthropic, Retell, Twilio, ElevenLabs, Google Cloud (Calendar API)**
- A way to expose a public HTTPS URL for Retell webhooks in dev (e.g. `ngrok`)

## Getting each API key

> Detailed, click-by-click steps are filled in as each integration lands
> (build steps 3–7). Summary:

- **Anthropic** — console.anthropic.com → API Keys → set `ANTHROPIC_API_KEY`. _(details: step 3)_
- **Retell** — dashboard.retellai.com → API key + agent → `RETELL_API_KEY`, `RETELL_AGENT_ID`. _(details: step 3)_
- **Twilio** — console.twilio.com → Account SID/Auth Token + a voice number → `TWILIO_*`. _(details: step 3)_
- **ElevenLabs** — elevenlabs.io → Profile → API key; pick a `voice_id`. _(details: step 3)_
- **Google Calendar (OAuth)** — Google Cloud Console → enable Calendar API →
  OAuth consent screen → OAuth client (Desktop) → run the helper to mint a
  `GOOGLE_OAUTH_REFRESH_TOKEN`. _(details: step 6)_

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in your keys
# Review config.yaml (voice, hours, qualifying criteria, script, compliance)
```

## Initialize the database

```bash
alembic upgrade head          # authoritative; creates all tables
# or, quick dev bootstrap:
python -c "from voiceagent.db.session import init_db; init_db()"
```

## Run the tests — IN ORDER (verify each integration in isolation)

Each script prints a clear **PASS/FAIL** with the real error on failure. Run
these and confirm they pass **before** any campaign. _(implemented in step 3+)_

```bash
python healthcheck.py         # all keys present + valid, in one command
python test_excel.py          # full ingest on a sample sheet
python test_anthropic.py      # Claude tool-calling works
python test_elevenlabs.py     # generates a sample audio file to listen to
python test_twilio.py         # credentials valid + lists your numbers
python test_calendar.py       # reads availability, creates + deletes an event
python test_retell.py +1XXX   # ONE live call to a number you specify (role-play)
python test_end_to_end.py     # ingest → call your own number → book a real slot
```

## Validate a spreadsheet (dry run, no DB writes)

```bash
python validate_spreadsheet.py leads.xlsx    # writes rejected_leads.xlsx
```

## Run a campaign

```bash
python run_campaign.py leads.xlsx             # ingest + dial new/retry leads
# Dashboard (leads by status, calls today, bookings, transcripts, costs):
uvicorn voiceagent.api.app:app --reload
python report.py                              # export outcomes to Excel
```

---

## Project layout

```
voiceagent/
  config.py            # typed Settings (.env) + AppConfig (config.yaml)
  db/                  # SQLAlchemy models, session, schema.sql reference
  ingest/              # Excel: aliasing, E.164, tz inference, de-dupe (step 2)
  prompts/             # editable agent prompt + sales framework (step 5)
  brain/               # Claude tool-calling engine + tool defs (step 5)
  voice/               # VoiceProvider interface (step 1) + Retell/Vapi (step 4)
  telephony/           # Twilio (step 4)
  gcal/                # Google Calendar availability + booking (step 6)
  orchestration/       # campaign, concurrency, calling hours, retry (step 7)
  webhooks/            # idempotent Retell webhook routers (step 7)
  api/                 # FastAPI app + dashboard (steps 7–8)
  observability/       # loguru logging (step 1) + cost tracking (step 8)
  compliance/          # DNC, AI disclosure, consent_log (step 7)
alembic/               # migrations (Postgres-clean)
config.yaml  .env.example  requirements.txt
validate_spreadsheet.py  run_campaign.py  report.py  healthcheck.py
test_*.py              # one standalone test per integration (step 3+)
```

## Build status

- [x] 1. Scaffold + schema + .env.example + config + README skeleton
- [x] 2. Excel ingest + `validate_spreadsheet.py` + `test_excel.py`
- [x] 3. Integration test scripts + `healthcheck.py`
- [x] 4. VoiceProvider layer (Retell/Twilio)
- [x] 5. Conversation engine + tools + editable prompt
- [x] 6. Google Calendar integration
- [x] 7. Orchestration + webhooks + retry + compliance
- [x] 8. Dashboard + reporting + cost tracking

### Test status
- `pytest` — **42 passing** (ingest, phone/E.164, tz inference, calling-hours,
  retry/backoff, tools + booking guard, DNC, webhook idempotency, dashboard,
  provider parsing, prompt rendering, live-session turns).
- `test_excel.py` — passes (offline). The credential-dependent scripts
  (`healthcheck`, `test_anthropic/elevenlabs/twilio/retell/calendar`,
  `test_end_to_end`) are implemented and report clean PASS/FAIL — run them once
  you've filled in `.env`. They are the gate before placing real calls.

> What can't be auto-verified here: the live external API calls (no keys in CI).
> Run the `test_*.py` scripts with your own keys to validate those paths.
