import logging
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from agents.stage1_business_interview.graph import build_stage1_graph
from agents.stage2_employee_interview.graph import build_stage2_graph
from agents.stage3_document_generation.generator import GenerationRequest, generate_document
from api.persistence.checkpointer import create_checkpointer
from api.session_manager import get_session_store
from api.webhooks import on_document_generated, on_stage1_complete, on_stage2_complete
from config.settings import settings
from output.exporters.word_exporter import generate_docx
from output.formatters.document_formatter import parse_llm_output

logger = logging.getLogger(__name__)


# ---- Request / Response Models ----

class CreateStage2Request(BaseModel):
    # Employee sessions are created from a single-use invite token, never from
    # the Stage 1 session ID — the manager's session must stay private.
    invite_token: str

class SendMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)

class GenerateDocumentRequest(BaseModel):
    format: str = Field(default="docx", pattern=r"^(docx|pdf)$")

class SessionCreatedResponse(BaseModel):
    session_id: str
    message: str

class MessageResponse(BaseModel):
    message: str
    session_complete: bool = False
    profile: Optional[dict] = None
    # Returned once, on Stage 1 completion (manager's own session):
    invite_token: Optional[str] = None    # share link for the employee
    manager_token: Optional[str] = None   # gates document generation/download

class SessionStatusResponse(BaseModel):
    session_id: str
    stage: int
    session_complete: bool
    current_block: Optional[str] = None
    current_question_index: Optional[int] = None

class HistoryMessage(BaseModel):
    role: str  # "agent" | "user"
    content: str

class SessionHistoryResponse(BaseModel):
    session_id: str
    stage: int
    session_complete: bool
    current_block: Optional[str] = None
    messages: List[HistoryMessage] = []

class GenerateDocumentResponse(BaseModel):
    document_id: str
    download_url: str
    status: str = "generating"

class GenerationStatusResponse(BaseModel):
    document_id: str
    status: str  # "generating" | "complete" | "failed"
    download_url: Optional[str] = None
    error: Optional[str] = None

class ManagerOverviewResponse(BaseModel):
    stage1_session_id: str
    stage2_status: str  # "not_started" | "in_progress" | "complete"
    risk_flag_count: Optional[int] = None
    document_id: Optional[str] = None
    document_status: Optional[str] = None  # "generating" | "complete" | "failed"
    download_url: Optional[str] = None
    document_error: Optional[str] = None


# ---- GraphRegistry ----

@dataclass
class GraphInstance:
    graph: Any
    config: dict
    stage: int
    checkpointer: Any


class GraphRegistry:
    """Builds one compiled graph per stage over a single shared checkpointer.

    A GraphInstance is a lightweight (graph, config, stage) view — the actual
    conversation state lives in the checkpointer, keyed by thread_id (the
    session_id). Because the checkpointer is shared and the graphs are stateless
    to build, an instance for any session can be produced on demand — even in a
    process that never created it — which is what makes the API stateless and
    survive restarts (with a durable checkpointer).
    """

    def __init__(self, checkpointer=None):
        self._checkpointer = checkpointer or MemorySaver()
        self._graphs = {
            1: build_stage1_graph(checkpointer=self._checkpointer),
            2: build_stage2_graph(checkpointer=self._checkpointer),
        }
        self._locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def instance_for(self, session_id: str, stage: int) -> GraphInstance:
        return GraphInstance(
            graph=self._graphs[stage],
            config={"configurable": {"thread_id": session_id}},
            stage=stage,
            checkpointer=self._checkpointer,
        )

    def create_stage1(self, session_id: str) -> GraphInstance:
        return self.instance_for(session_id, 1)

    def create_stage2(self, session_id: str) -> GraphInstance:
        return self.instance_for(session_id, 2)

    def get_lock(self, session_id: str) -> threading.Lock:
        with self._locks_guard:
            if session_id not in self._locks:
                self._locks[session_id] = threading.Lock()
            return self._locks[session_id]


