"""Tolerant JSON extraction for classifier responses.

Classifier nodes ask Haiku for a bare JSON object or array, but models routinely
wrap output in markdown code fences or prepend a sentence. json.loads() on the
raw string then raises, and every caller treats that as "no follow-up needed" /
"no risk flags found" — silently degrading the product with nothing but a debug
log to show for it.

These helpers recover the payload from the common wrappings, and distinguish a
parse failure from a genuine negative result so callers can log them differently.
"""

import json
import re
from typing import Any

# ```json ... ``` or ``` ... ```
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


class ClassifierParseError(ValueError):
    """Raised when a classifier response cannot be parsed as JSON.

    Distinct from a successfully parsed negative result, so callers can tell
    "the model said no" apart from "we could not read the model's answer".
    """


def _first_balanced_span(text: str, open_ch: str, close_ch: str) -> str | None:
    """Return the first balanced {...} or [...] span, ignoring braces in strings."""
    start = text.find(open_ch)
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False

    for i in range(start, len(text)):
        ch = text[i]

        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue

        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return None


def extract_json(raw: str) -> Any:
    """Parse JSON from a classifier response, tolerating fences and preamble.

    Raises:
        ClassifierParseError: if no JSON value can be recovered.
    """
    if raw is None:
        raise ClassifierParseError("classifier returned no content")

    text = str(raw).strip()
    if not text:
        raise ClassifierParseError("classifier returned an empty response")

    # 1. Straight parse — the expected case.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Unwrap a markdown code fence and retry.
    fence_match = _FENCE_RE.search(text)
    if fence_match:
        inner = fence_match.group(1).strip()
        try:
            return json.loads(inner)
        except json.JSONDecodeError:
            text = inner  # fall through to span extraction on the unwrapped body

    # 3. Extract the first balanced object or array from surrounding prose.
    #    Whichever appears first in the text wins, so an object containing an
    #    array (or vice versa) is not truncated.
    candidates = []
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        span = _first_balanced_span(text, open_ch, close_ch)
        if span:
            candidates.append((text.find(span), span))

    for _, span in sorted(candidates):
        try:
            return json.loads(span)
        except json.JSONDecodeError:
            continue

    raise ClassifierParseError(
        f"could not extract JSON from classifier response: {text[:200]!r}"
    )
