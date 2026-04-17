# alfred_ Execution Decision Layer

A prototype decision engine built with **Agno Team of Agents** that determines how an AI text-message assistant should handle proposed actions — whether to execute silently, notify after, confirm before, ask a clarifying question, or refuse.

**Live URL:** [TODO: add after deploy]
**Repo:** [TODO: add repo link]

---

## Architecture

```
┌──────────────────────────────────────────────────┐
│              Decision Team (Leader)                │
│  Coordinates, synthesizes, makes final decision   │
│  ┌──────────────┐    ┌────────────────────────┐  │
│  │ Risk Assessor │    │ Conversation Analyst    │  │
│  │ Evaluates     │    │ Reads history for       │  │
│  │ action risk   │    │ conflicts & intent      │  │
│  └──────────────┘    └────────────────────────┘  │
│                                                    │
│  Memory: learns user patterns across sessions      │
│  Output: structured decision (Pydantic schema)     │
└──────────────────────────────────────────────────┘
```

The team is an **Agno Team** — same framework and patterns used in the Project Heart supply chain agent. Three roles:

1. **Risk Assessor**: Evaluates the action itself — reversibility, external impact, scope, content sensitivity. Knows alfred_'s capabilities (what each action does and whether it's reversible).

2. **Conversation Analyst**: Reads the full conversation history for contradictions, unresolved conditions, ambiguous references, and intent shifts. This is the agent that catches "Yep, send it" after "hold off until legal reviews."

3. **Decision Leader**: Delegates to both analysts, reads their findings, and makes the final call. Uses a structured output schema to return a consistent decision.

## What Signals the System Uses

All signals come from the LLM reasoning over the context — **no regex, no keyword matching, no hardcoded rules.** The agents know alfred_'s capabilities (what's reversible, what affects externals) through their instructions, and reason about each scenario contextually.

