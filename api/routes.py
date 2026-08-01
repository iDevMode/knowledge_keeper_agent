import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from agents.stage1_business_interview.graph import build_stage1_graph
from agents.stage2_employee_interview.graph import build_stage2_graph
from agents.stage3_document_generation.generator import GenerationRequest, generate_document
from api.auth import (
    EMPLOYEE,
    MANAGER,
    InvalidToken,
    TokenClaims,
    extract_bearer,
    issue_token,
    verify_token,
)
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
    token: str

class Stage2CreatedResponse(BaseModel):
    """Returned to the MANAGER, who then forwards the employee link.

    The employee token is minted here rather than when the employee first opens
    the link, because minting it requires proving you are the manager.
    """
    session_id: str
    message: str
    employee_token: str

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
    document_id: Optional[str] = None
    # Why no document exists, for the manager only. Otherwise a Stage 3 failure
    # leaves them watching "preparing the handover pack..." indefinitely.
    generation_error: Optional[str] = None
    # The employee's session is now created by the manager, so the employee
    # never sees the creation response that carried the opening question. They
    # pick it up here when they open their link, which also restores the last
    # question after a refresh.
    last_agent_message: Optional[str] = None

class GenerateDocumentResponse(BaseModel):
    document_id: str
    download_url: str
    status: str = "generating"

class GenerationStatusResponse(BaseModel):
    document_id: str
    status: str  # "generating" | "complete" | "failed"
    download_url: Optional[str] = None
    error: Optional[str] = None


# ---- Checkpointer ----

_checkpointer: Optional[Any] = None
_checkpointer_guard = threading.Lock()


def _build_checkpointer() -> Any:
    if not settings.database_url:
        logger.warning(
            "checkpointer: in-process — interviews will not survive a restart. "
            "Set DATABASE_URL to persist them."
        )
        return MemorySaver()

    # Imported lazily so psycopg is not needed to run in-memory.
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=10,
        timeout=10,
        # PostgresSaver requires both of these on every connection it uses.
        kwargs={"autocommit": True, "row_factory": dict_row},
        open=True,
    )
    saver = PostgresSaver(pool)
    saver.setup()
    logger.info("checkpointer: postgres")
    return saver


def get_checkpointer() -> Any:
    """The process-wide checkpointer. Postgres when configured, else in-memory."""
    global _checkpointer
    if _checkpointer is None:
        with _checkpointer_guard:
            if _checkpointer is None:
                _checkpointer = _build_checkpointer()
    return _checkpointer


def reset_checkpointer() -> None:
    """Drop the cached checkpointer. Used by tests to switch backends."""
    global _checkpointer
    _checkpointer = None


# ---- GraphRegistry ----

@dataclass
class GraphInstance:
    graph: Any
    config: dict
    stage: int
    checkpointer: Any


