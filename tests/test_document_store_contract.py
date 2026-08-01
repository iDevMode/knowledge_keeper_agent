"""One contract, both document stores (review finding H3).

Every test runs against InMemoryDocumentStore AND PostgresDocumentStore, for the
same reason as the session-store contract: routes.py holds a DocumentStore and
does not know which it has, so any divergence becomes a production-only bug.

The Postgres parameter skips without TEST_DATABASE_URL; the in-memory parameter
always runs, so the logic is covered either way.
"""

import os
import time

import pytest

from api.document_store import COMPLETE, FAILED, GENERATING, InMemoryDocumentStore

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

requires_postgres = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set TEST_DATABASE_URL to run the Postgres half of the document contract",
)

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PACK = b"RISK SUMMARY: sole owner of the reconciliation process"


def _make_postgres_store(ttl_hours):
    from api.postgres_store import PostgresDocumentStore

    store = PostgresDocumentStore(conninfo=TEST_DATABASE_URL, ttl_hours=ttl_hours)
    with store._pool.connection() as conn:
        conn.execute("TRUNCATE kk_documents, kk_generation_errors")
    return store


@pytest.fixture(
    params=[
        pytest.param("memory", id="memory"),
        pytest.param("postgres", id="postgres", marks=requires_postgres),
    ]
)
def store_factory(request):
    created = []

    def make(ttl_hours=72):
        store = (
            InMemoryDocumentStore(ttl_hours=ttl_hours)
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


class TestJobLifecycle:
    def test_a_started_job_is_generating(self, store):
        store.start_job("doc-1", "session-1")
        assert store.get_job("doc-1")["status"] == GENERATING

    def test_a_generating_job_offers_no_download_url(self, store):
        store.start_job("doc-1", "session-1")
        assert store.get_job("doc-1")["download_url"] is None

    def test_completion_makes_the_content_available(self, store):
        store.start_job("doc-1", "session-1")
        store.complete_job("doc-1", "pack.docx", DOCX, PACK)

        job = store.get_job("doc-1")
        assert job["status"] == COMPLETE
        assert job["download_url"] == "/api/documents/doc-1"
        assert job["error"] is None
        assert store.get_content("doc-1") == ("pack.docx", DOCX, PACK)

    def test_failure_records_the_reason_and_no_content(self, store):
        store.start_job("doc-1", "session-1")
        store.fail_job("doc-1", "Stage 1 profile not found")

        job = store.get_job("doc-1")
        assert job["status"] == FAILED
        assert job["error"] == "Stage 1 profile not found"
        assert job["download_url"] is None
        assert store.get_content("doc-1") is None

    def test_unknown_document_has_no_job_or_content(self, store):
        assert store.get_job("nope") is None
        assert store.get_content("nope") is None

    def test_content_round_trips_bytes_exactly(self, store):
        blob = bytes(range(256)) * 40  # binary, not text
        store.start_job("doc-1", "session-1")
        store.complete_job("doc-1", "pack.pdf", "application/pdf", blob)

        assert store.get_content("doc-1")[2] == blob


class TestOwnership:
    def test_a_document_knows_its_session(self, store):
        store.start_job("doc-1", "session-1")
        assert store.owner_of("doc-1") == "session-1"

    def test_unknown_document_has_no_owner(self, store):
        assert store.owner_of("nope") is None

    def test_a_session_points_at_its_document(self, store):
        store.start_job("doc-1", "session-1")
        assert store.document_for_session("session-1") == "doc-1"

    def test_regeneration_supersedes_the_previous_document(self, store):
        store.start_job("doc-1", "session-1")
        time.sleep(0.01)  # created_at ordering
        store.start_job("doc-2", "session-1")

        assert store.document_for_session("session-1") == "doc-2"
        # The old one is still owned and downloadable until it is swept.
        assert store.owner_of("doc-1") == "session-1"

    def test_a_session_with_no_document(self, store):
        assert store.document_for_session("session-never-used") is None


class TestGenerationErrors:
    def test_an_error_is_recorded_and_read_back(self, store):
        store.set_generation_error("session-1", "Stage 1 profile not found")
        assert store.get_generation_error("session-1") == "Stage 1 profile not found"

    def test_no_error_by_default(self, store):
        assert store.get_generation_error("session-1") is None

    def test_an_error_can_be_cleared(self, store):
        store.set_generation_error("session-1", "boom")
        store.clear_generation_error("session-1")
        assert store.get_generation_error("session-1") is None

    def test_starting_a_job_clears_a_previous_error(self, store):
        """A successful retry must not leave the manager staring at a stale error."""
        store.set_generation_error("session-1", "an earlier failure")
        store.start_job("doc-1", "session-1")
        assert store.get_generation_error("session-1") is None

    def test_setting_twice_replaces(self, store):
        store.set_generation_error("session-1", "first")
        store.set_generation_error("session-1", "second")
        assert store.get_generation_error("session-1") == "second"


class TestExpiry:
    TINY_TTL_HOURS = 1 / 3600  # one second

    def test_expired_documents_are_swept(self, store_factory):
        store = store_factory(ttl_hours=self.TINY_TTL_HOURS)
        store.start_job("doomed", "session-1")
        store.complete_job("doomed", "pack.docx", DOCX, PACK)
        time.sleep(1.2)

        assert store.sweep_expired() == 1
        assert store.get_job("doomed") is None
        assert store.get_content("doomed") is None

    def test_fresh_documents_survive_the_sweep(self, store_factory):
        store = store_factory(ttl_hours=self.TINY_TTL_HOURS)
        store.start_job("old", "session-1")
        time.sleep(1.2)
        store.start_job("new", "session-2")

        assert store.sweep_expired() == 1
        assert store.get_job("new") is not None

    def test_sweeping_clears_the_session_pointer(self, store_factory):
        store = store_factory(ttl_hours=self.TINY_TTL_HOURS)
        store.start_job("doc-1", "session-1")
        time.sleep(1.2)
        store.sweep_expired()

        assert store.document_for_session("session-1") is None

    def test_sweep_with_nothing_expired_returns_zero(self, store):
        store.start_job("doc-1", "session-1")
        assert store.sweep_expired() == 0


class TestProtocolConformance:
    def test_the_store_satisfies_the_protocol(self, store):
        from api.document_store import DocumentStore

        assert isinstance(store, DocumentStore)
