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


class PersistentSessionStore:
    """Durable SessionStore: Redis for ephemeral session state and tokens
    (naturally TTL'd — this is the "session state between stages" Redis is for),
    Postgres for the durable Role Intelligence Profile record.

    `kv` is any object exposing get/set(ttl)/delete/exists over JSON dicts;
    `profiles` is any object exposing upsert(session_id, dict) and get(session_id).
    Both are duck-typed so tests can inject in-memory fakes.
    """

    _SESSION = "kk:session:"
    _LINK = "kk:link:"
    _INVITE = "kk:invite:"
    _MANAGER = "kk:manager:"

    def __init__(self, kv, profiles, ttl_hours: int | None = None, link_ttl_hours: int | None = None):
        self._kv = kv
        self._profiles = profiles
        self._session_ttl = (ttl_hours or settings.session_ttl_hours) * 3600
        # Tokens and links must outlive a single session — the gap between
        # Stage 1 and Stage 2 can be days.
        self._link_ttl = (link_ttl_hours or settings.stage1_to_stage2_link_ttl_hours) * 3600

    def create_session(self, stage: int, metadata: Dict[str, Any] | None = None) -> str:
        session_id = str(uuid.uuid4())
        self._kv.set(
            self._SESSION + session_id,
            {
                "_created_at": time.time(),
                "stage": stage,
                "session_id": session_id,
                **(metadata or {}),
            },
            self._session_ttl,
        )
        return session_id

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._kv.get(self._SESSION + session_id)

    def update_session(self, session_id: str, data: Dict[str, Any]) -> None:
        session = self._kv.get(self._SESSION + session_id)
        if session:
            session.update(data)
            # Reset TTL on activity so an in-progress session doesn't expire.
            self._kv.set(self._SESSION + session_id, session, self._session_ttl)

    def link_sessions(self, stage1_id: str, stage2_id: str) -> None:
        self._kv.set(self._LINK + stage1_id, {"other": stage2_id}, self._link_ttl)
        self._kv.set(self._LINK + stage2_id, {"other": stage1_id}, self._link_ttl)

    def get_linked_session(self, session_id: str) -> Optional[str]:
        entry = self._kv.get(self._LINK + session_id)
        return entry["other"] if entry else None

    def store_profile(self, session_id: str, profile: RoleIntelligenceProfile) -> None:
        self._profiles.upsert(session_id, profile.model_dump())

    def get_profile(self, session_id: str) -> Optional[RoleIntelligenceProfile]:
        data = self._profiles.get(session_id)
        if data is None:
            return None
        return RoleIntelligenceProfile.model_validate(data)

    # ---- Tokens ----

    def create_invite_token(self, stage1_id: str) -> str:
        token = secrets.token_urlsafe(32)
        self._kv.set(self._INVITE + token, {"stage1_id": stage1_id, "used": False}, self._link_ttl)
        return token

    def resolve_invite_token(self, token: str) -> Optional[str]:
        entry = self._kv.get(self._INVITE + token)
        if entry is None or entry["used"]:
            return None
        if self.get_session(entry["stage1_id"]) is None:
            return None
        return entry["stage1_id"]

    def is_invite_token_used(self, token: str) -> bool:
        entry = self._kv.get(self._INVITE + token)
        return bool(entry and entry["used"])

    def consume_invite_token(self, token: str) -> None:
        entry = self._kv.get(self._INVITE + token)
        if entry:
            entry["used"] = True
            self._kv.set(self._INVITE + token, entry, self._link_ttl)

    def create_manager_token(self, stage1_id: str) -> str:
        existing = self._kv.get(self._MANAGER + stage1_id)
        if existing:
            return existing["token"]
        token = secrets.token_urlsafe(32)
        self._kv.set(self._MANAGER + stage1_id, {"token": token}, self._link_ttl)
        return token

    def validate_manager_token(self, stage1_id: str, token: str) -> bool:
        existing = self._kv.get(self._MANAGER + stage1_id)
        return bool(existing and token and secrets.compare_digest(existing["token"], token))


_store: SessionStore | None = None


def _build_store() -> SessionStore:
    if settings.storage_backend == "persistent":
        from api.persistence.redis_kv import RedisKVStore, create_redis_client
        from api.persistence.postgres import PostgresProfileRepo

        kv = RedisKVStore(create_redis_client(settings.redis_url))
        profiles = PostgresProfileRepo(settings.database_url)
        profiles.ensure_schema()
        return PersistentSessionStore(kv, profiles)
    return InMemorySessionStore()


def get_session_store() -> SessionStore:
    global _store
    if _store is None:
        _store = _build_store()
    return _store
