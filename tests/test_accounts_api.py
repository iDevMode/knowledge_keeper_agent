"""Manager sign-in and the handover dashboard, through the API.

Before this, anyone could start a handover anonymously and it belonged to the
browser tab holding the manager token: close it before the employee finished
and the pack could not be reached again.
"""

import json
import re
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.auth import ACCOUNT, EMPLOYEE, MANAGER, issue_token
from models.role_intelligence_profile import RoleIntelligenceProfile
from tests.api_client import AuthedTestClient

FIXTURE = "tests/fixtures/sample_role_profiles.json"
NO_FOLLOWUP = json.dumps({"needs_followup": False, "reason": "clear", "suggested_followup": ""})


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


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _profile(**overrides):
    with open(FIXTURE) as f:
        data = json.load(f)["process_heavy"]
    data.update(overrides)
    return RoleIntelligenceProfile.model_validate(data)


def _link_token(message) -> str:
    match = re.search(r"/auth/verify#token=([A-Za-z0-9_\-]+)", message.text)
    assert match, message.text
    return match.group(1)


def _sign_in(client, outbox, email="sarah@acme.co.uk") -> str:
    assert client.post("/api/auth/login", json={"email": email}).status_code == 202
    token = _link_token(outbox.messages[-1])
    res = client.post("/api/auth/verify", json={"token": token})
    assert res.status_code == 200, res.text
    return res.json()["account_token"]


# ---- Sign-in ----

class TestMagicLink:
    def test_login_emails_a_link_with_the_token_in_the_fragment(self, client, outbox):
        res = client.post("/api/auth/login", json={"email": "Sarah@Acme.co.uk"})
        assert res.status_code == 202
        message = outbox.messages[-1]
        assert message.to == ["sarah@acme.co.uk"]
        # Fragment, not query string: never sent to a server or in a Referer.
        assert "/auth/verify#token=" in message.text
        assert "?token=" not in message.text

    def test_the_link_signs_in_and_identifies_the_manager(self, client, outbox):
        account = _sign_in(client, outbox)
        res = client.get("/api/account/me", headers=_bearer(account))
        assert res.json() == {"email": "sarah@acme.co.uk"}

    def test_the_link_works_only_once(self, client, outbox):
        client.post("/api/auth/login", json={"email": "sarah@acme.co.uk"})
        token = _link_token(outbox.messages[-1])
        assert client.post("/api/auth/verify", json={"token": token}).status_code == 200
        res = client.post("/api/auth/verify", json={"token": token})
        assert res.status_code == 400
        assert "already been used" in res.json()["detail"]

    def test_an_expired_link_is_refused(self, client, outbox):
        with patch("api.account_routes.settings") as s:
            s.login_link_ttl_minutes = -1
            s.login_request_window_minutes = 15
            s.login_requests_per_window = 5
            s.public_base_url = "http://localhost:3000"
            s.environment = "test"
            s.sendgrid_api_key = ""
            client.post("/api/auth/login", json={"email": "sarah@acme.co.uk"})
        token = _link_token(outbox.messages[-1])
        assert client.post("/api/auth/verify", json={"token": token}).status_code == 400

    def test_signing_in_twice_is_the_same_account(self, client, outbox):
        first = _sign_in(client, outbox)
        second = _sign_in(client, outbox)
        client.post("/api/sessions/stage1", headers=_bearer(first))
        listed = client.get("/api/handovers", headers=_bearer(second)).json()
        assert len(listed) == 1

    def test_an_invalid_address_is_rejected(self, client, outbox):
        res = client.post("/api/auth/login", json={"email": "not an email"})
        assert res.status_code == 422
        assert outbox.messages == []

    def test_requests_are_rate_limited_without_saying_so(self, client, outbox):
        responses = [
            client.post("/api/auth/login", json={"email": "sarah@acme.co.uk"}) for _ in range(8)
        ]
        assert {r.status_code for r in responses} == {202}
        assert len({r.json()["message"] for r in responses}) == 1
        assert len(outbox.messages) == 5

    def test_a_mail_failure_is_reported(self, client, outbox):
        from api.mailer import MailerError

        outbox.fail_with = MailerError("SendGrid returned 500")
        res = client.post("/api/auth/login", json={"email": "sarah@acme.co.uk"})
        assert res.status_code == 503


# ---- Token boundaries ----

