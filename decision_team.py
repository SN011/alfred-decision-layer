"""
Alfred Decision Team — Agno Team of Agents

Three-agent team that decides how alfred_ should handle a proposed action:
  - Risk Assessor: evaluates action risk (reversibility, external impact, scope)
  - Conversation Analyst: reads conversation history for conflicts and intent clarity
  - Decision Leader: synthesizes both analyses into a final decision

Uses Agno memory so the team learns user patterns over time.
Follows the same patterns as the Project Heart supply_chain_agent.py.
"""

import os
import json
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from agno.agent import Agent
from agno.team import Team
from agno.db.sqlite import SqliteDb

load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path(__file__).parent.parent / ".env.local")


# ═══════════════════════════════════════════════════════════════════════════
# STRUCTURED OUTPUT
# ═══════════════════════════════════════════════════════════════════════════

class DecisionOutput(BaseModel):
    decision: str = Field(description="One of: execute_silent, execute_notify, confirm, clarify, refuse")
    confidence: float = Field(description="0.0 to 1.0")
    rationale: str = Field(description="2-3 sentence explanation")
    user_facing_message: str = Field(description="What alfred_ says to the user. Empty string if execute_silent.")
    risk_assessment: str = Field(description="Summary of the Risk Assessor's analysis")
    conversation_analysis: str = Field(description="Summary of the Conversation Analyst's findings")
    key_factors: list[str] = Field(description="The top factors that drove this decision")
    risks_identified: list[str] = Field(description="Specific risks found")


# ═══════════════════════════════════════════════════════════════════════════
# MODEL RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════

def get_model():
    """Resolve LLM from available API keys. xAI first — same as supply_chain_agent.py."""
    from agno.models.xai import xAI

    if os.getenv("XAI_API_KEY"):
        model_id = os.getenv("ALFRED_MODEL", "grok-4-1-fast-reasoning")
        return xAI(
            id=model_id,
            api_key=os.getenv("XAI_API_KEY"),
            temperature=0.1,
            max_tokens=4096,
            retries=3,
            delay_between_retries=2,
        ), f"xai/{model_id}"

    if os.getenv("OPENAI_API_KEY"):
        from agno.models.openai import OpenAIChat
        model_id = os.getenv("ALFRED_MODEL", "gpt-4o-mini")
        return OpenAIChat(
            id=model_id,
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0.1,
            max_tokens=4096,
        ), f"openai/{model_id}"

    if os.getenv("GROQ_API_KEY"):
        from agno.models.groq import Groq
        model_id = os.getenv("ALFRED_MODEL", "llama-3.3-70b-versatile")
        return Groq(
            id=model_id,
            api_key=os.getenv("GROQ_API_KEY"),
            temperature=0.1,
            max_tokens=4096,
        ), f"groq/{model_id}"

    raise ValueError("No LLM API key found. Set XAI_API_KEY, OPENAI_API_KEY, or GROQ_API_KEY.")


# ═══════════════════════════════════════════════════════════════════════════
# AGENT DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════

ALFRED_CAPABILITIES = """
alfred_ is an AI assistant that lives in the user's text messages. It can:

ACTIONS (what alfred_ can do):
  - Send, reply, forward emails
  - Schedule, cancel, reschedule meetings
  - Set reminders and create tasks
  - Search and summarize emails
  - Read calendar

IRREVERSIBILITY GUIDE (alfred_ knows this about its own actions):
  - Sending/replying/forwarding emails → IRREVERSIBLE once sent
  - Scheduling meetings → reversible (can cancel)
  - Cancelling meetings → awkward to undo, especially with external attendees
  - Rescheduling meetings → reversible but affects others' calendars
  - Setting reminders → fully reversible, harmless
  - Deleting emails → mostly irreversible (trash has limits)
  - Reading/searching → zero side effects

EXTERNAL IMPACT:
  - Anything involving other people (emails, meetings) has external impact
  - Internal-only actions (reminders, reading) have no external impact
  - The more people affected, the higher the stakes
"""


