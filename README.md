# alfred_ Execution Decision Layer

A prototype decision engine that determines how an AI text-message assistant should handle a proposed action: **execute silently**, **execute and notify**, **confirm before executing**, **ask a clarifying question**, or **refuse / escalate**.

Built with an **Agno Team of Agents** (Risk Assessor + Conversation Analyst + Decision Leader), persistent memory, and xAI `grok-4-1-fast-reasoning`.

- **Live URL:** https://alfred-decision-layer.onrender.com *(update after deploy)*
- **Repo:** https://github.com/SN011/alfred-decision-layer

---

## Why this design

The challenge frames this as a **contextual conversation decision problem, not one-shot classification**. The canonical failure case is:

> User asks alfred_ to draft a pricing email to Acme → alfred_ drafts it → user says "hold off until legal reviews pricing language" → minutes later, user says "Yep, send it."

A one-shot classifier reads "Yep, send it" and fires. A good decision layer recognizes the pending legal condition was never resolved.

So the system is built around two things the challenge explicitly cares about:

1. **Context over the latest message** — conversation history is a first-class input, not a retrieval afterthought.
2. **Judgment, not rules** — no regex, no keyword matching, no hand-coded risk scores. The reasoning is LLM-native.

## Architecture

```
                    ┌─────────────────────────────────────┐
                    │          Decision Leader            │
                    │  Delegates, synthesizes, decides    │
                    │  Output: structured JSON decision   │
                    └──────┬──────────────────────┬───────┘
                           │                      │
                   delegates to              delegates to
                           │                      │
             ┌─────────────▼─────────┐  ┌────────▼──────────────┐
             │    Risk Assessor      │  │ Conversation Analyst  │
             │  reversibility,       │  │ contradictions,       │
             │  external impact,     │  │ unresolved conditions,│
             │  scope, sensitivity   │  │ ambiguous references  │
             └───────────────────────┘  └───────────────────────┘

        Shared context: alfred_'s capabilities (what actions it can take
        and what their real-world effects are — sending emails is
        irreversible, reminders aren't, etc.)

        Memory: SqliteDb with user_memories + agentic_memory +
        add_history_to_context. The team learns user patterns over time.
```

## What signals the system uses, and why

All signals come from the LLM reasoning over the context. No regex, no keyword matching. Each agent is prompted to look for specific dimensions:

