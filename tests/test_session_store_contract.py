"""One contract, both session stores (review finding H3).

Every test here runs against InMemorySessionStore AND PostgresSessionStore. The
point is that the two cannot quietly diverge: api/routes.py holds a SessionStore
and does not know which implementation it has, so any behavioural difference
becomes a production-only bug.

The Postgres parameter is skipped when TEST_DATABASE_URL is unset. That skip is
deliberate but it is also a trap — a security test on this project once skipped
silently in CI and proved nothing for weeks — so:

  * the in-memory parameter always runs, covering the logic;
  * the skip reason names the variable to set;
  * tests/test_postgres_store.py asserts the Postgres path was actually
    exercised when the variable IS set, rather than trusting a green run.

    docker run -d --name kk-test-pg -e POSTGRES_PASSWORD=kktest \
        -e POSTGRES_DB=kk -p 55432:5432 postgres:16-alpine
    TEST_DATABASE_URL=postgresql://postgres:kktest@localhost:55432/kk pytest
"""

import json
import os
import time
import uuid

import pytest

from api.session_manager import InMemorySessionStore
from models.role_intelligence_profile import RoleIntelligenceProfile

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

requires_postgres = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set TEST_DATABASE_URL to run the Postgres half of the store contract",
)


def _load_profile(profile_id: str = "process_heavy") -> RoleIntelligenceProfile:
    with open("tests/fixtures/sample_role_profiles.json") as f:
        return RoleIntelligenceProfile.model_validate(json.load(f)[profile_id])


def _make_postgres_store(ttl_hours):
    from api.postgres_store import PostgresSessionStore

    store = PostgresSessionStore(conninfo=TEST_DATABASE_URL, ttl_hours=ttl_hours)
    # Each test gets a clean slate; the container is shared across the run.
    with store._pool.connection() as conn:
        conn.execute("TRUNCATE kk_sessions, kk_session_links, kk_profiles")
    return store


@pytest.fixture(
    params=[
        pytest.param("memory", id="memory"),
        pytest.param("postgres", id="postgres", marks=requires_postgres),
    ]
)
def store_factory(request):
    """Returns a callable(ttl_hours) -> SessionStore for the backend under test."""
    created = []

    def make(ttl_hours=72):
        store = (
            InMemorySessionStore(ttl_hours=ttl_hours)
            if request.param == "memory"
            else _make_postgres_store(ttl_hours)
        )
        created.append(store)
        return store

    make.backend = request.param
    yield make

    for store in created:
        if hasattr(store, "close"):
            store.close()


@pytest.fixture
def store(store_factory):
    return store_factory()


class TestSessionLifecycle:
    def test_create_returns_a_unique_id(self, store):
        a, b = store.create_session(stage=1), store.create_session(stage=1)
        assert a != b
        assert uuid.UUID(a)

    def test_created_session_is_readable(self, store):
        session_id = store.create_session(stage=1)
        session = store.get_session(session_id)

        assert session is not None
        assert session["session_id"] == session_id
        assert session["stage"] == 1
        assert isinstance(session["_created_at"], float)

    def test_unknown_session_is_none(self, store):
        assert store.get_session("no-such-session") is None

    def test_metadata_is_round_tripped(self, store):
        session_id = store.create_session(
            stage=2, metadata={"stage1_session_id": "abc", "nested": {"a": 1}}
        )
        session = store.get_session(session_id)

        assert session["stage1_session_id"] == "abc"
        assert session["nested"] == {"a": 1}

    def test_update_merges_rather_than_replaces(self, store):
        session_id = store.create_session(stage=2, metadata={"keep": "this"})
        store.update_session(session_id, {"session_complete": True})

        session = store.get_session(session_id)
        assert session["session_complete"] is True
        assert session["keep"] == "this", "update clobbered existing metadata"

    def test_update_of_unknown_session_is_a_no_op(self, store):
        store.update_session("no-such-session", {"session_complete": True})
        assert store.get_session("no-such-session") is None


