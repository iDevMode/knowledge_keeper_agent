import logging
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from agents.stage1_business_interview.graph import build_stage1_graph
from agents.stage2_employee_interview.graph import build_stage2_graph
from agents.stage3_document_generation.generator import GenerationRequest, generate_document
from api.session_manager import get_session_store
from api.webhooks import on_document_generated, on_stage1_complete, on_stage2_complete
from config.settings import settings
from output.exporters.word_exporter import generate_docx
from output.formatters.document_formatter import parse_llm_output

logger = logging.getLogger(__name__)


# ---- Request / Response Models ----

class CreateStage2Request(BaseModel):
    stage1_session_id: str

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

class SessionStatusResponse(BaseModel):
    session_id: str
    stage: int
    session_complete: bool
    current_block: Optional[str] = None
    current_question_index: Optional[int] = None
    linked_session_id: Optional[str] = None
    risk_flag_count: Optional[int] = None

class GenerateDocumentResponse(BaseModel):
    document_id: str
    download_url: str
    status: str = "generating"

class GenerationStatusResponse(BaseModel):
    document_id: str
    status: str  # "generating" | "complete" | "failed"
    download_url: Optional[str] = None
    error: Optional[str] = None


# ---- GraphRegistry ----

@dataclass
class GraphInstance:
    graph: Any
    config: dict
    stage: int
    checkpointer: MemorySaver


class GraphRegistry:
    def __init__(self):
        self._instances: Dict[str, GraphInstance] = {}
        self._locks: Dict[str, threading.Lock] = {}

    def create_stage1(self, session_id: str) -> GraphInstance:
        checkpointer = MemorySaver()
        graph = build_stage1_graph(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": session_id}}
        instance = GraphInstance(graph=graph, config=config, stage=1, checkpointer=checkpointer)
        self._instances[session_id] = instance
        return instance

    def create_stage2(self, session_id: str) -> GraphInstance:
        checkpointer = MemorySaver()
        graph = build_stage2_graph(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": session_id}}
        instance = GraphInstance(graph=graph, config=config, stage=2, checkpointer=checkpointer)
        self._instances[session_id] = instance
        return instance

    def get(self, session_id: str) -> Optional[GraphInstance]:
        return self._instances.get(session_id)

    def get_lock(self, session_id: str) -> threading.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = threading.Lock()
        return self._locks[session_id]

    def remove(self, session_id: str) -> None:
        self._instances.pop(session_id, None)
        self._locks.pop(session_id, None)


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

# Module-level singletons
_registry = GraphRegistry()
_document_store: Dict[str, str] = {}  # document_id -> file_path
_generation_jobs: Dict[str, Dict[str, Any]] = {}  # document_id -> {status, download_url, error}


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
    key = settings.anthropic_api_key
    return {
        "status": "ok",
        "api_key_set": bool(key),
        "api_key_preview": f"{key[:12]}..." if key else "(empty)",
        "environment": settings.environment,
        "allowed_origins": settings.allowed_origins,
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

    # Validate Stage 1 session exists
    stage1_session = store.get_session(request.stage1_session_id)
    if not stage1_session:
        raise HTTPException(status_code=404, detail="Stage 1 session not found")

    # Validate profile exists
    profile = store.get_profile(request.stage1_session_id)
    if not profile:
        raise HTTPException(status_code=400, detail="Stage 1 profile not yet generated")

    session_id = store.create_session(stage=2, metadata={"stage1_session_id": request.stage1_session_id})
    store.link_sessions(request.stage1_session_id, session_id)

    instance = _registry.create_stage2(session_id)

    initial_state = {
        "session_id": session_id,
        "stage1_session_id": request.stage1_session_id,
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

    logger.info("session=%s stage=2 action=created linked_to=%s", session_id, request.stage1_session_id)
    return SessionCreatedResponse(session_id=session_id, message=greeting)


@app.post("/api/sessions/{session_id}/message", response_model=MessageResponse)
def send_message(session_id: str, request: SendMessageRequest):
    store = get_session_store()

    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    instance = _registry.get(session_id)
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
        on_stage1_complete(session_id)

    elif session_complete and instance.stage == 2:
        store.update_session(session_id, {"session_complete": True})
        on_stage2_complete(session_id)

    return response


@app.get("/api/sessions/{session_id}/status", response_model=SessionStatusResponse)
def get_session_status(session_id: str):
    store = get_session_store()

    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    instance = _registry.get(session_id)

    response = SessionStatusResponse(
        session_id=session_id,
        stage=session.get("stage", 0),
        session_complete=session.get("session_complete", False),
        linked_session_id=store.get_linked_session(session_id),
    )

    if instance:
        snapshot = instance.graph.get_state(instance.config)
        state = snapshot.values
        response.current_block = state.get("current_block")
        response.current_question_index = state.get("current_question_index")
        response.session_complete = state.get("session_complete", False)

        if instance.stage == 2:
            risk_flags = state.get("risk_flags", [])
            response.risk_flag_count = len(risk_flags)

    return response


@app.post("/api/sessions/{session_id}/generate", response_model=GenerateDocumentResponse)
def generate_document_endpoint(session_id: str, request: GenerateDocumentRequest):
    """Start document generation in the background. Returns a document_id for polling."""
    store = get_session_store()

    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.get("stage") != 2:
        raise HTTPException(status_code=400, detail="Document generation requires a Stage 2 session")

    instance = _registry.get(session_id)
    if not instance:
        raise HTTPException(status_code=404, detail="No active graph for session")

    snapshot = instance.graph.get_state(instance.config)
    state = snapshot.values
    if not state.get("session_complete"):
        raise HTTPException(status_code=400, detail="Stage 2 session is not yet complete")

    # Get the linked Stage 1 profile
    stage1_id = state.get("stage1_session_id") or store.get_linked_session(session_id)
    if not stage1_id:
        raise HTTPException(status_code=400, detail="No linked Stage 1 session found")

    profile = store.get_profile(stage1_id)
    if not profile:
        raise HTTPException(status_code=400, detail="Stage 1 profile not found")

    # Build generation request from Stage 2 state
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
    output_format = request.format or settings.default_output_format

    # Track the job
    _generation_jobs[document_id] = {
        "status": "generating",
        "download_url": None,
        "error": None,
    }

    # Run in background thread
    thread = threading.Thread(
        target=_run_generation_in_background,
        args=(document_id, session_id, gen_request, profile, output_format),
        daemon=True,
    )
    thread.start()

    logger.info("session=%s document=%s format=%s generation=started", session_id, document_id, output_format)
    return GenerateDocumentResponse(
        document_id=document_id,
        download_url=f"/api/documents/{document_id}",
        status="generating",
    )


@app.get("/api/documents/{document_id}/status", response_model=GenerationStatusResponse)
def get_generation_status(document_id: str):
    """Poll for document generation status."""
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
def download_document(document_id: str):
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