**Risk Assessor considers:**
- **Reversibility** — can the action be undone? (Emails can't be unsent. Reminders can be deleted.)
- **External reach** — does it touch people outside the user?
- **Scope** — one item vs. bulk operations ("this email" vs. "all emails from last 6 months")
- **Content sensitivity** — confidential data, pricing, legal language
- **Missing parameters** — what's needed to execute but not in the request?

Output: a risk paragraph ending with `LOW / MEDIUM / HIGH / CRITICAL`.

**Conversation Analyst considers:**
- **Contradictions** — "hold off" followed by "do it"
- **Unresolved conditions** — "wait until legal reviews" — was that confirmed?
- **Ambiguous references** — "cancel my meeting" when there are three
- **Intent shifts** — "actually..." / "instead..."

Output: an analysis paragraph ending with `CLEAR INTENT / AMBIGUOUS INTENT / CONFLICTING SIGNALS`.

**Decision Leader** reads both findings plus the full conversation, then returns structured JSON.

## Code vs LLM split

### What the LLM decides (everything judgment-shaped)
- Whether the action is reversible, external, sensitive
- Whether the conversation has conflicting or unresolved signals
- The final decision (`execute_silent` / `execute_notify` / `confirm` / `clarify` / `refuse`)
- Confidence
- The rationale and user-facing message

### What code does (plumbing only)
- Format the incoming message and route to the team
- Parse the leader's JSON output
- Handle failures (timeout, malformed output, missing context) with a safe fallback
- Persist conversation + memory via Agno's `SqliteDb`
- Serve the API + UI

### Why this split
An earlier draft computed deterministic "signals" — keyword matches, regex for scope, hardcoded reversibility scores. It mis-classified "call the dentist" as a bulk operation because "c**all**" contains "all". The LLM doesn't make that mistake. The model understands context; code doesn't. So code's job is formatting, parsing, persistence, and safe fallback — nothing more.

## Prompt design

Each agent gets focused instructions with four pieces:
1. **Role** — what they own ("evaluate risk" vs "analyze the conversation")
2. **Shared capabilities context** — what alfred_ can do, what's reversible, what has external impact
3. **What to look for** — specific dimensions to analyze
4. **What NOT to do** — don't make the final decision, just analyze

The Leader gets the full five-option decision framework, explicit boundaries (clarify when intent unresolved; confirm when intent resolved but risky; refuse when policy-violating), a default-safe principle ("when uncertain, escalate"), and the structured output format. See `decision_team.py`.

**The exact prompt the model receives is visible in the UI** on every decision — system message, history, memories, user input. Click "Exact Prompt Sent to Model" under any decision.

## Failure handling

| Failure | Detected by | Safe default |
|---|---|---|
| **LLM timeout** | Exception → `get_fallback_decision` | `confirm` — never silently execute |
| **Malformed output** | `JSONDecodeError` in parser | `confirm` — never silently execute |
| **Missing critical context** | Natural LLM response (e.g. "do the thing") | `clarify` — ask the user |

**The principle:** no failure path results in silent execution of an irreversible action. Every failure either asks the user or escalates.

All three failure paths are wired to buttons in the UI so they're demonstrable in one click.

## Memory

Uses Agno's `SqliteDb` with:
- `add_history_to_context=True` + `num_history_runs=5` — the last 5 turns come along in every prompt
- `enable_user_memories=True` — the team extracts and stores facts about the user
- `enable_agentic_memory=True` — the team can decide when to update memories based on the run

Over time, the team learns patterns like "this user always wants external emails confirmed" and adjusts. The UI has a live **Memories** panel so you can see memories appear as you chat.

## Scenarios (8 preloaded)

| # | Name | Difficulty | Expected |
|---|---|---|---|
| 1 | Set a reminder | easy | execute_silent |
| 2 | Check tomorrow's calendar | easy | execute_silent |
| 3 | Quick reply to teammate | easy | execute_notify |
| 4 | Cancel which meeting? | ambiguous | clarify |
| 5 | Reschedule with external client | ambiguous | confirm |
| 6 | Send after "hold off" (the canonical example) | ambiguous | confirm |
| 7 | Delete 6 months of emails | risky | refuse |
| 8 | Forward board notes to press | risky | refuse |

Scenario 6 is the one from the challenge doc. The Conversation Analyst should catch that "hold off until legal reviews" is an unresolved condition — "Yep, send it" doesn't confirm legal finished reviewing.

## How this evolves as alfred_ gains riskier tools

- **More agents, not more rules.** Add a Policy Enforcer (GDPR, HIPAA, org policy) and a Trust Scorer that adjusts thresholds based on each user's correction history. The team pattern scales; a rule engine doesn't.
- **Action-specific sub-teams.** Financial transactions, bulk data operations, external communications get routed to specialist sub-teams with extra scrutiny and their own risk models.
- **Standing permissions.** "Always approve internal calendar moves" — user-granted blanket trust, reducing unnecessary confirmation friction.
- **Risk-adjusted latency budgets.** Calendar checks should be instant; sending an external email can afford 2 seconds of deliberation.

## What I'd build next (6-month roadmap if I owned this)

**Month 1–2 — Foundation**
- User feedback loop: in-UI "you didn't need to ask" / "you should have asked" signals that feed back into per-user calibration
- Full audit log of every decision with the exact prompt, analyst outputs, and final decision (for trust and debugging)
- Latency budgets per action type

**Month 3–4 — Learning**
- Anomaly detection on action patterns (user who never sends bulk emails suddenly does → escalate)
- Multi-step action chains evaluated together, not turn-by-turn
- A/B framework for decision thresholds
- Organization-level policy engine

**Month 5–6 — Scale**
- Fine-tuned decision model on real user feedback — faster, cheaper, more reliable than a general LLM for this task
- Compliance integration (GDPR, HIPAA) injected as team context
- Real-time risk dashboard for alfred_ operators
- Delegation protocols with standing permissions

## What I chose not to build

- **Real action execution.** The prototype decides but doesn't actually send emails or edit calendars. The decision layer is the product.
- **Streaming.** Request/response is fine at this stage; streaming is a polish item.
- **Per-user auth.** Everyone is `demo_user`. Production would personalize and scope memory per-user.
- **Fine-tuned model.** Prompt-engineered general LLM is faster to build; a fine-tune is a later optimization.

## Running locally

```bash
git clone https://github.com/SN011/alfred-decision-layer
cd alfred-decision-layer

pip install -r requirements.txt

cp .env.example .env
# Edit .env and add ONE of: XAI_API_KEY, OPENAI_API_KEY, or GROQ_API_KEY

python server.py
# → http://localhost:8080
```

## Deploy to Render (one-click)

This repo includes a `render.yaml`. On [render.com](https://render.com):

1. New → Blueprint → connect this repo
2. Render reads `render.yaml` and creates the web service
3. Set the `XAI_API_KEY` (or OpenAI / Groq) env var in the Render dashboard
4. Deploy

See [RENDER_DEPLOY.md](./RENDER_DEPLOY.md) for the step-by-step.

## Tech stack

- **Agents:** Agno Team of Agents
- **Backend:** FastAPI
- **LLM:** xAI `grok-4-1-fast-reasoning` (falls back to OpenAI or Groq if xAI key not set)
- **Memory:** Agno `SqliteDb` (user memories + agentic memory + chat history)
- **Frontend:** Vanilla HTML + Tailwind (no build step)
- **Deploy:** Render (Docker optional)
