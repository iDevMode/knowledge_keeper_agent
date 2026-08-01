"""Shared agent output helpers.

CLAUDE.md Core Principle 3 makes "one question at a time" a hard constraint
enforced at the node level, not merely requested in the prompt. Both interview
stages need identical enforcement, so it lives here rather than being duplicated.
"""


def validate_single_question(text: str) -> bool:
    """Check that the text contains at most one question mark.

    Returns True if valid (0 or 1 question marks).
    """
    return text.count("?") <= 1


def enforce_single_question(text: str) -> str:
    """Truncate the text to end at its first question.

    Used as the last-resort fallback when the model has already been re-prompted
    once and still returned multiple questions. Everything up to and including
    the first "?" is kept, which preserves the leading acknowledgement and the
    first question while discarding the extras — the constraint is honoured
    rather than quietly violated.

    Text with no question mark is returned unchanged.
    """
    first_mark = text.find("?")
    if first_mark == -1:
        return text
    return text[: first_mark + 1].strip()
