import re
from typing import List

from pydantic import BaseModel

from config.constants import RiskFlagType, Severity


class RiskFlag(BaseModel):
    flag_type: RiskFlagType
    severity: Severity
    description: str
    recommended_action: str
    source_block: str
    source_question_index: int


_SEVERITY_RANK = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2}

_STOPWORDS = {
    "the", "a", "an", "of", "to", "and", "or", "is", "are", "in", "on", "for",
    "only", "person", "who", "that", "this", "with", "has", "have", "no", "one",
}


def _signature(text: str) -> frozenset:
    """A bag of significant words for fuzzy duplicate detection."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return frozenset(w for w in words if w not in _STOPWORDS and len(w) > 2)


def _looks_like_duplicate(a: RiskFlag, b: RiskFlag) -> bool:
    """Two flags of the same type describing substantially the same thing."""
    if a.flag_type != b.flag_type:
        return False
    sig_a, sig_b = _signature(a.description), _signature(b.description)
    if not sig_a or not sig_b:
        return False
    overlap = len(sig_a & sig_b)
    smaller = min(len(sig_a), len(sig_b))
    # >= 60% of the smaller flag's significant words shared → same underlying risk
    return smaller > 0 and overlap / smaller >= 0.6


def merge_risk_flags(flags: List[RiskFlag]) -> List[RiskFlag]:
    """Deduplicate risk flags that describe the same underlying risk.

    The same single-point-of-failure often surfaces in three different blocks;
    without merging, the Risk Summary repeats it three times. Flags are merged
    by type and description similarity; the merged flag keeps the highest
    severity and the union of source blocks in its description. Order of
    first appearance (and severity) is preserved.
    """
    merged: List[RiskFlag] = []
    sources: List[set] = []

    for flag in flags:
        for i, existing in enumerate(merged):
            if _looks_like_duplicate(existing, flag):
                if _SEVERITY_RANK[flag.severity] < _SEVERITY_RANK[existing.severity]:
                    existing.severity = flag.severity
                sources[i].add(flag.source_block)
                # Keep the longer, more specific description / action.
                if len(flag.description) > len(existing.description):
                    existing.description = flag.description
                if len(flag.recommended_action) > len(existing.recommended_action):
                    existing.recommended_action = flag.recommended_action
                break
        else:
            merged.append(flag.model_copy(deep=True))
            sources.append({flag.source_block})

    # Annotate flags that were merged from multiple blocks.
    for flag, block_set in zip(merged, sources):
        if len(block_set) > 1:
            blocks = ", ".join(sorted(block_set))
            flag.source_block = f"multiple ({blocks})"

    merged.sort(key=lambda f: _SEVERITY_RANK[f.severity])
    return merged
