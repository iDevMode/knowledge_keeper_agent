"""Retention and deletion, through the API. Policy: docs/data-retention.md.

Three problems this phase fixes, each pinned here:

* Packs vanished 72 hours after generation, taking the dashboard's download
  with them.
* Interview transcripts (LangGraph checkpoints) were never deleted at all.
* An employee who finished more than 72 hours after the manager started setup
  got no pack, because the manager's profile had already been swept.
"""

import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.test_accounts_api import (
    _await,
    _bearer,
    _classifier,
    _primary,
    _profile,
    _sign_in,
)

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


@pytest.fixture(autouse=True)
def reset_singletons():
    import api.document_store as doc_mod
    import api.routes as routes_mod
    import api.session_manager as sm_mod

    sm_mod._store = None
    routes_mod._registry = routes_mod.GraphRegistry()
    doc_mod.reset_document_store()
    yield


@pytest.fixture
def client():
    from api.routes import app

    return TestClient(app)


def _stores():
    from api.accounts import get_account_store
    from api.document_store import get_document_store
    from api.session_manager import get_session_store

    return get_session_store(), get_document_store(), get_account_store()


def _transcript_exists(session_id: str) -> bool:
    from api.routes import get_checkpointer

    return get_checkpointer().get_tuple({"configurable": {"thread_id": session_id}}) is not None


def _expire_sessions(*session_ids):
    sessions, _, _ = _stores()
    for session_id in session_ids:
        record = sessions._sessions.get(session_id)
        if record is not None:
            record["_expires_at"] = time.time() - 1


def _sweep():
    import api.routes as routes_mod

    return routes_mod.sweep_resources(force=True)


def _summary(client, account):
    listed = client.get("/api/handovers", headers=_bearer(account)).json()
    return listed[0] if listed else None


def _fake_docx(doc, path):
    with open(path, "wb") as f:
        f.write(b"HANDOVER PACK")
    return path


@pytest.fixture
def stubbed():
    """LLMs and the exporter stubbed; generation can be held with `gate`."""
    gate = threading.Event()
    gate.set()

    def generate(*a, **k):
        assert gate.wait(10), "generation gate never opened"
        return MagicMock(raw_markdown="# Handover")

    with patch("agents.stage2_employee_interview.nodes._get_primary_llm", side_effect=_primary), \
         patch("agents.stage2_employee_interview.nodes._get_classifier_llm", side_effect=_classifier), \
         patch("api.routes.generate_document", side_effect=generate), \
         patch("api.routes.parse_llm_output"), \
         patch("api.routes.generate_docx", side_effect=_fake_docx):
        yield gate


def _setup_handover(client, outbox):
    """Signed-in manager, Stage 1 confirmed, employee session created."""
    sessions, _, _ = _stores()
    account = _sign_in(client, outbox)
    stage1 = client.post("/api/sessions/stage1", headers=_bearer(account)).json()["session_id"]
    sessions.store_profile(stage1, _profile(recipient_emails=["hr@acme.co.uk"]))
    sessions.update_session(stage1, {"session_complete": True})
    manager = client.post(f"/api/handovers/{stage1}/access", headers=_bearer(account)).json()["token"]
    created = client.post(
        "/api/sessions/stage2", json={"stage1_session_id": stage1}, headers=_bearer(manager)
    ).json()
    return account, stage1, created["session_id"], created["employee_token"]


def _finish_interview(client, stage2, employee_token):
    for i in range(200):
        body = client.post(
            f"/api/sessions/{stage2}/message",
            json={"message": f"Answer {i}."},
            headers=_bearer(employee_token),
        ).json()
        if body.get("session_complete"):
            return
    raise AssertionError("Stage 2 never completed")


def _ready_handover(client, outbox):
    account, stage1, stage2, employee = _setup_handover(client, outbox)
    _finish_interview(client, stage2, employee)
    _await(lambda: (s := _summary(client, account)) and s["status"] == "ready" and s.get("delivery"))
    return account, stage1, stage2


# ---- Transcripts ----