def _rehydrate_instance(session_id: str, session: Optional[dict] = None) -> Optional[GraphInstance]:
    """Build a graph instance for an existing session, loading state from the
    (shared, possibly durable) checkpointer. Returns None if the session is
    unknown or has no persisted conversation state yet."""
    store = get_session_store()
    session = session or store.get_session(session_id)
    if not session:
        return None
    stage = session.get("stage")
    if stage not in (1, 2):
        return None
    instance = _registry.instance_for(session_id, stage)
    # A session with no checkpoint (empty state) can't be resumed.
    if not instance.graph.get_state(instance.config).values:
        return None
    return instance


# ---- Graph Invocation Helpers ----

def _run_graph_initial(instance: GraphInstance, initial_state: dict) -> dict:
    """Stream graph from START, return final state at interrupt."""
    result = None
    for event in instance.graph.stream(initial_state, instance.config, stream_mode="values"):
        result = event
    return result


def _run_graph_resume(instance: GraphInstance, user_message: str) -> dict:
    """Inject HumanMessage into state, resume graph, return final state."""
    instance.graph.update_state(
        instance.config,
        {"conversation_history": [HumanMessage(content=user_message)]},
    )
    result = None
    for event in instance.graph.stream(None, instance.config, stream_mode="values"):
        result = event
    return result


# ---- App Factory ----

def create_app() -> FastAPI:
    app = FastAPI(title="KnowledgeKeeper API", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins.split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


app = create_app()

# Validate settings on startup
settings.validate_for_production()

# Serve frontend static files in production
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="static-assets")

# Module-level singletons. The registry's checkpointer is durable when
# STORAGE_BACKEND=persistent, so sessions survive a restart.
_registry = GraphRegistry(checkpointer=create_checkpointer())
_document_store: Dict[str, str] = {}  # document_id -> file_path
_generation_jobs: Dict[str, Dict[str, Any]] = {}  # document_id -> {status, download_url, error}
_document_owners: Dict[str, str] = {}  # document_id -> stage1_session_id (for manager token checks)


# ---- Background Generation Worker ----

def _run_generation_in_background(
    document_id: str,
    session_id: str,
    gen_request: GenerationRequest,
    profile: Any,
    output_format: str,
):
    """Run document generation in a background thread."""
    try:
        gen_result = generate_document(gen_request)

        # Parse and export
        interim_doc = parse_llm_output(gen_result.raw_markdown, profile, session_id)

        output_dir = tempfile.mkdtemp(prefix="kk_")

        if output_format == "pdf":
            try:
                from output.exporters.pdf_exporter import generate_pdf
                file_path = os.path.join(output_dir, f"{document_id}.pdf")
                file_path = generate_pdf(interim_doc, file_path)
            except (ImportError, RuntimeError) as e:
                logger.warning("PDF export unavailable (%s), falling back to DOCX", e)
                file_path = os.path.join(output_dir, f"{document_id}.docx")
                file_path = generate_docx(interim_doc, file_path)
        else:
            file_path = os.path.join(output_dir, f"{document_id}.docx")
            file_path = generate_docx(interim_doc, file_path)

        _document_store[document_id] = file_path
        _generation_jobs[document_id] = {
            "status": "complete",
            "download_url": f"/api/documents/{document_id}",
            "error": None,
        }

        on_document_generated(session_id, document_id, file_path)
        logger.info("session=%s document=%s format=%s generation=complete", session_id, document_id, output_format)

    except Exception as e:
        logger.error("session=%s document=%s generation failed: %s", session_id, document_id, e)
        _generation_jobs[document_id] = {
            "status": "failed",
            "download_url": None,
            "error": str(e),
        }


# ---- Endpoints ----

@app.get("/api/health")
def health_check():
    # Deliberately minimal — no key previews, env var names, or config values.
    return {
        "status": "ok",
        "api_key_set": bool(settings.anthropic_api_key),
    }


@app.post("/api/sessions/stage1", response_model=SessionCreatedResponse)
def create_stage1():
    store = get_session_store()
    session_id = store.create_session(stage=1)

    instance = _registry.create_stage1(session_id)

    initial_state = {
        "session_id": session_id,
        "business_name": "",
        "current_block": "business_context",
        "current_question_index": 0,
        "answers": {},
        "conversation_history": [],
        "role_intelligence_profile": None,
        "profile_confirmed": False,
        "session_complete": False,
        "followup_count": 0,
        "pending_followup": None,
        "last_agent_message": "",
    }

    state = _run_graph_initial(instance, initial_state)
    greeting = state.get("last_agent_message", "")

    logger.info("session=%s stage=1 action=created", session_id)
    return SessionCreatedResponse(session_id=session_id, message=greeting)


