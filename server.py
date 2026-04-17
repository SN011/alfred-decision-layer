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

        # Try to parse structured decision from response
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
    if body.failure_type == "timeout":
        return {
            "decision": get_fallback_decision("LLM request timed out after 30 seconds"),
            "raw_content": "[TIMEOUT — no response received]",
            "latency_ms": 30000,
            "is_fallback": True,
            "failure_type": "timeout",
        }
    elif body.failure_type == "malformed":
        return {
            "decision": get_fallback_decision("Model returned invalid JSON"),
            "raw_content": '{"decision": "sure_go_ahead", confidence: very_high, rationale: missing quotes}',
            "latency_ms": 800,
            "is_fallback": True,
            "failure_type": "malformed_output",
        }
    elif body.failure_type == "missing_context":
        if team:
            try:
                response = team.run("Do the thing", stream=False, session_id=str(uuid.uuid4()), user_id="demo_user")
                raw = response.content if isinstance(response.content, str) else str(response.content)
                try:
                    decision = json.loads(raw.strip())
                except:
                    decision = {"decision": "clarify", "rationale": raw, "user_facing_message": raw}
                return {
                    "decision": decision,
                    "raw_content": raw,
                    "latency_ms": 0,
                    "is_fallback": False,
                    "failure_type": "missing_context",
                }
            except Exception as e:
                pass
        fallback = get_fallback_decision("No recognizable action or context")
        fallback["decision"] = "clarify"
        fallback["user_facing_message"] = "I'm not sure what you'd like me to do. Could you give me more details?"
        return {
            "decision": fallback,
            "raw_content": "",
            "latency_ms": 0,
            "is_fallback": True,
            "failure_type": "missing_context",
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
