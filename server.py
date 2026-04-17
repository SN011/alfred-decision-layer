"""
Alfred Decision Layer — FastAPI Server

Chat-based API. User sends messages, team evaluates and responds.
Agno memory handles conversation history — no manual JSON input.

Follows python_api/supply_chain_agent.py patterns exactly.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
import os
import json
import time
import uuid
from pathlib import Path
from dotenv import load_dotenv
from agno.db.sqlite import SqliteDb

load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path(__file__).parent.parent / ".env.local")

from decision_team import (
    create_decision_team,
    get_fallback_decision,
    DecisionOutput,
    LEADER_INSTRUCTIONS,
    ALFRED_CAPABILITIES,
)
from scenarios import get_all_scenarios, get_scenario_by_id

app = FastAPI(title="alfred_ Execution Decision Layer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

team = None
db: Optional[SqliteDb] = None
model_name: str = "none"


def extract_prompt(response) -> list:
    """Pull the exact messages sent to the model. This is THE prompt — system
    instructions + memory + history + user message — as Agno assembled it."""
    msgs = getattr(response, "messages", None) or []
    out = []
    for m in msgs:
        role = getattr(m, "role", None)
        content = getattr(m, "content", None)
        if content is None and hasattr(m, "get_content_string"):
            try:
                content = m.get_content_string()
            except Exception:
                content = ""
        out.append({"role": role, "content": content or ""})
    return out


def extract_member_runs(response) -> list:
    """Extract each team member's run so we can show what each agent said."""
    member_responses = getattr(response, "member_responses", None) or []
    out = []
    for mr in member_responses:
        out.append({
            "agent_name": getattr(mr, "agent_name", None) or getattr(mr, "team_name", "member"),
            "content": mr.content if isinstance(mr.content, str) else str(mr.content or ""),
            "messages": [
                {"role": getattr(m, "role", None), "content": getattr(m, "content", "") or ""}
                for m in (getattr(mr, "messages", None) or [])
            ],
        })
    return out


# ═══════════════════════════════════════════════════════════════════════════
# REQUEST MODELS
# ═══════════════════════════════════════════════════════════════════════════

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    user_id: Optional[str] = "demo_user"


class FailureRequest(BaseModel):
    failure_type: str


# ═══════════════════════════════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup():
    global team, db, model_name
    print("Starting alfred_ Decision Layer...")

    os.makedirs("tmp", exist_ok=True)
    db = SqliteDb(db_file="tmp/alfred_decisions.db")
    print("  Session database initialized")

    try:
        team, model_name = create_decision_team(db)
        print(f"  Decision Team ready ({model_name})")
        print(f"  Members: {[m.name for m in team.members]}")
        print(f"  Memory: enabled")
    except Exception as e:
        print(f"  Team creation failed: {e}")


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "alfred-decision-layer",
        "model": model_name,
        "team_ready": team is not None,
        "members": [m.name for m in team.members] if team else [],
        "memory": "enabled",
        "scenarios_loaded": len(get_all_scenarios()),
    }


# ═══════════════════════════════════════════════════════════════════════════
# CHAT — single message, Agno handles history
# ═══════════════════════════════════════════════════════════════════════════

