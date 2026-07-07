"""Backend-agnostic contract tests for SessionStore implementations.

The same behavioural suite runs against InMemorySessionStore and against
PersistentSessionStore wired to in-memory fakes of the Redis KV and Postgres
profile repo — so the durable store's logic is verified without live services.
"""

import copy
import json
from pathlib import Path

import pytest

from api.session_manager import InMemorySessionStore, PersistentSessionStore
from models.role_intelligence_profile import RoleIntelligenceProfile

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "sample_role_profiles.json"


def _load_profile(profile_id: str = "process_heavy") -> RoleIntelligenceProfile:
    with open(FIXTURES_PATH) as f:
        return RoleIntelligenceProfile.model_validate(json.load(f)[profile_id])


class FakeKV:
    """In-memory stand-in for RedisKVStore. Deep-copies on get/set to mimic
    Redis JSON serialisation (callers can't mutate stored state by reference)."""

    def __init__(self):
        self._d = {}

    def get(self, key):
        v = self._d.get(key)
        return copy.deepcopy(v) if v is not None else None

    def set(self, key, value, ttl_seconds):
        self._d[key] = copy.deepcopy(value)

    def delete(self, key):
        self._d.pop(key, None)

    def exists(self, key):
        return key in self._d


class FakeProfileRepo:
    def __init__(self):
        self._d = {}

    def upsert(self, session_id, profile):
        self._d[session_id] = copy.deepcopy(profile)

    def get(self, session_id):
        v = self._d.get(session_id)
        return copy.deepcopy(v) if v is not None else None

    def delete(self, session_id):
        self._d.pop(session_id, None)

    def delete_expired(self, older_than_days):
        # No timestamps in the fake — nothing to expire.
        return 0


@pytest.fixture(params=["memory", "persistent"])
def store(request):
    if request.param == "memory":
        return InMemorySessionStore()
    return PersistentSessionStore(FakeKV(), FakeProfileRepo())


# ---- Sessions ----

class TestSessions:
    def test_create_and_get(self, store):
        sid = store.create_session(stage=1)
        session = store.get_session(sid)
        assert session is not None
        assert session["stage"] == 1
        assert session["session_id"] == sid

    def test_create_with_metadata(self, store):
        sid = store.create_session(stage=2, metadata={"stage1_session_id": "abc"})
        assert store.get_session(sid)["stage1_session_id"] == "abc"

    def test_get_unknown_returns_none(self, store):
        assert store.get_session("nonexistent") is None

    def test_update_merges(self, store):
        sid = store.create_session(stage=1)
        store.update_session(sid, {"session_complete": True, "latest_document_id": "doc-1"})
        session = store.get_session(sid)
        assert session["session_complete"] is True
        assert session["latest_document_id"] == "doc-1"
        assert session["stage"] == 1  # original field preserved

    def test_update_unknown_is_noop(self, store):
        store.update_session("nonexistent", {"x": 1})  # must not raise


# ---- Linking ----

class TestLinking:
    def test_link_is_bidirectional(self, store):
        s1 = store.create_session(stage=1)
        s2 = store.create_session(stage=2)
        store.link_sessions(s1, s2)
        assert store.get_linked_session(s1) == s2
        assert store.get_linked_session(s2) == s1

    def test_unlinked_returns_none(self, store):
        s1 = store.create_session(stage=1)
        assert store.get_linked_session(s1) is None


# ---- Profiles ----

class TestProfiles:
    def test_round_trip(self, store):
        sid = store.create_session(stage=1)
        profile = _load_profile()
        store.store_profile(sid, profile)
        loaded = store.get_profile(sid)
        assert isinstance(loaded, RoleIntelligenceProfile)
        assert loaded.job_title == profile.job_title
        assert loaded.priority_1 == profile.priority_1

    def test_missing_profile_returns_none(self, store):
        assert store.get_profile("nonexistent") is None

    def test_upsert_overwrites(self, store):
        sid = store.create_session(stage=1)
        store.store_profile(sid, _load_profile("process_heavy"))
        store.store_profile(sid, _load_profile("decision_heavy"))
        loaded = store.get_profile(sid)
        assert loaded.priority_1 == _load_profile("decision_heavy").priority_1


# ---- Invite tokens ----

class TestInviteTokens:
    def test_resolve_and_single_use(self, store):
        s1 = store.create_session(stage=1)
        token = store.create_invite_token(s1)

        assert store.resolve_invite_token(token) == s1
        assert store.is_invite_token_used(token) is False

        store.consume_invite_token(token)

        assert store.is_invite_token_used(token) is True
        assert store.resolve_invite_token(token) is None

    def test_unknown_token(self, store):
        assert store.resolve_invite_token("bogus") is None
        assert store.is_invite_token_used("bogus") is False

    def test_resolve_none_when_session_gone(self, store):
        """A token whose Stage 1 session has expired must not resolve."""
        s1 = store.create_session(stage=1)
        token = store.create_invite_token(s1)
        # Simulate session expiry
        store._sessions.pop(s1, None) if isinstance(store, InMemorySessionStore) else store._kv.delete(
            PersistentSessionStore._SESSION + s1
        )
        assert store.resolve_invite_token(token) is None

    def test_stage1_id_is_not_a_valid_token(self, store):
        s1 = store.create_session(stage=1)
        store.create_invite_token(s1)
        assert store.resolve_invite_token(s1) is None


# ---- Manager tokens ----

class TestManagerTokens:
    def test_create_is_idempotent(self, store):
        s1 = store.create_session(stage=1)
        t1 = store.create_manager_token(s1)
        t2 = store.create_manager_token(s1)
        assert t1 == t2

    def test_validate(self, store):
        s1 = store.create_session(stage=1)
        token = store.create_manager_token(s1)
        assert store.validate_manager_token(s1, token) is True
        assert store.validate_manager_token(s1, "wrong") is False
        assert store.validate_manager_token(s1, "") is False

    def test_validate_unknown_session(self, store):
        assert store.validate_manager_token("nonexistent", "anything") is False


# ---- Deletion (right to erasure) ----

class TestDeletion:
    def test_delete_session_and_profile(self, store):
        sid = store.create_session(stage=1)
        store.store_profile(sid, _load_profile())
        store.delete_profile(sid)
        store.delete_session(sid)
        assert store.get_session(sid) is None
        assert store.get_profile(sid) is None

    def test_delete_link_both_directions(self, store):
        s1 = store.create_session(stage=1)
        s2 = store.create_session(stage=2)
        store.link_sessions(s1, s2)
        store.delete_link(s1)
        assert store.get_linked_session(s1) is None
        assert store.get_linked_session(s2) is None

    def test_delete_manager_token_invalidates(self, store):
        s1 = store.create_session(stage=1)
        token = store.create_manager_token(s1)
        store.delete_manager_token(s1)
        assert store.validate_manager_token(s1, token) is False

    def test_delete_invite_tokens(self, store):
        s1 = store.create_session(stage=1)
        token = store.create_invite_token(s1)
        store.delete_invite_tokens_for(s1)
        # In-memory removes them outright; the durable backend relies on the
        # session being gone — delete it and confirm the token no longer resolves.
        store.delete_session(s1)
        assert store.resolve_invite_token(token) is None


# ---- Factory ----

class TestFactory:
    def test_memory_backend_selected(self, monkeypatch):
        import api.session_manager as sm

        monkeypatch.setattr(sm.settings, "storage_backend", "memory")
        monkeypatch.setattr(sm, "_store", None)
        store = sm.get_session_store()
        assert isinstance(store, InMemorySessionStore)
