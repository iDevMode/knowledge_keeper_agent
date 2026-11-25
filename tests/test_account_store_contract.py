"""One contract, both account stores.

Same pattern as the session and document contracts: routes hold an
AccountStore without knowing which, so any divergence is a production-only bug.
The Postgres half skips without TEST_DATABASE_URL.
"""

import os
import threading
import time

import pytest

from api.accounts import InMemoryAccountStore, hash_login_token

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

requires_postgres = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set TEST_DATABASE_URL to run the Postgres half of the account contract",
)


def _make_postgres_store():
    from api.postgres_store import PostgresAccountStore

    store = PostgresAccountStore(conninfo=TEST_DATABASE_URL)
    with store._pool.connection() as conn:
        conn.execute("TRUNCATE kk_managers, kk_login_tokens, kk_handovers")
    return store


@pytest.fixture(
    params=[
        pytest.param("memory", id="memory"),
        pytest.param("postgres", id="postgres", marks=requires_postgres),
    ]
)
def store(request):
    s = InMemoryAccountStore() if request.param == "memory" else _make_postgres_store()
    s.backend = request.param
    yield s
    if hasattr(s, "close"):
        s.close()


class TestManagers:
    def test_the_same_email_is_the_same_manager(self, store):
        first = store.get_or_create_manager("sarah@acme.co.uk")
        assert store.get_or_create_manager("sarah@acme.co.uk") == first

    def test_email_matching_ignores_case_and_whitespace(self, store):
        first = store.get_or_create_manager("Sarah@Acme.co.uk")
        assert store.get_or_create_manager("  sarah@acme.co.uk ") == first
        assert store.get_manager(first)["email"] == "sarah@acme.co.uk"

    def test_different_emails_are_different_managers(self, store):
        assert store.get_or_create_manager("a@acme.co.uk") != store.get_or_create_manager("b@acme.co.uk")

    def test_unknown_manager(self, store):
        assert store.get_manager("nobody") is None


class TestLoginTokens:
    def test_a_token_signs_in_once(self, store):
        raw = store.create_login_token("sarah@acme.co.uk", 900)
        assert store.consume_login_token(raw) == "sarah@acme.co.uk"
        assert store.consume_login_token(raw) is None

    def test_an_expired_token_does_not_sign_in(self, store):
        raw = store.create_login_token("sarah@acme.co.uk", -1)
        assert store.consume_login_token(raw) is None

    def test_an_unknown_token_does_not_sign_in(self, store):
        assert store.consume_login_token("not-a-real-token-at-all") is None

    def test_requests_are_counted_per_address(self, store):
        before = time.time() - 1
        store.create_login_token("a@acme.co.uk", 900)
        store.create_login_token("a@acme.co.uk", 900)
        store.create_login_token("b@acme.co.uk", 900)
        assert store.count_login_tokens_since("A@acme.co.uk", before) == 2
        assert store.count_login_tokens_since("a@acme.co.uk", time.time() + 10) == 0

    def test_sweep_drops_expired_tokens(self, store):
        store.create_login_token("a@acme.co.uk", -1)
        live = store.create_login_token("a@acme.co.uk", 900)
        assert store.sweep_expired() == 1
        assert store.consume_login_token(live) == "a@acme.co.uk"

    def test_concurrent_clicks_on_one_link_sign_in_once(self, store):
        raw = store.create_login_token("sarah@acme.co.uk", 900)
        results = []
        barrier = threading.Barrier(6)

        def click():
            barrier.wait()
            results.append(store.consume_login_token(raw))

        threads = [threading.Thread(target=click) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count("sarah@acme.co.uk") == 1

    @requires_postgres
    def test_postgres_never_stores_the_raw_token(self):
        store = _make_postgres_store()
        try:
            raw = store.create_login_token("sarah@acme.co.uk", 900)
            with store._pool.connection() as conn:
                rows = conn.execute("SELECT token_hash FROM kk_login_tokens").fetchall()
            assert [r[0] for r in rows] == [hash_login_token(raw)]
            assert raw not in rows[0][0]
        finally:
            store.close()


class TestHandovers:
    def test_a_handover_belongs_to_its_manager(self, store):
        store.add_handover("s1", "m1")
        assert store.owner_of_handover("s1") == "m1"
        assert store.owner_of_handover("s2") is None

    def test_ownership_cannot_be_overwritten(self, store):
        store.add_handover("s1", "m1")
        store.add_handover("s1", "intruder")
        assert store.owner_of_handover("s1") == "m1"

    def test_list_is_newest_first_and_per_manager(self, store):
        store.add_handover("old", "m1")
        time.sleep(0.01)
        store.add_handover("other", "m2")
        time.sleep(0.01)
        store.add_handover("new", "m1")

        listed = store.list_handovers("m1")
        assert [h["stage1_session_id"] for h in listed] == ["new", "old"]
        assert "manager_id" not in listed[0]

    def test_title_is_kept(self, store):
        store.add_handover("s1", "m1")
        store.set_handover_title("s1", "Senior Operations Coordinator")
        assert store.list_handovers("m1")[0]["title"] == "Senior Operations Coordinator"