@app.post("/chat")
async def chat(body: ChatRequest):
    session_id = body.session_id or str(uuid.uuid4())
    start = time.time()

    if team is None:
        return JSONResponse(status_code=503, content={
            "error": "Decision team not initialized. Check API keys.",
        })

    try:
        response = team.run(
            body.message,
            stream=False,
            session_id=session_id,
            user_id=body.user_id,
        )

        latency_ms = round((time.time() - start) * 1000, 1)
        raw_content = response.content if isinstance(response.content, str) else str(response.content)

        decision = None
        try:
            cleaned = raw_content.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            decision = json.loads(cleaned)
        except (json.JSONDecodeError, IndexError):
            decision = {
                "decision": "unknown",
                "confidence": 0.5,
                "rationale": raw_content,
                "user_facing_message": raw_content,
                "risk_assessment": "",
                "conversation_analysis": "",
                "key_factors": [],
                "risks_identified": [],
            }

        return {
            "decision": decision,
            "raw_content": raw_content,
            "prompt_sent": extract_prompt(response),
            "member_runs": extract_member_runs(response),
            "session_id": session_id,
            "model": model_name,
            "latency_ms": latency_ms,
            "is_fallback": False,
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        latency_ms = round((time.time() - start) * 1000, 1)
        return {
            "decision": get_fallback_decision(str(e)),
            "raw_content": "",
            "session_id": session_id,
            "model": model_name,
            "latency_ms": latency_ms,
            "is_fallback": True,
            "error": str(e),
        }


# ═══════════════════════════════════════════════════════════════════════════
# SCENARIOS
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/scenarios")
async def list_scenarios():
    return {"scenarios": get_all_scenarios()}


@app.post("/api/play-scenario")
async def play_scenario(body: dict):
    """Play a multi-turn scenario. Sends each turn through the team with the same session."""
    scenario_id = body.get("scenario_id")
    scenario = get_scenario_by_id(scenario_id)
    if not scenario:
        return JSONResponse(status_code=404, content={"error": f"Scenario '{scenario_id}' not found"})

    if team is None:
        return JSONResponse(status_code=503, content={"error": "Team not initialized"})

    session_id = str(uuid.uuid4())
    results = []

    for turn_msg in scenario["turns"]:
        start = time.time()
        try:
            response = team.run(
                turn_msg,
                stream=False,
                session_id=session_id,
                user_id="demo_user",
            )
            latency_ms = round((time.time() - start) * 1000, 1)
            raw_content = response.content if isinstance(response.content, str) else str(response.content)

            decision = None
            try:
                cleaned = raw_content.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                decision = json.loads(cleaned)
            except (json.JSONDecodeError, IndexError):
                decision = {
                    "decision": "unknown",
                    "rationale": raw_content,
                    "user_facing_message": raw_content,
                }

            results.append({
                "user_message": turn_msg,
                "decision": decision,
                "raw_content": raw_content,
                "latency_ms": latency_ms,
                "is_fallback": False,
            })

        except Exception as e:
            results.append({
                "user_message": turn_msg,
                "decision": get_fallback_decision(str(e)),
                "raw_content": "",
                "latency_ms": round((time.time() - start) * 1000, 1),
                "is_fallback": True,
                "error": str(e),
            })

    return {
        "scenario": scenario,
        "session_id": session_id,
        "model": model_name,
        "turns": results,
    }


# ═══════════════════════════════════════════════════════════════════════════
# FAILURE SIMULATION
# ═══════════════════════════════════════════════════════════════════════════

@app.post("/api/simulate-failure")
async def simulate_failure(body: FailureRequest):
    """Demonstrate how the Decision Layer handles failure modes.

    Returns structured diagnostic data so the UI can render each as a
    labelled pipeline view: INPUT → DETECTION → FALLBACK → USER-FACING."""

    if body.failure_type == "timeout":
        malformed_raw = ""
        detection_error = "asyncio.TimeoutError after 30000ms — no response from model"
        fallback = get_fallback_decision("LLM request timed out after 30s")
        return {
            "failure_type": "timeout",
            "kind": "pipeline_failure",
            "simulated_input": "Send the quarterly revenue deck to board@acme.com",
            "detection": {
                "where": "server.py /chat handler, team.run() call",
                "signal": "Exception raised — request exceeded deadline",
                "error": detection_error,
            },
            "code_path": "except Exception → get_fallback_decision(error)",
            "principle": "When we can't get a decision, we never silently execute. We always fall back to 'confirm' so the human stays in control.",
            "decision": fallback,
            "raw_content": malformed_raw,
            "latency_ms": 30000,
            "is_fallback": True,
        }

    if body.failure_type == "malformed":
        broken_json = '{"decision": "sure_go_ahead", confidence: very_high, rationale: "missing quotes and invalid enum"}'
        fallback = get_fallback_decision("JSONDecodeError while parsing model output")
        return {
            "failure_type": "malformed",
            "kind": "pipeline_failure",
            "simulated_input": "Cancel my 2pm meeting with Sarah",
            "detection": {
                "where": "server.py /chat handler, json.loads(response.content)",
                "signal": "json.JSONDecodeError — unquoted keys, invalid enum value",
                "error": "Expecting property name enclosed in double quotes: line 1 column 36 (char 35)",
            },
            "code_path": "except JSONDecodeError → get_fallback_decision(error)",
            "principle": "A model that returns broken JSON is a model whose reasoning we can't trust. Default to confirm.",
            "decision": fallback,
            "raw_content": broken_json,
            "latency_ms": 840,
            "is_fallback": True,
        }

    if body.failure_type == "missing_context":
        simulated = "Do the thing"
        if team:
            try:
                start = time.time()
                response = team.run(simulated, stream=False, session_id=str(uuid.uuid4()), user_id="demo_user")
                latency_ms = round((time.time() - start) * 1000, 1)
                raw = response.content if isinstance(response.content, str) else str(response.content)
                try:
                    cleaned = raw.strip()
                    if cleaned.startswith("```"):
                        cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                    decision = json.loads(cleaned)
                except Exception:
                    decision = {
                        "decision": "clarify",
                        "rationale": raw,
                        "user_facing_message": raw,
                        "confidence": 0.5,
                    }
                return {
                    "failure_type": "missing_context",
                    "kind": "happy_path_demo",
                    "simulated_input": simulated,
                    "detection": {
                        "where": "Conversation Analyst (agent reasoning, not code)",
                        "signal": "Agent identifies no parseable action, no entities, no history",
                        "error": None,
                    },
                    "code_path": "team.run() → Conversation Analyst flags ambiguous intent → Leader returns clarify",
                    "principle": "Missing context isn't a bug — the team recognizes it and asks. No fallback was needed; the system handled it on its own.",
                    "decision": decision,
                    "raw_content": raw,
                    "latency_ms": latency_ms,
                    "is_fallback": False,
                }
            except Exception as e:
                fallback = get_fallback_decision(f"Team run failed: {e}")
                return {
                    "failure_type": "missing_context",
                    "kind": "pipeline_failure",
                    "simulated_input": simulated,
                    "detection": {
                        "where": "team.run()",
                        "signal": "Exception during team execution",
                        "error": str(e),
                    },
                    "code_path": "except Exception → get_fallback_decision(error)",
                    "principle": "Even if the team fails on ambiguous input, we still ask the user rather than guessing.",
                    "decision": fallback,
                    "raw_content": "",
                    "latency_ms": 0,
                    "is_fallback": True,
                }
        fallback = get_fallback_decision("Team not initialized")
        fallback["decision"] = "clarify"
        fallback["user_facing_message"] = "I'm not sure what you'd like me to do. Could you give me more details?"
        return {
            "failure_type": "missing_context",
            "kind": "pipeline_failure",
            "simulated_input": simulated,
            "detection": {"where": "startup", "signal": "Team unavailable", "error": "Team was not initialized — API key missing?"},
            "code_path": "fallback",
            "principle": "Even with no team, we ask rather than act.",
            "decision": fallback,
            "raw_content": "",
            "latency_ms": 0,
            "is_fallback": True,
        }

    return JSONResponse(status_code=400, content={"error": f"Unknown: {body.failure_type}"})


# ═══════════════════════════════════════════════════════════════════════════
# MEMORIES & HISTORY (live Agno state)
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/memories")
async def get_memories(user_id: str = "demo_user"):
    """Return all user memories the team has stored."""
    if not db:
        return {"memories": []}
    try:
        memories = db.get_user_memories(user_id=user_id)
        return {
            "memories": [
                {
                    "memory_id": m.memory_id,
                    "content": m.input,
                    "topics": m.topics,
                    "created_at": str(m.created_at) if m.created_at else None,
                    "updated_at": str(m.updated_at) if m.updated_at else None,
                }
                for m in (memories if isinstance(memories, list) else memories[0])
            ]
        }
    except Exception as e:
        return {"memories": [], "error": str(e)}


@app.get("/api/history")
async def get_history(session_id: str):
    """Return conversation history for a session from Agno's DB."""
    if not db:
        return {"messages": []}
    try:
        from agno.db.base import SessionType
        session = db.get_session(session_id=session_id, session_type=SessionType.TEAM, user_id="demo_user")
        if not session:
            return {"messages": [], "session_id": session_id}
        messages = []
        for run in session.runs:
            if run.input and run.input.input_content:
                messages.append({"role": "user", "content": run.input.input_content})
            if run.content:
                messages.append({"role": "assistant", "content": run.get_content_as_string()})
        return {"messages": messages, "session_id": session_id}
    except Exception as e:
        return {"messages": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# TEAM CONFIG (for under-the-hood view)
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/team-config")
async def team_config():
    if not team:
        return {"error": "Team not initialized"}
    return {
        "leader_instructions": LEADER_INSTRUCTIONS,
        "capabilities": ALFRED_CAPABILITIES,
        "members": [
            {"name": m.name, "role": m.role, "instructions": m.instructions}
            for m in team.members
        ],
        "model": model_name,
        "memory": {
            "user_memories": True,
            "agentic_memory": True,
            "history_runs": 5,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# STATIC FILES
# ═══════════════════════════════════════════════════════════════════════════

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def serve_frontend():
    index = static_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "alfred_ Decision Layer API — see /docs"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    print(f"Starting on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
