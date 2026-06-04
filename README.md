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
  `transfer_to_human`, `end_call`). Lowest latency.
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

- **Anthropic** — console.anthropic.com → *API Keys* → create key → `ANTHROPIC_API_KEY`.
- **ElevenLabs** — elevenlabs.io → profile → *API Key* → `ELEVENLABS_API_KEY`. Pick a
  voice in *Voices* and copy its id into `config.yaml` (`elevenlabs.voice_id` and,
  for the Retell agent, `retell.voice_id`).
- **Twilio** — console.twilio.com → copy *Account SID* + *Auth Token* → `TWILIO_ACCOUNT_SID`,
  `TWILIO_AUTH_TOKEN`. Buy a voice-capable number (*Phone Numbers → Buy a number*)
  and set it as `TWILIO_FROM_NUMBER` (E.164, e.g. `+14155551234`).
- **Retell** — dashboard.retellai.com → *API Keys* → `RETELL_API_KEY`. Connect your
  Twilio number to Retell, create an agent, and set `RETELL_AGENT_ID`. See *Wiring
  Retell* below.
- **Google Calendar** — see *Google OAuth* below.

### Google OAuth (Calendar) — mint a refresh token
1. Google Cloud Console → create/select a project → **enable the Google Calendar API**.
2. *APIs & Services → OAuth consent screen* → External → add yourself as a **test user**.
3. *Credentials → Create credentials → OAuth client ID → Desktop app*. Copy the
   client id/secret into `.env` (`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`).
4. Run the helper (opens a browser, asks for offline access):
   ```bash
   python get_google_token.py
   ```
   Paste the printed `GOOGLE_OAUTH_REFRESH_TOKEN` into `.env`. Set `GOOGLE_CALENDAR_ID`
   (`primary`, or a specific calendar id). *(Service accounts work too via
   `GOOGLE_SERVICE_ACCOUNT_FILE`, but can't access a personal calendar without
   domain-wide delegation — OAuth is simpler for your own calendar.)*

### Wiring Retell (telephony + webhooks)
1. Expose this server publicly: `ngrok http 8000` (dev) or your host → set
   `PUBLIC_BASE_URL` to the https URL.
2. **Provision the agent from your config** — one command, no dashboard clicking.
   It creates the LLM + agent and wires the prompt, tools, voice, webhook, voicemail,
   barge-in, and (if enabled) human transfer:
   ```bash
   python provision_retell.py --dry-run   # inspect the payloads first
   python provision_retell.py             # create it; prints RETELL_AGENT_ID
   ```
   Paste the printed `RETELL_AGENT_ID` (and `RETELL_LLM_WEBSOCKET_URL` for
   `custom_claude`) into `.env`, then attach your Twilio number to the agent in the
   Retell dashboard.
3. `llm_mode` picks the brain: `retell_managed` (Retell's model + our tool webhooks)
   or `custom_claude` (Claude drives over `${PUBLIC_BASE_URL}/llm-websocket`). The
   provisioning script wires whichever you set.
4. Human handoff: set `escalation.enabled: true` + `transfer_number` in `config.yaml`
   and re-provision; the agent gets a native Retell `transfer_call` tool.

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

There are two processes:

```bash
# 1) The web service — receives Retell webhooks/tools + serves the dashboard.
#    This MUST be running and reachable at PUBLIC_BASE_URL during calls.
uvicorn voiceagent.api.app:app --host 0.0.0.0 --port 8000

# 2) The campaign worker — places the calls. Run on demand or on a schedule.
python run_campaign.py leads.xlsx             # ingest + dial new/retry leads
python run_campaign.py --dry-run              # preview who'd be called now
python report.py                              # export outcomes to Excel
```

## Deploy (production)

1. **Database** — set `DATABASE_URL` to Postgres
   (`postgresql+psycopg://user:pass@host/db`) and `pip install "psycopg[binary]"`.
   Run `alembic upgrade head`. (SQLite is fine for testing; it doesn't survive a
   container restart.)
2. **Web service** — host the FastAPI app on a stable HTTPS URL and set
   `PUBLIC_BASE_URL` to it. A `Dockerfile` is included:
   ```bash
   docker build -t voice-agent .
   docker run -p 8000:8000 --env-file .env voice-agent   # runs migrations + uvicorn
   ```
   Put it behind your platform's TLS (Fly/Render/Cloud Run/ECS, etc.).
3. **Provision Retell** against that URL: `python provision_retell.py`, then attach
   your Twilio number to the agent.
4. **Run the worker** (`run_campaign.py`) from a cron/scheduler. It self-limits to the
   concurrency cap and only dials leads inside their local calling window, so you can
   safely run it every few minutes.
5. **Before real leads:** run the `test_*.py` suite with live keys, replace the EXAMPLE
   `script`/`compliance`/`qualifying_criteria` in `config.yaml` with your real offer,
   and confirm your lawful basis to call (see the compliance note above).

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
- [x] 9. Gap-closing: human transfer, timezone-aware booking, voicemail message,
      one-command Retell provisioning, Dockerfile + deploy guide

### Test status
- `pytest` — **47 passing** (ingest, phone/E.164, tz inference, calling-hours,
  retry/backoff, tools + booking guard + transfer, DNC, webhook idempotency,
  dashboard, provider parsing, Retell tool schema, prompt rendering + timezones,
  live-session turns).
- `test_excel.py` — passes (offline). The credential-dependent scripts
  (`healthcheck`, `test_anthropic/elevenlabs/twilio/retell/calendar`,
  `test_end_to_end`) are implemented and report clean PASS/FAIL — run them once
  you've filled in `.env`. They are the gate before placing real calls.

> What can't be auto-verified here: the live external API calls (no keys in CI).
> Run the `test_*.py` scripts with your own keys to validate those paths.