class TestLinking:
    def test_link_is_bidirectional(self, store):
        s1 = store.create_session(stage=1)
        s2 = store.create_session(stage=2)
        store.link_sessions(s1, s2)

        assert store.get_linked_session(s1) == s2
        assert store.get_linked_session(s2) == s1

    def test_unlinked_session_returns_none(self, store):
        assert store.get_linked_session(store.create_session(stage=1)) is None

    def test_relinking_replaces_the_previous_target(self, store):
        s1 = store.create_session(stage=1)
        first = store.create_session(stage=2)
        second = store.create_session(stage=2)

        store.link_sessions(s1, first)
        store.link_sessions(s1, second)

        assert store.get_linked_session(s1) == second


class TestProfiles:
    def test_profile_round_trips_intact(self, store):
        session_id = store.create_session(stage=1)
        original = _load_profile()
        store.store_profile(session_id, original)

        loaded = store.get_profile(session_id)
        assert loaded is not None
        assert loaded.model_dump() == original.model_dump()

    def test_missing_profile_is_none(self, store):
        assert store.get_profile(store.create_session(stage=1)) is None

    def test_storing_twice_overwrites(self, store):
        session_id = store.create_session(stage=1)
        store.store_profile(session_id, _load_profile("process_heavy"))
        store.store_profile(session_id, _load_profile("relationship_heavy"))

        assert store.get_profile(session_id).role_type == (
            _load_profile("relationship_heavy").role_type
        )

    @pytest.mark.parametrize(
        "profile_id", ["process_heavy", "decision_heavy", "relationship_heavy"]
    )
    def test_every_fixture_role_type_survives(self, store, profile_id):
        session_id = store.create_session(stage=1)
        original = _load_profile(profile_id)
        store.store_profile(session_id, original)
        assert store.get_profile(session_id).model_dump() == original.model_dump()


class TestExpiry:
    """A tiny TTL plus a real wait, so both backends are tested the same way."""

    TINY_TTL_HOURS = 1 / 3600  # one second

    def test_expired_session_is_invisible_before_any_sweep(self, store_factory):
        store = store_factory(ttl_hours=self.TINY_TTL_HOURS)
        session_id = store.create_session(stage=1)
        assert store.get_session(session_id) is not None

        time.sleep(1.2)

        assert store.get_session(session_id) is None, (
            "an expired session was still readable"
        )

    def test_sweep_reclaims_expired_sessions_only(self, store_factory):
        short = store_factory(ttl_hours=self.TINY_TTL_HOURS)
        doomed = short.create_session(stage=1)
        time.sleep(1.2)
        survivor = short.create_session(stage=1)

        assert short.sweep_expired() == 1
        assert short.get_session(doomed) is None
        assert short.get_session(survivor) is not None

    def test_sweep_drops_the_profile_too(self, store_factory):
        store = store_factory(ttl_hours=self.TINY_TTL_HOURS)
        session_id = store.create_session(stage=1)
        store.store_profile(session_id, _load_profile())
        time.sleep(1.2)

        store.sweep_expired()
        assert store.get_profile(session_id) is None, (
            "the Role Intelligence Profile outlived its session"
        )

    def test_sweep_drops_both_sides_of_a_link(self, store_factory):
        store = store_factory(ttl_hours=self.TINY_TTL_HOURS)
        s1 = store.create_session(stage=1)
        s2 = store.create_session(stage=2)
        store.link_sessions(s1, s2)
        time.sleep(1.2)

        store.sweep_expired()
        assert store.get_linked_session(s1) is None
        assert store.get_linked_session(s2) is None, "a dangling reverse link survived"

    def test_sweep_with_nothing_expired_returns_zero(self, store):
        store.create_session(stage=1)
        assert store.sweep_expired() == 0


class TestProtocolConformance:
    def test_the_store_satisfies_the_protocol(self, store):
        from api.session_manager import SessionStore

        assert isinstance(store, SessionStore)
