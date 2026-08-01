import logging
import time
import uuid
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from models.role_intelligence_profile import RoleIntelligenceProfile
from config.settings import settings

logger = logging.getLogger(__name__)


@runtime_checkable
class SessionStore(Protocol):
    def create_session(self, stage: int, metadata: Dict[str, Any] | None = None) -> str: ...
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]: ...
    def update_session(self, session_id: str, data: Dict[str, Any]) -> None: ...
    def link_sessions(self, stage1_id: str, stage2_id: str) -> None: ...
    def get_linked_session(self, session_id: str) -> Optional[str]: ...
    def store_profile(self, session_id: str, profile: RoleIntelligenceProfile) -> None: ...
    def get_profile(self, session_id: str) -> Optional[RoleIntelligenceProfile]: ...
    def sweep_expired(self) -> int: ...


class InMemorySessionStore:
    def __init__(self, ttl_hours: int | None = None):
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._links: Dict[str, str] = {}  # stage1_id <-> stage2_id (bidirectional)
        self._profiles: Dict[str, dict] = {}  # session_id -> profile dict
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

    def sweep_expired(self) -> int:
        """Drop expired sessions and their links and profiles.

        Expiry was previously only applied lazily on next access, so a session
        that was abandoned — the common case for an interview someone never
        finishes — stayed resident forever along with its profile.

        Returns the number of sessions removed.
        """
        expired = [sid for sid in list(self._sessions) if self._is_expired(sid)]

        for session_id in expired:
            self._sessions.pop(session_id, None)
            self._profiles.pop(session_id, None)
            linked = self._links.pop(session_id, None)
            if linked is not None:
                self._links.pop(linked, None)

        return len(expired)


_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    """Return the process-wide session store.

    Postgres-backed when DATABASE_URL is set, in-process otherwise. The
    in-process path keeps local development and the test suite free of any
    infrastructure; production refuses to start without DATABASE_URL, the same
    way it refuses without API_SECRET_KEY — see Settings.validate_for_production.
    """
    global _store
    if _store is None:
        if settings.database_url:
            # Imported lazily so psycopg is not required to run in-memory.
            from api.postgres_store import PostgresSessionStore

            _store = PostgresSessionStore()
            logger.info("session store: postgres")
        else:
            _store = InMemorySessionStore()
            logger.warning(
                "session store: in-process — sessions will not survive a restart. "
                "Set DATABASE_URL to persist them."
            )
    return _store


def reset_session_store() -> None:
    """Drop the cached store. Used by tests to switch backends."""
    global _store
    _store = None