class GraphRegistry:
    """Builds graphs on demand against one shared checkpointer.

    This used to cache a compiled graph AND its own private MemorySaver per
    session in a process dictionary. That is what tied a session to the process
    that created it: a restart lost every interview, and a second worker could
    not serve a session the first one had started — it saw no graph and returned
    404.

    A compiled graph carries no session state; the state lives in the
    checkpointer under thread_id. So one graph per stage is compiled once and
    reused for every session, and which session it is serving comes from the
    config passed at call time. Any worker can now serve any session, and an
    interview resumes after a redeploy.
    """

    def __init__(self, checkpointer: Optional[Any] = None):
        self._checkpointer = checkpointer
        self._graphs: Dict[int, Any] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._last_used: Dict[str, float] = {}
        # Guards the lock dictionary itself. Without it two requests for the
        # same new session could each build a lock and serialise against
        # nothing.
        self._registry_guard = threading.Lock()

    @property
    def checkpointer(self) -> Any:
        if self._checkpointer is None:
            self._checkpointer = get_checkpointer()
        return self._checkpointer

    def _graph_for(self, stage: int) -> Any:
        with self._registry_guard:
            if stage not in self._graphs:
                builder = build_stage1_graph if stage == 1 else build_stage2_graph
                self._graphs[stage] = builder(checkpointer=self.checkpointer)
            return self._graphs[stage]

    def _instance(self, session_id: str, stage: int) -> GraphInstance:
        self._last_used[session_id] = time.time()
        return GraphInstance(
            graph=self._graph_for(stage),
            config={"configurable": {"thread_id": session_id}},
            stage=stage,
            checkpointer=self.checkpointer,
        )

    def create_stage1(self, session_id: str) -> GraphInstance:
        return self._instance(session_id, 1)

    def create_stage2(self, session_id: str) -> GraphInstance:
        return self._instance(session_id, 2)

    def get(self, session_id: str) -> Optional[GraphInstance]:
        """Return a graph bound to this session, or None if there is no session.

        The stage comes from the session store rather than a process dictionary,
        which is what lets a worker serve a session it never created.
        """
        session = get_session_store().get_session(session_id)
        if session is None:
            return None

        stage = session.get("stage")
        if stage not in (1, 2):
            logger.warning("session=%s has unusable stage %r", session_id, stage)
            return None

        return self._instance(session_id, stage)

    def get_lock(self, session_id: str) -> threading.Lock:
        with self._registry_guard:
            if session_id not in self._locks:
                self._locks[session_id] = threading.Lock()
            return self._locks[session_id]

    def remove(self, session_id: str) -> None:
        with self._registry_guard:
            self._locks.pop(session_id, None)
            self._last_used.pop(session_id, None)

    def sweep_idle(self, max_idle_seconds: float) -> int:
        """Release the per-session locks of sessions nobody has touched.

        Graphs are no longer cached, so there is nothing heavyweight to evict —
        but the lock dictionary would still grow without bound for the life of
        the process.
        """
        cutoff = time.time() - max_idle_seconds
        stale = [
            session_id
            for session_id, last_used in list(self._last_used.items())
            if last_used < cutoff
        ]
        for session_id in stale:
            self.remove(session_id)
        return len(stale)


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

