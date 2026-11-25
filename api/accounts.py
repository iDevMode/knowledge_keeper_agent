"""Manager accounts: magic-link sign-in and handover ownership.

Before this, a handover belonged to whichever browser tab held the manager
token. Close the tab before the employee finished and there was no way back to
the pack. Now a manager signs in with an emailed one-time link, every Stage 1
session they start is recorded against them, and the dashboard lists them.

Login tokens are random, single-use, short-lived, and stored only as a SHA-256
hash — a database read does not yield a usable sign-in link.

Two implementations behind one protocol, chosen by DATABASE_URL, exactly as
with the session and document stores. One contract suite covers both.
"""

import hashlib
import logging
import secrets
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from config.settings import settings

logger = logging.getLogger(__name__)


def hash_login_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_login_token() -> str:
    return secrets.token_urlsafe(32)


@runtime_checkable
class AccountStore(Protocol):
    def get_or_create_manager(self, email: str) -> str: ...
    def get_manager(self, manager_id: str) -> Optional[Dict[str, Any]]: ...
    def create_login_token(self, email: str, ttl_seconds: float) -> str: ...
    def consume_login_token(self, raw_token: str) -> Optional[str]: ...
    def count_login_tokens_since(self, email: str, since: float) -> int: ...
    def add_handover(self, stage1_session_id: str, manager_id: str) -> None: ...
    def set_handover_title(self, stage1_session_id: str, title: str) -> None: ...
    def owner_of_handover(self, stage1_session_id: str) -> Optional[str]: ...
    def list_handovers(self, manager_id: str) -> List[Dict[str, Any]]: ...
    def sweep_expired(self) -> int: ...


class InMemoryAccountStore:
    """Process-local. Everything here dies with the process, by design."""

    def __init__(self):
        self._managers: Dict[str, Dict[str, Any]] = {}
        self._by_email: Dict[str, str] = {}
        self._login_tokens: Dict[str, Dict[str, Any]] = {}
        self._handovers: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get_or_create_manager(self, email: str) -> str:
        email = email.strip().lower()
        with self._lock:
            existing = self._by_email.get(email)
            if existing:
                return existing
            manager_id = str(uuid.uuid4())
            self._managers[manager_id] = {
                "manager_id": manager_id, "email": email, "created_at": time.time(),
            }
            self._by_email[email] = manager_id
            return manager_id

    def get_manager(self, manager_id: str) -> Optional[Dict[str, Any]]:
        record = self._managers.get(manager_id)
        return dict(record) if record else None

    def create_login_token(self, email: str, ttl_seconds: float) -> str:
        raw = new_login_token()
        now = time.time()
        with self._lock:
            self._login_tokens[hash_login_token(raw)] = {
                "email": email.strip().lower(),
                "created_at": now,
                "expires_at": now + ttl_seconds,
                "used": False,
            }
        return raw

    def consume_login_token(self, raw_token: str) -> Optional[str]:
        key = hash_login_token(raw_token)
        with self._lock:
            record = self._login_tokens.get(key)
            if record is None or record["used"] or record["expires_at"] < time.time():
                return None
            record["used"] = True
            return record["email"]

    def count_login_tokens_since(self, email: str, since: float) -> int:
        email = email.strip().lower()
        return sum(
            1 for r in self._login_tokens.values()
            if r["email"] == email and r["created_at"] >= since
        )

    def add_handover(self, stage1_session_id: str, manager_id: str) -> None:
        with self._lock:
            self._handovers.setdefault(stage1_session_id, {
                "stage1_session_id": stage1_session_id,
                "manager_id": manager_id,
                "title": None,
                "created_at": time.time(),
            })

    def set_handover_title(self, stage1_session_id: str, title: str) -> None:
        with self._lock:
            record = self._handovers.get(stage1_session_id)
            if record is not None:
                record["title"] = title

    def owner_of_handover(self, stage1_session_id: str) -> Optional[str]:
        record = self._handovers.get(stage1_session_id)
        return record["manager_id"] if record else None

    def list_handovers(self, manager_id: str) -> List[Dict[str, Any]]:
        rows = [dict(r) for r in self._handovers.values() if r["manager_id"] == manager_id]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        for r in rows:
            r.pop("manager_id", None)
        return rows

    def sweep_expired(self) -> int:
        """Drop login tokens past their expiry. Handover records are kept."""
        now = time.time()
        with self._lock:
            stale = [k for k, r in self._login_tokens.items() if r["expires_at"] < now]
            for k in stale:
                del self._login_tokens[k]
        return len(stale)


_store: Optional[AccountStore] = None
_store_guard = threading.Lock()


def get_account_store() -> AccountStore:
    global _store
    if _store is not None:
        return _store
    with _store_guard:
        if _store is not None:
            return _store
        if settings.database_url:
            from api.postgres_store import PostgresAccountStore

            _store = PostgresAccountStore()
            logger.info("account store: postgres")
        else:
            _store = InMemoryAccountStore()
            logger.warning("account store: in-process — accounts will not survive a restart")
    return _store


def reset_account_store() -> None:
    """Drop the cached store. Used by tests to switch backends."""
    global _store
    _store = None
