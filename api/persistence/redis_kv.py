"""Redis-backed key/value store for ephemeral session state and tokens.

Values are JSON blobs with a TTL. `redis` is imported lazily so the dependency
is only required when STORAGE_BACKEND=persistent.
"""

import json
from typing import Any, Dict, Optional


def create_redis_client(url: str):
    """Create a redis client from a URL (lazy import)."""
    import redis  # noqa: PLC0415 — optional dependency, only needed for persistent backend

    return redis.from_url(url, decode_responses=True)


class RedisKVStore:
    """Thin JSON-over-Redis adapter.

    Expected client API (a subset of redis-py): get, set(name, value, ex=),
    delete, exists.
    """

    def __init__(self, client):
        self._client = client

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        raw = self._client.get(key)
        return json.loads(raw) if raw is not None else None

    def set(self, key: str, value: Dict[str, Any], ttl_seconds: int) -> None:
        self._client.set(key, json.dumps(value), ex=ttl_seconds)

    def delete(self, key: str) -> None:
        self._client.delete(key)

    def exists(self, key: str) -> bool:
        return bool(self._client.exists(key))
