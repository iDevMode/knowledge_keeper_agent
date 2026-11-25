"""Remove secrets from interview messages before they are stored.

The systems block asks which credentials only the employee holds, and people
answer that by typing the password. Without this, it went into the LangGraph
checkpoint, the answer store, the Stage 3 prompt and the handover pack emailed
as a Word file. Redaction runs on the raw message before it enters graph state,
so nothing downstream ever sees the value.

What is caught:
  * "password/PIN/passcode/secret/API key/token/security answer is|:|= VALUE"
    where VALUE looks like a credential (has a digit, a symbol or mixed case) —
    so "the password is in the safe" is left alone;
  * PEM private-key blocks;
  * well-known token formats (AWS access keys, GitHub, Slack, OpenAI/Anthropic
    style "sk-" keys, JWTs);
  * long, high-entropy strings mixing upper case, lower case and digits.

Deliberately conservative: a missed secret is bad, but so is mangling an
ordinary answer the successor needs. The v2 turn analyst adds a model-based
check on top (docs/interview-agent-v2/02-agent-design.md, section 7).
"""

import math
import re
from collections import Counter
from dataclasses import dataclass

REDACTED = "[removed]"

REDACTION_NOTICE = (
    "I've removed what looked like a password or key from the record, so it won't appear in "
    "the handover pack. It's worth changing it, since it was typed here."
)

_LABEL = (
    r"(?:pass(?:word|wd|code|phrase)?|pwd|pin(?:\s*(?:code|number))?|secret|"
    r"api[\s_-]?key|access[\s_-]?key|secret[\s_-]?key|private[\s_-]?key|"
    r"(?:auth|access|api|bearer)?[\s_-]?token|security\s+answer|memorable\s+word)"
)
# "password is X", "password: X", "password = X", "password was X", "pin for the alarm is X"
_LABELLED_RE = re.compile(
    rf"(?P<label>\b{_LABEL}\b(?:\s+(?:for|to|on)\s+(?:the\s+)?[\w\- ]{{1,30}}?)?\s*(?:is|was|=|:|-)\s*)"
    rf"(?P<quote>[\"'`]?)(?P<value>[^\s\"'`]{{3,}})(?P=quote)",
    re.IGNORECASE,
)

_PEM_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
    re.DOTALL,
)

_TOKEN_PATTERNS = [
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                          # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),                 # GitHub
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),              # Slack
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),                     # sk- style API keys
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"),  # JWT
]

_CANDIDATE_RE = re.compile(r"[A-Za-z0-9+/=_\-!@#$%^&*]{20,}")
_MIN_ENTROPY_BITS_PER_CHAR = 3.5


@dataclass(frozen=True)
class RedactionResult:
    text: str
    count: int

    @property
    def redacted(self) -> bool:
        return self.count > 0


# Labels whose value is a secret whatever it looks like: the answer to a
# security question or a memorable word is usually an ordinary word.
_ALWAYS_SECRET_LABEL = re.compile(r"security\s+answer|memorable\s+word", re.IGNORECASE)


def _looks_like_credential(value: str) -> bool:
    """A digit, a symbol, or a capital after the first letter.

    A plain word ("in", "kept", "Monday") is far more often the start of a
    sentence about the password than the password itself.
    """
    has_digit = any(c.isdigit() for c in value)
    has_symbol = any(not c.isalnum() for c in value)
    inner_capital = any(c.isupper() for c in value[1:]) and any(c.islower() for c in value)
    return has_digit or has_symbol or inner_capital


def _entropy(value: str) -> float:
    counts = Counter(value)
    total = len(value)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def _looks_random(token: str) -> bool:
    if "/" in token or "@" in token or token.lower().startswith(("http", "www")):
        return False  # paths, URLs and emails are things a successor needs
    classes = sum([
        any(c.islower() for c in token),
        any(c.isupper() for c in token),
        any(c.isdigit() for c in token),
    ])
    return classes == 3 and _entropy(token) >= _MIN_ENTROPY_BITS_PER_CHAR


def redact_secrets(text: str) -> RedactionResult:
    """Return `text` with anything that looks like a secret replaced."""
    if not text:
        return RedactionResult(text or "", 0)

    count = 0

    def _count(replacement):
        def inner(match):
            nonlocal count
            count += 1
            return replacement(match) if callable(replacement) else replacement
        return inner

    text = _PEM_RE.sub(_count(REDACTED), text)

    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub(_count(REDACTED), text)

    def labelled(match):
        nonlocal count
        # Sentence punctuation after the value is kept; "!" is not stripped
        # because it is as likely to be the last character of the password.
        value = match.group("value").rstrip(".,;:?)")
        trailing = match.group("value")[len(value):]
        always = bool(_ALWAYS_SECRET_LABEL.search(match.group("label")))
        if value == REDACTED or not (always or _looks_like_credential(value)):
            return match.group(0)
        count += 1
        return f"{match.group('label')}{REDACTED}{trailing}"

    text = _LABELLED_RE.sub(labelled, text)

    def random_token(match):
        nonlocal count
        token = match.group(0)
        if not _looks_random(token):
            return token
        count += 1
        return REDACTED

    text = _CANDIDATE_RE.sub(random_token, text)

    return RedactionResult(text, count)
