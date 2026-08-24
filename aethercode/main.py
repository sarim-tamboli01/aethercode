import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, cast

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import settings
from graph import run_aethercode, stream_aethercode

app = FastAPI(
    title="AetherCode",
    description="Autonomous Multi-Agent Software Engineering System",
    version="1.0.0",
)

_cors_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
if settings.CORS_ORIGINS.strip():
    _cors_origins.extend(
        origin.strip()
        for origin in settings.CORS_ORIGINS.split(",")
        if origin.strip()
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

MAX_STRING_LEN = 4000


def _serialize_value(value: Any) -> Any:
    """Recursively convert graph state values to JSON-safe primitives."""
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > MAX_STRING_LEN:
            return value[:MAX_STRING_LEN] + "… [truncated]"
        return value

    if isinstance(value, Enum):
        return value.value

    if is_dataclass(value):
        return _serialize_value(asdict(cast(Any, value)))

    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]

    return str(value)


def serialize_state(state: dict) -> dict:
    """
    JSON-serialize a GraphState dict. Omits bulky repo chunks (replaced
    with a count) so SSE payloads stay small enough to stream.
    """
    serialized = _serialize_value(state)
    chunks = state.get("all_chunks")
    if chunks is not None:
        serialized["all_chunks"] = None
        serialized["chunk_count"] = len(chunks)
    return serialized


@app.get("/health")
async def health_check():
    """Basic health check endpoint."""
    return {
        "status": "ok",
        "app": "AetherCode",
        "supabase_configured": bool(settings.SUPABASE_URL),
        "github_configured": bool(settings.GITHUB_TOKEN),
    }


class RunTaskRequest(BaseModel):
    task: str
    repo_url: str
    apply_changes: bool = True


class RunTaskResponse(BaseModel):
    task: str
    repo_url: str
    halted: bool
    halt_reason: str
    awaiting_human_approval: bool
    pr_url: str | None = None
    steps_completed: int
    steps_total: int


def _sse_line(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _done_payload(final_state: dict) -> dict:
    pr_result = final_state.get("pr_result")
    pr_url = None
    if pr_result and getattr(pr_result, "success", False):
        pr_url = pr_result.pr_url

    plan = final_state.get("plan")
    return {
        "event": "done",
        "halted": final_state.get("halted", False),
        "halt_reason": final_state.get("halt_reason", ""),
        "awaiting_human_approval": final_state.get("awaiting_human_approval", False),
        "pr_url": pr_url,
        "steps_completed": final_state.get("current_step_index", 0),
        "steps_total": len(plan.steps) if plan else 0,
    }


@app.post("/run-task", response_model=RunTaskResponse)
async def run_task(request: RunTaskRequest):
    """
    Runs the full AetherCode pipeline against a given repo for a
    given natural language task.

    Returns as soon as the graph either halts (with a reason) or
    reaches the Human Approval Gate (PR opened, awaiting review).
    Never merges anything automatically — that action does not
    exist anywhere in this codebase.
    """
    if not settings.GITHUB_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="GITHUB_TOKEN not configured — cannot open pull requests.",
        )

    try:
        final_state = run_aethercode(
            task=request.task,
            repo_url=request.repo_url,
            apply_changes=request.apply_changes,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline crashed: {e}")

    plan = final_state.get("plan")
    pr_result = final_state.get("pr_result")

    return RunTaskResponse(
        task=request.task,
        repo_url=request.repo_url,
        halted=final_state.get("halted", False),
        halt_reason=final_state.get("halt_reason", ""),
        awaiting_human_approval=final_state.get("awaiting_human_approval", False),
        pr_url=pr_result.pr_url if pr_result and pr_result.success else None,
        steps_completed=final_state.get("current_step_index", 0),
        steps_total=len(plan.steps) if plan else 0,
    )


@app.post("/run-task/stream")
async def run_task_stream(request: RunTaskRequest):
    """
    SSE variant of /run-task. Emits one JSON event per graph node,
    then a final 'done' event with PR URL or halt reason.
    """
    if not settings.GITHUB_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="GITHUB_TOKEN not configured — cannot open pull requests.",
        )

    def event_generator():
        final_state = None
        try:
            for node_name, state_update, full_state in stream_aethercode(
                task=request.task,
                repo_url=request.repo_url,
                apply_changes=request.apply_changes,
            ):
                final_state = full_state
                yield _sse_line({
                    "event": "step",
                    "node": node_name,
                    "state_update": serialize_state(state_update),
                    "full_state": serialize_state(full_state),
                })

            if final_state is not None:
                yield _sse_line(_done_payload(final_state))
            else:
                yield _sse_line({
                    "event": "error",
                    "detail": "Pipeline produced no events.",
                })
        except Exception as e:
            yield _sse_line({"event": "error", "detail": f"Pipeline crashed: {e}"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