The Risk Assessor considers:
- **Reversibility**: Can this be undone? (Emails can't be unsent. Reminders can be deleted.)
- **External reach**: Does this affect people outside the user?
- **Scope**: One item vs bulk operations
- **Content sensitivity**: Confidential data, pricing info, legal language
- **Missing parameters**: What would we need to execute this?

The Conversation Analyst considers:
- **Contradictions**: "Hold off" followed by "send it"
- **Unresolved conditions**: "Wait for legal review" — did that happen?
- **Ambiguous references**: "Cancel my meeting" when there are multiple
- **Intent shifts**: "Actually..." or "instead..."

## Code vs LLM Responsibility

### What the LLM decides (everything that matters)

- Risk assessment of the action
- Whether conversation history contains concerning patterns
- The final decision (execute/notify/confirm/clarify/refuse)
- The rationale in human terms
- What to say to the user
- Whether to escalate despite partial clarity

### What code does (plumbing only)

- **Formats the scenario** into a structured message for the team
- **Parses the structured output** from the team's response
- **Handles failures** — timeout, malformed output, missing context → safe fallback
- **Persists memory** via Agno's SqliteDb (the team remembers user patterns)
- **Serves the API** via FastAPI

### Why this split

The previous version tried to compute signals deterministically with regex and keyword matching. That was dumb. "Call the dentist" matched "all" because "c**all**" contains "all." The LLM doesn't make that mistake.

The LLM understands context. It knows that "Yep, send it" after "hold off until legal reviews pricing language" is not a clear green light. No amount of keyword heuristics captures that. Let the model think.

Code handles what code is good at: formatting, parsing, error handling, persistence. The model handles what models are good at: judgment, context, nuance.

## Prompt Design

Each agent has focused instructions that tell it:
1. **What it's responsible for** (risk vs conversation analysis)
2. **What alfred_ can do** (shared capabilities context — reversibility, external impact)
3. **What to look for** (specific things to analyze)
4. **What NOT to do** (don't make the final decision — that's the leader's job)

The leader's instructions define:
1. **The five decisions** with clear definitions and examples
2. **Decision boundaries** — when to clarify vs confirm vs refuse
3. **The default-safe principle** — when uncertain, escalate
4. **The process** — delegate to both analysts, read their findings, synthesize

The scenario is formatted as a structured message with sections: Proposed Action, Latest User Message, Conversation History, User Preferences. Clean and readable for the model.

## Agno Memory

The team uses Agno's built-in memory (`enable_user_memories=True`, `enable_agentic_memory=True`):
- **User memories**: The team remembers facts about users across sessions
- **Agentic memory**: The team learns from past decisions
- **Chat history**: 5 previous runs are included in context

Over time, the team learns patterns: "This user always wants external emails confirmed," "This user is fine with calendar changes being automatic." The decision quality improves with usage.

## Failure Handling

| Failure | What happens | Safe default |
|---------|-------------|--------------|
| **LLM timeout** | Team can't deliberate | CONFIRM — let the human decide |
| **Malformed output** | Can't parse structured decision | CONFIRM — let the human decide |
| **Missing context** | Action is vague ("do the thing") | Runs through team naturally → agents ask to CLARIFY |

**The principle**: no failure ever results in silent execution. If something goes wrong, we either ask the user to confirm or ask them to clarify. Irreversible actions should never happen by default.

All three failure cases are demonstrable in the UI.

## Scenarios

| # | Name | Difficulty | Expected | Why |
|---|------|-----------|----------|-----|
| 1 | Set a reminder | Easy | Execute silent | Low risk, clear intent, fully reversible, no external impact |
| 2 | Check calendar | Easy | Execute silent | Read-only, zero risk, zero side effects |
| 3 | Cancel which meeting? | Ambiguous | Clarify | 3 meetings this afternoon — entity unresolved |
| 4 | Send email after "hold off" | Ambiguous | Confirm | Conversation history has conflicting signals |
| 5 | Delete 6 months of emails | Risky | Refuse | Irreversible bulk destruction |
| 6 | Forward board notes to press | Risky | Refuse | Confidential data to journalist |
| 7 | Quick reply to teammate | Easy | Execute & notify | Low risk internal, but user should know it happened |
| 8 | Reschedule with external client | Ambiguous | Confirm | Affects someone else's calendar |

Scenario 4 is the example from the challenge. The Conversation Analyst should catch that "hold off until legal reviews pricing language" creates a pending condition, and that the subsequent "Yep, send it" doesn't confirm that condition was met.

## How This System Evolves

### As alfred_ gains riskier tools

- **More specialized agents**: Add a Policy Enforcer agent that checks organizational rules (GDPR, HIPAA, company policy). Add a User Trust Scorer that adjusts thresholds based on the user's correction history.
- **Action-specific sub-teams**: High-risk actions (financial transactions, data deletion) get routed to a specialized sub-team with extra scrutiny.
- **Standing permissions**: Users can grant alfred_ blanket approval for certain action patterns ("always approve internal calendar changes"), reducing unnecessary confirmations.

### What I'd build next (6-month roadmap)

**Month 1-2: Foundation**
- User feedback loop: "You didn't need to ask me that" / "You should have asked" signals
- Per-user threshold calibration based on correction history
- Audit log of every decision with full context (for trust and debugging)
- Latency budgets per action type (calendar checks should be instant; email sends can wait 2s)

**Month 3-4: Learning**
- Anomaly detection: user who never sends bulk emails suddenly wants to → escalate regardless
- Multi-step action chains: evaluate compound actions together
- A/B framework for testing decision thresholds
- Organization-level policy engine

**Month 5-6: Scale**
- Fine-tuned decision model trained on real user feedback
- Delegation protocols with standing permissions
- Compliance integration (GDPR, HIPAA constraints as team context)
- Real-time risk dashboard for alfred_ operators

## What I Chose Not to Build

- **Real action execution**: The prototype decides but doesn't send emails or cancel meetings
- **Streaming**: Each evaluation is request/response; streaming would improve UX but isn't the core problem
- **User authentication**: Prototype is open; production would personalize per-user
- **Fine-tuned model**: Using a general-purpose LLM with prompt engineering; a fine-tuned model would be faster and more reliable

## Running Locally

```bash
cd alfred_decision_layer

# Install deps (or use existing venv)
pip install -r requirements.txt

# Set an LLM API key (any one)
export GROQ_API_KEY=your-key     # Fast + free tier
# OR export XAI_API_KEY=your-key
# OR export OPENAI_API_KEY=your-key

python server.py
# → http://localhost:8080
```

## Tech Stack

- **Agents**: Agno Team of Agents (same framework as Project Heart supply chain agent)
- **Backend**: FastAPI — same patterns as `python_api/supply_chain_agent.py`
- **LLM**: Groq / xAI / OpenAI (auto-detects from env)
- **Memory**: Agno SqliteDb — user memories + agentic memory
- **Frontend**: Vanilla HTML + Tailwind CSS (no build step)
- **Deploy**: Docker → any container platform
