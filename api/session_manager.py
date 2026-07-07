import secrets
import time
import uuid
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from models.role_intelligence_profile import RoleIntelligenceProfile
from config.settings import settings


@runtime_checkable
class SessionStore(Protocol):
    def create_session(self, stage: int, metadata: Dict[str, Any] | None = None) -> str: ...
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]: ...
    def update_session(self, session_id: str, data: Dict[str, Any]) -> None: ...
    def link_sessions(self, stage1_id: str, stage2_id: str) -> None: ...
    def get_linked_session(self, session_id: str) -> Optional[str]: ...
    def store_profile(self, session_id: str, profile: RoleIntelligenceProfile) -> None: ...
    def get_profile(self, session_id: str) -> Optional[RoleIntelligenceProfile]: ...
    def create_invite_token(self, stage1_id: str) -> str: ...
    def resolve_invite_token(self, token: str) -> Optional[str]: ...
    def consume_invite_token(self, token: str) -> None: ...
    def create_manager_token(self, stage1_id: str) -> str: ...
    def validate_manager_token(self, stage1_id: str, token: str) -> bool: ...


class InMemorySessionStore:
    def __init__(self, ttl_hours: int | None = None):
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._links: Dict[str, str] = {}  # stage1_id <-> stage2_id (bidirectional)
        self._profiles: Dict[str, dict] = {}  # session_id -> profile dict
        # Employee invite tokens: the Stage 2 share link must never expose the
        # Stage 1 session ID (it would let the employee access the manager's
        # confidential session). token -> {stage1_id, used}
        self._invite_tokens: Dict[str, Dict[str, Any]] = {}
        # Manager tokens gate document generation/download. stage1_id -> token
        self._manager_tokens: Dict[str, str] = {}
        self._ttl_seconds = (ttl_hours or settings.session_ttl_hours) * 3600

    def _is_expired(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if not session:
            return True
        return time.time() - session["_created_at"] > self._ttl_seconds

    def create_session(self, stage: int, metadata: Dict[str, Any] | None = None) -> str:
        session_id = str(uuid.uuid4())
        self._sessions[session_id] = {
            "_created_at": time.time(),
            "stage": stage,
            "session_id": session_id,
            **(metadata or {}),
        }
        return session_id

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        if self._is_expired(session_id):
            self._sessions.pop(session_id, None)
            return None
        return self._sessions.get(session_id)

    def update_session(self, session_id: str, data: Dict[str, Any]) -> None:
        session = self._sessions.get(session_id)
        if session and not self._is_expired(session_id):
            session.update(data)

    def link_sessions(self, stage1_id: str, stage2_id: str) -> None:
        self._links[stage1_id] = stage2_id
        self._links[stage2_id] = stage1_id

    def get_linked_session(self, session_id: str) -> Optional[str]:
        return self._links.get(session_id)

    def store_profile(self, session_id: str, profile: RoleIntelligenceProfile) -> None:
        self._profiles[session_id] = profile.model_dump()

    def get_profile(self, session_id: str) -> Optional[RoleIntelligenceProfile]:
        data = self._profiles.get(session_id)
        if data is None:
            return None
        return RoleIntelligenceProfile.model_validate(data)

    # ---- Tokens ----

    def create_invite_token(self, stage1_id: str) -> str:
        """Create a single-use employee invite token for a Stage 1 session."""
        token = secrets.token_urlsafe(32)
        self._invite_tokens[token] = {"stage1_id": stage1_id, "used": False}
        return token

    def resolve_invite_token(self, token: str) -> Optional[str]:
        """Return the Stage 1 session ID for an unused, unexpired invite token."""
        entry = self._invite_tokens.get(token)
        if entry is None or entry["used"]:
            return None
        if self._is_expired(entry["stage1_id"]):
            return None
        return entry["stage1_id"]

    def is_invite_token_used(self, token: str) -> bool:
        entry = self._invite_tokens.get(token)
        return bool(entry and entry["used"])

    def consume_invite_token(self, token: str) -> None:
        entry = self._invite_tokens.get(token)
        if entry:
            entry["used"] = True

    def create_manager_token(self, stage1_id: str) -> str:
        """Create (or return the existing) manager token for a Stage 1 session."""
        if stage1_id not in self._manager_tokens:
            self._manager_tokens[stage1_id] = secrets.token_urlsafe(32)
        return self._manager_tokens[stage1_id]

    def validate_manager_token(self, stage1_id: str, token: str) -> bool:
        expected = self._manager_tokens.get(stage1_id)
        return bool(expected and token and secrets.compare_digest(expected, token))


_store: InMemorySessionStore | None = None


def get_session_store() -> InMemorySessionStore:
    global _store
    if _store is None:
        _store = InMemorySessionStore()
    return _store
