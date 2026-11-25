"""Secrets typed into an interview never reach state, Stage 3 or the pack."""

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import json
import pytest

from agents.redaction import REDACTED, REDACTION_NOTICE, redact_secrets
from tests.api_client import AuthedTestClient

PASSWORD = "Summer2024!xY"


class TestPatterns:
    @pytest.mark.parametrize("text, secret", [
        (f"The Sage admin password is {PASSWORD} and only I know it.", PASSWORD),
        ("password: hunter2", "hunter2"),
        ("The alarm PIN is 4821.", "4821"),
        ("pin for the alarm is 4821", "4821"),
        ("Our API key = sk-abcdefghijklmnopqrstuvwx123", "sk-abcdefghijklmnopqrstuvwx123"),
        ("Token is ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
        ("The key AKIAABCDEFGHIJKLMNOP is in the CI config", "AKIAABCDEFGHIJKLMNOP"),
        ("the webhook secret Xk9mPq2LrT7vWz4NbY8c lives in Zapier", "Xk9mPq2LrT7vWz4NbY8c"),
        ("my memorable word is pineapple", "pineapple"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAK\n-----END RSA PRIVATE KEY-----", "MIIEowIBAAK"),
    ])
    def test_secrets_are_removed(self, text, secret):
        result = redact_secrets(text)
        assert secret not in result.text
        assert REDACTED in result.text
        assert result.redacted

    @pytest.mark.parametrize("text", [
        "The password is in the safe in the finance office.",
        "I reset the password every quarter.",
        "The token is stored in 1Password under 'Xero API'.",
        "Order reference PO-2024-00123 and invoice INV20240093.",
        "The URL is https://acme.sharepoint.com/sites/OpsTeam2024ABCdef",
        r"The share is \\fileserver\finance\month-end",
        "Email dev@acme.co.uk. GUID 3f2504e0-4f89-11d3-9a0c-0305e82c3301.",
        "The password is Monday's job.",
    ])
    def test_ordinary_answers_are_untouched(self, text):
        result = redact_secrets(text)
        assert result.text == text
        assert not result.redacted

    def test_empty(self):
        assert redact_secrets("").text == ""


# ---- Through the API ----

FIXTURE = "tests/fixtures/sample_role_profiles.json"


def _primary():
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="Thanks. Where is that kept?")
    return llm


def _classifier():
    llm = MagicMock()

    def respond(messages):
        prompt = messages[0].content
        if "risk flag classifier" in prompt:
            return MagicMock(content="[]")
        return MagicMock(content=json.dumps({"needs_followup": False, "reason": "", "suggested_followup": ""}))

    llm.invoke.side_effect = respond
    return llm


@pytest.fixture
def client():
    import api.document_store as doc_mod
    import api.routes as routes_mod
    import api.session_manager as sm_mod

    sm_mod._store = None
    routes_mod._registry = routes_mod.GraphRegistry()
    doc_mod.reset_document_store()
    from api.routes import app
    return AuthedTestClient(app)


def test_a_typed_password_is_absent_everywhere_downstream(client):
    from agents.stage3_document_generation.prompts import build_context_block
    from api.routes import _registry
    from api.session_manager import get_session_store
    from models.role_intelligence_profile import RoleIntelligenceProfile

    with ExitStack() as stack:
        stack.enter_context(patch("agents.stage2_employee_interview.nodes._get_primary_llm", return_value=_primary()))
        stack.enter_context(patch("agents.stage2_employee_interview.nodes._get_classifier_llm", return_value=_classifier()))

        store = get_session_store()
        stage1_id = store.create_session(stage=1)
        with open(FIXTURE) as f:
            store.store_profile(stage1_id, RoleIntelligenceProfile.model_validate(json.load(f)["process_heavy"]))
        client.adopt(stage1_id)
        session_id = client.post("/api/sessions/stage2", json={"stage1_session_id": stage1_id}).json()["session_id"]

        response = client.post(
            f"/api/sessions/{session_id}/message",
            json={"message": f"Only I have the Sage admin login. The password is {PASSWORD}."},
        )

    assert response.status_code == 200
    assert response.json()["message"].startswith(REDACTION_NOTICE)

    instance = _registry.get(session_id)
    values = instance.graph.get_state(instance.config).values

    assert PASSWORD not in repr(values["conversation_history"])
    assert PASSWORD not in repr(values["answers"])
    assert REDACTED in repr(values["answers"])

    context = build_context_block(
        values["profile"], values["conversation_history"], values["risk_flags"],
        values["answers"], values["block_order"], values["block_depths"],
    )
    assert PASSWORD not in context


def test_no_notice_when_nothing_was_removed(client):
    from api.session_manager import get_session_store
    from models.role_intelligence_profile import RoleIntelligenceProfile

    with ExitStack() as stack:
        stack.enter_context(patch("agents.stage2_employee_interview.nodes._get_primary_llm", return_value=_primary()))
        stack.enter_context(patch("agents.stage2_employee_interview.nodes._get_classifier_llm", return_value=_classifier()))
        store = get_session_store()
        stage1_id = store.create_session(stage=1)
        with open(FIXTURE) as f:
            store.store_profile(stage1_id, RoleIntelligenceProfile.model_validate(json.load(f)["process_heavy"]))
        client.adopt(stage1_id)
        session_id = client.post("/api/sessions/stage2", json={"stage1_session_id": stage1_id}).json()["session_id"]
        response = client.post(
            f"/api/sessions/{session_id}/message",
            json={"message": "The password is kept in the finance safe."},
        )

    assert REDACTION_NOTICE not in response.json()["message"]
