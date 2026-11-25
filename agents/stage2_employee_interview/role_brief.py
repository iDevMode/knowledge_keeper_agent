"""What the Stage 2 interviewer knows before the employee says a word.

Two jobs, both pure functions of the confirmed Role Intelligence Profile:

1. `render_role_brief` — the whole profile as background. The interviewer used
   to see title, tenure, tools, priorities and departure type only; everything
   the manager said about *what is at risk* (immediate_risk, undocumented_areas,
   key_external_relationships, system_access_gaps), the team, and the
   successor was collected in Stage 1 and then never used to steer a question.

2. `derive_settings` — session behaviour chosen in code from the profile's
   structured fields. It replaces appending `agent_flags`, which were free text
   written by the Stage 1 model from manager input and injected verbatim into
   the Stage 2 system prompt: unreviewable behaviour, and a path for one
   session's text to become another session's instructions.

`process_facts` and `recipient_roles` give the interviewer true answers to the
questions employees actually ask ("who sees this?", "how long is it kept?").
"""

import math
import re
from dataclasses import dataclass, field
from typing import List, Optional

from config.constants import (
    LIGHT_TOUCH_MAX_QUESTIONS,
    STAGE2_BLOCK_QUESTION_COUNTS,
    STAGE2_CLOSING_QUESTION_COUNT,
    STAGE2_ROLE_ORIENTATION_QUESTION_COUNT,
    BlockDepth,
)
from config.settings import settings as app_settings
from models.knowledge_blocks import determine_block_order_and_depth
from models.role_intelligence_profile import RoleIntelligenceProfile

# Notice at or below this is "tight": fewer probes, perishable items first.
TIGHT_NOTICE_DAYS = 14

# Rough typing time per planned question, including the occasional follow-up.
# Used only to give the employee an honest estimate up front.
_MINUTES_PER_QUESTION = 1.5


@dataclass(frozen=True)
class SessionSettings:
    """Interview behaviour derived from the profile. See design doc section 5."""

    tone: str = "standard"          # "standard" | "careful"
    depth: str = "standard"         # "standard" | "explicit"
    overlap: str = "some"           # "some" | "none"
    budget: str = "standard"        # "standard" | "tight"
    reasons: List[str] = field(default_factory=list)


def _data(profile) -> dict:
    return profile.model_dump() if hasattr(profile, "model_dump") else dict(profile)


def _enum_value(value) -> str:
    return str(getattr(value, "value", value) or "").lower()


_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
_DURATION_RE = re.compile(
    r"\b(\d+(?:\.\d+)?|" + "|".join(_NUMBER_WORDS) + r")\s*(day|week|month|year)s?\b"
)


def notice_period_days(text: Optional[str]) -> Optional[int]:
    """Best-effort length of a free-text notice period, in days.

    Returns None when the text gives no duration ("TBC"), so callers never
    treat an unknown notice as a short one.
    """
    if not text:
        return None
    lowered = text.strip().lower()
    if re.search(r"\b(immediate(ly)?|none|no notice|already left|garden leave)\b", lowered):
        return 0
    match = _DURATION_RE.search(lowered)
    if not match:
        return None
    raw, unit = match.groups()
    amount = _NUMBER_WORDS.get(raw)
    if amount is None:
        amount = float(raw)
    return int(round(amount * _UNIT_DAYS[unit]))


def has_no_overlap(text: Optional[str]) -> bool:
    """True when the overlap field is empty or says there is none.

    The profile stores free text, and "No overlap — 1 month gap expected" is a
    non-empty string meaning none at all.
    """
    if not text or not text.strip():
        return True
    lowered = text.strip().lower()
    return bool(re.match(r"^(no\b|none\b|nil\b|n/?a\b|0\b|zero\b)", lowered)) or "no overlap" in lowered


