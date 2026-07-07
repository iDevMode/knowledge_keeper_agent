"""Eval harness tests.

Two jobs: (1) the scripted harness runs green against the current agent —
this is the CI regression gate for interview effectiveness; (2) the harness
actually FAILS when a capability is broken — a scorecard that can't go red
is worthless.
"""

import pytest

import agents.stage2_employee_interview.nodes as stage2_nodes
from evals.personas import PERSONAS
from evals.scorecard import render_markdown, score_run
from evals.simulator import run_interview


@pytest.fixture(params=list(PERSONAS))
def persona(request):
    return PERSONAS[request.param]


class TestScriptedEvalGate:
    """The regression gate: current agent must extract all planted knowledge."""

    def test_full_scorecard(self, persona):
        run = run_interview(persona)
        card = score_run(run, persona)

        assert card.completed, "interview did not reach session_complete"
        assert card.recall == 1.0, f"missed facts: {[o.fact_id for o in card.fact_outcomes if not o.captured]}"
        assert card.risk_recall == 1.0
        assert card.followup_precision == 1.0
        assert card.within_budget
        assert card.single_question_compliance == 1.0
        assert card.coverage == 1.0
        # Every persona plants at least one entity_probe fact, so the sweep
        # must have run for recall to be perfect.
        assert card.entity_sweep_ran

    def test_report_renders(self):
        persona = PERSONAS["veteran_ops"]
        run = run_interview(persona)
        report = render_markdown([score_run(run, persona)])
        assert "veteran_ops" in report
        assert "Overall planted-knowledge recall" in report


class TestHarnessDetectsRegressions:
    """Break a capability on purpose — the scorecard must go red."""

    def test_detects_broken_entity_sweep(self, monkeypatch):
        monkeypatch.setattr(stage2_nodes, "ENTITY_SWEEP_MAX_QUESTIONS", 0)
        persona = PERSONAS["veteran_ops"]
        card = score_run(run_interview(persona), persona)
        assert card.recall_by_reveal["entity_probe"] < 1.0
        assert not card.entity_sweep_ran

    def test_detects_broken_followups(self, monkeypatch):
        # An agent that never follows up must lose the hidden facts.
        monkeypatch.setattr(
            stage2_nodes, "followup_classifier_node", lambda state: {"pending_followup": None}
        )
        # The graph binds node functions at build time, so patch via the
        # module used when the simulator rebuilds the graph.
        import agents.stage2_employee_interview.graph as stage2_graph
        monkeypatch.setattr(
            stage2_graph, "followup_classifier_node", lambda state: {"pending_followup": None}
        )
        persona = PERSONAS["relationship_mgr"]
        card = score_run(run_interview(persona), persona)
        assert card.recall_by_reveal["followup"] < 1.0

    def test_tight_budget_closes_early_but_completes(self, monkeypatch):
        # Squeeze the budget: the interview must shorten (skip/lighten blocks)
        # yet still close properly instead of running forever or crashing.
        monkeypatch.setattr(stage2_nodes, "QUESTION_BUDGET_HEADROOM", 0.4)
        persona = PERSONAS["veteran_ops"]
        run = run_interview(persona)
        card = score_run(run, persona)
        assert card.completed
        full_run_questions = 35  # observed with the normal budget
        assert card.questions_asked < full_run_questions


# ---- Phase 3: document knowledge-survival scorecard ----

class TestDocumentScorecard:
    def test_captured_knowledge_survives_to_document(self, persona):
        from evals.document import score_document

        run = run_interview(persona)
        card = score_document(run, persona)
        assert card.document_recall == 1.0, f"dropped in document: {card.missing_fact_ids}"
        assert card.risk_summary_recall == 1.0

    def test_broken_routing_drops_facts(self, monkeypatch):
        """A block with no section mapping must lose its facts — proving the
        scorecard catches routing regressions, not just prints green."""
        import evals.document as doc_mod
        from evals.document import score_document
        from evals.personas import PERSONAS

        broken = dict(doc_mod.BLOCK_TO_SECTION)
        broken.pop("undocumented_workarounds")
        monkeypatch.setattr(doc_mod, "BLOCK_TO_SECTION", broken)

        persona = PERSONAS["veteran_ops"]
        run = run_interview(persona)
        card = score_document(run, persona)
        assert card.document_recall < 1.0
        assert "ops_followup_batch_fix" in card.missing_fact_ids
