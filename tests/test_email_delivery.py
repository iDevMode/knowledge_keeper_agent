"""Emailing the finished handover pack (MVP scope: "Email delivery of completed
document to recipients").

webhooks.on_document_generated only logged, so a manager who listed HR and the
incoming hire as recipients had to download the pack and send it on by hand.
"""

import base64
import json
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from api.auth import MANAGER, issue_token
from api.delivery import FAILED, SENT, SKIPPED, build_pack_email, deliver_handover_pack, recipients_for
from api.mailer import EmailMessage, MailerError, SendGridMailer, extract_emails
from models.role_intelligence_profile import RoleIntelligenceProfile

FIXTURE = "tests/fixtures/sample_role_profiles.json"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
NO_FOLLOWUP = json.dumps({"needs_followup": False, "reason": "clear", "suggested_followup": ""})


def _profile(profile_id="process_heavy", **overrides) -> RoleIntelligenceProfile:
    with open(FIXTURE) as f:
        data = json.load(f)[profile_id]
    data.update(overrides)
    return RoleIntelligenceProfile.model_validate(data)


@pytest.fixture(autouse=True)
def reset_singletons():
    import api.document_store as doc_mod
    import api.routes as routes_mod
    import api.session_manager as sm_mod

    sm_mod._store = None
    routes_mod._registry = routes_mod.GraphRegistry()
    doc_mod.reset_document_store()
    yield


# ---- Address extraction ----

class TestExtractEmails:
    def test_bare_addresses(self):
        assert extract_emails(["hr@acme.co.uk", "Sam@Acme.co.uk"]) == ["hr@acme.co.uk", "sam@acme.co.uk"]

    def test_addresses_inside_free_text(self):
        assert extract_emails(["Sarah, HR Manager <sarah.jones@acme.co.uk>"]) == ["sarah.jones@acme.co.uk"]

    def test_duplicates_are_removed_case_insensitively(self):
        assert extract_emails(["a@acme.com", "A@ACME.com", "b@acme.com"]) == ["a@acme.com", "b@acme.com"]

    def test_names_without_addresses_yield_nothing(self):
        assert extract_emails(["Head of Operations", "Incoming hire"]) == []

    def test_incomplete_addresses_are_rejected(self):
        assert extract_emails(["sarah@", "@acme.com", "sarah@acme"]) == []

    def test_recipients_for_prefers_recipient_emails_and_scans_legacy_names(self):
        profile = _profile(
            recipient_emails=["hr@acme.co.uk"],
            recipients=["HR Manager", "Incoming hire <new.starter@acme.co.uk>"],
        )
        assert recipients_for(profile) == ["hr@acme.co.uk", "new.starter@acme.co.uk"]

    def test_old_profiles_without_the_field_still_validate(self):
        profile = _profile()
        assert profile.recipient_emails == []


# ---- SendGrid client ----