def parse_allowed_origins(raw: str) -> list[str]:
    """Split a comma-separated origin list, tolerating whitespace.

    Without stripping, ALLOWED_ORIGINS="https://a.com, https://b.com" — the way
    anyone would naturally write it — yields a second origin of " https://b.com"
    that matches nothing, so that origin is silently blocked in production.
    """
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def create_app() -> FastAPI:
    app = FastAPI(title="KnowledgeKeeper API", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=parse_allowed_origins(settings.allowed_origins),
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
_document_created_at: Dict[str, float] = {}  # document_id -> unix timestamp
_document_owner: Dict[str, str] = {}  # document_id -> owning stage 2 session_id
_session_document: Dict[str, str] = {}  # stage 2 session_id -> document_id
# Why the automatic Stage 3 run never started, surfaced to the manager. Without
# this the manager watches an empty progress line forever and the only record is
# a log entry nobody reads.
_session_generation_error: Dict[str, str] = {}

# One managed directory for generated documents, instead of a fresh mkdtemp per
# generation that was never cleaned up.
_DOCUMENT_DIR = Path(tempfile.gettempdir()) / "knowledgekeeper_documents"

# How long generated documents and idle graph instances are retained. Tied to
# the session TTL so a document outlives the session that produced it by the
# same window.
_RETENTION_SECONDS = settings.session_ttl_hours * 3600

# Sweeps are opportunistic — triggered by request handlers rather than a
# background thread — so they are rate limited to avoid doing this work on
# every call.
_SWEEP_INTERVAL_SECONDS = 300.0
_last_sweep_at = 0.0
_sweep_lock = threading.Lock()


# ---- Authorisation ----

def _employee_token_ttl() -> float:
    """Employee link lifetime, capped at the life of the session behind it.

    STAGE1_TO_STAGE2_LINK_TTL_HOURS defaults to 168 while SESSION_TTL_HOURS
    defaults to 72, so an uncapped token stayed cryptographically valid for four
    days after the session store had already dropped the session. The employee
    followed a link they were told was good for a week and got "Session not
    found" on day four. A token must never outlive the data it points at.
    """
    return min(
        settings.stage1_to_stage2_link_ttl_hours,
        settings.session_ttl_hours,
    ) * 3600


def _claims_or_401(token: str) -> TokenClaims:
    try:
        return verify_token(token)
    except InvalidToken as e:
        # 401 with a coarse message: the caller learns their token was rejected,
        # not which check rejected it.
        raise HTTPException(status_code=401, detail="Invalid or expired token") from e


def _authorises(claims: TokenClaims, session_id: str) -> bool:
    """Does `claims` grant access to `session_id`?

    A manager owns the whole engagement, so their token covers both halves of a
    linked Stage 1 / Stage 2 pair — they need this to start the employee session
    and to generate the document from the Stage 2 session. An employee token
    covers exactly the session it was minted for and nothing else.
    """
    if claims.session_id == session_id:
        return True
    if not claims.is_manager:
        return False
    return get_session_store().get_linked_session(claims.session_id) == session_id


def require_session_access(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
) -> TokenClaims:
    """Any valid token bound to this session — either scope."""
    try:
        token = extract_bearer(authorization)
    except InvalidToken as e:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from e

    claims = _claims_or_401(token)
    if not _authorises(claims, session_id):
        logger.warning(
            "session=%s access denied for token scope=%s bound_to=%s",
            session_id, claims.scope, claims.session_id,
        )
        raise HTTPException(status_code=403, detail="Token does not grant access to this session")
    return claims


def require_manager_access(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
) -> TokenClaims:
    """Manager scope only — generation and anything that exposes the document.

    This is what stops the departing employee producing and downloading their own
    handover pack, Risk Summary included.
    """
    claims = require_session_access(session_id, authorization)
    if not claims.is_manager:
        logger.warning("session=%s manager-only endpoint refused for employee token", session_id)
        raise HTTPException(status_code=403, detail="This action requires a manager token")
    return claims


def require_document_access(
    document_id: str,
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
) -> TokenClaims:
    """Manager scope for the session that produced `document_id`.

    Accepts the token as a query parameter as well as a header: the browser
    downloads the file through a plain <a href>, which cannot set headers. Tokens
    in URLs leak through history and referrers, so this fallback is confined to
    the document endpoints.
    """
    try:
        raw = extract_bearer(authorization, token)
    except InvalidToken as e:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from e

    claims = _claims_or_401(raw)

    owner_session = _document_owner.get(document_id)
    if owner_session is None:
        # Unknown or swept document. 404 rather than 403 — an unauthenticated
        # caller should not be able to distinguish "exists" from "does not".
        raise HTTPException(status_code=404, detail="Document not found")

    if not claims.is_manager or not _authorises(claims, owner_session):
        logger.warning("document=%s access denied for scope=%s", document_id, claims.scope)
        raise HTTPException(status_code=403, detail="This action requires a manager token")
    return claims


def safe_static_path(base: Path, relative: str) -> Optional[Path]:
    """Resolve `relative` under `base`, returning None if it escapes or is absent.

    Kept as a module-level function rather than inlined in the SPA handler so the
    containment guard can be tested without a built frontend. frontend/dist is a
    gitignored build artefact, so a test that needs the route mounted skips in CI
    and on any fresh clone — exactly where the guard matters most.
    """
    if not relative:
        return None

    root = base.resolve()
    candidate = (root / relative).resolve()

    if not candidate.is_file():
        return None
    if not candidate.is_relative_to(root):
        return None
    return candidate


def _document_dir() -> Path:
    """Return the managed document directory, restricted to the running user.

    The directory this replaced was created by tempfile.mkdtemp, which is
    documented as readable/writable/searchable only by the creating user (0o700).
    Path.mkdir defaults to 0o777 & ~umask — typically 0o755 on Linux — so
    switching to a shared managed directory would have widened access to
    generated handover documents, which contain sensitive HR content.

    chmod is applied on every call because mkdir's mode argument is ignored when
    the directory already exists. It is best-effort: Windows does not honour
    POSIX modes, and a directory owned by another user cannot be chmod'ed.
    """
    _DOCUMENT_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(_DOCUMENT_DIR, 0o700)
    except OSError as e:
        logger.warning("could not restrict permissions on %s: %s", _DOCUMENT_DIR, e)
    return _DOCUMENT_DIR


def sweep_resources(force: bool = False) -> Dict[str, int]:
    """Release expired sessions, idle graph instances and old documents.

    Without this every one of these grows monotonically for the lifetime of the
    process: graph instances and their checkpoints, job records, and the
    generated files on disk.
    """
    global _last_sweep_at

    with _sweep_lock:
        now = time.time()
        if not force and now - _last_sweep_at < _SWEEP_INTERVAL_SECONDS:
            return {}
        _last_sweep_at = now

    sessions = get_session_store().sweep_expired()
    instances = _registry.sweep_idle(_RETENTION_SECONDS)

    cutoff = time.time() - _RETENTION_SECONDS
    stale_documents = [
        doc_id for doc_id, created in list(_document_created_at.items()) if created < cutoff
    ]
    for doc_id in stale_documents:
        file_path = _document_store.pop(doc_id, None)
        _generation_jobs.pop(doc_id, None)
        _document_created_at.pop(doc_id, None)
        owner = _document_owner.pop(doc_id, None)
        if owner is not None and _session_document.get(owner) == doc_id:
            _session_document.pop(owner, None)
            _session_generation_error.pop(owner, None)
        if file_path:
            try:
                os.remove(file_path)
            except OSError as e:
                logger.warning("could not delete expired document %s: %s", file_path, e)

    result = {
        "sessions": sessions,
        "graph_instances": instances,
        "documents": len(stale_documents),
    }
    if any(result.values()):
        logger.info("resource sweep released %s", result)
    return result


# ---- Document Generation ----

class GenerationNotReady(Exception):
    """The session cannot produce a document yet. Carries a caller-safe reason."""


def _build_generation_request(session_id: str) -> tuple[GenerationRequest, Any]:
    """Assemble the Stage 3 input from a completed Stage 2 session."""
    store = get_session_store()

    session = store.get_session(session_id)
    if not session:
        raise GenerationNotReady("Session not found")

    if session.get("stage") != 2:
        raise GenerationNotReady("Document generation requires a Stage 2 session")

    instance = _registry.get(session_id)
    if not instance:
        raise GenerationNotReady("No active graph for session")

    state = instance.graph.get_state(instance.config).values
    if not state.get("session_complete"):
        raise GenerationNotReady("Stage 2 session is not yet complete")

    stage1_id = state.get("stage1_session_id") or store.get_linked_session(session_id)
    if not stage1_id:
        raise GenerationNotReady("No linked Stage 1 session found")

    profile = store.get_profile(stage1_id)
    if not profile:
        raise GenerationNotReady("Stage 1 profile not found")

    gen_request = GenerationRequest(
        session_id=session_id,
        profile=profile,
        conversation_history=state.get("conversation_history", []),
        risk_flags=state.get("risk_flags", []),
        answers=state.get("answers", {}),
        block_order=state.get("block_order", []),
        block_depths=state.get("block_depths", {}),
    )
    return gen_request, profile


def _start_generation(session_id: str, output_format: str) -> str:
    """Kick off generation in the background and return the new document id.

    Shared by the manager-triggered endpoint and the automatic trigger that fires
    when Stage 2 completes, so both produce identically-tracked documents.
    """
    gen_request, profile = _build_generation_request(session_id)

    document_id = str(uuid.uuid4())
    _generation_jobs[document_id] = {
        "status": "generating",
        "download_url": None,
        "error": None,
    }
    _document_created_at[document_id] = time.time()
    _document_owner[document_id] = session_id
    _session_document[session_id] = document_id
    _session_generation_error.pop(session_id, None)

    thread = threading.Thread(
        target=_run_generation_in_background,
        args=(document_id, session_id, gen_request, profile, output_format),
        daemon=True,
    )
    thread.start()

    logger.info(
        "session=%s document=%s format=%s generation=started",
        session_id, document_id, output_format,
    )
    return document_id


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

        output_dir = str(_document_dir())

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
        _document_created_at[document_id] = time.time()
        _generation_jobs[document_id] = {
            "status": "complete",
            "download_url": f"/api/documents/{document_id}",
            "error": None,
        }

        on_document_generated(session_id, document_id, file_path)
        logger.info("session=%s document=%s format=%s generation=complete", session_id, document_id, output_format)

    except Exception as e:
        logger.error("session=%s document=%s generation failed: %s", session_id, document_id, e)
        _document_created_at[document_id] = time.time()
        _generation_jobs[document_id] = {
            "status": "failed",
            "download_url": None,
            "error": str(e),
        }


# ---- Endpoints ----

@app.get("/api/health")
def health_check():
    """Liveness probe. Deliberately discloses no configuration — it is public."""
    return {"status": "ok"}


@app.post("/api/sessions/stage1", response_model=SessionCreatedResponse)
def create_stage1():
    sweep_resources()
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
        "profile_generation_errors": [],
        "profile_generation_attempts": 0,
    }

    state = _run_graph_initial(instance, initial_state)
    greeting = state.get("last_agent_message", "")

    logger.info("session=%s stage=1 action=created", session_id)
    return SessionCreatedResponse(
        session_id=session_id,
        message=greeting,
        token=issue_token(session_id, MANAGER),
    )