class TestAccountTokenBoundaries:
    def test_starting_a_handover_requires_sign_in(self, client):
        assert client.post("/api/sessions/stage1").status_code == 401

    def test_a_manager_session_token_cannot_start_a_handover(self, client):
        res = client.post("/api/sessions/stage1", headers=_bearer(issue_token("s1", MANAGER)))
        assert res.status_code == 401

    def test_an_account_token_does_not_open_a_session_directly(self, client, outbox):
        account = _sign_in(client, outbox)
        session_id = client.post("/api/sessions/stage1", headers=_bearer(account)).json()["session_id"]
        res = client.get(f"/api/sessions/{session_id}/status", headers=_bearer(account))
        assert res.status_code == 403

    @pytest.mark.parametrize("scope", [MANAGER, EMPLOYEE])
    def test_session_tokens_cannot_list_handovers(self, client, scope):
        res = client.get("/api/handovers", headers=_bearer(issue_token("s1", scope)))
        assert res.status_code == 401

    def test_listing_requires_a_token(self, client):
        assert client.get("/api/handovers").status_code == 401


# ---- Dashboard ----

class TestDashboard:
    def test_a_started_handover_is_listed_as_in_setup(self, client, outbox):
        account = _sign_in(client, outbox)
        session_id = client.post("/api/sessions/stage1", headers=_bearer(account)).json()["session_id"]

        listed = client.get("/api/handovers", headers=_bearer(account)).json()
        assert [h["stage1_session_id"] for h in listed] == [session_id]
        assert listed[0]["status"] == "setup"
        assert listed[0]["status_label"]

    def test_managers_only_see_their_own(self, client, outbox):
        sarah = _sign_in(client, outbox, "sarah@acme.co.uk")
        tom = _sign_in(client, outbox, "tom@acme.co.uk")
        client.post("/api/sessions/stage1", headers=_bearer(sarah))

        assert client.get("/api/handovers", headers=_bearer(tom)).json() == []

    def test_access_mints_a_working_manager_token(self, client, outbox):
        account = _sign_in(client, outbox)
        session_id = client.post("/api/sessions/stage1", headers=_bearer(account)).json()["session_id"]

        res = client.post(f"/api/handovers/{session_id}/access", headers=_bearer(account))
        assert res.status_code == 200
        manager = res.json()["token"]
        status = client.get(f"/api/sessions/{session_id}/status", headers=_bearer(manager))
        assert status.status_code == 200
        assert status.json()["last_agent_message"]

    def test_another_managers_handover_is_not_found(self, client, outbox):
        sarah = _sign_in(client, outbox, "sarah@acme.co.uk")
        tom = _sign_in(client, outbox, "tom@acme.co.uk")
        session_id = client.post("/api/sessions/stage1", headers=_bearer(sarah)).json()["session_id"]

        res = client.post(f"/api/handovers/{session_id}/access", headers=_bearer(tom))
        assert res.status_code == 404

    def test_an_unknown_handover_is_not_found(self, client, outbox):
        account = _sign_in(client, outbox)
        res = client.post("/api/handovers/does-not-exist/access", headers=_bearer(account))
        assert res.status_code == 404

    def test_an_expired_handover_still_lists_by_title(self, client, outbox):
        from api.accounts import get_account_store
        from api.session_manager import get_session_store

        account = _sign_in(client, outbox)
        session_id = client.post("/api/sessions/stage1", headers=_bearer(account)).json()["session_id"]
        get_account_store().set_handover_title(session_id, "Finance Manager")
        get_session_store()._sessions.pop(session_id)  # swept

        listed = client.get("/api/handovers", headers=_bearer(account)).json()
        assert listed[0]["status"] == "expired"
        assert listed[0]["title"] == "Finance Manager"
        res = client.post(f"/api/handovers/{session_id}/access", headers=_bearer(account))
        assert res.status_code == 410


# ---- The full journey, as the dashboard sees it ----

def _classifier(*a, **k):
    llm = MagicMock()

    def respond(messages):
        r = MagicMock()
        r.content = "[]" if "risk flag classifier" in messages[0].content else NO_FOLLOWUP
        return r

    llm.invoke.side_effect = respond
    return llm


def _primary(*a, **k):
    llm = MagicMock()
    r = MagicMock()
    r.content = "Understood. What happens next?"
    llm.invoke.return_value = r
    return llm


