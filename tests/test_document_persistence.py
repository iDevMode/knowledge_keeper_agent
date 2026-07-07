"""Unit tests for the document blob store and metadata registry."""

from api.persistence.document_registry import InMemoryDocumentRegistry, RedisDocumentRegistry
from api.persistence.document_store import LocalDocumentBlobStore


class FakeKV:
    def __init__(self):
        self._d = {}

    def get(self, key):
        import copy
        v = self._d.get(key)
        return copy.deepcopy(v) if v is not None else None

    def set(self, key, value, ttl_seconds):
        import copy
        self._d[key] = copy.deepcopy(value)

    def delete(self, key):
        self._d.pop(key, None)

    def exists(self, key):
        return key in self._d


class TestLocalBlobStore:
    def test_put_read_roundtrip(self, tmp_path):
        store = LocalDocumentBlobStore(str(tmp_path))
        store.put("doc-1.docx", b"hello")
        assert store.read("doc-1.docx") == b"hello"
        assert store.exists("doc-1.docx")

    def test_read_missing_returns_none(self, tmp_path):
        store = LocalDocumentBlobStore(str(tmp_path))
        assert store.read("nope.docx") is None
        assert store.exists("nope.docx") is False

    def test_key_traversal_is_contained(self, tmp_path):
        store = LocalDocumentBlobStore(str(tmp_path))
        # A traversal-looking key is reduced to its basename, staying in-root.
        store.put("../escape.docx", b"x")
        assert (tmp_path / "escape.docx").exists()
        assert not (tmp_path.parent / "escape.docx").exists()


class TestDocumentRegistryContract:
    def _both(self):
        return [
            InMemoryDocumentRegistry(),
            RedisDocumentRegistry(FakeKV(), ttl_seconds=3600),
        ]

    def test_create_then_complete(self):
        for reg in self._both():
            reg.create("d1", owner_id="s1", fmt="docx")
            assert reg.get("d1")["status"] == "generating"
            assert reg.get("d1")["owner_id"] == "s1"

            reg.mark_complete("d1", key="d1.docx", content_type="application/x", filename="H.docx")
            rec = reg.get("d1")
            assert rec["status"] == "complete"
            assert rec["key"] == "d1.docx"
            assert rec["filename"] == "H.docx"

    def test_mark_failed(self):
        for reg in self._both():
            reg.create("d2", owner_id="s1", fmt="pdf")
            reg.mark_failed("d2", "boom")
            rec = reg.get("d2")
            assert rec["status"] == "failed"
            assert rec["error"] == "boom"

    def test_get_unknown_returns_none(self):
        for reg in self._both():
            assert reg.get("nope") is None
