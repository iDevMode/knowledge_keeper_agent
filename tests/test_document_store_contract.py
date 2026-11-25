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


def _make_postgres_store(ttl_hours, retention_days=None):
    from api.postgres_store import PostgresDocumentStore

    store = PostgresDocumentStore(
        conninfo=TEST_DATABASE_URL, ttl_hours=ttl_hours, retention_days=retention_days
    )
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

    def make(ttl_hours=72, retention_days=None):
        store = (
            InMemoryDocumentStore(ttl_hours=ttl_hours, retention_days=retention_days)
            if request.param == "memory"
            else _make_postgres_store(ttl_hours, retention_days)
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


class TestDelivery:
    def test_a_new_job_has_no_delivery(self, store):
        store.start_job("doc-1", "session-1")
        assert store.get_job("doc-1")["delivery"] is None

    def test_a_sent_delivery_round_trips(self, store):
        store.start_job("doc-1", "session-1")
        store.complete_job("doc-1", "pack.docx", DOCX, PACK)
        store.record_delivery("doc-1", "sent", ["hr@acme.co.uk", "new@acme.co.uk"], None)

        assert store.get_job("doc-1")["delivery"] == {
            "status": "sent",
            "recipients": ["hr@acme.co.uk", "new@acme.co.uk"],
            "error": None,
        }

    def test_a_skipped_delivery_keeps_its_reason(self, store):
        store.start_job("doc-1", "session-1")
        store.record_delivery("doc-1", "skipped", [], "No recipient email addresses")

        assert store.get_job("doc-1")["delivery"] == {
            "status": "skipped",
            "recipients": [],
            "error": "No recipient email addresses",
        }

    def test_recording_delivery_leaves_the_document_intact(self, store):
        store.start_job("doc-1", "session-1")
        store.complete_job("doc-1", "pack.docx", DOCX, PACK)
        store.record_delivery("doc-1", "failed", ["hr@acme.co.uk"], "SendGrid returned 500")

        assert store.get_job("doc-1")["status"] == COMPLETE
        assert store.get_content("doc-1") == ("pack.docx", DOCX, PACK)

    def test_delivery_for_an_unknown_document_is_ignored(self, store):
        store.record_delivery("nope", "sent", ["hr@acme.co.uk"], None)
        assert store.get_job("nope") is None


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
    """Retention: a finished pack is kept for the retention period from
    completion; an unfinished or failed job only as long as an interview."""

    TINY_TTL_HOURS = 1 / 3600  # one second
    TINY_RETENTION_DAYS = 1 / 86400  # one second

    def test_a_finished_pack_outlives_the_interview_lifetime(self, store_factory):
        """It used to be swept with the interview after 72 hours."""
        store = store_factory(ttl_hours=self.TINY_TTL_HOURS, retention_days=365)
        store.start_job("pack", "session-1")
        store.complete_job("pack", "pack.docx", DOCX, PACK)
        time.sleep(1.2)

        assert store.sweep_expired() == 0
        assert store.get_content("pack") == ("pack.docx", DOCX, PACK)

    def test_a_finished_pack_reports_when_it_will_be_deleted(self, store_factory):
        store = store_factory(retention_days=365)
        store.start_job("pack", "session-1")
        assert store.get_job("pack")["expires_at"] is None
        store.complete_job("pack", "pack.docx", DOCX, PACK)

        expires_at = store.get_job("pack")["expires_at"]
        assert abs(expires_at - (time.time() + 365 * 86400)) < 60

    def test_a_finished_pack_is_deleted_after_the_retention_period(self, store_factory):
        store = store_factory(retention_days=self.TINY_RETENTION_DAYS)
        store.start_job("doomed", "session-1")
        store.complete_job("doomed", "pack.docx", DOCX, PACK)
        time.sleep(1.2)

        assert store.sweep_expired() == 1
        assert store.get_job("doomed") is None
        assert store.get_content("doomed") is None

    def test_unfinished_jobs_expire_with_the_interview(self, store_factory):
        store = store_factory(ttl_hours=self.TINY_TTL_HOURS, retention_days=365)
        store.start_job("stuck", "session-1")
        store.start_job("broken", "session-2")
        store.fail_job("broken", "Stage 3 failed")
        time.sleep(1.2)

        assert store.sweep_expired() == 2

    def test_fresh_unfinished_jobs_survive_the_sweep(self, store_factory):
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


@requires_postgres
class TestPacksFromBeforeRetention:
    def test_get_the_retention_period_from_when_they_were_made(self):
        """Without this they would be swept on the old 72-hour rule."""
        import psycopg

        made = time.time() - 3600
        _make_postgres_store(72)  # clean tables
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO kk_documents (document_id, session_id, status, filename, "
                "media_type, content, created_at) VALUES ('legacy', 's1', 'complete', "
                "'p.docx', 'x', 'PACK', %s), ('stuck', 's2', 'generating', NULL, NULL, NULL, %s)",
                (made, made),
            )
        from api.postgres_store import PostgresDocumentStore

        store = PostgresDocumentStore(conninfo=TEST_DATABASE_URL, retention_days=365)
        try:
            assert abs(store.get_job("legacy")["expires_at"] - (made + 365 * 86400)) < 1
            assert store.get_job("stuck")["expires_at"] is None
        finally:
            store.close()


class TestSupersede:
    def test_a_finished_pack_replaces_earlier_ones(self, store):
        store.start_job("v1", "session-1")
        store.complete_job("v1", "v1.docx", DOCX, PACK)
        time.sleep(0.01)
        store.start_job("v2", "session-1")
        # Until v2 finishes, v1 is still there to download.
        assert store.get_content("v1") is not None

        store.complete_job("v2", "v2.pdf", "application/pdf", b"PDF")

        assert store.get_job("v1") is None
        assert store.document_for_session("session-1") == "v2"
        assert store.get_content("v2") == ("v2.pdf", "application/pdf", b"PDF")

    def test_a_failed_regeneration_keeps_the_previous_pack(self, store):
        store.start_job("v1", "session-1")
        store.complete_job("v1", "v1.docx", DOCX, PACK)
        time.sleep(0.01)
        store.start_job("v2", "session-1")
        store.fail_job("v2", "Stage 3 failed")

        assert store.get_content("v1") == ("v1.docx", DOCX, PACK)

    def test_other_sessions_are_untouched(self, store):
        store.start_job("mine", "session-1")
        store.start_job("theirs", "session-2")
        store.complete_job("theirs", "t.docx", DOCX, PACK)
        store.complete_job("mine", "m.docx", DOCX, PACK)

        assert store.get_content("theirs") is not None


class TestCompletedDocument:
    def test_none_until_a_pack_finishes(self, store):
        store.start_job("v1", "session-1")
        assert store.completed_document_for_session("session-1") is None
        store.complete_job("v1", "v1.docx", DOCX, PACK)
        assert store.completed_document_for_session("session-1") == "v1"

    def test_a_running_or_failed_regeneration_does_not_hide_the_pack(self, store):
        store.start_job("v1", "session-1")
        store.complete_job("v1", "v1.docx", DOCX, PACK)
        time.sleep(0.01)
        store.start_job("v2", "session-1")
        assert store.document_for_session("session-1") == "v2"
        assert store.completed_document_for_session("session-1") == "v1"
        store.fail_job("v2", "boom")
        assert store.completed_document_for_session("session-1") == "v1"


class TestDeleteForSession:
    def test_deletes_every_document_and_error_for_the_session(self, store):
        store.start_job("v1", "session-1")
        store.complete_job("v1", "v1.docx", DOCX, PACK)
        time.sleep(0.01)
        store.start_job("v2", "session-1")
        store.set_generation_error("session-1", "boom")

        assert store.delete_for_session("session-1") == 2
        assert store.get_job("v1") is None
        assert store.get_job("v2") is None
        assert store.document_for_session("session-1") is None
        assert store.get_generation_error("session-1") is None

    def test_leaves_other_sessions_alone(self, store):
        store.start_job("mine", "session-1")
        store.start_job("theirs", "session-2")
        store.delete_for_session("session-1")
        assert store.get_job("theirs") is not None

    def test_unknown_session_deletes_nothing(self, store):
        assert store.delete_for_session("nobody") == 0


class TestProtocolConformance:
    def test_the_store_satisfies_the_protocol(self, store):
        from api.document_store import DocumentStore

        assert isinstance(store, DocumentStore)
