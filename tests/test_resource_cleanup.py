"""Resource retention and cleanup (review finding M4).

Every one of these grew monotonically for the lifetime of the process: graph
instances and their checkpoints, session records, generation job records, and
the generated files on disk. A long-running instance leaked memory and disk
until it fell over.
"""

import os
import time
from pathlib import Path

import pytest

import api.routes as routes_mod
import api.session_manager as sm_mod


@pytest.fixture(autouse=True)
def reset_singletons():
    sm_mod._store = None
    routes_mod._registry = routes_mod.GraphRegistry()
    routes_mod._document_store = {}
    routes_mod._generation_jobs = {}
    routes_mod._document_created_at = {}
    routes_mod._last_sweep_at = 0.0
    yield


class TestGraphRegistryEviction:
    def test_idle_instances_are_evicted(self):
        registry = routes_mod.GraphRegistry()
        registry.create_stage1("old-session")
        registry.create_stage1("fresh-session")

        # Age one instance past the retention window.
        registry._last_used["old-session"] = time.time() - 10_000

        evicted = registry.sweep_idle(max_idle_seconds=3600)

        assert evicted == 1
        assert registry.get("old-session") is None
        assert registry.get("fresh-session") is not None

    def test_eviction_releases_the_lock_too(self):
        registry = routes_mod.GraphRegistry()
        registry.create_stage1("s1")
        registry.get_lock("s1")
        registry._last_used["s1"] = time.time() - 10_000

        registry.sweep_idle(max_idle_seconds=3600)

        assert "s1" not in registry._locks
        assert "s1" not in registry._last_used

    def test_activity_defers_eviction(self):
        registry = routes_mod.GraphRegistry()
        registry.create_stage1("s1")
        registry._last_used["s1"] = time.time() - 10_000

        # Accessing the session marks it in use again.
        registry.get("s1")

        assert registry.sweep_idle(max_idle_seconds=3600) == 0
        assert registry.get("s1") is not None


class TestSessionStoreSweep:
    def test_expired_sessions_and_profiles_are_dropped(self):
        store = sm_mod.InMemorySessionStore(ttl_hours=1)
        keep = store.create_session(stage=1)
        drop = store.create_session(stage=1)

        store._sessions[drop]["_created_at"] = time.time() - 10_000

        removed = store.sweep_expired()

        assert removed == 1
        assert store.get_session(drop) is None
        assert store.get_session(keep) is not None

    def test_links_are_cleaned_up_on_both_sides(self):
        store = sm_mod.InMemorySessionStore(ttl_hours=1)
        stage1 = store.create_session(stage=1)
        stage2 = store.create_session(stage=2)
        store.link_sessions(stage1, stage2)

        store._sessions[stage1]["_created_at"] = time.time() - 10_000
        store.sweep_expired()

        assert store.get_linked_session(stage1) is None
        assert store.get_linked_session(stage2) is None, (
            "the reverse link leaked after the session expired"
        )

    def test_abandoned_sessions_do_not_require_access_to_expire(self):
        # Previously expiry was applied lazily on next access, so a session
        # nobody touched again stayed resident forever.
        store = sm_mod.InMemorySessionStore(ttl_hours=1)
        for _ in range(5):
            sid = store.create_session(stage=1)
            store._sessions[sid]["_created_at"] = time.time() - 10_000

        assert store.sweep_expired() == 5
        assert store._sessions == {}
        assert store._profiles == {}


class TestDocumentRetention:
    def _make_document(self, doc_id: str, age_seconds: float) -> Path:
        path = routes_mod._document_dir() / f"{doc_id}.docx"
        path.write_bytes(b"fake docx")
        routes_mod._document_store[doc_id] = str(path)
        routes_mod._generation_jobs[doc_id] = {
            "status": "complete", "download_url": f"/api/documents/{doc_id}", "error": None,
        }
        routes_mod._document_created_at[doc_id] = time.time() - age_seconds
        return path

    def test_expired_documents_are_deleted_from_disk_and_memory(self):
        stale = self._make_document("stale-doc", age_seconds=10_000_000)
        fresh = self._make_document("fresh-doc", age_seconds=0)

        try:
            routes_mod.sweep_resources(force=True)

            assert not stale.exists(), "expired document file was left on disk"
            assert "stale-doc" not in routes_mod._document_store
            assert "stale-doc" not in routes_mod._generation_jobs
            assert "stale-doc" not in routes_mod._document_created_at

            assert fresh.exists()
            assert "fresh-doc" in routes_mod._document_store
        finally:
            for p in (stale, fresh):
                if p.exists():
                    os.remove(p)

    def test_sweep_survives_a_missing_file(self):
        self._make_document("ghost-doc", age_seconds=10_000_000)
        os.remove(routes_mod._document_store["ghost-doc"])

        # Must not raise even though the file is already gone.
        routes_mod.sweep_resources(force=True)

        assert "ghost-doc" not in routes_mod._document_store

    def test_documents_share_one_managed_directory(self):
        a = self._make_document("doc-a", age_seconds=0)
        b = self._make_document("doc-b", age_seconds=0)
        try:
            assert a.parent == b.parent == routes_mod._DOCUMENT_DIR, (
                "each generation should not create its own temp directory"
            )
        finally:
            for p in (a, b):
                if p.exists():
                    os.remove(p)


class TestSweepRateLimiting:
    def test_sweep_is_rate_limited_by_default(self):
        routes_mod._last_sweep_at = time.time()
        assert routes_mod.sweep_resources() == {}, (
            "an unforced sweep inside the interval should be a no-op"
        )

    def test_force_bypasses_the_rate_limit(self):
        routes_mod._last_sweep_at = time.time()
        assert routes_mod.sweep_resources(force=True) != {}

    def test_sweep_runs_once_the_interval_has_elapsed(self):
        routes_mod._last_sweep_at = time.time() - routes_mod._SWEEP_INTERVAL_SECONDS - 1
        assert routes_mod.sweep_resources() != {}
