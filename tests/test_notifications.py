"""Tests for email delivery: digest composition, senders, and orchestration."""

import json
from pathlib import Path

from api.notifications import (
    ConsoleEmailSender,
    build_manager_digest,
    deliver_handover,
)
from api.session_manager import InMemorySessionStore
from models.risk_flags import RiskFlag
from models.role_intelligence_profile import RoleIntelligenceProfile

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "sample_role_profiles.json"


def _profile(profile_id="process_heavy") -> RoleIntelligenceProfile:
    with open(FIXTURES_PATH) as f:
        return RoleIntelligenceProfile.model_validate(json.load(f)[profile_id])


class _CapturingSender:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, body):
        self.sent.append({"to": to, "subject": subject, "body": body})


class _FakeGenRequest:
    def __init__(self, profile, risk_flags, answers, block_order):
        self.profile = profile
        self.risk_flags = risk_flags
        self.answers = answers
        self.block_order = block_order
        self.block_depths = {}


def _risk(severity):
    return RiskFlag(
        flag_type="single_point_of_failure",
        severity=severity,
        description="x",
        recommended_action="y",
        source_block="internal_processes_workflows",
        source_question_index=0,
    )


class TestDigest:
    def test_includes_key_facts(self):
        subject, body = build_manager_digest(
            profile=_profile(),
            risk_flags=[_risk("critical"), _risk("high"), _risk("high")],
            answers={"a.0": ["x"], "a.1": ["y"]},
            block_order=["internal_processes_workflows", "technical_systems_tools"],
            duration_minutes=37,
            download_link="https://app.example.com/api/documents/d1?token=t",
        )
        assert "Handover ready" in subject
        assert "https://app.example.com/api/documents/d1?token=t" in body
        assert "~37 min" in body
        assert "2" in body  # blocks covered
        assert "1 critical, 2 high" in body
        assert "critical risk flag" in body  # critical callout present

    def test_no_critical_callout_when_none(self):
        _, body = build_manager_digest(
            profile=_profile(),
            risk_flags=[_risk("medium")],
            answers={},
            block_order=[],
            duration_minutes=None,
            download_link="http://x/y",
        )
        assert "critical risk flag" not in body


class TestConsoleSender:
    def test_send_does_not_raise(self, caplog):
        import logging
        with caplog.at_level(logging.INFO):
            ConsoleEmailSender().send("a@b.com", "subj", "body")
        assert "a@b.com" in caplog.text


class TestDeliverHandover:
    def _setup(self):
        store = InMemorySessionStore()
        stage1 = store.create_session(stage=1)
        store.store_profile(stage1, _profile())
        stage2 = store.create_session(stage=2)
        store.link_sessions(stage1, stage2)
        gen = _FakeGenRequest(_profile(), [_risk("critical")], {"a.0": ["x"]}, ["internal_processes_workflows"])
        return store, stage1, stage2, gen

    def test_sends_when_email_set(self):
        store, stage1, stage2, gen = self._setup()
        store.update_session(stage1, {"manager_email": "manager@co.com"})
        sender = _CapturingSender()

        sent = deliver_handover(
            store=store, sender=sender, stage2_session_id=stage2,
            document_id="doc-1", gen_request=gen,
        )
        assert sent is True
        assert len(sender.sent) == 1
        assert sender.sent[0]["to"] == "manager@co.com"
        assert "doc-1" in sender.sent[0]["body"]

    def test_skips_when_no_email(self):
        store, stage1, stage2, gen = self._setup()
        sender = _CapturingSender()

        sent = deliver_handover(
            store=store, sender=sender, stage2_session_id=stage2,
            document_id="doc-1", gen_request=gen,
        )
        assert sent is False
        assert sender.sent == []
