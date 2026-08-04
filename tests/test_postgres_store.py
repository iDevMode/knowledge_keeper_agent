"""Postgres-specific behaviour the shared contract cannot express (H3).

The contract suite in test_session_store_contract.py proves both backends agree.
This file covers what only matters for the durable one: that a second process
sees the first one's writes, that a restart does not lose anything, and that
concurrent workers do not corrupt each other — the three things H3 exists for.

Requires TEST_DATABASE_URL:

    docker run -d --name kk-test-pg -e POSTGRES_PASSWORD=kktest \
        -e POSTGRES_DB=kk -p 55432:5432 postgres:16-alpine
    TEST_DATABASE_URL=postgresql://postgres:kktest@localhost:55432/kk pytest
"""

import json
import os
import threading

import pytest

from models.role_intelligence_profile import RoleIntelligenceProfile

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set TEST_DATABASE_URL to run the Postgres store tests",
)


def _load_profile(profile_id: str = "process_heavy") -> RoleIntelligenceProfile:
    with open("tests/fixtures/sample_role_profiles.json") as f:
        return RoleIntelligenceProfile.model_validate(json.load(f)[profile_id])


def _store(ttl_hours: float = 72):
    from api.postgres_store import PostgresSessionStore

    return PostgresSessionStore(conninfo=TEST_DATABASE_URL, ttl_hours=ttl_hours)


@pytest.fixture
def clean_db():
    store = _store()
    with store._pool.connection() as conn:
        conn.execute("TRUNCATE kk_sessions, kk_session_links, kk_profiles")
    yield store
    store.close()


class TestStateCrossesProcessBoundaries:
    """The whole point of H3: another worker, or this one after a restart."""

    def test_a_second_store_instance_sees_the_first_ones_session(self, clean_db):
        worker_a = clean_db
        session_id = worker_a.create_session(stage=1, metadata={"from": "worker A"})

        worker_b = _store()  # a separate pool, as a second uvicorn worker would be
        try:
            session = worker_b.get_session(session_id)
            assert session is not None, "worker B could not see worker A's session"
            assert session["from"] == "worker A"
        finally:
            worker_b.close()

    def test_a_profile_written_by_one_worker_is_readable_by_another(self, clean_db):
        session_id = clean_db.create_session(stage=1)
        clean_db.store_profile(session_id, _load_profile())

        worker_b = _store()
        try:
            assert worker_b.get_profile(session_id) is not None
        finally:
            worker_b.close()

    def test_an_update_from_one_worker_is_visible_to_another(self, clean_db):
        session_id = clean_db.create_session(stage=2)

        worker_b = _store()
        try:
            worker_b.update_session(session_id, {"session_complete": True})
            assert clean_db.get_session(session_id)["session_complete"] is True
        finally:
            worker_b.close()

    def test_links_cross_workers_in_both_directions(self, clean_db):
        s1 = clean_db.create_session(stage=1)
        s2 = clean_db.create_session(stage=2)
        clean_db.link_sessions(s1, s2)

        worker_b = _store()
        try:
            assert worker_b.get_linked_session(s1) == s2
            assert worker_b.get_linked_session(s2) == s1
        finally:
            worker_b.close()

    def test_state_survives_dropping_every_connection(self, clean_db):
        """Stands in for a redeploy: the process goes, the data does not."""
        session_id = clean_db.create_session(stage=1, metadata={"before": "restart"})
        clean_db.store_profile(session_id, _load_profile())
        clean_db.close()

        after_restart = _store()
        try:
            session = after_restart.get_session(session_id)
            assert session is not None, "the interview did not survive the restart"
            assert session["before"] == "restart"
            assert after_restart.get_profile(session_id) is not None
        finally:
            after_restart.close()


class TestConcurrency:
    def test_parallel_session_creation_loses_none(self, clean_db):
        created, errors = [], []
        lock = threading.Lock()

        def worker():
            try:
                sid = clean_db.create_session(stage=1)
                with lock:
                    created.append(sid)
            except Exception as e:  # noqa: BLE001 - surfaced in the assertion
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"concurrent creation raised: {errors[:3]}"
        assert len(set(created)) == 20, "session ids collided under concurrency"
        assert all(clean_db.get_session(s) is not None for s in created)

    def test_concurrent_updates_to_one_session_all_land(self, clean_db):
        session_id = clean_db.create_session(stage=2)

        def worker(n):
            clean_db.update_session(session_id, {f"key_{n}": n})

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        session = clean_db.get_session(session_id)
        missing = [i for i in range(20) if session.get(f"key_{i}") != i]
        assert not missing, f"concurrent updates lost keys: {missing}"


class TestSchema:
    def test_ensure_schema_is_idempotent(self, clean_db):
        clean_db.ensure_schema()
        clean_db.ensure_schema()
        assert clean_db.get_session(clean_db.create_session(stage=1)) is not None

    def test_tables_are_namespaced(self, clean_db):
        with clean_db._pool.connection() as conn:
            names = {
                r[0]
                for r in conn.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                ).fetchall()
            }
        ours = {"kk_sessions", "kk_session_links", "kk_profiles"}
        assert ours <= names
        # Everything we create is prefixed, so the database can be shared with
        # the langgraph checkpointer's own tables without collision.
        assert all(n.startswith("kk_") for n in names if n in ours)


class TestTheSkipGuardItself:
    """A skipped security test once proved nothing here for weeks.

    If TEST_DATABASE_URL is set, these tests must genuinely run against
    Postgres. This asserts the wiring rather than trusting a green summary.
    """

    def test_this_module_is_really_talking_to_postgres(self, clean_db):
        with clean_db._pool.connection() as conn:
            version = conn.execute("SELECT version()").fetchone()[0]
        assert "PostgreSQL" in version, f"not a Postgres connection: {version[:60]}"