@app.post("/api/sessions/stage2", response_model=Stage2CreatedResponse)
def create_stage2(
    request: CreateStage2Request,
    authorization: Optional[str] = Header(default=None),
):
    """Create the employee's interview session. Manager credential required.

    The employee token is returned to the MANAGER, who forwards it in the
    interview link. Previously the employee's own browser created this session
    from the manager's Stage 1 id, which meant the shared link handed the
    employee the manager's session — enough to read and write the manager's
    interview.
    """
    sweep_resources()
    store = get_session_store()

    # Authorise BEFORE looking the session up. The other way round, an
    # anonymous caller gets 404 for an id that does not exist and 401 for one
    # that does, which enumerates valid Stage 1 session ids.
    require_manager_access(request.stage1_session_id, authorization)

    stage1_session = store.get_session(request.stage1_session_id)
    if not stage1_session:
        raise HTTPException(status_code=404, detail="Stage 1 session not found")

    # Idempotent: one Stage 1 has exactly one employee interview. Creating a
    # second would overwrite the store link and orphan the first — the employee
    # could keep using a link the manager could no longer reach, and its
    # document would be unreachable too.
    #
    # Since H3 the graph is rebuilt on demand from the shared checkpointer, so
    # a live session is always servable and the opening question is read back
    # from the checkpoint rather than from a cached instance.
    existing = store.get_linked_session(request.stage1_session_id)
    if existing and store.get_session(existing):
        instance = _registry.get(existing)
        greeting = ""
        if instance:
            greeting = instance.graph.get_state(instance.config).values.get(
                "last_agent_message", ""
            )
        logger.info(
            "session=%s stage=2 action=reissued linked_to=%s",
            existing, request.stage1_session_id,
        )
        return Stage2CreatedResponse(
            session_id=existing,
            message=greeting,
            employee_token=issue_token(existing, EMPLOYEE, ttl_seconds=_employee_token_ttl()),
        )

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
    return Stage2CreatedResponse(
        session_id=session_id,
        message=greeting,
        employee_token=issue_token(session_id, EMPLOYEE, ttl_seconds=_employee_token_ttl()),
    )