class TestTranscriptsAreDeleted:
    def test_an_expired_sessions_transcript_goes_with_it(self, client, outbox):
        """They used to stay in the checkpoint tables forever."""
        account = _sign_in(client, outbox)
        stage1 = client.post("/api/sessions/stage1", headers=_bearer(account)).json()["session_id"]
        assert _transcript_exists(stage1)

        _expire_sessions(stage1)
        result = _sweep()

        assert result["sessions"] == 1
        assert result["transcripts"] >= 1
        assert not _transcript_exists(stage1)

    def test_a_live_sessions_transcript_is_kept(self, client, outbox):
        account = _sign_in(client, outbox)
        stage1 = client.post("/api/sessions/stage1", headers=_bearer(account)).json()["session_id"]
        _sweep()
        assert _transcript_exists(stage1)


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="set TEST_DATABASE_URL to run the Postgres orphan purge test")
class TestOrphanedTranscriptsInPostgres:
    def test_transcripts_without_a_session_are_found_and_deleted(self):
        import uuid

        from langgraph.checkpoint.postgres import PostgresSaver
        from langgraph.graph import END, START, StateGraph
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        from typing_extensions import TypedDict

        from api.postgres_store import PostgresSessionStore, orphaned_transcript_ids, setup_checkpointer
        from api.retention import delete_transcripts

        sessions = PostgresSessionStore(conninfo=TEST_DATABASE_URL)
        pool = ConnectionPool(
            TEST_DATABASE_URL, min_size=1, max_size=2, open=True,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )
        try:
            saver = PostgresSaver(pool)
            setup_checkpointer(saver, pool)

            class S(TypedDict):
                n: int

            builder = StateGraph(S)
            builder.add_node("step", lambda s: {"n": s["n"] + 1})
            builder.add_edge(START, "step")
            builder.add_edge("step", END)
            graph = builder.compile(checkpointer=saver)

            live = sessions.create_session(stage=1)
            orphan = str(uuid.uuid4())  # a transcript whose session was swept long ago
            for thread in (live, orphan):
                graph.invoke({"n": 0}, {"configurable": {"thread_id": thread}})

            found = orphaned_transcript_ids(sessions._pool)
            assert orphan in found
            assert live not in found

            delete_transcripts(saver, [orphan])
            assert saver.get_tuple({"configurable": {"thread_id": orphan}}) is None
            assert saver.get_tuple({"configurable": {"thread_id": live}}) is not None
            assert orphan not in orphaned_transcript_ids(sessions._pool)
        finally:
            pool.close()
            sessions.close()


# ---- The manager's profile survives a slow employee ----

class TestStage1LivesAsLongAsStage2:
    def test_creating_the_employee_session_extends_the_managers(self, client, outbox, stubbed):
        sessions, _, _ = _stores()
        account = _sign_in(client, outbox)
        stage1 = client.post("/api/sessions/stage1", headers=_bearer(account)).json()["session_id"]
        sessions.store_profile(stage1, _profile())
        sessions.update_session(stage1, {"session_complete": True})
        # Setup finished 70 hours ago; on the old rule Stage 1 had 2 hours left.
        sessions._sessions[stage1]["_created_at"] = time.time() - 70 * 3600
        manager = client.post(f"/api/handovers/{stage1}/access", headers=_bearer(account)).json()["token"]

        client.post("/api/sessions/stage2", json={"stage1_session_id": stage1}, headers=_bearer(manager))

        ttl = 72 * 3600
        assert sessions._sessions[stage1]["_expires_at"] > time.time() + ttl - 60

    def test_a_late_finish_still_produces_a_pack(self, client, outbox, stubbed):
        """The end-to-end failure: profile swept before the employee finished."""
        sessions, _, _ = _stores()
        account, stage1, stage2, employee = _setup_handover(client, outbox)
        # Pretend setup started 70h ago; the extension must carry it through.
        sessions._sessions[stage1]["_created_at"] = time.time() - 70 * 3600
        sessions._sessions[stage1].pop("_expires_at", None)
        sessions.extend_session(stage1, time.time() + 72 * 3600)

        _finish_interview(client, stage2, employee)
        ready = _await(lambda: (s := _summary(client, account)) and s["status"] == "ready" and s)
        assert ready["document_id"]


# ---- Packs are kept for the retention period ----

