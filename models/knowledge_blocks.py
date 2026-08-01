import logging
import re
from typing import Dict, List, Tuple

from config.constants import BlockDepth, KnowledgeBlock

logger = logging.getLogger(__name__)

# Map priority string labels to KnowledgeBlock enums
_LABEL_TO_BLOCK = {
    "client and stakeholder relationships": KnowledgeBlock.CLIENT_STAKEHOLDER_RELATIONSHIPS,
    "client_stakeholder_relationships": KnowledgeBlock.CLIENT_STAKEHOLDER_RELATIONSHIPS,
    "internal processes and workflows": KnowledgeBlock.INTERNAL_PROCESSES_WORKFLOWS,
    "internal_processes_workflows": KnowledgeBlock.INTERNAL_PROCESSES_WORKFLOWS,
    "technical systems and tool knowledge": KnowledgeBlock.TECHNICAL_SYSTEMS_TOOLS,
    "technical_systems_tools": KnowledgeBlock.TECHNICAL_SYSTEMS_TOOLS,
    "decision-making logic and judgment calls": KnowledgeBlock.DECISION_MAKING_LOGIC,
    "decision_making_logic": KnowledgeBlock.DECISION_MAKING_LOGIC,
    "team dynamics and management context": KnowledgeBlock.TEAM_DYNAMICS_MANAGEMENT,
    "team_dynamics_management": KnowledgeBlock.TEAM_DYNAMICS_MANAGEMENT,
    "supplier and vendor relationships": KnowledgeBlock.SUPPLIER_VENDOR_RELATIONSHIPS,
    "supplier_vendor_relationships": KnowledgeBlock.SUPPLIER_VENDOR_RELATIONSHIPS,
    "regulatory or compliance knowledge": KnowledgeBlock.REGULATORY_COMPLIANCE,
    "regulatory_compliance": KnowledgeBlock.REGULATORY_COMPLIANCE,
    "undocumented workarounds and tribal knowledge": KnowledgeBlock.UNDOCUMENTED_WORKAROUNDS,
    "undocumented_workarounds": KnowledgeBlock.UNDOCUMENTED_WORKAROUNDS,
    "strategic context": KnowledgeBlock.STRATEGIC_CONTEXT,
    "strategic_context": KnowledgeBlock.STRATEGIC_CONTEXT,
}


# Distinctive tokens per block, used when a label does not match a canonical
# form exactly. Deliberately excludes words shared across blocks — "relationships"
# (client + supplier), "knowledge" (technical + regulatory + undocumented) and
# "context" (team + strategic) would all produce ambiguous matches.
_BLOCK_KEYWORDS: Dict[KnowledgeBlock, frozenset] = {
    KnowledgeBlock.CLIENT_STAKEHOLDER_RELATIONSHIPS: frozenset(
        {"client", "clients", "stakeholder", "stakeholders", "customer", "customers"}
    ),
    KnowledgeBlock.INTERNAL_PROCESSES_WORKFLOWS: frozenset(
        {"process", "processes", "workflow", "workflows", "procedure", "procedures"}
    ),
    KnowledgeBlock.TECHNICAL_SYSTEMS_TOOLS: frozenset(
        {"technical", "system", "systems", "tool", "tools", "software", "platform", "platforms"}
    ),
    KnowledgeBlock.DECISION_MAKING_LOGIC: frozenset(
        {"decision", "decisions", "judgment", "judgement"}
    ),
    # "management"/"managerial" are deliberately absent: they are generic business
    # words that also appear in "vendor management" and "stakeholder management",
    # which would tie against the supplier and client blocks.
    KnowledgeBlock.TEAM_DYNAMICS_MANAGEMENT: frozenset(
        {"team", "teams", "people", "staff", "reports", "colleagues", "headcount", "direct"}
    ),
    KnowledgeBlock.SUPPLIER_VENDOR_RELATIONSHIPS: frozenset(
        {"supplier", "suppliers", "vendor", "vendors", "procurement"}
    ),
    KnowledgeBlock.REGULATORY_COMPLIANCE: frozenset(
        {"regulatory", "regulation", "regulations", "compliance", "legal", "audit"}
    ),
    KnowledgeBlock.UNDOCUMENTED_WORKAROUNDS: frozenset(
        {"undocumented", "workaround", "workarounds", "tribal", "unwritten", "informal"}
    ),
    KnowledgeBlock.STRATEGIC_CONTEXT: frozenset({"strategic", "strategy", "strategies"}),
}


