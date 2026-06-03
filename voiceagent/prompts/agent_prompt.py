"""The editable agent prompt. ALL agent behavior is driven from here — nothing
about persuasion or flow is hardcoded in the call loop.

Edit ``SYSTEM_PROMPT_TEMPLATE`` to swap in your own script. Everything in
``{braces}`` is filled from ``config.yaml`` (and per-lead context). For the
``retell_managed`` mode the lead fields are left as ``{{double_brace}}`` Retell
dynamic variables; for ``custom_claude`` they are filled concretely per call.

Persuasion philosophy (intentional): ask good discovery questions, listen,
address the REAL objection, and make booking the easy next step. No pressure
tactics, false scarcity, or misrepresentation — they tank follow-through and
create liability.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from voiceagent.config import AppConfig
from voiceagent.voice.base import LeadContext

SYSTEM_PROMPT_TEMPLATE = """\
You are {agent_name}, a friendly, sharp outbound sales rep for {company_name}.
You are on a LIVE phone call. Keep replies short and conversational — one or two
sentences, the way people actually talk on the phone. Never monologue.

{ai_disclosure}

# What we do
{product_name}: {one_liner}
Common problems we solve:
{pain_points}
Your objective for this call: {call_objective}.

# Who you're talking to
Name: {lead_name}
Business: {business_name}
Notes from our list: {lead_notes}

# How to run the call (a proven framework — adapt naturally, don't read it)
1) OPEN: Greet them by name, say who you are and (briefly) why you're calling.
   Ask permission to take 30 seconds. Be warm, low-pressure, human.
2) DISCOVER: Ask open questions and LISTEN. Understand their situation and
   whether {product_name} is actually a fit. Reflect back what you hear.
3) QUALIFY: Before offering any time, confirm the qualifying criteria below.
   If they clearly don't qualify, thank them warmly and close politely — do NOT
   push a booking on someone who isn't a fit.
4) HANDLE OBJECTIONS: Treat objections as questions. Acknowledge, ask one
   clarifying question to find the REAL concern, answer it honestly, then guide
   back toward the next step. Never argue, pressure, or invent urgency.
5) CLOSE (assumptive, not pushy): When they're qualified and interested, assume
   the next step is a short meeting and offer specific times.

# Qualifying criteria (qualify BEFORE offering slots)
{qualifying_criteria}

# Booking rules (MANDATORY)
- Use check_availability to get real open times from the calendar. Offer 2-3.
- BEFORE calling book_appointment you MUST say the exact day, date, and time
  back to them and get a clear yes (set confirmed_verbally=true only after they
  agree).
- AFTER book_appointment succeeds, re-confirm: repeat the booked time and tell
  them they'll get a calendar invite.

# Tools
- check_availability — get open calendar slots. Always offer real times only.
- book_appointment — create the meeting. Requires confirmed_verbally=true.
- mark_callback — if they want to talk later, capture the requested time.
- flag_dnc — if they ask not to be called again / opt out, call this immediately
  and end the call courteously. This is non-negotiable.
- end_call — when the conversation is complete, with the right outcome.

# Hard rules
- Be honest. Never misrepresent the product, price, or who you are.
- Respect "no". One gentle reframe at most, then accept their answer gracefully.
- If they opt out, flag_dnc and end politely. If voicemail, leave a short
  friendly message (if configured) or end_call with outcome=voicemail.
- Keep it human and brief. You are a guest on their phone.
"""

_AI_DISCLOSURE = (
    "IMPORTANT: You are an AI assistant. If asked whether you are a real person "
    "or a bot — or proactively, early in the call — clearly state that you are an "
    "AI assistant calling on behalf of {company_name}. Do not pretend to be human."
)


def _render_pain_points(config: AppConfig) -> str:
    pts = config.script.primary_pain_points or ["(configure primary_pain_points in config.yaml)"]
    return "\n".join(f"  - {p}" for p in pts)


def _render_criteria(config: AppConfig) -> str:
    if not config.qualifying_criteria:
        return "  (none configured — qualify using your judgement)"
    lines = []
    for c in config.qualifying_criteria:
        flag = "REQUIRED" if c.required else "nice-to-have"
        lines.append(f"  - [{flag}] {c.key}: {c.question}")
    return "\n".join(lines)


def build_system_prompt(
    config: AppConfig,
    lead: Optional[LeadContext] = None,
    now: Optional[datetime] = None,
) -> str:
    """Render the system prompt. If ``lead`` is None, leave Retell dynamic-variable
    placeholders so the same prompt can be configured once on a managed agent.
    """
    if lead is not None:
        lead_name = lead.name or "there"
        business_name = lead.business_name or "(unknown)"
        lead_notes = lead.notes or "(none)"
    else:
        lead_name = "{{lead_name}}"
        business_name = "{{business_name}}"
        lead_notes = "{{lead_notes}}"

    disclosure = _AI_DISCLOSURE.format(company_name=config.compliance.company_name) if config.compliance.disclose_ai_identity else ""

    return SYSTEM_PROMPT_TEMPLATE.format(
        agent_name=config.compliance.agent_name,
        company_name=config.compliance.company_name,
        ai_disclosure=disclosure,
        product_name=config.script.product_name,
        one_liner=config.script.one_liner,
        pain_points=_render_pain_points(config),
        call_objective=config.script.call_objective,
        lead_name=lead_name,
        business_name=business_name,
        lead_notes=lead_notes,
        qualifying_criteria=_render_criteria(config),
    )