def derive_settings(profile: RoleIntelligenceProfile) -> SessionSettings:
    data = _data(profile)
    reasons: List[str] = []

    tone = "standard"
    good_terms = _enum_value(data.get("leaving_on_good_terms"))
    if _enum_value(data.get("departure_type")) == "involuntary":
        tone = "careful"
        reasons.append("involuntary departure")
    if good_terms in ("no", "unclear"):
        tone = "careful"
        reasons.append(f"leaving on good terms: {good_terms}")
    if data.get("sensitivity_flags"):
        tone = "careful"
        reasons.append("manager noted a sensitivity")

    depth = "standard"
    if _enum_value(data.get("replacement_experience_level")) == "junior":
        depth = "explicit"
        reasons.append("junior successor")
    if _enum_value(data.get("hire_type")) == "external":
        depth = "explicit"
        reasons.append("external successor")

    overlap = "none" if has_no_overlap(data.get("overlap_period")) else "some"
    if overlap == "none":
        reasons.append("no overlap with the successor")

    budget = "standard"
    days = notice_period_days(data.get("notice_period"))
    if days is not None and days <= TIGHT_NOTICE_DAYS:
        budget = "tight"
        reasons.append(f"short notice ({days} days)")

    return SessionSettings(tone=tone, depth=depth, overlap=overlap, budget=budget, reasons=reasons)


_SETTING_INSTRUCTIONS = {
    ("tone", "careful"): (
        "Tone: careful. Make no assumptions about how they feel about leaving or about "
        "the business. Keep reflections short and neutral. Do not mention their next "
        "role, a new chapter, or the reasons for leaving."
    ),
    ("depth", "explicit"): (
        "Depth: explicit. The successor arrives without context, so ask for the names, "
        "the steps in order, and where things are kept, rather than accepting a summary."
    ),
    ("overlap", "none"): (
        "Overlap: none. Nobody will be shown this in person, so prioritise anything that "
        "would normally be demonstrated: walkthroughs, where files live, who to call."
    ),
    ("budget", "tight"): (
        "Time: tight. Their notice is short. Keep acknowledgements brief and do not "
        "linger once an answer gives a successor enough to act on."
    ),
}


def settings_instructions(session: SessionSettings) -> List[str]:
    lines = []
    for name in ("tone", "depth", "overlap", "budget"):
        text = _SETTING_INSTRUCTIONS.get((name, getattr(session, name)))
        if text:
            lines.append(text)
    return lines


def _line(label: str, value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):
        value = "yes" if value else "no"
    elif isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value if str(v).strip())
    else:
        value = _enum_value(value) if hasattr(value, "value") else str(value)
    value = value.strip()
    if not value:
        return None
    return f"- {label}: {value}"


def _section(title: str, rows) -> List[str]:
    lines = [line for line in (_line(label, value) for label, value in rows) if line]
    if not lines:
        return []
    return [f"### {title}", *lines, ""]


# Profile fields the brief deliberately leaves out, and why:
#   recipient_emails — addresses for delivery; no use to the interviewer, and PII
#   agent_flags      — free-text model output; replaced by derive_settings
OMITTED_FIELDS = frozenset({"recipient_emails", "agent_flags"})