def _tokenise(label: str) -> set:
    return set(re.split(r"[^a-z0-9]+", label.strip().lower())) - {""}


def _resolve_block(label: str) -> KnowledgeBlock | None:
    """Resolve a priority label string to a KnowledgeBlock enum.

    Priorities are free text produced by the profile-generation LLM, so exact
    matching alone silently drops realistic phrasings ("Client relationships",
    "Decision making"). Resolution order: canonical alias, enum value, then
    distinctive-keyword scoring. An unresolvable or ambiguous label returns None
    and is logged — it must never fail silently, because a dropped priority means
    the employee is never interviewed on it.
    """
    normalised = label.strip().lower()

    if normalised in _LABEL_TO_BLOCK:
        return _LABEL_TO_BLOCK[normalised]

    try:
        return KnowledgeBlock(normalised)
    except ValueError:
        pass

    tokens = _tokenise(normalised)
    scores = {
        block: len(tokens & keywords)
        for block, keywords in _BLOCK_KEYWORDS.items()
        if tokens & keywords
    }

    if not scores:
        logger.warning("Could not resolve knowledge block from label %r", label)
        return None

    best = max(scores.values())
    winners = [block for block, score in scores.items() if score == best]
    if len(winners) > 1:
        logger.warning(
            "Ambiguous knowledge block label %r — matched %s equally; skipping",
            label, [b.value for b in winners],
        )
        return None

    logger.info("Resolved knowledge block label %r to %s", label, winners[0].value)
    return winners[0]


def determine_block_order_and_depth(
    priority_1: str,
    priority_2: str,
    priority_3: str,
    supporting_categories: list[str] | None = None,
) -> Tuple[List[KnowledgeBlock], Dict[KnowledgeBlock, BlockDepth]]:
    """Determine Stage 2 block execution order and depth from profile priorities.

    Returns:
        (ordered_blocks, depth_map) where ordered_blocks lists blocks in
        execution order and depth_map maps each block to full or light depth.
    """
    requested = [priority_1, priority_2, priority_3]
    priorities = []
    unresolved = []
    for label in requested:
        block = _resolve_block(label)
        if block is None:
            unresolved.append(label)
            continue
        if block not in priorities:
            priorities.append(block)

    if unresolved:
        # Loud on purpose: each dropped priority is a block the manager asked
        # for that the employee will never be interviewed on.
        logger.error(
            "%d of %d ranked priorities could not be resolved to a knowledge "
            "block and will NOT be covered in Stage 2: %s",
            len(unresolved), len(requested), unresolved,
        )

    depth_map: Dict[KnowledgeBlock, BlockDepth] = {}
    ordered: List[KnowledgeBlock] = []

    # Top 3 priorities at full depth
    for block in priorities:
        ordered.append(block)
        depth_map[block] = BlockDepth.FULL

    # Supporting categories at light depth
    if supporting_categories:
        for label in supporting_categories:
            block = _resolve_block(label)
            if block and block not in depth_map:
                ordered.append(block)
                depth_map[block] = BlockDepth.LIGHT

    # Undocumented workarounds always full depth, add if not already present
    if KnowledgeBlock.UNDOCUMENTED_WORKAROUNDS not in depth_map:
        ordered.append(KnowledgeBlock.UNDOCUMENTED_WORKAROUNDS)
    depth_map[KnowledgeBlock.UNDOCUMENTED_WORKAROUNDS] = BlockDepth.FULL

    return ordered, depth_map
