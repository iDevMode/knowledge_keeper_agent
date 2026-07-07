"""Durable registry of generated-document metadata and generation status.

Tracks, per document_id: the owning Stage 1 session (for manager-token checks),
generation status (generating/complete/failed), the blob key, content type,
download filename, and any error. Kept durable/shared so the manager can poll
status and download from any worker, and links survive a restart.

- memory     → in-process dict (dev/tests)
- persistent → Redis (shared across workers, TTL'd)
"""

from typing import Any, Dict, Optional

from config.settings import settings


def _new_record(document_id: str, owner_id: str, fmt: str) -> Dict[str, Any]:
    return {
        "document_id": document_id,
        "owner_id": owner_id,
        "status": "generating",
        "format": fmt,
        "key": None,
        "content_type": None,
        "filename": None,
        "error": None,
    }


class InMemoryDocumentRegistry:
    def __init__(self):
        self._d: Dict[str, Dict[str, Any]] = {}

    def create(self, document_id: str, owner_id: str, fmt: str) -> None:
        self._d[document_id] = _new_record(document_id, owner_id, fmt)

    def mark_complete(self, document_id: str, key: str, content_type: str, filename: str) -> None:
        rec = self._d.get(document_id)
        if rec:
            rec.update(status="complete", key=key, content_type=content_type, filename=filename, error=None)

    def mark_failed(self, document_id: str, error: str) -> None:
        rec = self._d.get(document_id)
        if rec:
            rec.update(status="failed", error=error)

    def get(self, document_id: str) -> Optional[Dict[str, Any]]:
        rec = self._d.get(document_id)
        return dict(rec) if rec else None

    def delete(self, document_id: str) -> None:
        self._d.pop(document_id, None)


class RedisDocumentRegistry:
    """Same contract, backed by a JSON KV (RedisKVStore)."""

    _PREFIX = "kk:doc:"

    def __init__(self, kv, ttl_seconds: int):
        self._kv = kv
        self._ttl = ttl_seconds

    def _key(self, document_id: str) -> str:
        return self._PREFIX + document_id

    def create(self, document_id: str, owner_id: str, fmt: str) -> None:
        self._kv.set(self._key(document_id), _new_record(document_id, owner_id, fmt), self._ttl)

    def mark_complete(self, document_id: str, key: str, content_type: str, filename: str) -> None:
        rec = self._kv.get(self._key(document_id))
        if rec:
            rec.update(status="complete", key=key, content_type=content_type, filename=filename, error=None)
            self._kv.set(self._key(document_id), rec, self._ttl)

    def mark_failed(self, document_id: str, error: str) -> None:
        rec = self._kv.get(self._key(document_id))
        if rec:
            rec.update(status="failed", error=error)
            self._kv.set(self._key(document_id), rec, self._ttl)

    def get(self, document_id: str) -> Optional[Dict[str, Any]]:
        return self._kv.get(self._key(document_id))

    def delete(self, document_id: str) -> None:
        self._kv.delete(self._key(document_id))


def create_document_registry():
    if settings.storage_backend == "persistent":
        from api.persistence.redis_kv import RedisKVStore, create_redis_client

        kv = RedisKVStore(create_redis_client(settings.redis_url))
        return RedisDocumentRegistry(kv, settings.stage1_to_stage2_link_ttl_hours * 3600)
    return InMemoryDocumentRegistry()
