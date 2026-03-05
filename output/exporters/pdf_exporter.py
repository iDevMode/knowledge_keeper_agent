import logging
import os
import re

from output.formatters.document_formatter import CONFIDENTIAL_PLACEHOLDER, InterimDocument

logger = logging.getLogger(__name__)

try:
    from weasyprint import HTML as WeasyHTML
    WEASYPRINT_AVAILABLE = True
except (ImportError, OSError):
    WeasyHTML = None
    WEASYPRINT_AVAILABLE = False


CSS_STYLES = """\
body {
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #333;
    max-width: 800px;
    margin: 0 auto;
    padding: 40px;
}
h1 {
    font-size: 22pt;
    color: #1a1a1a;
    border-bottom: 2px solid #333;
    padding-bottom: 8px;
    margin-top: 30px;
}
h2 {
    font-size: 16pt;
    color: #2a2a2a;
    margin-top: 24px;
}
h3 {
    font-size: 13pt;
    color: #3a3a3a;
    margin-top: 18px;
}
.subtitle {
    font-size: 12pt;
    color: #666;
    margin-bottom: 20px;
}
.confidential {
    background-color: #f5f5f5;
    border-left: 4px solid #999;
    padding: 12px 16px;
    font-style: italic;
    color: #666;
}
.confidentiality-notice {
    background-color: #fff3cd;
    border: 1px solid #ffc107;
    padding: 12px 16px;
    margin-bottom: 20px;
    font-style: italic;
}
.gap {
    background-color: #fff3cd;
    border-left: 4px solid #ffc107;
    padding: 8px 12px;
    margin: 8px 0;
    font-weight: bold;
}
ul {
    margin: 8px 0;
    padding-left: 24px;
}
li {
    margin-bottom: 4px;
}
"""


def generate_pdf(document: InterimDocument, output_path: str) -> str:
    """Write a .pdf file from an InterimDocument.

    Returns the absolute path of the written file.

    Raises:
        RuntimeError: If WeasyPrint is not installed.
    """
    if not WEASYPRINT_AVAILABLE:
        raise RuntimeError(
            "WeasyPrint is not installed. PDF export requires WeasyPrint and its "
            "system-level dependencies (cairo, pango, etc.). "
            "Install with: pip install weasyprint"
        )

    html_string = _build_html(document)
    WeasyHTML(string=html_string).write_pdf(output_path)

    abs_path = os.path.abspath(output_path)
    logger.info("session=%s pdf written to %s", document.session_id, abs_path)
    return abs_path


def _build_html(document: InterimDocument) -> str:
    """Build a complete HTML string for the document."""
    parts = [
        "<!DOCTYPE html>",
        "<html><head>",
        "<meta charset='utf-8'>",
        f"<title>{_escape_html(document.title)}</title>",
        f"<style>{CSS_STYLES}</style>",
        "</head><body>",
        f"<h1>{_escape_html(document.title)}</h1>",
        f"<p class='subtitle'>{_escape_html(document.subtitle)}</p>",
    ]

    if document.confidential_sections_note:
        parts.append(
            f"<div class='confidentiality-notice'>Confidentiality Notice: "
            f"Some sections have been redacted. Flagged areas: "
            f"{_escape_html(document.confidential_sections_note)}</div>"
        )

    for section in document.sections:
        if section.is_confidential:
            parts.append(f"<h1>{_escape_html(section.heading)} [CONFIDENTIAL]</h1>")
            parts.append(f"<div class='confidential'>{_escape_html(CONFIDENTIAL_PLACEHOLDER)}</div>")
        else:
            parts.append(f"<h1>{_escape_html(section.heading)}</h1>")
            parts.append(_markdown_to_html(section.content_markdown))

    parts.append("</body></html>")
    return "\n".join(parts)


def _markdown_to_html(markdown_text: str) -> str:
    """Convert basic markdown to HTML fragment."""
    lines = markdown_text.split("\n")
    html_parts = []
    in_list = False

    for line in lines:
        stripped = line.strip()

        if not stripped:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            continue

        # Headings
        if stripped.startswith("### "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<h3>{_escape_html(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<h2>{_escape_html(stripped[3:])}</h2>")

        # Bullet points
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{_escape_html(stripped[2:])}</li>")

        # Gap markers
        elif stripped.startswith("[GAP:"):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            gap_text = stripped.strip("[]")
            if gap_text.startswith("GAP:"):
                gap_text = gap_text[4:].strip()
            html_parts.append(f"<p class='gap'>KNOWLEDGE GAP: {_escape_html(gap_text)}</p>")

        # Bold lines
        elif stripped.startswith("**") and stripped.endswith("**"):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<p><strong>{_escape_html(stripped.strip('*').strip())}</strong></p>")

        # Normal text
        else:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<p>{_escape_html(stripped)}</p>")

    if in_list:
        html_parts.append("</ul>")

    return "\n".join(html_parts)


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
