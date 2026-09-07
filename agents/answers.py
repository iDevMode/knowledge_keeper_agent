"""The interview answer store, and why one question holds several answers.

Both stages file answers under a `"{block}.{index}"` key. The follow-up loop
routes `followup_question -> process_answer` with the *same* block and index, so
a single key legitimately collects an exchange: the original answer, then one
entry per follow-up. Storing a bare string there meant each follow-up overwrote
what came before it, and Stage 3 was handed the last fragment of every exchange
instead of the substance — losing exactly the depth the follow-up mechanism
exists to produce. CLAUDE.md specifies `Dict[str, List[str]]`; these helpers are
where that shape is maintained.

`coerce()` accepts the old string-valued shape as well as the current one. A
checkpoint written before this change can still be resumed and still generates a
document, rather than an in-flight interview dying on a deploy.
"""

from typing import Any, Dict, List, Mapping


def coerce(answers: Mapping[str, Any] | None) -> Dict[str, List[str]]:
    """Return the answer store in its list-valued shape.

    A string value is wrapped rather than iterated — `list("abc")` would explode
    a legacy answer into single characters, which is the one failure mode a
    tolerant reader must not have.
    """
    if not answers:
        return {}

    coerced: Dict[str, List[str]] = {}
    for key, value in answers.items():
        if isinstance(value, str):
            coerced[key] = [value]
        elif isinstance(value, (list, tuple)):
            coerced[key] = [str(item) for item in value]
        else:
            coerced[key] = [str(value)]
    return coerced


def append(answers: Mapping[str, Any] | None, key: str, text: str) -> Dict[str, List[str]]:
    """Append one answer to `key`, returning a new store.

    Returns a copy because LangGraph state updates must not mutate the state
    they were derived from.
    """
    updated = coerce(answers)
    updated.setdefault(key, []).append(text)
    return updated


def first(answers: Mapping[str, Any] | None, key: str) -> str:
    """The original answer to a question, ignoring any follow-up answers."""
    values = coerce(answers).get(key, [])
    return values[0] if values else ""


def joined(answers: Mapping[str, Any] | None, key: str, separator: str = " ") -> str:
    """The whole exchange for a question as one string."""
    return separator.join(coerce(answers).get(key, []))