@app.post("/api/sessions/stage2", response_model=SessionCreatedResponse)
def create_stage2(request: CreateStage2Request):
    store = get_session_store()

    # Resolve the single-use invite token — the employee never sees the
    # Stage 1 session ID
    if store.is_invite_token_used(request.invite_token):
        raise HTTPException(
            status_code=410,
            detail="This interview link has already been used. Please ask for a new link.",
        )
    stage1_session_id = store.resolve_invite_token(request.invite_token)
    if not stage1_session_id:
        raise HTTPException(status_code=404, detail="Invalid or expired interview link")

    # Validate Stage 1 session exists
    stage1_session = store.get_session(stage1_session_id)
    if not stage1_session:
        raise HTTPException(status_code=404, detail="Stage 1 session not found")

    # Validate profile exists
    profile = store.get_profile(stage1_session_id)
    if not profile:
        raise HTTPException(status_code=400, detail="Stage 1 profile not yet generated")

    session_id = store.create_session(stage=2, metadata={"stage1_session_id": stage1_session_id})
    store.link_sessions(stage1_session_id, session_id)
    store.consume_invite_token(request.invite_token)

    instance = _registry.create_stage2(session_id)

    initial_state = {
        "session_id": session_id,
        "stage1_session_id": stage1_session_id,
        "profile": None,
        "current_phase": "role_orientation",
        "current_block": "role_orientation",
        "current_question_index": 0,
        "current_block_index": 0,
        "block_order": [],
        "block_depths": {},
        "followup_count": 0,
        "pending_followup": None,
        "answers": {},
        "conversation_history": [],
        "risk_flags": [],
        "last_agent_message": "",
        "session_complete": False,
    }

    state = _run_graph_initial(instance, initial_state)
    greeting = state.get("last_agent_message", "")

    logger.info("session=%s stage=2 action=created linked_to=%s", session_id, stage1_session_id)
    return SessionCreatedResponse(session_id=session_id, message=greeting)


@app.post("/api/sessions/{session_id}/message", response_model=MessageResponse)
def send_message(session_id: str, request: SendMessageRequest):
    store = get_session_store()

    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    instance = _rehydrate_instance(session_id, session)
    if not instance:
        raise HTTPException(status_code=404, detail="No active graph for session")

    # Check if session is already complete
    snapshot = instance.graph.get_state(instance.config)
    current_state = snapshot.values
    if current_state.get("session_complete"):
        raise HTTPException(status_code=400, detail="Session is already complete")

    lock = _registry.get_lock(session_id)
    with lock:
        state = _run_graph_resume(instance, request.message)

    agent_message = state.get("last_agent_message", "")
    session_complete = state.get("session_complete", False)

    response = MessageResponse(message=agent_message, session_complete=session_complete)

    if session_complete and instance.stage == 1:
        profile = state.get("role_intelligence_profile")
        if profile:
            profile_obj = profile if hasattr(profile, "model_dump") else None
            if profile_obj:
                store.store_profile(session_id, profile_obj)
                response.profile = profile_obj.model_dump()
            else:
                # profile is already a dict
                from models.role_intelligence_profile import RoleIntelligenceProfile
                profile_validated = RoleIntelligenceProfile.model_validate(profile)
                store.store_profile(session_id, profile_validated)
                response.profile = profile if isinstance(profile, dict) else profile_validated.model_dump()
        store.update_session(session_id, {"session_complete": True})
        # Mint the employee invite token (goes in the share link) and the
        # manager token (gates document access). Returned once, to the
        # manager's own browser.
        response.invite_token = store.create_invite_token(session_id)
        response.manager_token = store.create_manager_token(session_id)
        on_stage1_complete(session_id)

    elif session_complete and instance.stage == 2:
        store.update_session(session_id, {"session_complete": True})
        on_stage2_complete(session_id)
        # Auto-trigger document generation — the handover is delivered to the
        # manager, not generated on demand by the employee.
        try:
            _start_generation_for_stage2(session_id, settings.default_output_format)
        except Exception as e:
            # Never break the employee's completion response over this — the
            # manager can retry generation from their overview page.
            logger.error("session=%s auto-generation failed to start: %s", session_id, e)

    return response


