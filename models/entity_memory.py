"""Entity memory for Stage 2 interviews.

Every answer is scanned (by the same Haiku call that detects risk flags) for
named entities — people, clients, systems, processes — that the employee
mentions in passing. Entities accumulate in graph state as plain dicts (so
they serialise cleanly through any checkpointer) and drive two behaviours:

1. Question awareness — unprobed entities are surfaced to the primary model
   so questions can reference them by name instead of staying generic.
2. Entity sweep — before the closing sequence, the highest-importance
   entities that were never explored get their own targeted questions.
"""

from typing import Dict, List

from pydantic import BaseModel

from config.constants import EntityImportance, EntityType


class TrackedEntity(BaseModel):
    """An entity accumulated in interview state."""

    name: str
    entity_type: EntityType
    importance: EntityImportance
    context: str  # short note on what was said about it
    source_block: str
    mention_count: int = 1
    probed: bool = False  # has any agent question explicitly explored it?


_IMPORTANCE_RANK = {
    EntityImportance.HIGH: 0,
    EntityImportance.MEDIUM: 1,
    EntityImportance.LOW: 2,
}


def merge_entities(
    existing: Dict[str, dict], detected: List, source_block: str
) -> Dict[str, dict]:
    """Merge newly detected entities into the tracked set (keyed by lowercased
    name). Repeat mentions bump mention_count and can upgrade importance."""
    merged = dict(existing)
    for det in detected:
        name = det.name.strip()
        if not name:
            continue
        key = name.lower()
        if key in merged:
            current = TrackedEntity.model_validate(merged[key])
            current.mention_count += 1
            if _IMPORTANCE_RANK[det.importance] < _IMPORTANCE_RANK[current.importance]:
                current.importance = det.importance
            if det.context and len(det.context) > len(current.context):
                current.context = det.context
            merged[key] = current.model_dump()
        else:
            merged[key] = TrackedEntity(
                name=name,
                entity_type=det.entity_type,
                importance=det.importance,
                context=det.context or "",
                source_block=source_block,
            ).model_dump()
    return merged


def mark_probed(entities: Dict[str, dict], agent_text: str) -> Dict[str, dict]:
    """Mark entities whose name appears in an agent question as probed."""
    if not entities or not agent_text:
        return entities
    lowered = agent_text.lower()
    updated = dict(entities)
    for key, data in entities.items():
        if not data.get("probed") and key in lowered:
            updated[key] = {**data, "probed": True}
    return updated


def unprobed_entities(entities: Dict[str, dict]) -> List[TrackedEntity]:
    """Unprobed entities, most important (then most mentioned) first."""
    items = [
        TrackedEntity.model_validate(d) for d in entities.values() if not d.get("probed")
    ]
    items.sort(key=lambda e: (_IMPORTANCE_RANK[e.importance], -e.mention_count))
    return items


def entity_context_lines(entities: Dict[str, dict], limit: int = 8) -> List[str]:
    """Render unprobed entities as prompt lines for the primary model."""
    lines = []
    for entity in unprobed_entities(entities)[:limit]:
        note = f" — {entity.context}" if entity.context else ""
        lines.append(
            f"- {entity.name} ({entity.entity_type.value}, importance: {entity.importance.value}){note}"
        )
    return lines