def _await(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("timed out")


@pytest.fixture
def journey(client, outbox):
    """A signed-in manager whose Stage 1 is confirmed, with the LLMs stubbed."""
    from api.session_manager import get_session_store

    def fake_docx(doc, path):
        with open(path, "wb") as f:
            f.write(b"HANDOVER PACK")
        return path

    with patch("agents.stage2_employee_interview.nodes._get_primary_llm", side_effect=_primary), \
         patch("agents.stage2_employee_interview.nodes._get_classifier_llm", side_effect=_classifier), \
         patch("api.routes.generate_document") as gen, \
         patch("api.routes.parse_llm_output"), \
         patch("api.routes.generate_docx", side_effect=fake_docx):
        gen.return_value = MagicMock(raw_markdown="# Handover")
        account = _sign_in(client, outbox)
        stage1 = client.post("/api/sessions/stage1", headers=_bearer(account)).json()["session_id"]
        store = get_session_store()
        store.store_profile(stage1, _profile(recipient_emails=["hr@acme.co.uk"]))
        store.update_session(stage1, {"session_complete": True})
        manager = client.post(
            f"/api/handovers/{stage1}/access", headers=_bearer(account)
        ).json()["token"]
        yield client, outbox, account, stage1, manager


def _summary(client, account):
    return client.get("/api/handovers", headers=_bearer(account)).json()[0]


class TestJourneyOnTheDashboard:
    def test_statuses_progress_through_to_ready(self, journey):
        client, outbox, account, stage1, manager = journey
        assert _summary(client, account)["status"] == "awaiting_employee"
        assert _summary(client, account)["title"] == "Senior Operations Coordinator"

        created = client.post(
            "/api/sessions/stage2", json={"stage1_session_id": stage1}, headers=_bearer(manager)
        ).json()
        for i in range(200):
            body = client.post(
                f"/api/sessions/{created['session_id']}/message",
                json={"message": f"Answer {i}."},
                headers=_bearer(created["employee_token"]),
            ).json()
            if body.get("session_complete"):
                break

        ready = _await(lambda: (s := _summary(client, account))["status"] == "ready" and s.get("delivery") and s)
        assert ready["stage2_session_id"] == created["session_id"]
        assert ready["document_id"]
        assert ready["delivery"]["status"] == "sent"

        # The download works with the token the dashboard minted.
        res = client.get(
            f"/api/documents/{ready['document_id']}", headers=_bearer(manager)
        )
        assert res.content == b"HANDOVER PACK"

    def test_the_manager_is_emailed_when_the_pack_is_ready(self, journey):
        client, outbox, account, stage1, manager = journey
        created = client.post(
            "/api/sessions/stage2", json={"stage1_session_id": stage1}, headers=_bearer(manager)
        ).json()
        for i in range(200):
            body = client.post(
                f"/api/sessions/{created['session_id']}/message",
                json={"message": f"Answer {i}."},
                headers=_bearer(created["employee_token"]),
            ).json()
            if body.get("session_complete"):
                break

        notice = _await(lambda: next(
            (m for m in outbox.messages if m.subject.startswith("Handover pack ready")), None
        ))
        assert notice.to == ["sarah@acme.co.uk"]
        assert "/dashboard" in notice.text
        assert notice.attachments == []  # the pack itself goes to recipients
        assert "emailed to the recipients" in notice.text

    def test_reopened_stage1_status_carries_the_profile(self, journey):
        client, outbox, account, stage1, manager = journey
        status = client.get(f"/api/sessions/{stage1}/status", headers=_bearer(manager)).json()
        assert status["profile"]["job_title"] == "Senior Operations Coordinator"


class TestTitleIsRecordedWhenSetupCompletes:
    def test_finishing_stage1_names_the_handover(self):
        """Through the normal Stage 1 completion path, not by seeding."""
        from api.accounts import get_account_store
        from api.routes import app
        from api.session_manager import get_session_store
        from tests.api_client import TEST_MANAGER_ID

        client = AuthedTestClient(app)
        session_id = client.post("/api/sessions/stage1").json()["session_id"]
        get_session_store().store_profile(session_id, _profile())

        with patch("api.routes._run_graph_resume") as run:
            run.return_value = {"last_agent_message": "All done.", "session_complete": True}
            client.post(f"/api/sessions/{session_id}/message", json={"message": "Yes, confirmed."})

        rows = get_account_store().list_handovers(TEST_MANAGER_ID)
        assert rows[0]["title"] == "Senior Operations Coordinator"
