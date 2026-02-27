from typing import Dict, List, Tuple

from config.constants import BlockDepth, KnowledgeBlock

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


def _resolve_block(label: str) -> KnowledgeBlock | None:
    """Resolve a priority label string to a KnowledgeBlock enum."""
    normalised = label.strip().lower()
    if normalised in _LABEL_TO_BLOCK:
        return _LABEL_TO_BLOCK[normalised]
    # Try matching by enum value directly
    try:
        return KnowledgeBlock(normalised)
    except ValueError:
        pass
    # Fuzzy: check if any key is contained in the label
    for key, block in _LABEL_TO_BLOCK.items():
        if key in normalised or normalised in key:
            return block
    return None


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
    priorities = []
    for label in [priority_1, priority_2, priority_3]:
        block = _resolve_block(label)
        if block and block not in priorities:
            priorities.append(block)

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