@app.get("/api/sessions/{session_id}/status", response_model=SessionStatusResponse)
def get_session_status(session_id: str):
    store = get_session_store()

    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    instance = _rehydrate_instance(session_id, session)

    # Note: deliberately does NOT expose the linked session ID — a Stage 2
    # caller must never learn the manager's Stage 1 session ID.
    response = SessionStatusResponse(
        session_id=session_id,
        stage=session.get("stage", 0),
        session_complete=session.get("session_complete", False),
    )

    if instance:
        snapshot = instance.graph.get_state(instance.config)
        state = snapshot.values
        response.current_block = state.get("current_block")
        response.current_question_index = state.get("current_question_index")
        response.session_complete = state.get("session_complete", False)

    return response


@app.get("/api/sessions/{session_id}/history", response_model=SessionHistoryResponse)
def get_session_history(session_id: str):
    """Return the conversation so far so the UI can restore after a refresh or a
    later sitting. Scoped to the caller's own session (same access model as
    /message) — a Stage 2 caller only ever sees their own transcript, never the
    manager's Stage 1 session."""
    store = get_session_store()

    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    instance = _rehydrate_instance(session_id, session)

    messages: List[HistoryMessage] = []
    current_block = None
    session_complete = session.get("session_complete", False)

    if instance:
        state = instance.graph.get_state(instance.config).values
        session_complete = state.get("session_complete", session_complete)
        current_block = state.get("current_block")
        for msg in state.get("conversation_history", []):
            if isinstance(msg, AIMessage):
                messages.append(HistoryMessage(role="agent", content=msg.content))
            elif isinstance(msg, HumanMessage):
                messages.append(HistoryMessage(role="user", content=msg.content))

    return SessionHistoryResponse(
        session_id=session_id,
        stage=session.get("stage", 0),
        session_complete=session_complete,
        current_block=current_block,
        messages=messages,
    )


# ---- Document Generation (manager-facing) ----
#
# The handover document — including the Risk Summary about the departing
# employee and any confidential sections — is for the manager/recipients.
# It is never generated or downloaded from the employee's session.

def _start_generation_for_stage2(session_id: str, output_format: str) -> str:
    """Kick off background document generation for a completed Stage 2 session.

    Returns the document_id. Raises HTTPException on invalid preconditions.
    """
    store = get_session_store()

    instance = _rehydrate_instance(session_id)
    if not instance:
        raise HTTPException(status_code=404, detail="No active graph for session")

    snapshot = instance.graph.get_state(instance.config)
    state = snapshot.values
    if not state.get("session_complete"):
        raise HTTPException(status_code=400, detail="Stage 2 session is not yet complete")

    stage1_id = state.get("stage1_session_id") or store.get_linked_session(session_id)
    if not stage1_id:
        raise HTTPException(status_code=400, detail="No linked Stage 1 session found")

    profile = store.get_profile(stage1_id)
    if not profile:
        raise HTTPException(status_code=400, detail="Stage 1 profile not found")

    gen_request = GenerationRequest(
        session_id=session_id,
        profile=profile,
        conversation_history=state.get("conversation_history", []),
        risk_flags=state.get("risk_flags", []),
        answers=state.get("answers", {}),
        block_order=state.get("block_order", []),
        block_depths=state.get("block_depths", {}),
    )

    document_id = str(uuid.uuid4())
    _document_owners[document_id] = stage1_id
    store.update_session(stage1_id, {"latest_document_id": document_id})

    _generation_jobs[document_id] = {
        "status": "generating",
        "download_url": None,
        "error": None,
    }

    thread = threading.Thread(
        target=_run_generation_in_background,
        args=(document_id, session_id, gen_request, profile, output_format),
        daemon=True,
    )
    thread.start()

    logger.info("session=%s document=%s format=%s generation=started", session_id, document_id, output_format)
    return document_id