def _sendgrid(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SendGridMailer("SG.test-key", "handover@nukode.co.uk", "KnowledgeKeeper", client=client)


def _message(**kw):
    defaults = dict(
        to=["hr@acme.co.uk", "boss@acme.co.uk"],
        subject="Handover pack: Coordinator",
        text="Body",
        html="<p>Body</p>",
    )
    defaults.update(kw)
    return EmailMessage(**defaults)


class TestSendGridMailer:
    def test_posts_one_personalization_per_recipient(self):
        captured = {}

        def handler(request):
            captured["request"] = request
            return httpx.Response(202)

        _sendgrid(handler).send(_message())

        request = captured["request"]
        assert str(request.url) == "https://api.sendgrid.com/v3/mail/send"
        assert request.headers["Authorization"] == "Bearer SG.test-key"
        body = json.loads(request.content)
        # Separate personalizations: recipients do not see each other.
        assert body["personalizations"] == [
            {"to": [{"email": "hr@acme.co.uk"}]},
            {"to": [{"email": "boss@acme.co.uk"}]},
        ]
        assert body["from"] == {"email": "handover@nukode.co.uk", "name": "KnowledgeKeeper"}
        assert [c["type"] for c in body["content"]] == ["text/plain", "text/html"]
        assert body["tracking_settings"]["click_tracking"]["enable"] is False

    def test_attachments_are_base64_encoded(self):
        from api.mailer import Attachment

        payload = _sendgrid(lambda r: httpx.Response(202)).build_payload(
            _message(attachments=[Attachment("pack.docx", b"PACK BYTES", DOCX)])
        )
        attachment = payload["attachments"][0]
        assert base64.b64decode(attachment["content"]) == b"PACK BYTES"
        assert attachment["filename"] == "pack.docx"
        assert attachment["type"] == DOCX
        assert attachment["disposition"] == "attachment"

    @pytest.mark.parametrize(
        "status,expected",
        [
            (401, "API key"),
            (403, "sender address"),
            (413, "too large"),
            (429, "rate limit"),
            (500, "SendGrid returned 500"),
        ],
    )
    def test_failures_become_actionable_errors(self, status, expected):
        mailer = _sendgrid(lambda r: httpx.Response(status, json={"errors": [{"message": "nope"}]}))
        with pytest.raises(MailerError, match=expected):
            mailer.send(_message())

    def test_network_failure_is_a_mailer_error(self):
        def handler(request):
            raise httpx.ConnectError("down", request=request)

        with pytest.raises(MailerError, match="Could not reach SendGrid"):
            _sendgrid(handler).send(_message())

    def test_errors_do_not_echo_addresses(self):
        mailer = _sendgrid(lambda r: httpx.Response(400, json={"errors": [{"message": "bad request"}]}))
        with pytest.raises(MailerError) as info:
            mailer.send(_message())
        assert "hr@acme.co.uk" not in str(info.value)

    def test_refuses_to_send_without_recipients(self):
        with pytest.raises(MailerError):
            _sendgrid(lambda r: httpx.Response(202)).send(_message(to=[]))


# ---- The pack email ----

class TestPackEmail:
    def test_subject_and_attachment_name_use_the_role(self):
        message = build_pack_email(
            _profile(), ["hr@acme.co.uk"], "1f2e-uuid.docx", DOCX, b"PACK"
        )
        assert "Senior Operations Coordinator" in message.subject
        assert message.attachments[0].filename == "Handover - Senior Operations Coordinator.docx"
        assert message.attachments[0].content == b"PACK"

    def test_body_asks_for_confidential_handling(self):
        message = build_pack_email(_profile(), ["hr@acme.co.uk"], "x.pdf", "application/pdf", b"P")
        assert "confidential" in message.text.lower()
        assert message.attachments[0].filename.endswith(".pdf")


class TestDeliverHandoverPack:
    def _start(self, document_id="doc-1"):
        from api.document_store import get_document_store

        store = get_document_store()
        store.start_job(document_id, "s2")
        store.complete_job(document_id, f"{document_id}.docx", DOCX, b"PACK")
        return store

    def test_sends_and_records_who_received_it(self, outbox):
        store = self._start()
        profile = _profile(recipient_emails=["hr@acme.co.uk", "new@acme.co.uk"])

        status = deliver_handover_pack("s2", "doc-1", profile, "doc-1.docx", DOCX, b"PACK")

        assert status == SENT
        assert len(outbox.messages) == 1
        assert outbox.messages[0].to == ["hr@acme.co.uk", "new@acme.co.uk"]
        assert store.get_job("doc-1")["delivery"] == {
            "status": SENT,
            "recipients": ["hr@acme.co.uk", "new@acme.co.uk"],
            "error": None,
        }

    def test_skips_and_says_why_when_no_addresses_were_given(self, outbox):
        store = self._start()
        status = deliver_handover_pack("s2", "doc-1", _profile(), "doc-1.docx", DOCX, b"PACK")

        assert status == SKIPPED
        assert outbox.messages == []
        delivery = store.get_job("doc-1")["delivery"]
        assert delivery["status"] == SKIPPED
        assert "No recipient email addresses" in delivery["error"]

    def test_a_provider_failure_is_recorded_not_raised(self, outbox):
        store = self._start()
        outbox.fail_with = MailerError("SendGrid rejected the API key (401)")

        status = deliver_handover_pack(
            "s2", "doc-1", _profile(recipient_emails=["hr@acme.co.uk"]), "doc-1.docx", DOCX, b"PACK"
        )

        assert status == FAILED
        delivery = store.get_job("doc-1")["delivery"]
        assert delivery["error"] == "SendGrid rejected the API key (401)"
        # The pack is still there to download.
        assert store.get_job("doc-1")["status"] == "complete"

    def test_an_unexpected_crash_is_recorded_not_raised(self, outbox):
        store = self._start()
        outbox.fail_with = ValueError("boom")

        status = deliver_handover_pack(
            "s2", "doc-1", _profile(recipient_emails=["hr@acme.co.uk"]), "doc-1.docx", DOCX, b"PACK"
        )
        assert status == FAILED
        assert "ValueError" in store.get_job("doc-1")["delivery"]["error"]


# ---- End to end through the API ----

def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


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


def _await_delivery(document_id, timeout=10.0):
    from api.document_store import get_document_store

    deadline = time.time() + timeout
    while time.time() < deadline:
        job = get_document_store().get_job(document_id)
        if job and job.get("delivery"):
            return job["delivery"]
        time.sleep(0.02)
    raise AssertionError("delivery was never recorded")


@pytest.fixture
def flow(tmp_path):
    from api.routes import app
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
        client = TestClient(app)
        store = get_session_store()
        stage1 = store.create_session(stage=1)
        store.store_profile(stage1, _profile(recipient_emails=["hr@acme.co.uk"]))
        manager = issue_token(stage1, MANAGER)
        created = client.post(
            "/api/sessions/stage2", json={"stage1_session_id": stage1}, headers=_bearer(manager)
        ).json()
        yield client, manager, created["session_id"], created["employee_token"]


def _complete(client, session_id, token):
    for i in range(200):
        body = client.post(
            f"/api/sessions/{session_id}/message",
            json={"message": f"Answer {i}."},
            headers=_bearer(token),
        ).json()
        if body.get("session_complete"):
            return
    raise AssertionError("Stage 2 never completed")


def _await_document(session_id, timeout=10.0):
    from api.document_store import get_document_store

    store = get_document_store()
    deadline = time.time() + timeout
    while time.time() < deadline:
        doc_id = store.document_for_session(session_id)
        if doc_id and store.get_job(doc_id)["status"] != "generating":
            return doc_id
        time.sleep(0.02)
    raise AssertionError("no document")


class TestPackIsEmailedWhenTheEmployeeFinishes:
    def test_recipients_receive_the_pack(self, flow, outbox):
        client, manager, stage2, employee = flow
        _complete(client, stage2, employee)
        doc_id = _await_document(stage2)
        delivery = _await_delivery(doc_id)

        assert delivery["status"] == SENT
        assert len(outbox.messages) == 1
        message = outbox.messages[0]
        assert message.to == ["hr@acme.co.uk"]
        assert message.attachments[0].content == b"HANDOVER PACK"

    def test_manager_status_reports_the_delivery(self, flow, outbox):
        client, manager, stage2, employee = flow
        _complete(client, stage2, employee)
        _await_delivery(_await_document(stage2))

        body = client.get(f"/api/sessions/{stage2}/status", headers=_bearer(manager)).json()
        assert body["delivery"]["status"] == SENT
        assert body["delivery"]["recipients"] == ["hr@acme.co.uk"]

    def test_employee_status_never_reports_the_delivery(self, flow, outbox):
        client, manager, stage2, employee = flow
        _complete(client, stage2, employee)
        _await_delivery(_await_document(stage2))

        body = client.get(f"/api/sessions/{stage2}/status", headers=_bearer(employee)).json()
        assert body["delivery"] is None

    def test_a_manual_regenerate_does_not_email_again(self, flow, outbox):
        client, manager, stage2, employee = flow
        _complete(client, stage2, employee)
        _await_delivery(_await_document(stage2))

        response = client.post(
            f"/api/sessions/{stage2}/generate", json={"format": "docx"}, headers=_bearer(manager)
        )
        assert response.status_code == 200
        new_id = response.json()["document_id"]
        _await_document(stage2)
        time.sleep(0.2)
        assert len(outbox.messages) == 1
        assert get_job_delivery(new_id) is None

    def test_a_manual_regenerate_can_ask_to_email(self, flow, outbox):
        client, manager, stage2, employee = flow
        _complete(client, stage2, employee)
        _await_delivery(_await_document(stage2))

        response = client.post(
            f"/api/sessions/{stage2}/generate",
            json={"format": "docx", "send_email": True},
            headers=_bearer(manager),
        )
        _await_delivery(response.json()["document_id"])
        assert len(outbox.messages) == 2


def get_job_delivery(document_id):
    from api.document_store import get_document_store

    return get_document_store().get_job(document_id).get("delivery")


# ---- Startup configuration ----

class TestEmailConfiguration:
    def _production(self, **kw):
        from config.settings import Settings

        return Settings(
            _env_file=None,
            anthropic_api_key="x",
            api_secret_key="x",
            database_url="postgresql://unused",
            environment="production",
            allowed_origins="https://example.com",
            **kw,
        )

    def test_a_key_without_a_sender_refuses_to_start(self, capsys):
        with pytest.raises(SystemExit):
            self._production(sendgrid_api_key="SG.x", email_from_address="").validate_for_production()
        assert "EMAIL_FROM_ADDRESS" in capsys.readouterr().err

    def test_no_key_only_warns(self, capsys):
        self._production(sendgrid_api_key="").validate_for_production()
        assert "SENDGRID_API_KEY is not set" in capsys.readouterr().err

    def test_a_complete_email_config_is_accepted(self, capsys):
        self._production(
            sendgrid_api_key="SG.x", email_from_address="handover@nukode.co.uk"
        ).validate_for_production()
        assert "SENDGRID" not in capsys.readouterr().err

    def test_without_a_key_the_mailer_only_logs(self):
        from api.mailer import LogMailer, get_mailer, set_mailer

        set_mailer(None)
        with patch("api.mailer.settings") as s:
            s.sendgrid_api_key = ""
            s.email_from_address = ""
            assert isinstance(get_mailer(), LogMailer)


# ---- The manager confirms the addresses ----

class TestProfileReviewShowsTheAddresses:
    """The pack carries a Risk Summary, so the manager sees exactly where it
    will be emailed before confirming the profile."""

    def _review(self, profile):
        from agents.stage1_business_interview.nodes import profile_review_node

        return profile_review_node({"role_intelligence_profile": profile, "session_id": "s1"})[
            "last_agent_message"
        ]

    def test_addresses_are_listed(self):
        message = self._review(_profile(recipient_emails=["hr@acme.co.uk", "new@acme.co.uk"]))
        assert "Email the pack to: hr@acme.co.uk, new@acme.co.uk" in message

    def test_missing_addresses_are_called_out(self):
        message = self._review(_profile())
        assert "the pack will not be emailed" in message
