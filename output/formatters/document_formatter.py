import re
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

from models.role_intelligence_profile import RoleIntelligenceProfile


CONFIDENTIAL_PLACEHOLDER = (
    "[This section has been marked confidential and is not included "
    "in this version of the document.]"
)


@dataclass
class DocumentSection:
    name: str
    heading: str
    content_markdown: str
    is_confidential: bool = False
    section_number: int = 0


@dataclass
class InterimDocument:
    session_id: str
    title: str
    subtitle: str
    sections: List[DocumentSection] = field(default_factory=list)
    has_risk_flags: bool = False
    confidential_sections_note: Optional[str] = None


def parse_llm_output(
    raw_markdown: str,
    profile: RoleIntelligenceProfile,
    session_id: str,
) -> InterimDocument:
    """Split raw LLM markdown on sentinel markers and build InterimDocument."""
    data = profile.model_dump() if hasattr(profile, "model_dump") else profile

    # Split on sentinel markers
    chunks = re.split(r"###\s+SECTION:\s+", raw_markdown)

    sections = []
    for i, chunk in enumerate(chunks):
        chunk = chunk.strip()
        if not chunk:
            continue

        # First line is the section name, rest is content
        lines = chunk.split("\n", 1)
        section_name = lines[0].strip()
        content = lines[1].strip() if len(lines) > 1 else ""

        sections.append(DocumentSection(
            name=section_name,
            heading=section_name,
            content_markdown=content,
            section_number=len(sections) + 1,
        ))

    # Apply confidentiality filter
    confidential_text = data.get("confidential_sections")
    if confidential_text:
        sections = _apply_confidentiality_filter(sections, confidential_text)

    # Check for risk flags
    has_flags = any(s.name == "Risk Summary" and s.content_markdown.strip() for s in sections)

    title = f"Handover Document — {data.get('job_title', 'Unknown Role')}"
    company = data.get("company_name") or data.get("industry", "")
    subtitle = f"{company} | Generated {date.today().strftime('%d %B %Y')}"

    return InterimDocument(
        session_id=session_id,
        title=title,
        subtitle=subtitle,
        sections=sections,
        has_risk_flags=has_flags,
        confidential_sections_note=confidential_text,
    )


def _apply_confidentiality_filter(
    sections: List[DocumentSection],
    confidential_sections_text: str,
) -> List[DocumentSection]:
    """Deterministically redact confidential content (plan 3.2).

    Redaction is enforced in code, not left to the generation prompt:

    - If a confidential keyword matches the section *name/heading*, the whole
      section is a designated confidential section and is fully redacted.
    - Otherwise redaction is *paragraph-level*: only paragraphs that mention a
      confidential keyword are replaced, so a single sensitive detail no longer
      forces the entire (otherwise useful) section to be discarded.
    """
    keywords = re.split(r"[,;]|\band\b", confidential_sections_text.lower())
    keywords = [kw.strip() for kw in keywords if kw.strip()]
    if not keywords:
        return sections

    for section in sections:
        name_lower = section.name.lower()

        if any(kw in name_lower for kw in keywords):
            section.is_confidential = True
            section.content_markdown = CONFIDENTIAL_PLACEHOLDER
            continue

        # Paragraph-level redaction within an otherwise-retained section.
        paragraphs = section.content_markdown.split("\n\n")
        redacted_any = False
        new_paragraphs = []
        for para in paragraphs:
            if any(kw in para.lower() for kw in keywords):
                new_paragraphs.append(CONFIDENTIAL_PLACEHOLDER)
                redacted_any = True
            else:
                new_paragraphs.append(para)
        if redacted_any:
            section.is_confidential = True
            section.content_markdown = "\n\n".join(new_paragraphs)

    return sections