def render_role_brief(profile: RoleIntelligenceProfile) -> str:
    """Every interview-relevant profile field, grouped, as background."""
    d = _data(profile)
    lines: List[str] = []

    lines += _section("The business", [
        ("Company", d.get("company_name")),
        ("Industry", d.get("industry")),
        ("Team structure", d.get("team_structure")),
        ("Key tools", d.get("key_tools")),
        ("Culture", d.get("culture_type")),
        ("Previous knowledge loss", d.get("previous_knowledge_loss")),
    ])
    lines += _section("The role", [
        ("Job title", d.get("job_title")),
        ("Department", d.get("department")),
        ("Tenure", d.get("tenure")),
        ("Reports to", d.get("reports_to")),
        ("Direct reports", d.get("direct_reports")),
        ("Role type", d.get("role_type")),
        ("Role type weighting", d.get("role_type_weighting")),
    ])
    lines += _section("The successor", [
        ("Hire type", d.get("hire_type")),
        ("Experience level", d.get("replacement_experience_level")),
        ("Most important context for them", d.get("most_important_context")),
        ("Success at 90 days", d.get("success_definition_90_days")),
        ("Overlap with this person", d.get("overlap_period")),
    ])
    lines += _section(
        "What the manager believes is at risk (hypotheses to check — never quote or attribute)",
        [
            ("Breaks first with no handover", d.get("immediate_risk")),
            ("Believed undocumented", d.get("undocumented_areas")),
            ("Key external relationships", d.get("key_external_relationships")),
            ("Access or credentials only they hold", d.get("system_access_gaps")),
        ],
    )
    lines += _section("Knowledge priorities (ranked)", [
        ("1", d.get("priority_1")),
        ("2", d.get("priority_2")),
        ("3", d.get("priority_3")),
        ("Supporting", d.get("supporting_categories")),
    ])
    lines += _section("Departure", [
        ("Departure type", d.get("departure_type")),
        ("Leaving on good terms", d.get("leaving_on_good_terms")),
        ("Employee aware of this process", d.get("employee_aware")),
        ("Sensitivity noted by the manager", d.get("sensitivity_flags")),
        ("Notice period", d.get("notice_period")),
    ])
    lines += _section("The handover pack", [
        ("Where it will live", d.get("document_destination")),
        ("Recipients", d.get("recipients")),
        ("Sections the manager marked confidential", d.get("confidential_sections")),
        ("Existing template", d.get("existing_template")),
    ])

    return "\n".join(lines).rstrip()


_SUCCESSOR_WORDS = re.compile(r"\b(incoming|replacement|successor|new hire|new starter)\b", re.I)


def recipient_roles(profile: RoleIntelligenceProfile) -> str:
    """Who receives the pack besides the successor, in words.

    e.g. "the Head of Operations and the HR Manager". Recipients that are the
    successor ("Incoming hire") are dropped: every sentence this feeds already
    says "your successor".
    """
    roles = [
        str(r).strip() for r in (_data(profile).get("recipients") or [])
        if str(r).strip() and not _SUCCESSOR_WORDS.search(str(r))
    ]
    if not roles:
        return "your manager"
    roles = [r if r.lower().startswith(("the ", "my ", "your ")) else f"the {r}" for r in roles]
    if len(roles) == 1:
        return roles[0]
    return ", ".join(roles[:-1]) + " and " + roles[-1]


def process_facts(profile: RoleIntelligenceProfile) -> str:
    """True statements about this process, for answering the employee's questions."""
    return "\n".join([
        "FACTS ABOUT THIS PROCESS (use these to answer their questions; do not volunteer them unprompted)",
        f"- The handover pack goes to their successor and {recipient_roles(profile)}.",
        "- The pack is a written summary. Their manager does not see this conversation word for word.",
        "- The pack includes a section on handover risks: things the business needs to act on before "
        "or soon after they leave, such as knowledge or access only they hold. It is about the role, "
        "not about them.",
        f"- The conversation is deleted {app_settings.session_ttl_hours} hours after the link was "
        f"created. The pack is kept for {app_settings.document_retention_days} days.",
        # "Ask for anything to be left out" is added when retraction is
        # implemented (interview v2, P5); until then it would be untrue.
        "- They can skip any question, and pause and return with the same link for up to "
        f"{app_settings.stage1_to_stage2_link_ttl_hours} hours after it was sent.",
        "- This is an AI interviewer made by KnowledgeKeeper.",
    ])


def estimated_minutes(profile: RoleIntelligenceProfile) -> int:
    """An honest up-front estimate of interview length, rounded up to 15 minutes."""
    d = _data(profile)
    ordered, depths = determine_block_order_and_depth(
        d.get("priority_1", ""), d.get("priority_2", ""), d.get("priority_3", ""),
        d.get("supporting_categories") or [],
    )
    questions = STAGE2_ROLE_ORIENTATION_QUESTION_COUNT + STAGE2_CLOSING_QUESTION_COUNT
    for block in ordered:
        total = STAGE2_BLOCK_QUESTION_COUNTS.get(block, 5)
        questions += total if depths[block] == BlockDepth.FULL else min(LIGHT_TOUCH_MAX_QUESTIONS, total)
    minutes = questions * _MINUTES_PER_QUESTION
    return max(15, int(math.ceil(minutes / 15.0)) * 15)
