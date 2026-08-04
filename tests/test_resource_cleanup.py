"""Resource retention and cleanup (review finding M4).

Every one of these grew monotonically for the lifetime of the process: graph
instances and their checkpoints, session records, generation job records, and
the generated files on disk. A long-running instance leaked memory and disk
until it fell over.
"""

import time
from unittest.mock import MagicMock, patch

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
    """Documents are bytes in the store now, not files on disk (H3).

    Keeping them out of the container filesystem is deliberate: a handover pack
    carries the Risk Summary written about a departing employee, and the managed
    temp directory previously had to be chmod'ed 0o700 to stop other local users
    reading it.
    """

    def _documents(self):
        from api.document_store import get_document_store

        return get_document_store()

    def _make_document(self, doc_id: str, age_seconds: float) -> None:
        store = self._documents()
        store.start_job(doc_id, f"session-for-{doc_id}")
        store.complete_job(doc_id, f"{doc_id}.docx", routes_mod.DOCX_MEDIA_TYPE, b"fake docx")
        store._documents[doc_id]["created_at"] = time.time() - age_seconds

    def test_expired_documents_are_dropped_and_fresh_ones_kept(self):
        self._make_document("stale-doc", age_seconds=10_000_000)
        self._make_document("fresh-doc", age_seconds=0)

        routes_mod.sweep_resources(force=True)

        store = self._documents()
        assert store.get_content("stale-doc") is None
        assert store.get_job("stale-doc") is None
        assert store.owner_of("stale-doc") is None

        assert store.get_content("fresh-doc") is not None

    def test_the_swept_count_is_reported(self):
        self._make_document("a", age_seconds=10_000_000)
        self._make_document("b", age_seconds=10_000_000)
        self._make_document("c", age_seconds=0)

        assert routes_mod.sweep_resources(force=True)["documents"] == 2

    def test_a_swept_document_takes_its_session_pointer_with_it(self):
        self._make_document("orphan-doc", age_seconds=10_000_000)
        store = self._documents()
        assert store.document_for_session("session-for-orphan-doc") == "orphan-doc"

        routes_mod.sweep_resources(force=True)

        assert store.document_for_session("session-for-orphan-doc") is None

    def test_generation_leaves_no_file_behind(self):
        """The exporters write a file; it must not outlive the export."""
        from api.document_store import get_document_store
        from output.formatters.document_formatter import InterimDocument

        doc_dir = routes_mod._document_dir()
        before = set(doc_dir.iterdir())

        with patch("api.routes.generate_document") as gen, \
             patch("api.routes.parse_llm_output"), \
             patch("api.routes.generate_docx") as docx:
            gen.return_value = MagicMock(raw_markdown="# Handover")
            docx.side_effect = lambda d, p: (open(p, "wb").write(b"PACK"), p)[1]

            get_document_store().start_job("transient-doc", "some-session")
            routes_mod._run_generation_in_background(
                "transient-doc", "some-session", MagicMock(), MagicMock(), "docx"
            )

        assert set(doc_dir.iterdir()) == before, (
            "an exported handover pack was left on the container filesystem"
        )
        assert get_document_store().get_content("transient-doc") == (
            "transient-doc.docx", routes_mod.DOCX_MEDIA_TYPE, b"PACK"
        )


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
