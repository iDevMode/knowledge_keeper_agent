"""Generated handover packs and their generation jobs (review finding H3).

Document state was six process dictionaries plus files on local disk. A restart
lost the lot: the manager's status poll went from "preparing your pack" to
nothing at all, with the file orphaned on a filesystem the next container would
not have.

Documents are stored as bytes rather than paths. That is deliberate beyond
persistence: a handover pack contains the Risk Summary written about a departing
employee, and keeping it out of the container filesystem entirely removes a
whole class of exposure — the managed temp directory previously had to be
chmod'ed to 0o700 to stop other local users reading it. Only a transient file
exists now, during export, and it is deleted immediately.

Two implementations behind one protocol, chosen by DATABASE_URL, exactly as with
SessionStore. One contract suite covers both.
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from config.settings import settings

logger = logging.getLogger(__name__)

GENERATING = "generating"
COMPLETE = "complete"
FAILED = "failed"


@runtime_checkable
class DocumentStore(Protocol):
    def start_job(self, document_id: str, session_id: str) -> None: ...
    def complete_job(self, document_id: str, filename: str, media_type: str,
                     content: bytes) -> None: ...
    def fail_job(self, document_id: str, error: str) -> None: ...
    def record_delivery(self, document_id: str, status: str, recipients: List[str],
                        error: Optional[str]) -> None: ...
    def get_job(self, document_id: str) -> Optional[Dict[str, Any]]: ...
    def get_content(self, document_id: str) -> Optional[tuple[str, str, bytes]]: ...
    def owner_of(self, document_id: str) -> Optional[str]: ...
    def document_for_session(self, session_id: str) -> Optional[str]: ...
    def completed_document_for_session(self, session_id: str) -> Optional[str]: ...
    def set_generation_error(self, session_id: str, message: str) -> None: ...
    def clear_generation_error(self, session_id: str) -> None: ...
    def get_generation_error(self, session_id: str) -> Optional[str]: ...
    def sweep_expired(self) -> int: ...
    def delete_for_session(self, session_id: str) -> int: ...


class InMemoryDocumentStore:
    """Process-local. Everything here dies with the process, by design."""

    def __init__(self, ttl_hours: float | None = None, retention_days: float | None = None):
        # Unfinished and failed jobs live as long as an interview does; a
        # finished pack is kept for the retention period from completion.
        self._ttl_seconds = (ttl_hours or settings.session_ttl_hours) * 3600
        self._retention_seconds = (retention_days or settings.document_retention_days) * 86400
        self._documents: Dict[str, Dict[str, Any]] = {}
        self._session_document: Dict[str, str] = {}
        self._generation_errors: Dict[str, str] = {}
        self._lock = threading.Lock()

    def start_job(self, document_id: str, session_id: str) -> None:
        with self._lock:
            self._documents[document_id] = {
                "session_id": session_id,
                "status": GENERATING,
                "error": None,
                "filename": None,
                "media_type": None,
                "content": None,
                "delivery": None,
                "created_at": time.time(),
                "expires_at": None,
            }
            self._session_document[session_id] = document_id
            self._generation_errors.pop(session_id, None)

    def complete_job(self, document_id: str, filename: str, media_type: str,
                     content: bytes) -> None:
        with self._lock:
            record = self._documents.get(document_id)
            if record is None:
                return
            record.update(
                status=COMPLETE, filename=filename, media_type=media_type,
                content=content, error=None,
                expires_at=time.time() + self._retention_seconds,
            )
            # A finished pack supersedes every earlier one for the session:
            # there is no reason to keep a year of old drafts of the same
            # Risk Summary.
            for other_id, other in list(self._documents.items()):
                if (
                    other_id != document_id
                    and other["session_id"] == record["session_id"]
                    and other["created_at"] <= record["created_at"]
                ):
                    del self._documents[other_id]

    def fail_job(self, document_id: str, error: str) -> None:
        with self._lock:
            record = self._documents.get(document_id)
            if record is None:
                return
            record.update(status=FAILED, error=error)

    def record_delivery(self, document_id: str, status: str, recipients: List[str],
                        error: Optional[str]) -> None:
        with self._lock:
            record = self._documents.get(document_id)
            if record is None:
                return
            record["delivery"] = {
                "status": status,
                "recipients": list(recipients),
                "error": error,
            }

    def get_job(self, document_id: str) -> Optional[Dict[str, Any]]:
        record = self._documents.get(document_id)
        if record is None:
            return None
        return {
            "status": record["status"],
            "error": record["error"],
            "download_url": (
                f"/api/documents/{document_id}" if record["status"] == COMPLETE else None
            ),
            "delivery": dict(record["delivery"]) if record.get("delivery") else None,
            "expires_at": record.get("expires_at"),
        }

    def get_content(self, document_id: str) -> Optional[tuple[str, str, bytes]]:
        record = self._documents.get(document_id)
        if record is None or record["content"] is None:
            return None
        return record["filename"], record["media_type"], record["content"]

    def owner_of(self, document_id: str) -> Optional[str]:
        record = self._documents.get(document_id)
        return record["session_id"] if record else None

    def document_for_session(self, session_id: str) -> Optional[str]:
        return self._session_document.get(session_id)

    def completed_document_for_session(self, session_id: str) -> Optional[str]:
        """The newest finished pack — what a manager can actually download.

        Differs from document_for_session when a regeneration is running or
        has failed: the previous pack is still there and still the answer.
        """
        done = [
            (r["created_at"], d) for d, r in self._documents.items()
            if r["session_id"] == session_id and r["status"] == COMPLETE
        ]
        return max(done)[1] if done else None

    def set_generation_error(self, session_id: str, message: str) -> None:
        self._generation_errors[session_id] = message

    def clear_generation_error(self, session_id: str) -> None:
        self._generation_errors.pop(session_id, None)

    def get_generation_error(self, session_id: str) -> Optional[str]:
        return self._generation_errors.get(session_id)

    def _expiry(self, record: Dict[str, Any]) -> float:
        if record.get("expires_at") is not None:
            return record["expires_at"]
        return record["created_at"] + self._ttl_seconds

    def sweep_expired(self) -> int:
        now = time.time()
        with self._lock:
            stale = [
                doc_id
                for doc_id, record in list(self._documents.items())
                if self._expiry(record) < now
            ]
            for doc_id in stale:
                self._drop(doc_id)
        return len(stale)

    def _drop(self, doc_id: str) -> None:
        record = self._documents.pop(doc_id)
        owner = record["session_id"]
        if self._session_document.get(owner) == doc_id:
            self._session_document.pop(owner, None)
            self._generation_errors.pop(owner, None)

    def delete_for_session(self, session_id: str) -> int:
        """Delete every document and generation error for a session."""
        with self._lock:
            doomed = [d for d, r in self._documents.items() if r["session_id"] == session_id]
            for doc_id in doomed:
                self._drop(doc_id)
            self._session_document.pop(session_id, None)
            self._generation_errors.pop(session_id, None)
        return len(doomed)

    def clear(self) -> None:
        """Test helper — drop everything."""
        with self._lock:
            self._documents.clear()
            self._session_document.clear()
            self._generation_errors.clear()


_store: DocumentStore | None = None
_store_guard = threading.Lock()


def get_document_store() -> DocumentStore:
    global _store
    if _store is not None:
        return _store
    with _store_guard:
        if _store is not None:
            return _store
        if settings.database_url:
            from api.postgres_store import PostgresDocumentStore

            _store = PostgresDocumentStore()
            logger.info("document store: postgres")
        else:
            _store = InMemoryDocumentStore()
            logger.warning(
                "document store: in-process — generated packs will not survive a "
                "restart. Set DATABASE_URL to persist them."
            )
    return _store


def reset_document_store() -> None:
    """Drop the cached store. Used by tests to switch backends."""
    global _store
    _store = None