def _require_manager_token(stage1_session_id: str, token: str) -> None:
    store = get_session_store()
    if not store.get_session(stage1_session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    if not store.validate_manager_token(stage1_session_id, token):
        raise HTTPException(status_code=403, detail="Invalid manager token")


@app.get("/api/manager/{stage1_session_id}/handover", response_model=ManagerOverviewResponse)
def manager_handover_overview(stage1_session_id: str, token: str):
    """Manager view: employee interview progress and document status."""
    _require_manager_token(stage1_session_id, token)
    store = get_session_store()

    stage2_id = store.get_linked_session(stage1_session_id)
    stage2_status = "not_started"
    risk_flag_count = None

    if stage2_id:
        stage2_status = "in_progress"
        stage2_session = store.get_session(stage2_id)
        instance = _rehydrate_instance(stage2_id, stage2_session)
        if instance:
            state = instance.graph.get_state(instance.config).values
            if state.get("session_complete"):
                stage2_status = "complete"
            risk_flag_count = len(state.get("risk_flags", []))
        elif stage2_session and stage2_session.get("session_complete"):
            stage2_status = "complete"

    stage1_session = store.get_session(stage1_session_id) or {}
    document_id = stage1_session.get("latest_document_id")
    job = _generation_jobs.get(document_id) if document_id else None

    return ManagerOverviewResponse(
        stage1_session_id=stage1_session_id,
        stage2_status=stage2_status,
        risk_flag_count=risk_flag_count,
        document_id=document_id,
        document_status=job["status"] if job else None,
        download_url=job.get("download_url") if job else None,
        document_error=job.get("error") if job else None,
    )


@app.post("/api/manager/{stage1_session_id}/generate", response_model=GenerateDocumentResponse)
def manager_generate_document(stage1_session_id: str, request: GenerateDocumentRequest, token: str):
    """Manager-triggered (re)generation, e.g. in a different format."""
    _require_manager_token(stage1_session_id, token)
    store = get_session_store()

    stage2_id = store.get_linked_session(stage1_session_id)
    if not stage2_id:
        raise HTTPException(status_code=400, detail="Employee interview has not started yet")

    output_format = request.format or settings.default_output_format
    document_id = _start_generation_for_stage2(stage2_id, output_format)

    return GenerateDocumentResponse(
        document_id=document_id,
        download_url=f"/api/documents/{document_id}",
        status="generating",
    )


@app.get("/api/documents/{document_id}/status", response_model=GenerationStatusResponse)
def get_generation_status(document_id: str, token: str):
    """Poll for document generation status (manager token required)."""
    stage1_id = _document_owners.get(document_id)
    if not stage1_id:
        raise HTTPException(status_code=404, detail="Document generation job not found")
    _require_manager_token(stage1_id, token)

    job = _generation_jobs.get(document_id)
    if not job:
        raise HTTPException(status_code=404, detail="Document generation job not found")

    return GenerationStatusResponse(
        document_id=document_id,
        status=job["status"],
        download_url=job.get("download_url"),
        error=job.get("error"),
    )


@app.get("/api/documents/{document_id}")
def download_document(document_id: str, token: str):
    """Download a generated handover document (manager token required)."""
    stage1_id = _document_owners.get(document_id)
    if not stage1_id:
        raise HTTPException(status_code=404, detail="Document not found")
    _require_manager_token(stage1_id, token)

    file_path = _document_store.get(document_id)
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Document not found")

    filename = os.path.basename(file_path)
    media_type = "application/pdf" if file_path.endswith(".pdf") else (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return FileResponse(path=file_path, filename=filename, media_type=media_type)


# ---- SPA Fallback (must be last) ----
# Serve index.html for all non-API routes so React Router handles them
if _FRONTEND_DIST.exists():
    _index_html = (_FRONTEND_DIST / "index.html").read_text()

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        # If the path maps to a real file in dist, serve it
        file_path = _FRONTEND_DIST / full_path
        if full_path and file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        # Otherwise serve index.html for client-side routing
        return HTMLResponse(_index_html)