def create_risk_assessor() -> Agent:
    return Agent(
        name="Risk Assessor",
        role="Evaluate the risk profile of a proposed action for alfred_",
        instructions=[
            "You are the Risk Assessor for alfred_'s Execution Decision Layer.",
            "",
            ALFRED_CAPABILITIES,
            "",
            "Your job: given a proposed action, assess its risk. Consider:",
            "  1. Is this action reversible? Can we undo it if it's wrong?",
            "  2. Does it affect people outside the user? Who and how many?",
            "  3. What's the scope — one item or many? 'Delete this email' vs 'delete all emails' is night and day.",
            "  4. Is anything missing that we'd need to execute? (recipient, time, content, etc.)",
            "  5. Does the content itself carry risk? (confidential data, pricing info, legal language)",
            "",
            "Be concise. Output a brief risk assessment paragraph covering these dimensions.",
            "End with a risk level: LOW / MEDIUM / HIGH / CRITICAL",
            "Do NOT make the final decision — that's the team leader's job.",
        ],
        add_datetime_to_context=True,
    )


def create_conversation_analyst() -> Agent:
    return Agent(
        name="Conversation Analyst",
        role="Analyze conversation history for intent clarity and contradictions",
        instructions=[
            "You are the Conversation Analyst for alfred_'s Execution Decision Layer.",
            "",
            "Your job: analyze the conversation history to determine whether the user's latest message",
            "clearly expresses what they want, or whether there are red flags.",
            "",
            "Look for:",
            "  1. CONTRADICTIONS: Did the user say 'hold off' earlier and now says 'do it'?",
            "     A quick 'yes' after a 'wait for X' doesn't mean X happened.",
            "  2. UNRESOLVED CONDITIONS: 'Wait until legal reviews' — has that condition been met?",
            "     If the user just says 'send it' without mentioning the condition was satisfied, flag it.",
            "  3. AMBIGUOUS REFERENCES: 'Cancel my meeting' when there are multiple meetings.",
            "     'Send that email' when multiple drafts exist.",
            "  4. INTENT SHIFTS: 'Actually...' or 'instead...' signals the user changed their mind.",
            "  5. CONTEXT FROM HISTORY: What did alfred_ draft? What was the user responding to?",
            "",
            "If there's no conversation history, just say so — don't invent problems.",
            "Be concise. Output a brief analysis paragraph.",
            "End with: CLEAR INTENT / AMBIGUOUS INTENT / CONFLICTING SIGNALS",
            "Do NOT make the final decision — that's the team leader's job.",
        ],
        add_datetime_to_context=True,
    )


# ═══════════════════════════════════════════════════════════════════════════
# TEAM DEFINITION
# ═══════════════════════════════════════════════════════════════════════════

LEADER_INSTRUCTIONS = [
    "You are the Decision Leader for alfred_'s Execution Decision Layer.",
    "You coordinate a team of two analysts — a Risk Assessor and a Conversation Analyst.",
    "",
    ALFRED_CAPABILITIES,
    "",
    "═══ YOUR DECISION OPTIONS ═══",
    "",
    "Given a proposed action + context, delegate to both analysts, then decide ONE of:",
    "",
    "  execute_silent — Act without telling the user.",
    "    Use for: routine low-risk actions with clear intent. Reminders, reading email, calendar checks.",
    "",
    "  execute_notify — Act and tell the user after.",
    "    Use for: low-medium risk, clear intent, but the user would want to know it happened.",
    "    Example: quick reply to a teammate, accepting an obvious meeting invite.",
    "",
    "  confirm — Ask the user to confirm before acting.",
    "    Use for: intent IS resolved, but risk is above the silent threshold.",
    "    Example: sending an email to an external partner, cancelling a meeting with clients.",
    "",
    "  clarify — Ask a clarifying question.",
    "    Use for: intent, entity, or key parameters are UNRESOLVED.",
    "    Example: 'cancel my meeting' when there are 3 meetings.",
    "",
    "  refuse — Refuse or escalate.",
    "    Use for: policy violations, extreme risk, or safety can't be assured.",
    "    Example: forwarding confidential data to press, bulk deletion.",
    "",
    "═══ DECISION BOUNDARIES ═══",
    "",
    "• Clarify when you don't know WHAT the user wants.",
    "• Confirm when you know what they want but it's risky.",
    "• Refuse when it shouldn't be done regardless of confirmation.",
    "",
    "═══ CRITICAL: DEFAULT SAFE ═══",
    "",
    "When uncertain, ALWAYS err toward more caution (escalate up the list).",
    "execute_silent requires HIGH confidence that it's safe and wanted.",
    "Never execute silently if the Risk Assessor says HIGH or CRITICAL.",
    "Never execute silently if the Conversation Analyst says CONFLICTING SIGNALS.",
    "",
    "═══ PROCESS ═══",
    "",
    "1. Delegate to BOTH the Risk Assessor and Conversation Analyst.",
    "2. Read their analyses carefully.",
    "3. Make your decision. Include their findings in your response.",
    "4. Write the user_facing_message — what alfred_ would actually say to the user.",
    "",
    "═══ OUTPUT FORMAT ═══",
    "",
    "After synthesizing, respond with ONLY a JSON object (no markdown fences, no extra text):",
    '{',
    '  "decision": "execute_silent | execute_notify | confirm | clarify | refuse",',
    '  "confidence": 0.0 to 1.0,',
    '  "rationale": "2-3 sentence explanation",',
    '  "user_facing_message": "What alfred_ says to the user (empty if execute_silent)",',
    '  "risk_assessment": "Summary of the Risk Assessor findings",',
    '  "conversation_analysis": "Summary of the Conversation Analyst findings",',
    '  "key_factors": ["factor1", "factor2"],',
    '  "risks_identified": ["risk1", "risk2"]',
    '}',
]


