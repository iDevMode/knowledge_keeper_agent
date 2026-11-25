"""The interview eval harness (evals/).

Three jobs:
1. The scripted harness runs every persona through the real Stage 2 graph and
   no score is worse than evals/gate.json — the CI regression gate for
   interview quality.
2. The harness can go red: break a capability on purpose and the matching
   metric must move. A scorecard that cannot fail proves nothing.
3. Its fixed data — the v1-question-to-objective map and persona annotations —
   stays consistent with the prompts and the objective bank.
"""

import logging

import pytest

from evals import gate
from evals.objectives import CORE_OBJECTIVES, V1_QUESTION_OBJECTIVES, objective_ids
from evals.personas import PERSONAS
from evals.scorecard import render_markdown, score_run
from evals.simulator import run_interview

logging.getLogger("langgraph").setLevel(logging.ERROR)


@pytest.fixture(scope="module")
def cards():
    return {pid: score_run(run_interview(p), p) for pid, p in PERSONAS.items()}


class TestScriptedGate:
    def test_every_persona_completes(self, cards):
        assert all(c.completed for c in cards.values()), [
            pid for pid, c in cards.items() if not c.completed
        ]

    def test_no_score_is_worse_than_the_gate(self, cards):
        problems = gate.regressions(list(cards.values()))
        assert not problems, "\n".join(problems)

    def test_gate_covers_every_persona(self):
        import json

        assert set(json.loads(gate.GATE_PATH.read_text())) == set(PERSONAS)

    def test_scripted_runs_are_deterministic(self, cards):
        persona = PERSONAS["veteran_ops"]
        again = score_run(run_interview(persona), persona)
        assert gate.snapshot([again]) == gate.snapshot([cards["veteran_ops"]])

    def test_no_secret_reaches_the_pack(self, cards):
        assert all(c.secrets_in_pack == 0 for c in cards.values())

    def test_report_renders(self, cards):
        report = render_markdown(list(cards.values()))
        assert "### Capture" in report and "### Conduct and coverage" in report
        assert "Overall recall" in report


class TestHarnessGoesRed:
    def test_no_followups_loses_hidden_facts(self, monkeypatch):
        import agents.stage2_employee_interview.graph as stage2_graph

        monkeypatch.setattr(stage2_graph, "followup_classifier_node", lambda state: {"pending_followup": None})
        persona = PERSONAS["veteran_ops"]
        card = score_run(run_interview(persona), persona)
        assert card.recall_by_reveal["followup"] < 1.0
        assert gate.regressions([card])

    def test_without_redaction_the_password_reaches_the_pack(self, monkeypatch):
        import evals.simulator as simulator
        from agents.redaction import RedactionResult

        monkeypatch.setattr(simulator, "redact_secrets", lambda text: RedactionResult(text, 0))
        persona = PERSONAS["oversharer"]
        card = score_run(run_interview(persona), persona)
        assert card.secrets_in_pack == 1

    def test_asking_about_named_entities_is_credited(self, monkeypatch):
        """The scorecard can also go green: an interviewer that asks about the
        names the employee dropped earns the entity-probe facts."""
        import evals.simulator as simulator
        from evals.fakes import FakeInterviewerLLM
        from langchain_core.messages import AIMessage

        persona = PERSONAS["rambler"]
        names = [f.entity for f in persona.facts if f.entity]

        class NamingInterviewer(FakeInterviewerLLM):
            def invoke(self, messages):
                base = super().invoke(messages).content.rstrip("?")
                return AIMessage(content=f"{base}, including {', '.join(names)}?")

        monkeypatch.setattr(simulator, "FakeInterviewerLLM", NamingInterviewer)
        card = score_run(run_interview(persona), persona)
        assert card.recall_by_reveal["entity_probe"] == 1.0

    def test_walking_away_half_way_is_measured(self):
        persona = PERSONAS["veteran_ops"]
        run = run_interview(persona, stop_after_answers=10)
        card = score_run(run, persona)
        assert not card.completed
        assert card.questions_asked == 10


class TestFixedData:
    def test_v1_map_matches_the_question_lists(self):
        from agents.stage2_employee_interview.prompts import (
            BLOCK_QUESTIONS,
            CLOSING_QUESTIONS,
            ROLE_ORIENTATION_QUESTIONS,
        )

        lengths = {b: len(q) for b, q in BLOCK_QUESTIONS.items()}
        lengths["role_orientation"] = len(ROLE_ORIENTATION_QUESTIONS)
        lengths["closing_sequence"] = len(CLOSING_QUESTIONS)
        assert {b: len(o) for b, o in V1_QUESTION_OBJECTIVES.items()} == lengths

    def test_every_objective_reference_exists(self):
        ids = objective_ids()
        referenced = {o for objs in V1_QUESTION_OBJECTIVES.values() for o in objs}
        referenced |= {o for p in PERSONAS.values() for f in p.facts for o in f.covers}
        referenced |= set(CORE_OBJECTIVES)
        assert referenced <= ids, referenced - ids

    def test_planted_facts_sit_in_planned_blocks_or_are_reachable_at_close(self):
        """A fact in a block the profile never plans can only come out through
        the closing 'anything else?' question — only direct facts can."""
        from evals.simulator import load_profile
        from models.knowledge_blocks import determine_block_order_and_depth

        for persona in PERSONAS.values():
            profile = load_profile(persona.profile_id)
            planned, _ = determine_block_order_and_depth(
                profile.priority_1, profile.priority_2, profile.priority_3, profile.supporting_categories,
            )
            planned = {b.value for b in planned}
            for fact in persona.facts:
                assert fact.block in planned or fact.reveal == "direct", (persona.id, fact.id)

    def test_interjection_markers_appear_in_their_text(self):
        for persona in PERSONAS.values():
            for item in persona.interjections:
                assert item.marker.lower() in item.text.lower(), (persona.id, item.kind)