@app.post(
    "/api/sessions/{session_id}/message",
    response_model=MessageResponse,
    dependencies=[Depends(require_session_access)],
)
def send_message(session_id: str, request: SendMessageRequest):
    store = get_session_store()

    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    instance = _registry.get(session_id)
    if not instance:
        raise HTTPException(status_code=404, detail="No active graph for session")

    lock = _registry.get_lock(session_id)
    with lock:
        # Read the completion flag INSIDE the lock. Checking before acquiring it
        # let two near-simultaneous messages both observe an incomplete session
        # and both resume the graph.
        snapshot = instance.graph.get_state(instance.config)
        if snapshot.values.get("session_complete"):
            raise HTTPException(status_code=400, detail="Session is already complete")

        state = _run_graph_resume(instance, request.message)

    agent_message = state.get("last_agent_message", "")
    session_complete = state.get("session_complete", False)

    response = MessageResponse(message=agent_message, session_complete=session_complete)

    if session_complete and instance.stage == 1:
        # finalise_node is the single writer of the confirmed profile — it runs
        # only after the manager confirms, so it is the correct place for that
        # decision. This endpoint previously stored it a second time, which also
        # meant an unconfirmed profile could be persisted on paths that complete
        # without confirmation. Here we only echo what was stored.
        profile = store.get_profile(session_id)
        if profile is not None:
            response.profile = profile.model_dump()
        else:
            logger.warning(
                "session=%s stage=1 completed without a confirmed profile", session_id
            )
        store.update_session(session_id, {"session_complete": True})
        on_stage1_complete(session_id)

    elif session_complete and instance.stage == 2:
        store.update_session(session_id, {"session_complete": True})
        # CLAUDE.md: Stage 3 is "triggered automatically on Stage 2 completion".
        # It used to be client-driven, so an employee who closed the tab on the
        # final question left no document behind and nobody found out until the
        # manager went looking. A generation failure must not fail the
        # employee's last turn — they have finished, and the manager can retry.
        # The document id is deliberately NOT returned here: this response goes
        # to the employee. The manager picks it up from session status.
        try:
            _start_generation(session_id, settings.default_output_format)
            _session_generation_error.pop(session_id, None)
        except GenerationNotReady as e:
            logger.error("session=%s auto-generation skipped: %s", session_id, e)
            _session_generation_error[session_id] = str(e)
        except Exception as e:
            logger.error("session=%s auto-generation failed to start: %s", session_id, e)
            _session_generation_error[session_id] = "Document generation could not be started"
        on_stage2_complete(session_id)

    return response