def create_decision_team(db: SqliteDb) -> Team:
    """Create the alfred_ decision team. Mirrors supply_chain_agent.py patterns."""
    model, model_name = get_model()

    risk_assessor = create_risk_assessor()
    conversation_analyst = create_conversation_analyst()

    team = Team(
        name="Alfred Decision Layer",
        model=model,
        members=[risk_assessor, conversation_analyst],
        instructions=LEADER_INSTRUCTIONS,
        db=db,
        enable_user_memories=True,
        enable_agentic_memory=True,
        add_memories_to_context=True,
        read_chat_history=True,
        add_history_to_context=True,
        num_history_runs=5,
        show_members_responses=True,
        store_member_responses=True,
        markdown=False,
        add_datetime_to_context=True,
    )

    return team, model_name


# ═══════════════════════════════════════════════════════════════════════════
# SCENARIO FORMATTER
# ═══════════════════════════════════════════════════════════════════════════

def format_scenario_message(scenario: dict) -> str:
    """Format a scenario dict into a clear message for the team."""
    action = scenario.get("action", {})
    user_message = scenario.get("user_message", "")
    history = scenario.get("conversation_history", [])
    prefs = scenario.get("user_preferences", {})

    parts = []
    parts.append(f"## Proposed Action\n{json.dumps(action, indent=2)}")
    parts.append(f'## Latest User Message\n"{user_message}"')

    if history:
        lines = []
        for i, msg in enumerate(history):
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "")
            lines.append(f"  {i+1}. {role}: {content}")
        parts.append(f"## Conversation History\n" + "\n".join(lines))
    else:
        parts.append("## Conversation History\nNo prior conversation.")

    if prefs:
        pref_lines = [f"  - {k}: {v}" for k, v in prefs.items()]
        parts.append("## User Preferences\n" + "\n".join(pref_lines))

    parts.append("\nEvaluate this scenario. Delegate to both analysts, then make your decision.")
    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# FALLBACK (when LLM fails)
# ═══════════════════════════════════════════════════════════════════════════

FALLBACK_DECISION = {
    "decision": "confirm",
    "confidence": 0.0,
    "rationale": "The AI system encountered an error. Defaulting to confirmation to ensure no irreversible action is taken without the user's explicit approval.",
    "user_facing_message": "I want to proceed with this, but let me double-check — shall I go ahead?",
    "risk_assessment": "Unable to assess — system error.",
    "conversation_analysis": "Unable to analyze — system error.",
    "key_factors": ["system_error", "safe_default_applied"],
    "risks_identified": ["AI system was unavailable or returned an error"],
}


def get_fallback_decision(error: str) -> dict:
    """Safe fallback when team fails. Always errs toward caution."""
    result = dict(FALLBACK_DECISION)
    result["rationale"] = f"System error: {error}. Defaulting to confirmation for safety."
    result["risks_identified"] = [error]
    return result
