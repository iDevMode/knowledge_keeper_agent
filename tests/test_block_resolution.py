"""Knowledge-block resolution from free-text priority labels (review finding H2).

priority_1/2/3 are produced by the profile-generation LLM. Before this was
hardened, realistic phrasings such as "Client relationships" resolved to None
and were silently dropped, so Stage 2 never interviewed the employee on the
manager's stated priorities.
"""

import logging

import pytest

from config.constants import BlockDepth, KnowledgeBlock
from models.knowledge_blocks import _resolve_block, determine_block_order_and_depth


class TestCanonicalLabelsStillResolve:
    """The exact enum values the Stage 1 prompt now demands must always work."""

    @pytest.mark.parametrize("block", list(KnowledgeBlock))
    def test_enum_value_resolves_to_itself(self, block):
        assert _resolve_block(block.value) is block

    @pytest.mark.parametrize(
        "label,expected",
        [
            ("client and stakeholder relationships", KnowledgeBlock.CLIENT_STAKEHOLDER_RELATIONSHIPS),
            ("internal processes and workflows", KnowledgeBlock.INTERNAL_PROCESSES_WORKFLOWS),
            ("technical systems and tool knowledge", KnowledgeBlock.TECHNICAL_SYSTEMS_TOOLS),
            ("decision-making logic and judgment calls", KnowledgeBlock.DECISION_MAKING_LOGIC),
            ("team dynamics and management context", KnowledgeBlock.TEAM_DYNAMICS_MANAGEMENT),
            ("supplier and vendor relationships", KnowledgeBlock.SUPPLIER_VENDOR_RELATIONSHIPS),
            ("regulatory or compliance knowledge", KnowledgeBlock.REGULATORY_COMPLIANCE),
            ("undocumented workarounds and tribal knowledge", KnowledgeBlock.UNDOCUMENTED_WORKAROUNDS),
            ("strategic context", KnowledgeBlock.STRATEGIC_CONTEXT),
        ],
    )
    def test_prompt_menu_wording_resolves(self, label, expected):
        assert _resolve_block(label) is expected


class TestRealisticParaphrasesResolve:
    """Phrasings an LLM plausibly emits. Every one of these returned None before."""

    @pytest.mark.parametrize(
        "label,expected",
        [
            ("Client relationships", KnowledgeBlock.CLIENT_STAKEHOLDER_RELATIONSHIPS),
            ("Key stakeholder management", KnowledgeBlock.CLIENT_STAKEHOLDER_RELATIONSHIPS),
            ("Decision making", KnowledgeBlock.DECISION_MAKING_LOGIC),
            ("Judgement calls", KnowledgeBlock.DECISION_MAKING_LOGIC),
            ("Supplier relationships", KnowledgeBlock.SUPPLIER_VENDOR_RELATIONSHIPS),
            ("Vendor management", KnowledgeBlock.SUPPLIER_VENDOR_RELATIONSHIPS),
            ("Regulatory knowledge", KnowledgeBlock.REGULATORY_COMPLIANCE),
            ("Compliance", KnowledgeBlock.REGULATORY_COMPLIANCE),
            ("Internal processes", KnowledgeBlock.INTERNAL_PROCESSES_WORKFLOWS),
            ("Technical systems", KnowledgeBlock.TECHNICAL_SYSTEMS_TOOLS),
            ("Tribal knowledge", KnowledgeBlock.UNDOCUMENTED_WORKAROUNDS),
            ("Strategy", KnowledgeBlock.STRATEGIC_CONTEXT),
        ],
    )
    def test_paraphrase_resolves(self, label, expected):
        assert _resolve_block(label) is expected


class TestUnresolvableLabelsAreLoud:
    def test_gibberish_returns_none_and_warns(self, caplog):
        with caplog.at_level(logging.WARNING, logger="models.knowledge_blocks"):
            assert _resolve_block("something entirely unrelated") is None
        assert any("Could not resolve" in r.message for r in caplog.records)

    def test_ambiguous_label_returns_none_and_warns(self, caplog):
        # Equal keyword hits on two blocks must not silently pick one.
        with caplog.at_level(logging.WARNING, logger="models.knowledge_blocks"):
            result = _resolve_block("client and vendor")
        assert result is None
        assert any("Ambiguous" in r.message for r in caplog.records)

    def test_dropped_priorities_are_reported_at_error_level(self, caplog):
        with caplog.at_level(logging.ERROR, logger="models.knowledge_blocks"):
            determine_block_order_and_depth("total nonsense", "more nonsense", "yet more", [])
        assert any(
            "could not be resolved" in r.message for r in caplog.records
        ), "silently dropping every ranked priority must be logged at ERROR"


class TestBlockOrderWithParaphrasedPriorities:
    def test_paraphrased_priorities_all_run_at_full_depth(self):
        ordered, depths = determine_block_order_and_depth(
            "Client relationships", "Decision making", "Supplier relationships",
            ["Technical systems"],
        )

        assert ordered[0] is KnowledgeBlock.CLIENT_STAKEHOLDER_RELATIONSHIPS
        assert ordered[1] is KnowledgeBlock.DECISION_MAKING_LOGIC
        assert ordered[2] is KnowledgeBlock.SUPPLIER_VENDOR_RELATIONSHIPS

        for block in ordered[:3]:
            assert depths[block] is BlockDepth.FULL

        assert depths[KnowledgeBlock.TECHNICAL_SYSTEMS_TOOLS] is BlockDepth.LIGHT
        # Always-on block, regardless of ranking.
        assert depths[KnowledgeBlock.UNDOCUMENTED_WORKAROUNDS] is BlockDepth.FULL

    def test_undocumented_workarounds_always_full_depth(self):
        _, depths = determine_block_order_and_depth(
            "Client relationships", "Strategy", "Compliance", [],
        )
        assert depths[KnowledgeBlock.UNDOCUMENTED_WORKAROUNDS] is BlockDepth.FULL