class TestPacksOutliveTheInterview:
    def test_the_handover_record_is_kept_as_long_as_its_pack(self, client, outbox, stubbed):
        _, documents, accounts = _stores()
        account, stage1, stage2 = _ready_handover(client, outbox)

        pack = documents.get_job(documents.completed_document_for_session(stage2))
        record = accounts.get_handover(stage1)
        assert record["retain_until"] >= pack["expires_at"] - 1
        assert abs(pack["expires_at"] - (time.time() + 365 * 86400)) < 120
        assert _summary(client, account)["kept_until"] >= pack["expires_at"] - 1

    def test_after_the_interview_data_goes_the_pack_is_archived_not_lost(self, client, outbox, stubbed):
        account, stage1, stage2 = _ready_handover(client, outbox)

        _expire_sessions(stage1, stage2)
        _sweep()

        summary = _summary(client, account)
        assert summary["status"] == "archived"
        assert summary["interview_available"] is False
        assert summary["title"] == "Senior Operations Coordinator"
        assert not _transcript_exists(stage1) and not _transcript_exists(stage2)

        res = client.get(f"/api/handovers/{stage1}/document", headers=_bearer(account))
        assert res.status_code == 200
        assert res.content == b"HANDOVER PACK"
        assert 'filename="Handover - Senior Operations Coordinator.docx"' in res.headers["content-disposition"]

        # The interview itself cannot be reopened any more.
        assert client.post(f"/api/handovers/{stage1}/access", headers=_bearer(account)).status_code == 410

    def test_at_the_end_of_retention_everything_is_deleted(self, client, outbox, stubbed):
        _, documents, accounts = _stores()
        account, stage1, stage2 = _ready_handover(client, outbox)
        _expire_sessions(stage1, stage2)
        _sweep()

        pack_id = documents.completed_document_for_session(stage2)
        documents._documents[pack_id]["expires_at"] = time.time() - 1
        accounts._handovers[stage1]["retain_until"] = time.time() - 1
        result = _sweep()

        assert result["handovers"] == 1
        assert documents.get_job(pack_id) is None
        assert accounts.get_handover(stage1) is None
        assert _summary(client, account) is None

    def test_a_failed_regeneration_does_not_hide_the_pack(self, client, outbox, stubbed):
        _, documents, _ = _stores()
        account, stage1, stage2 = _ready_handover(client, outbox)
        manager = client.post(f"/api/handovers/{stage1}/access", headers=_bearer(account)).json()["token"]

        with patch("api.routes.generate_document", side_effect=RuntimeError("model down")):
            new_id = client.post(
                f"/api/sessions/{stage2}/generate", json={"format": "docx"}, headers=_bearer(manager)
            ).json()["document_id"]
            _await(lambda: documents.get_job(new_id)["status"] == "failed")

        summary = _summary(client, account)
        assert summary["status"] == "ready"
        assert summary["document_id"] != new_id
        assert client.get(f"/api/handovers/{stage1}/document", headers=_bearer(account)).content == b"HANDOVER PACK"


class TestAbandonedHandovers:
    def test_shown_as_expired_then_removed(self, client, outbox):
        _, _, accounts = _stores()
        account = _sign_in(client, outbox)
        stage1 = client.post("/api/sessions/stage1", headers=_bearer(account)).json()["session_id"]

        _expire_sessions(stage1)
        _sweep()
        assert _summary(client, account)["status"] == "expired"

        accounts._handovers[stage1]["retain_until"] = time.time() - 1
        _sweep()
        assert _summary(client, account) is None

    def test_an_expired_handover_stays_visible_for_the_grace_period(self, client, outbox):
        _, _, accounts = _stores()
        account = _sign_in(client, outbox)
        stage1 = client.post("/api/sessions/stage1", headers=_bearer(account)).json()["session_id"]
        kept_until = accounts.get_handover(stage1)["retain_until"]
        assert kept_until > time.time() + (72 * 3600) + 29 * 86400


# ---- Delete now ----

