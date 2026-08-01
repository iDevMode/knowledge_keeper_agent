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
    """After H3 the registry caches locks, not graphs.

    Graphs are compiled once per stage and rebuilt on demand against the shared
    checkpointer, so there is no per-session object to evict — only the
    per-session lock, which would otherwise grow for the life of the process.
    """

    def _seeded_registry(self, *session_ids):
        from api.session_manager import get_session_store

        store = get_session_store()
        registry = routes_mod.GraphRegistry()
        created = {}
        for name in session_ids:
            session_id = store.create_session(stage=1)
            created[name] = session_id
            registry.create_stage1(session_id)
        return registry, created

    def test_idle_locks_are_released(self):
        registry, ids = self._seeded_registry("old", "fresh")
        registry.get_lock(ids["old"])
        registry.get_lock(ids["fresh"])

        registry._last_used[ids["old"]] = time.time() - 10_000

        assert registry.sweep_idle(max_idle_seconds=3600) == 1
        assert ids["old"] not in registry._locks
        assert ids["fresh"] in registry._locks

    def test_eviction_releases_the_lock_too(self):
        registry, ids = self._seeded_registry("s1")
        registry.get_lock(ids["s1"])
        registry._last_used[ids["s1"]] = time.time() - 10_000

        registry.sweep_idle(max_idle_seconds=3600)

        assert ids["s1"] not in registry._locks
        assert ids["s1"] not in registry._last_used

    def test_activity_defers_eviction(self):
        registry, ids = self._seeded_registry("s1")
        registry._last_used[ids["s1"]] = time.time() - 10_000

        # Accessing the session marks it in use again.
        registry.get(ids["s1"])

        assert registry.sweep_idle(max_idle_seconds=3600) == 0

    def test_a_swept_session_is_still_servable(self):
        """The point of H3: eviction must not strand a live interview.

        Previously sweeping dropped the only copy of the graph, so the session
        became unreachable and every message 404'd. Now the graph is rebuilt.
        """
        registry, ids = self._seeded_registry("s1")
        registry.sweep_idle(max_idle_seconds=0)

        assert registry.get(ids["s1"]) is not None, (
            "a live session became unservable after its lock was released"
        )

    def test_get_reflects_the_session_store_not_a_cache(self):
        from api.session_manager import get_session_store

        registry = routes_mod.GraphRegistry()
        session_id = get_session_store().create_session(stage=2)

        # Never passed through create_stage2 — a second worker would be in
        # exactly this position, having never seen the session created.
        instance = registry.get(session_id)
        assert instance is not None
        assert instance.stage == 2

    def test_unknown_session_has_no_graph(self):
        registry = routes_mod.GraphRegistry()
        assert registry.get("no-such-session") is None


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