@app.get("/api/sessions/{session_id}/status", response_model=SessionStatusResponse)
def get_session_status(
    session_id: str,
    claims: TokenClaims = Depends(require_session_access),
):
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

    # Only the manager is told a document exists — it is theirs to collect —
    # or why one does not.
    if claims.is_manager:
        response.document_id = _session_document.get(session_id)
        response.generation_error = _session_generation_error.get(session_id)

    if instance:
        snapshot = instance.graph.get_state(instance.config)
        state = snapshot.values
        response.current_block = state.get("current_block")
        response.current_question_index = state.get("current_question_index")
        response.session_complete = state.get("session_complete", False)
        response.last_agent_message = state.get("last_agent_message") or None

        if instance.stage == 2:
            risk_flags = state.get("risk_flags", [])
            response.risk_flag_count = len(risk_flags)

    return response


@app.post(
    "/api/sessions/{session_id}/generate",
    response_model=GenerateDocumentResponse,
    dependencies=[Depends(require_manager_access)],
)
def generate_document_endpoint(session_id: str, request: GenerateDocumentRequest):
    """Regenerate the handover pack, e.g. in a different format.

    Generation now also runs automatically when Stage 2 completes, so this is a
    manager-driven re-run rather than the only route to a document.
    """
    try:
        document_id = _start_generation(session_id, request.format or settings.default_output_format)
    except GenerationNotReady as e:
        detail = str(e)
        status_code = 404 if "not found" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from e

    return GenerateDocumentResponse(
        document_id=document_id,
        download_url=f"/api/documents/{document_id}",
        status="generating",
    )


@app.get(
    "/api/documents/{document_id}/status",
    response_model=GenerationStatusResponse,
    dependencies=[Depends(require_document_access)],
)
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


@app.get("/api/documents/{document_id}", dependencies=[Depends(require_document_access)])
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
        # Only ever serve files that resolve INSIDE the dist directory. Starlette
        # normalises ".." out of request paths today, so this is defence in depth
        # — but the containment check must live here, not depend on an upstream
        # layer we do not control (a proxy forwarding raw dot segments, a
        # different ASGI server, or a direct call would all bypass it).
        candidate = safe_static_path(_FRONTEND_DIST, full_path)
        if candidate is not None:
            return FileResponse(str(candidate))
        # Otherwise serve index.html for client-side routing
        return HTMLResponse(_index_html)