class TestDeleteHandover:
    def test_deletes_everything_and_the_employee_link_stops_working(self, client, outbox, stubbed):
        sessions, documents, accounts = _stores()
        account, stage1, stage2, employee = _setup_handover(client, outbox)
        client.post(
            f"/api/sessions/{stage2}/message", json={"message": "First answer."},
            headers=_bearer(employee),
        )
        assert _transcript_exists(stage2)

        res = client.delete(f"/api/handovers/{stage1}", headers=_bearer(account))
        assert res.status_code == 204

        assert _summary(client, account) is None
        assert sessions.get_session(stage1) is None and sessions.get_session(stage2) is None
        assert sessions.get_profile(stage1) is None
        assert not _transcript_exists(stage1) and not _transcript_exists(stage2)
        assert accounts.get_handover(stage1) is None
        res = client.post(
            f"/api/sessions/{stage2}/message", json={"message": "Still here?"},
            headers=_bearer(employee),
        )
        assert res.status_code == 404

    def test_deletes_a_finished_pack(self, client, outbox, stubbed):
        _, documents, _ = _stores()
        account, stage1, stage2 = _ready_handover(client, outbox)
        pack_id = documents.completed_document_for_session(stage2)

        assert client.delete(f"/api/handovers/{stage1}", headers=_bearer(account)).status_code == 204
        assert documents.get_job(pack_id) is None
        assert client.get(f"/api/handovers/{stage1}/document", headers=_bearer(account)).status_code == 404

    def test_deletes_an_archived_pack(self, client, outbox, stubbed):
        _, documents, _ = _stores()
        account, stage1, stage2 = _ready_handover(client, outbox)
        _expire_sessions(stage1, stage2)
        _sweep()
        pack_id = documents.completed_document_for_session(stage2)

        assert client.delete(f"/api/handovers/{stage1}", headers=_bearer(account)).status_code == 204
        assert documents.get_job(pack_id) is None

    def test_another_managers_handover_is_not_found(self, client, outbox, stubbed):
        sarah, stage1, _, _ = _setup_handover(client, outbox)
        tom = _sign_in(client, outbox, "tom@acme.co.uk")
        assert client.delete(f"/api/handovers/{stage1}", headers=_bearer(tom)).status_code == 404
        assert _summary(client, sarah) is not None

    def test_unknown_handover_is_not_found(self, client, outbox):
        account = _sign_in(client, outbox)
        assert client.delete("/api/handovers/nope", headers=_bearer(account)).status_code == 404

    @pytest.mark.parametrize("scope", ["manager", "employee"])
    def test_session_tokens_cannot_delete(self, client, outbox, stubbed, scope):
        from api.auth import issue_token

        _, stage1, _, _ = _setup_handover(client, outbox)
        res = client.delete(f"/api/handovers/{stage1}", headers=_bearer(issue_token(stage1, scope)))
        assert res.status_code == 401

    def test_a_pack_generating_when_deleted_is_never_emailed(self, client, outbox, stubbed):
        """Generation runs in a thread. If the handover is deleted mid-way, the
        pack must not be stored, emailed to recipients, or announced."""
        _, documents, _ = _stores()
        account, stage1, stage2, employee = _setup_handover(client, outbox)
        stubbed.clear()  # hold generation
        _finish_interview(client, stage2, employee)
        _await(lambda: documents.document_for_session(stage2))

        assert client.delete(f"/api/handovers/{stage1}", headers=_bearer(account)).status_code == 204
        sent_before = len(outbox.messages)
        stubbed.set()  # let generation finish
        time.sleep(0.5)

        assert documents.completed_document_for_session(stage2) is None
        new = outbox.messages[sent_before:]
        assert new == [], [m.subject for m in new]


# ---- Download ----

class TestArchivedDownload:
    def test_needs_the_owners_account_token(self, client, outbox, stubbed):
        from api.auth import issue_token

        account, stage1, _ = _ready_handover(client, outbox)
        tom = _sign_in(client, outbox, "tom@acme.co.uk")
        assert client.get(f"/api/handovers/{stage1}/document", headers=_bearer(tom)).status_code == 404
        assert client.get(f"/api/handovers/{stage1}/document").status_code == 401
        manager = issue_token(stage1, "manager")
        assert client.get(f"/api/handovers/{stage1}/document", headers=_bearer(manager)).status_code == 401

    def test_is_not_accepted_with_a_token_in_the_url(self, client, outbox, stubbed):
        """Account tokens last 30 days; they never belong in a URL."""
        account, stage1, _ = _ready_handover(client, outbox)
        res = client.get(f"/api/handovers/{stage1}/document?token={account}")
        assert res.status_code == 401

    def test_before_a_pack_exists(self, client, outbox, stubbed):
        account, stage1, _, _ = _setup_handover(client, outbox)
        assert client.get(f"/api/handovers/{stage1}/document", headers=_bearer(account)).status_code == 404


class TestReadyEmail:
    def test_says_how_long_the_pack_is_kept(self, client, outbox, stubbed):
        _ready_handover(client, outbox)
        notice = _await(lambda: next(
            (m for m in outbox.messages if m.subject.startswith("Handover pack ready")), None
        ))
        year = time.strftime("%Y", time.gmtime(time.time() + 365 * 86400))
        assert "deleted automatically" in notice.text
        assert year in notice.text


class TestSettings:
    def test_retention_must_be_at_least_a_day(self, capsys):
        from config.settings import Settings

        with pytest.raises(SystemExit):
            Settings(
                _env_file=None, anthropic_api_key="x", api_secret_key="x",
                database_url="postgresql://unused", environment="production",
                allowed_origins="https://example.com", public_base_url="https://kk.example.com",
                sendgrid_api_key="SG.x", email_from_address="a@b.co",
                document_retention_days=0,
            ).validate_for_production()
        assert "DOCUMENT_RETENTION_DAYS" in capsys.readouterr().err
