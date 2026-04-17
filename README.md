# alfred_ Execution Decision Layer

A prototype decision engine that determines how an AI text-message assistant should handle a proposed action: **execute silently**, **execute and notify**, **confirm before executing**, **ask a clarifying question**, or **refuse / escalate**.

The system is implemented as a multi-agent team built on the [Agno](https://docs.agno.com/) framework, backed by persistent memory and running `xai/grok-4-1-fast-reasoning` as the reasoning model.

- **Live URL:** https://alfred-decision-layer.onrender.com *(update after deploy)*
- **Repo:** https://github.com/SN011/alfred-decision-layer

---

## Problem framing

The challenge specification makes an important distinction: this is *a contextual conversation decision problem, not a one-shot prompt classification task*. The canonical failure case:

> The user asks alfred_ to draft an external pricing email. alfred_ drafts it. The user says *"hold off until legal reviews the pricing language."* Several minutes later, the user says *"Yep, send it."*

A one-shot classifier reads the final message in isolation, scores it as high-confidence confirmation, and sends the email. A robust decision layer must recognize that the legal-review condition was never explicitly satisfied and, therefore, treat the confirmation as ambiguous.

Two design commitments follow from this framing:

1. **Conversation history is a first-class input.** Every decision must consider accumulated context, not only the latest turn.
2. **Judgment is delegated to reasoning, not encoded as rules.** Hand-authored heuristics (keyword matches, regex patterns, fixed risk scores) do not generalize across the space of natural-language directives the user might issue.

Both commitments have direct implications for the architecture.

---

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

        Shared context: alfred_'s action capabilities and their
        real-world effects (what is reversible, what affects third
        parties, what is purely informational).

        Persistent state: SqliteDb with user_memories + agentic_memory
        + add_history_to_context=True. The team accumulates user-
        specific context across sessions.
```

Two analyst agents examine orthogonal dimensions of the problem; a leader agent synthesizes their findings and produces a structured decision. This separation allows each analyst to be optimized against a narrow, well-defined responsibility.

---

## Framework selection: why Agno

Agno was selected over the other production agentic-AI frameworks (LangGraph, CrewAI, AutoGen, PydanticAI). The rationale is based on three criteria that matter for this specific problem:

### 1. First-class multi-agent **Team** primitive
The decision layer is a classic delegate-and-synthesize pattern. Agno's `Team` is a native construct that supports member agents, delegation, shared memory, and a structured synthesis step. LangGraph can express this as a state machine over a graph, but imposes substantial boilerplate for what is conceptually a simple hierarchy. CrewAI offers role-based crews but biases toward linear task sequences rather than fan-out/synthesis. AutoGen models agents as message-passing actors, which requires implementing coordination semantics manually.

### 2. Built-in persistent memory with semantic recall
The product requirement explicitly includes *user state* as input to decisions. Agno provides three memory modes out of the box:
- `add_history_to_context` — chat history injected into each prompt
- `enable_user_memories` — extraction and storage of durable user facts
- `enable_agentic_memory` — agent-directed memory updates

All three are backed by a single `SqliteDb` with no additional integration work. LangGraph requires checkpointers + separate retrieval logic; CrewAI's persistence is limited; AutoGen's memory is less transparent.

### 3. Performance characteristics
Published benchmarks (Agno documentation, October 2025, Apple M4 MacBook Pro, 1000-iteration `tracemalloc` methodology):

| Metric | Agno | LangGraph | PydanticAI | CrewAI |
|---|---|---|---|---|
| Agent instantiation | **3 μs** | 1,587 μs (≈529×) | 170 μs (≈57×) | 210 μs (≈70×) |
| Per-agent memory | **6.6 KiB** | 161 KiB (≈24×) | 29 KiB (≈4×) | 66 KiB (≈10×) |

Low instantiation overhead matters for this use case because the decision layer sits on the hot path of every user message — latency directly impacts UX.

### Trade-offs

Agno is not the right choice for every agent system. Its weaknesses relative to the alternatives:

- **LangGraph** offers superior observability, explicit state-machine semantics, and better tooling for long-running stateful workflows. For a workflow with conditional branches, loops, or human-in-the-loop checkpoints across minutes or hours, LangGraph is the stronger choice.
- **CrewAI** has a gentler learning curve and a richer ecosystem of pre-built role templates for business-process agents. For rapid prototyping of marketing, research, or content workflows, CrewAI accelerates time-to-working-prototype.
- **AutoGen** is best for agents that primarily communicate through natural-language conversation loops, including code-generating agents.

For the alfred_ decision layer specifically — a single synchronous request, a small fixed team, a latency-sensitive hot path, and strong memory requirements — Agno is the best fit.

---

## Signals the system considers

All signals are derived by LLM reasoning over the input context. The code contains no keyword matching, no regex over user text, no hardcoded risk tables. The agents are prompted to analyze specific dimensions.

**Risk Assessor examines:**
- **Reversibility** — can the action be undone if it turns out to be wrong? Sending an email is irreversible; setting a reminder is fully reversible.
- **External reach** — does the action touch parties outside the user's account? Emails, calendar invites, and forwarded content do. Reminders and searches do not.
- **Scope** — is the action addressing a single item or a bulk operation? *"Delete this email"* and *"Delete all emails from the last 6 months"* have different risk profiles.
- **Content sensitivity** — does the payload contain confidential, regulated, or legally sensitive material (internal pricing, compensation, acquisition details, etc.)?
- **Missing parameters** — is critical information absent from the request (recipient, time, subject, body)?

Output format: a paragraph ending with an explicit risk tier — `LOW / MEDIUM / HIGH / CRITICAL`.

**Conversation Analyst examines:**
- **Contradictions** — direct reversals across turns (*"hold off"* followed by *"do it"*).
- **Unresolved conditions** — predicates that the user attached to prior directives and did not explicitly release (*"wait until legal reviews the pricing"*).
- **Ambiguous references** — definite references (*"my meeting"*, *"that email"*) that resolve to multiple candidates.
- **Intent shifts** — lexical markers of change-of-mind (*"actually..."*, *"instead..."*).

Output format: a paragraph ending with an explicit intent assessment — `CLEAR INTENT / AMBIGUOUS INTENT / CONFLICTING SIGNALS`.

**Decision Leader** reads both analyst outputs alongside the full conversation history and produces a structured decision.

---

## Division of responsibility: model versus code

### Delegated to the LLM
- Determining whether an action is reversible, external-facing, or sensitive
- Identifying contradictions and unresolved conditions in the conversation
- Selecting one of the five decision categories
- Producing confidence, rationale, and the user-facing message

### Handled by code
- Routing an incoming message to the team
- Parsing the leader's structured JSON output
- Catching execution failures (timeout, malformed output) and returning a safe fallback
- Persisting conversation state and memories via `SqliteDb`
- Serving the API and static frontend

### Rationale
An earlier iteration of this system computed risk signals deterministically — keyword lists, regex-based scope detection, hardcoded reversibility scores. It exhibited the pathological failure mode typical of such systems: the substring-matching scope detector classified *"call the dentist"* as a bulk operation because the word *call* contains the string *all*. No amount of additional regex hygiene recovers a system whose foundation is pattern matching over natural language.

Language models are built to handle this class of reasoning. The engineering effort is better spent on the parts of the pipeline that are mechanical — formatting, parsing, error handling, persistence — and on carefully scoping the prompts rather than hand-authoring heuristics.

---

## Prompt design

Each member agent is given a tightly scoped prompt with four sections:
1. **Role** — what this agent is responsible for (*risk assessment* vs. *conversation analysis*)
2. **Capabilities context** — a shared block describing alfred_'s action space and the real-world effects of each action type (reversibility, external impact)
3. **Directed analysis** — explicit dimensions to examine
4. **Boundaries** — an instruction not to make the final decision, which is the leader's job

The Decision Leader's prompt contains:
1. The full five-option decision framework with definitions and examples
2. Decision boundaries: *clarify* when intent is unresolved; *confirm* when intent is resolved but risk exceeds the silent-execution threshold; *refuse* when policy prohibits the action regardless of confirmation
3. A default-safe principle: when uncertain, escalate up the decision hierarchy (prefer *confirm* over *execute*, prefer *refuse* over *confirm* under severe risk)
4. A strict JSON output schema

The full leader prompt is inspectable in the UI under "Team Config". The exact prompt sent to the model on any individual decision — including system instructions, injected memories, conversation history, and the user message — is visible per-turn under "Exact Prompt Sent to Model".

---

## Memory

The team is configured with Agno's full memory stack:
- `add_history_to_context=True`, `num_history_runs=5` — the last five turns are injected into each prompt, providing conversational continuity.
- `enable_user_memories=True` — the team extracts and stores durable facts about the user across sessions.
- `enable_agentic_memory=True` — the team itself can decide when to create or update memories, rather than relying on a fixed extraction schedule.

The UI exposes the current memory contents in a live panel so the accumulation of user-specific state is observable. Over time, this enables the system to learn patterns such as *"this user consistently approves internal calendar changes silently"* and adjust its threshold accordingly.

---

## Failure handling

| Failure mode | Detection point | Safe default |
|---|---|---|
| **LLM timeout** | Exception caught at the `team.run()` call site | `confirm` — never silent-execute |
| **Malformed model output** | `json.JSONDecodeError` raised during output parsing | `confirm` — never silent-execute |
| **Missing critical context** | Detected by the team itself (not a code-level failure) | `clarify` — the team asks the user |

**Design principle:** no failure path results in silent execution of an irreversible action. Code-level failures (timeout, malformed output) trigger a structured fallback that defaults to `confirm`. Model-level failures — cases where the user's intent cannot be resolved from the input — are handled by the team's own reasoning, which produces a `clarify` decision naturally.

All three failure paths are demonstrable in the UI through dedicated buttons. Each is rendered as a diagnostic card showing the simulated input, the detection point, the raw output (where applicable), the code path taken, the final decision (flagged as either *fallback* or *team-handled*), and the governing safety principle.

---

## Scenario coverage

Eight scenarios are preloaded to exercise the decision surface:

| # | Name | Difficulty | Expected decision | Rationale |
|---|---|---|---|---|
| 1 | Set a reminder | easy | `execute_silent` | Fully reversible, no external impact, clear intent |
| 2 | Check tomorrow's calendar | easy | `execute_silent` | Read-only, zero side effects |
| 3 | Quick reply to teammate | easy | `execute_notify` | Low-risk internal action; user should be informed |
| 4 | Cancel which meeting? | ambiguous | `clarify` | Multiple candidate entities; reference unresolved |
| 5 | Reschedule with external client | ambiguous | `confirm` | Affects a third-party calendar |
| 6 | Send after *"hold off"* (challenge canonical) | ambiguous | `confirm` | Unresolved pre-condition in conversation history |
| 7 | Delete 6 months of emails | risky | `refuse` | Irreversible bulk destruction |
| 8 | Forward board notes to press | risky | `refuse` | Confidential data to external journalist; policy violation |

Scenario 6 is the canonical example from the challenge specification. The Conversation Analyst should identify that *"hold off until legal reviews the pricing language"* introduced a pending condition and that the subsequent *"Yep, send it"* does not reference or discharge that condition.

---

## Evolution as alfred_ gains higher-risk capabilities

- **Specialized agents, not additional heuristics.** A Policy Enforcer agent can encode organizational and regulatory constraints (GDPR, HIPAA, internal compliance). A Trust Scorer can adjust per-user thresholds based on the user's correction history. The multi-agent team pattern composes cleanly as new concerns are added; a rule engine does not.
- **Action-specific sub-teams.** High-consequence actions (financial transactions, bulk data operations, external communication at scale) should be routed to specialist sub-teams with their own risk models and additional scrutiny.
- **Standing permissions.** Users can grant alfred_ pre-approved blanket permission for specific action patterns, reducing confirmation friction on routine low-risk operations while preserving the confirmation step for the rest.
- **Risk-adjusted latency budgets.** Informational actions (calendar checks, email search) should execute within a few hundred milliseconds. High-risk actions can tolerate two to three seconds of deliberation and multi-agent review.

## Roadmap for the next six months

**Months 1–2: Foundation**
- Feedback loop: in-UI signals for *"you did not need to ask"* and *"you should have asked"*, captured per-user and used for threshold calibration.
- Structured audit log of every decision (full prompt, analyst outputs, final decision, timing), for trust, debugging, and post-hoc analysis.
- Per-action-type latency budgets with alerting when exceeded.

**Months 3–4: Learning**
- Anomaly detection on action patterns at the per-user level (e.g., a user who never sends bulk email suddenly requests a bulk send).
- Compound-action evaluation: multi-step chains of dependent actions evaluated as a unit rather than turn-by-turn.
- A/B framework for decision threshold experimentation.
- Organization-level policy engine with versioned rule sets.

**Months 5–6: Scale**
- Fine-tuned decision model trained on accumulated user-feedback signals — expected improvements in latency, cost, and consistency over a general-purpose LLM.
- Compliance integration: GDPR, HIPAA, and organization-specific policies injected as team context.
- Real-time risk dashboard for alfred_ operators (decisions per second, confirm/refuse rates, latency distributions).
- Delegation protocols supporting standing permissions and revocation.

---

## Scope decisions

Intentionally out of scope for this prototype:
- **Action execution.** The system produces decisions but does not actually send emails, modify calendars, or set reminders. The decision layer is the unit under evaluation.
- **Streaming responses.** The protocol is synchronous request/response. Streaming is a user-experience optimization, not a correctness concern for the decision layer.
- **Per-user authentication and isolation.** All sessions use a single demo user identifier. A production system would scope sessions and memory per authenticated user.
- **Fine-tuned model.** The prototype uses a general-purpose frontier model with prompt engineering. A fine-tuned decision model is expected to improve both latency and consistency but is a later optimization.

---

## Local development

```bash
git clone https://github.com/SN011/alfred-decision-layer
cd alfred-decision-layer

pip install -r requirements.txt

cp .env.example .env
# Add one of: XAI_API_KEY, OPENAI_API_KEY, or GROQ_API_KEY

python server.py
# Served at http://localhost:8080
```

## Deployment

The repository includes a `render.yaml` Blueprint for one-click deployment to [Render](https://render.com). See [RENDER_DEPLOY.md](./RENDER_DEPLOY.md) for step-by-step instructions. A `Dockerfile` is provided for deployment to other container platforms.

## Technical stack

- **Agent framework:** Agno 2.3.x (Teams + SqliteDb)
- **Reasoning model:** xAI `grok-4-1-fast-reasoning` (with OpenAI and Groq as configured fallbacks)
- **API:** FastAPI
- **Memory:** Agno `SqliteDb` (user memories, agentic memory, chat history)
- **Frontend:** Vanilla HTML + Tailwind CSS (no build step)
- **Deployment:** Render (Python runtime) or any Docker-compatible platform
