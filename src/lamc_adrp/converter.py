"""Stages 4-5: document structuring and accessible HTML generation."""

from __future__ import annotations

import logging
import posixpath
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

from lamc_adrp.config import PipelineConfig
from lamc_adrp.database import DatabaseManager
from lamc_adrp.models import DocumentJob, JobStatus, RenderedPage
from lamc_adrp.zai_client import ZAIClient, ZAIClientError

logger = logging.getLogger(__name__)


class ConversionError(Exception):
    """Raised when HTML planning or generation fails."""


@dataclass
class _MarkdownBlock:
    """A paragraph-like source block with optional source-page context."""

    text: str
    page_number: int | None = None


@dataclass
class _HeadingMatch:
    """Heading metadata extracted from a markdown block."""

    title: str
    level: int
    consumed_text: str


@dataclass
class _StructuredSection:
    """A major document section used for chunked HTML generation."""

    page_key: str
    title: str
    anchor: str
    heading_level: int
    body_markdown: str
    full_markdown: str
    page_start: int | None = None
    page_end: int | None = None

    @property
    def source_page_range(self) -> str:
        """Return a human-readable source page range."""
        if self.page_start is None and self.page_end is None:
            return ""
        if self.page_start == self.page_end or self.page_end is None:
            return f"Source page {self.page_start}"
        return f"Source pages {self.page_start}-{self.page_end}"


@dataclass
class _StructuredDocument:
    """Structured representation of extracted markdown."""

    title: str
    front_matter_markdown: str
    sections: list[_StructuredSection]
    page_count: int
    is_long_document: bool


# ======================================================================
# LAMC HTML Template
# ======================================================================

LAMC_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — Los Angeles Mission College</title>
  <link rel="stylesheet" href="/assets/css/lamc-accessible.css">
  <style>
    /* Inline fallback styles for accessibility if external CSS fails to load */
    body {{
      font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      font-size: 1rem;
      line-height: 1.6;
      color: #1A1A2E;
      background: #FFFFFF;
      margin: 0;
      padding: 0;
    }}
    .skip-nav {{
      position: absolute;
      top: -100%;
      left: 0;
      background: #004590;
      color: #FFFFFF;
      padding: 0.75rem 1.5rem;
      z-index: 10000;
      font-weight: bold;
      text-decoration: none;
    }}
    .skip-nav:focus {{
      top: 0;
    }}
    header {{
      background: #004590;
      color: #FFFFFF;
      padding: 1rem 2rem;
    }}
    header a {{ color: #FFFFFF; text-decoration: underline; }}
    main {{
      max-width: 72ch;
      margin: 2rem auto;
      padding: 0 1.5rem;
    }}
    footer {{
      background: #F5F7FA;
      border-top: 3px solid #004590;
      padding: 1.5rem 2rem;
      margin-top: 3rem;
      font-size: 0.9rem;
      color: #5A6670;
    }}
    footer a {{ color: #004590; }}
    a {{ color: #004590; text-decoration: underline; }}
    a:focus {{
      outline: 3px solid #FF611A;
      outline-offset: 2px;
    }}
    h1 {{ color: #004590; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1.5rem 0; }}
    th, td {{ border: 1px solid #718089; padding: 0.5rem 0.75rem; text-align: left; }}
    th {{ background: #004590; color: #FFFFFF; }}
    img {{ max-width: 100%; height: auto; }}
    .no-print {{ }}
  </style>
</head>
<body>
  <a href="#main-content" class="skip-nav">Skip to main content</a>

  <header>
    <nav aria-label="Institution">
      <strong>Los Angeles Mission College</strong>
      &nbsp;|&nbsp;
      <span>Los Angeles Community College District</span>
    </nav>
    <p>{title}</p>
  </header>

  <main id="main-content">
{content}
  </main>

  <footer>
    <p>
      <strong>Accessibility Statement:</strong>
      Los Angeles Mission College is committed to making its web content
      accessible to all users. This page was generated from a document
      originally published in {file_type} format and has been converted
      to accessible HTML in compliance with WCAG 2.1 Level AA.
    </p>
    <p>
      <a href="{original_url}">Download the original document ({file_type})</a>
      <span aria-hidden="true"> | </span>
      <a href="https://www.lamission.edu/accessibility">Accessibility Information</a>
      <span aria-hidden="true"> | </span>
      <a href="mailto:accessibility@lamission.edu">Report an Accessibility Issue</a>
    </p>
    <p>
      Los Angeles Mission College &bull;
      13356 Eldridge Avenue, Sylmar, CA 91342 &bull;
      (818) 364-7600
    </p>
  </footer>
</body>
</html>"""


# ======================================================================
# WCAG requirements and prompts
# ======================================================================

_WCAG_REQUIREMENTS = """\
You MUST follow ALL of these WCAG 2.1 Level AA requirements when generating HTML:

SEMANTIC STRUCTURE:
- Use landmark elements: <header>, <nav>, <main>, <article>, <section>, <footer>.
- Exactly ONE <h1> per page. Logical <h2>-<h6> nesting that reflects document hierarchy.
- No consecutive headings without intervening content between them.
- No skipped heading levels (e.g., do NOT go from <h2> directly to <h4>).
- All content must be inside a landmark region. No orphaned text outside semantic containers.

TEXT ALTERNATIVES:
- Every meaningful image gets a descriptive alt attribute.
- Do NOT prefix alt text with "image of...", "picture of...", or "photo of...".
- Alt text must NOT duplicate an adjacent caption or link text.
- Purely decorative images get alt="" and role="presentation".

TABLES:
- Data tables must have <thead> with <th scope="col"> or <th scope="row">.
- Every data table must have a <caption> describing its purpose.
- Do NOT use tables for layout. No empty <th> elements.

CONTRAST AND COLOR:
- Text contrast ratio must be at least 4.5:1 (normal text) and 3:1 (large text, >=18pt or >=14pt bold).
- Color must NEVER be the sole means of conveying information.

KEYBOARD AND INTERACTION:
- All interactive elements must be keyboard navigable (use native HTML elements).
- Visible focus indicators on all focusable elements.
- Tap/click targets must be at least 44x44 CSS pixels.

LANGUAGE:
- <html lang="en"> on the document root.
- Use lang attribute on any content in a language other than English.

RESPONSIVE DESIGN:
- Content must reflow at 320px viewport width without loss.
- No horizontal scrolling at 400% zoom.

LINKS:
- All links must have unique, descriptive text that makes sense in context.
- Do NOT use "click here", "read more", "learn more" as link text.
- Do not have multiple links with identical text pointing to different destinations.
- External links must have a visible indicator or aria-label noting they open externally.
- All links must use <a href="..."> with a real URL, never JavaScript-only navigation.

FORMS:
- Every form input must have an associated <label>.
- Error messages must be descriptive and programmatically associated with the input.

ARIA:
- Use ARIA attributes ONLY when native HTML semantics are insufficient.
- Do NOT add redundant ARIA roles to semantic elements.

SKIP NAVIGATION:
- "Skip to main content" must be the first focusable element on the page.

TEXT FORMATTING:
- Do NOT use text-align: justify (causes uneven word spacing, harms readability for dyslexic users).
- Use <em> and <strong> instead of <i> and <b> for emphasis.
- Paragraph text must be at least 1rem (16px).
"""

_PLANNING_SYSTEM_PROMPT = """\
You are an expert document accessibility analyst. Your job is to analyse \
the extracted content of a document and produce a detailed conversion plan \
for turning it into a WCAG 2.1 Level AA compliant HTML page.

Analyse the document and produce a structured plan covering:

1. **Document Purpose & Audience** — What kind of document is this? \
(schedule, form, report, flyer, catalog, etc.) Who is the intended audience?

2. **Heading Hierarchy** — Propose the heading structure (H1-H6). \
The H1 should be the document title. Map existing sections to heading levels.

3. **Table Identification** — List all tables found, describe their purpose, \
and specify whether each is a data table (needs thead/th/caption) or a \
layout table (should be converted to non-table HTML).

4. **Images Requiring Vision Processing** — List images that need alt text \
generation via GLM-4.6V. Note which are decorative (alt="").

5. **Complex Visuals** — Identify charts, diagrams, infographics, or other \
visuals that should be recreated as accessible SVG/HTML with data tables.

6. **Reading Order** — Describe the logical reading order, especially for \
multi-column layouts or non-linear documents.

7. **Landmark Regions** — Map content to HTML landmarks: header, nav, main, \
article, section, aside, footer.

8. **Content Flags** — Note any content that may be difficult to convert \
accurately, requires manual review, or has special formatting needs.

9. **Links & Navigation** — Identify links that need descriptive text \
improvements and any navigation structures.

10. **Language** — Note any non-English content that needs lang attributes.

Output your plan as a structured Markdown document with the numbered sections above.
"""

_PLANNING_USER_TEMPLATE = """\
Document type: {file_type}
Original URL: {url}
Link text on referring page: {link_text}
Context around the link: {link_context}

--- EXTRACTED CONTENT ---
{content}
--- END CONTENT ---

Analyse this document and produce a detailed conversion plan.
"""

_FRONT_MATTER_SYSTEM_PROMPT = """\
You are an expert HTML accessibility engineer. Convert the provided document front matter \
into semantic HTML that will be inserted inside a page's <main> element.

""" + _WCAG_REQUIREMENTS + """

CRITICAL INSTRUCTIONS:
- Output ONLY the HTML fragment for the front matter.
- Do NOT include <!DOCTYPE>, <html>, <head>, <body>, <header>, <footer>, <main>, or skip links.
- Include exactly one <h1> for the document title.
- Preserve ALL content from the supplied chunk. Do not summarise or omit.
- Print controls, accordion controls, breadcrumbs, and section navigation are added elsewhere. Do not add them.
- Do NOT say content continues elsewhere, refer the reader to the original document, or describe the source as incomplete unless that text explicitly appears in the source chunk.
- For tables, always include <caption>, <thead>, and proper <th scope>.
- Use clean semantic HTML and avoid inline styles unless they are essential for accessibility.
"""

_FRONT_MATTER_USER_TEMPLATE = """\
CONVERSION PLAN:
{plan}

Document title: {title}
Document type: {file_type}
Original URL: {url}
Link text: {link_text}
Context: {link_context}

--- FRONT MATTER SOURCE ---
{content}
--- END FRONT MATTER SOURCE ---

Generate the accessible HTML fragment for this front matter only.
"""

_SECTION_BODY_SYSTEM_PROMPT = """\
You are an expert HTML accessibility engineer. Convert a single major document section \
into semantic HTML that will be inserted inside an existing section wrapper.

""" + _WCAG_REQUIREMENTS + """

CRITICAL INSTRUCTIONS:
- Output ONLY the section body fragment.
- Do NOT include <!DOCTYPE>, <html>, <head>, <body>, <header>, <footer>, <main>, or skip links.
- Do NOT include the outer <section> wrapper or repeat the major section heading; that wrapper and heading are supplied separately.
- Preserve ALL content from the supplied section body. Do not summarise or omit anything.
- The major section heading already exists as an <h2>; nested headings in your response must start at <h3> or lower.
- Do NOT say content continues elsewhere, refer the reader to the original document, or describe the source as incomplete unless that text explicitly appears in the source chunk.
- For tables, always include <caption>, <thead>, and proper <th scope>.
- Use clean semantic HTML and avoid inline styles unless they are essential for accessibility.
"""

_SECTION_BODY_USER_TEMPLATE = """\
CONVERSION PLAN:
{plan}

Document title: {title}
Document type: {file_type}
Original URL: {url}
Link text: {link_text}
Context: {link_context}

Major section title: {section_title}
Source page range: {page_range}

--- SECTION SOURCE ---
{content}
--- END SECTION SOURCE ---

Generate the accessible HTML fragment for the body of this major section only.
"""

_PLACEHOLDER_LANGUAGE_RE = re.compile(
    r"(content|map)\s+continues\s+in\s+full\s+document|"
    r"refer\s+(?:the\s+reader\s+)?to\s+the\s+original\s+document|"
    r"source\s+appears\s+to\s+be\s+incomplete|"
    r"complete\s+document\s+including",
    re.IGNORECASE,
)
_PAGE_MARKER_RE = re.compile(r"<!--\s*Page\s+(\d+)\s*-->", re.IGNORECASE)
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_ROMAN_HEADING_RE = re.compile(
    r"^(?:[IVXLCDM]{1,8}|[A-Z]|[0-9]{1,2})[.)]?\s+.+$",
    re.IGNORECASE,
)
_ARTICLE_HEADING_RE = re.compile(
    r"^(article|appendix|chapter|part)\s+[\w.-]+\b.*$",
    re.IGNORECASE,
)
_SECTION_HEADING_RE = re.compile(r"^section\s+[\w.-]+\b.*$", re.IGNORECASE)


class HTMLConverter:
    """Converts extracted document content into full-content HTML pages."""

    def __init__(
        self,
        config: PipelineConfig,
        zai: ZAIClient,
        db: DatabaseManager,
    ) -> None:
        self._config = config
        self._zai = zai
        self._db = db

    async def convert(self, job: DocumentJob) -> str:
        """Run the full plan-then-generate pipeline for a document job."""
        await self.plan(job)
        return await self.generate(job)

    async def plan(self, job: DocumentJob) -> str:
        """Stage 4: analyse extracted content and produce a conversion plan."""
        if not job.ocr_markdown:
            raise ConversionError(
                f"Job {job.id} has no extracted content (ocr_markdown is empty)."
            )

        job.status = JobStatus.PLANNING
        await self._db.update_job(job)
        logger.info("Planning conversion for job %s", job.id)

        try:
            user_content = _PLANNING_USER_TEMPLATE.format(
                file_type=job.file_type.value if job.file_type else "unknown",
                url=job.url,
                link_text=job.link_text or "(not available)",
                link_context=job.link_context or "(not available)",
                content=job.ocr_markdown,
            )

            plan = await self._zai.chat(
                messages=[
                    {"role": "system", "content": _PLANNING_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                model="glm-5",
                thinking=True,
                max_tokens=8192,
                temperature=0.3,
            )

            if not plan.strip():
                raise ConversionError("GLM-5 returned an empty plan.")

            job.html_plan = plan
            job.status = JobStatus.PLANNED
            await self._db.update_job(job)
            logger.info(
                "Planning complete for job %s — %d chars of plan",
                job.id,
                len(plan),
            )
            return plan

        except ZAIClientError as exc:
            error_msg = f"Planning failed: {exc}"
            logger.error("Job %s: %s", job.id, error_msg)
            job.status = JobStatus.FAILED
            job.error_message = error_msg
            await self._db.update_job(job)
            raise ConversionError(error_msg) from exc

    async def generate(self, job: DocumentJob) -> str:
        """Stage 5: generate canonical and companion HTML artifacts."""
        if not job.html_plan:
            raise ConversionError(
                f"Job {job.id} has no conversion plan (html_plan is empty). "
                "Run plan() first."
            )
        if not job.ocr_markdown:
            raise ConversionError(
                f"Job {job.id} has no extracted content (ocr_markdown is empty)."
            )

        job.status = JobStatus.CONVERTING
        await self._db.update_job(job)
        logger.info("Generating HTML for job %s", job.id)

        try:
            title = self._derive_title(job)
            structured = self._structure_document(job.ocr_markdown, title)
            front_html = await self._generate_front_matter(job, structured)

            section_bodies: dict[str, str] = {}
            for section in structured.sections:
                section_bodies[section.page_key] = await self._generate_section_body(
                    job,
                    structured,
                    section,
                )

            rendered_pages = self._assemble_rendered_pages(
                job,
                structured,
                front_html,
                section_bodies,
            )

            job.set_rendered_pages(rendered_pages)
            job.status = JobStatus.CONVERTED
            await self._db.update_job(job)
            logger.info(
                "HTML generation complete for job %s — %d rendered page(s)",
                job.id,
                len(rendered_pages),
            )
            return job.generated_html

        except ZAIClientError as exc:
            error_msg = f"HTML generation failed: {exc}"
            logger.error("Job %s: %s", job.id, error_msg)
            job.status = JobStatus.FAILED
            job.error_message = error_msg
            await self._db.update_job(job)
            raise ConversionError(error_msg) from exc

    # ------------------------------------------------------------------
    # Structured document parsing
    # ------------------------------------------------------------------

    def _structure_document(
        self,
        markdown: str,
        title: str,
    ) -> _StructuredDocument:
        """Split extracted markdown into front matter and major sections."""
        blocks, page_count = self._parse_markdown_blocks(markdown)
        if not blocks:
            front_matter = f"# {title}"
            return _StructuredDocument(
                title=title,
                front_matter_markdown=front_matter,
                sections=[],
                page_count=page_count,
                is_long_document=False,
            )

        heading_map: dict[int, _HeadingMatch] = {}
        major_markdown_levels: list[int] = []
        for idx, block in enumerate(blocks):
            heading = self._classify_heading(block.text)
            if heading:
                heading_map[idx] = heading
                if heading.level > 1:
                    major_markdown_levels.append(heading.level)

        major_level = min(major_markdown_levels) if major_markdown_levels else 2

        front_blocks: list[_MarkdownBlock] = []
        sections: list[_StructuredSection] = []
        current: dict[str, Any] | None = None
        used_slugs: set[str] = set()
        section_index = 0

        for idx, block in enumerate(blocks):
            heading = heading_map.get(idx)
            if current is None and heading and heading.level == 1:
                front_blocks.append(block)
                continue

            if heading and self._is_major_heading(heading, major_level):
                if current is not None:
                    sections.append(self._build_section(current))

                section_index += 1
                slug = self._unique_slug(heading.title, used_slugs, section_index)
                body_from_heading = self._strip_heading_from_block(
                    block.text,
                    heading,
                )
                current = {
                    "page_key": f"section-{section_index:03d}-{slug}",
                    "title": heading.title,
                    "anchor": slug,
                    "heading_level": 2,
                    "body_parts": [],
                    "full_parts": [block.text.strip()],
                    "page_start": block.page_number,
                    "page_end": block.page_number,
                }
                if body_from_heading:
                    current["body_parts"].append(body_from_heading)
                continue

            if current is None:
                front_blocks.append(block)
                continue

            current["full_parts"].append(block.text.strip())
            if block.text.strip():
                current["body_parts"].append(block.text.strip())
            if block.page_number is not None:
                if current["page_start"] is None:
                    current["page_start"] = block.page_number
                current["page_end"] = block.page_number

        if current is not None:
            sections.append(self._build_section(current))

        if len(sections) < 2:
            return self._fallback_page_grouping(blocks, title, page_count)

        front_matter = "\n\n".join(
            block.text.strip() for block in front_blocks if block.text.strip()
        ).strip()
        front_matter = self._ensure_front_matter_title(front_matter, title)
        is_long_document = page_count > 15 or len(sections) > 10
        return _StructuredDocument(
            title=title,
            front_matter_markdown=front_matter,
            sections=sections,
            page_count=page_count,
            is_long_document=is_long_document,
        )

    def _parse_markdown_blocks(
        self,
        markdown: str,
    ) -> tuple[list[_MarkdownBlock], int]:
        """Split extracted markdown into paragraph-like blocks."""
        blocks: list[_MarkdownBlock] = []
        current_lines: list[str] = []
        current_page: int | None = None
        seen_pages: set[int] = set()

        def flush() -> None:
            text = "\n".join(current_lines).strip()
            if text:
                blocks.append(_MarkdownBlock(text=text, page_number=current_page))
            current_lines.clear()

        for raw_line in markdown.splitlines():
            line = raw_line.rstrip()
            page_match = _PAGE_MARKER_RE.fullmatch(line.strip())
            if page_match:
                flush()
                current_page = int(page_match.group(1))
                seen_pages.add(current_page)
                continue

            if line.strip() == "---":
                flush()
                continue

            if not line.strip():
                flush()
                continue

            current_lines.append(line)

        flush()
        page_count = max(seen_pages) if seen_pages else 0
        return blocks, page_count

    def _classify_heading(self, block_text: str) -> _HeadingMatch | None:
        """Identify a heading-like block and its relative depth."""
        stripped = block_text.strip()
        if not stripped:
            return None

        first_line = stripped.splitlines()[0].strip()
        md_match = _MD_HEADING_RE.match(first_line)
        if md_match:
            return _HeadingMatch(
                title=md_match.group(2).strip(),
                level=len(md_match.group(1)),
                consumed_text=first_line,
            )

        if _ARTICLE_HEADING_RE.match(first_line):
            return _HeadingMatch(title=first_line, level=2, consumed_text=first_line)

        if _SECTION_HEADING_RE.match(first_line):
            return _HeadingMatch(title=first_line, level=3, consumed_text=first_line)

        if _ROMAN_HEADING_RE.match(first_line) and len(first_line.split()) <= 18:
            return _HeadingMatch(title=first_line, level=2, consumed_text=first_line)

        if first_line.isupper() and len(first_line.split()) <= 12:
            return _HeadingMatch(title=first_line.title(), level=2, consumed_text=first_line)

        return None

    @staticmethod
    def _strip_heading_from_block(
        block_text: str,
        heading: _HeadingMatch,
    ) -> str:
        """Remove the leading heading line from a block."""
        lines = block_text.splitlines()
        if not lines:
            return ""
        if lines[0].strip() == heading.consumed_text.strip():
            return "\n".join(lines[1:]).strip()
        return block_text.strip()

    @staticmethod
    def _is_major_heading(
        heading: _HeadingMatch,
        major_level: int,
    ) -> bool:
        """Return True when a heading starts a new major section."""
        return heading.level <= major_level or heading.level == 2

    @staticmethod
    def _build_section(section_data: dict[str, Any]) -> _StructuredSection:
        """Create a structured section from accumulated block data."""
        full_markdown = "\n\n".join(
            part for part in section_data["full_parts"] if part
        ).strip()
        body_markdown = "\n\n".join(
            part for part in section_data["body_parts"] if part
        ).strip()
        return _StructuredSection(
            page_key=section_data["page_key"],
            title=section_data["title"],
            anchor=section_data["anchor"],
            heading_level=section_data["heading_level"],
            body_markdown=body_markdown,
            full_markdown=full_markdown,
            page_start=section_data["page_start"],
            page_end=section_data["page_end"],
        )

    def _fallback_page_grouping(
        self,
        blocks: list[_MarkdownBlock],
        title: str,
        page_count: int,
    ) -> _StructuredDocument:
        """Fallback grouping when heading-based structure is too weak."""
        pages: dict[int, list[str]] = {}
        front_parts: list[str] = []

        for block in blocks:
            if block.page_number is None:
                front_parts.append(block.text.strip())
                continue
            pages.setdefault(block.page_number, []).append(block.text.strip())

        ordered_pages = sorted(pages)
        if not ordered_pages:
            front_matter = self._ensure_front_matter_title(
                "\n\n".join(front_parts).strip(),
                title,
            )
            return _StructuredDocument(
                title=title,
                front_matter_markdown=front_matter,
                sections=[],
                page_count=page_count,
                is_long_document=False,
            )

        front_page_numbers = [ordered_pages[0]]
        if len(ordered_pages) > 1:
            second_page_text = " ".join(pages[ordered_pages[1]]).lower()
            if "table of contents" in second_page_text or "contents" in second_page_text:
                front_page_numbers.append(ordered_pages[1])

        for page_number in front_page_numbers:
            front_parts.extend(pages.pop(page_number, []))

        front_matter = self._ensure_front_matter_title(
            "\n\n".join(part for part in front_parts if part).strip(),
            title,
        )

        used_slugs: set[str] = set()
        group_size = 3
        sections: list[_StructuredSection] = []
        remaining_pages = sorted(pages)
        for index in range(0, len(remaining_pages), group_size):
            group = remaining_pages[index:index + group_size]
            if not group:
                continue
            if len(group) == 1:
                section_title = f"Page {group[0]}"
            else:
                section_title = f"Pages {group[0]}-{group[-1]}"
            slug = self._unique_slug(section_title, used_slugs, len(sections) + 1)
            full_markdown = "\n\n".join(
                text
                for page_number in group
                for text in pages.get(page_number, [])
                if text
            ).strip()
            sections.append(
                _StructuredSection(
                    page_key=f"section-{len(sections) + 1:03d}-{slug}",
                    title=section_title,
                    anchor=slug,
                    heading_level=2,
                    body_markdown=full_markdown,
                    full_markdown=full_markdown,
                    page_start=group[0],
                    page_end=group[-1],
                )
            )

        is_long_document = page_count > 15 or len(sections) > 10
        return _StructuredDocument(
            title=title,
            front_matter_markdown=front_matter,
            sections=sections,
            page_count=page_count,
            is_long_document=is_long_document,
        )

    def _generate_front_matter_fallback(self, title: str) -> str:
        """Return a minimal front matter block when no source is available."""
        return f"<h1>{self._escape_html(title)}</h1>"

    def _ensure_front_matter_title(self, front_matter: str, title: str) -> str:
        """Guarantee front matter starts with a document title."""
        if not front_matter:
            return f"# {title}"

        first_line = front_matter.splitlines()[0].strip()
        md_match = _MD_HEADING_RE.match(first_line)
        if md_match and md_match.group(2).strip():
            return front_matter

        return f"# {title}\n\n{front_matter}".strip()

    def _unique_slug(
        self,
        title: str,
        used_slugs: set[str],
        fallback_index: int,
    ) -> str:
        """Return a stable, unique slug for a section."""
        slug = self._slugify(title) or f"section-{fallback_index}"
        if slug not in used_slugs:
            used_slugs.add(slug)
            return slug

        suffix = 2
        while f"{slug}-{suffix}" in used_slugs:
            suffix += 1
        unique = f"{slug}-{suffix}"
        used_slugs.add(unique)
        return unique

    # ------------------------------------------------------------------
    # Chunked generation
    # ------------------------------------------------------------------

    async def _generate_front_matter(
        self,
        job: DocumentJob,
        structured: _StructuredDocument,
    ) -> str:
        """Generate the front matter fragment with a guaranteed H1."""
        if not structured.front_matter_markdown.strip():
            return self._generate_front_matter_fallback(structured.title)

        user_content = _FRONT_MATTER_USER_TEMPLATE.format(
            plan=job.html_plan,
            title=structured.title,
            file_type=job.file_type.value if job.file_type else "unknown",
            url=job.url,
            link_text=job.link_text or "(not available)",
            link_context=job.link_context or "(not available)",
            content=structured.front_matter_markdown,
        )
        front_html = await self._generate_fragment_with_retry(
            system_prompt=_FRONT_MATTER_SYSTEM_PROMPT,
            user_content=user_content,
            fragment_label="front matter",
        )

        if "<h1" not in front_html.lower():
            front_html = (
                f"<h1>{self._escape_html(structured.title)}</h1>\n"
                f"{front_html}"
            ).strip()
        return front_html

    async def _generate_section_body(
        self,
        job: DocumentJob,
        structured: _StructuredDocument,
        section: _StructuredSection,
    ) -> str:
        """Generate the HTML body for a single major section."""
        section_markdown = section.body_markdown or section.full_markdown
        user_content = _SECTION_BODY_USER_TEMPLATE.format(
            plan=job.html_plan,
            title=structured.title,
            file_type=job.file_type.value if job.file_type else "unknown",
            url=job.url,
            link_text=job.link_text or "(not available)",
            link_context=job.link_context or "(not available)",
            section_title=section.title,
            page_range=section.source_page_range or "Not available",
            content=section_markdown,
        )
        body_html = await self._generate_fragment_with_retry(
            system_prompt=_SECTION_BODY_SYSTEM_PROMPT,
            user_content=user_content,
            fragment_label=section.title,
        )
        return self._strip_duplicate_heading(body_html, section.title)

    async def _generate_fragment_with_retry(
        self,
        *,
        system_prompt: str,
        user_content: str,
        fragment_label: str,
        max_attempts: int = 3,
    ) -> str:
        """Generate a fragment and reject omission/disclaimer placeholders."""
        last_issue = ""
        for attempt in range(1, max_attempts + 1):
            retry_note = ""
            if last_issue:
                retry_note = (
                    "\n\nIMPORTANT RETRY INSTRUCTION:\n"
                    "Your previous response was rejected because it used omission "
                    "or disclaimer language. Preserve every source detail and do "
                    "not include placeholder phrases such as 'content continues in "
                    "full document' or 'refer to the original document'.\n"
                )

            fragment = await self._zai.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content + retry_note},
                ],
                model="glm-5",
                thinking=True,
                max_tokens=16384,
                temperature=0.2,
            )
            cleaned = self._clean_generated_fragment(fragment)

            if not cleaned:
                last_issue = "empty fragment"
                continue

            if _PLACEHOLDER_LANGUAGE_RE.search(cleaned):
                last_issue = "placeholder language"
                logger.warning(
                    "Rejected %s fragment attempt %d due to placeholder language.",
                    fragment_label,
                    attempt,
                )
                continue

            return cleaned

        raise ConversionError(
            f"Failed to generate a complete fragment for {fragment_label}: {last_issue}"
        )

    # ------------------------------------------------------------------
    # Page assembly
    # ------------------------------------------------------------------

    def _assemble_rendered_pages(
        self,
        job: DocumentJob,
        structured: _StructuredDocument,
        front_html: str,
        section_bodies: dict[str, str],
    ) -> list[RenderedPage]:
        """Build the canonical full document and long-document section pages."""
        canonical_relative_path = self._canonical_relative_path(job.url)
        is_form = self._document_is_form(job)

        canonical_sections = [
            self._render_canonical_section(
                section=section,
                body_html=section_bodies.get(section.page_key, ""),
                canonical_relative_path=canonical_relative_path,
                companion_relative_path=self._section_relative_path(job.url, section.anchor),
                include_disclosure=structured.is_long_document,
                section_index=index,
                section_count=len(structured.sections),
            )
            for index, section in enumerate(structured.sections, start=1)
        ]

        canonical_parts = [
            self._render_document_controls(
                print_label="Print This Form" if is_form else "Print This Page",
                include_long_doc_controls=structured.is_long_document and bool(structured.sections),
            ),
            front_html,
        ]
        if structured.is_long_document and structured.sections:
            canonical_parts.append(
                self._render_long_document_nav(
                    structured.sections,
                    canonical_relative_path,
                    job.url,
                )
            )
        canonical_parts.extend(canonical_sections)
        if structured.is_long_document and structured.sections:
            canonical_parts.append(self._render_long_document_script())

        file_type_display = job.file_type.value.upper() if job.file_type else "Document"
        canonical_html = self._render_full_html(
            title=structured.title,
            content=self._join_html(canonical_parts),
            file_type=file_type_display,
            original_url=job.url,
        )

        rendered_pages = [
            RenderedPage(
                page_key="canonical",
                kind="canonical",
                title=structured.title,
                relative_path=canonical_relative_path,
                html=canonical_html,
            )
        ]

        if structured.is_long_document and len(structured.sections) > 1:
            for index, section in enumerate(structured.sections):
                rendered_pages.append(
                    self._build_companion_page(
                        job=job,
                        title=structured.title,
                        section=section,
                        section_body=section_bodies.get(section.page_key, ""),
                        canonical_relative_path=canonical_relative_path,
                        previous_section=structured.sections[index - 1] if index > 0 else None,
                        next_section=(
                            structured.sections[index + 1]
                            if index + 1 < len(structured.sections)
                            else None
                        ),
                    )
                )

        return rendered_pages

    def _build_companion_page(
        self,
        *,
        job: DocumentJob,
        title: str,
        section: _StructuredSection,
        section_body: str,
        canonical_relative_path: str,
        previous_section: _StructuredSection | None,
        next_section: _StructuredSection | None,
    ) -> RenderedPage:
        """Build a section-only companion page."""
        companion_relative_path = self._section_relative_path(job.url, section.anchor)
        full_doc_href = self._relative_href(
            companion_relative_path,
            canonical_relative_path,
        )
        full_doc_section_href = self._relative_href(
            companion_relative_path,
            f"{canonical_relative_path}#{section.anchor}",
        )

        pagination = self._render_companion_pagination(
            current_relative_path=companion_relative_path,
            canonical_relative_path=canonical_relative_path,
            previous_section=previous_section,
            next_section=next_section,
            current_anchor=section.anchor,
        )

        section_html = self._render_section_content(
            section=section,
            body_html=section_body,
        )

        content = self._join_html(
            [
                self._render_document_controls(
                    print_label="Print This Page",
                    include_long_doc_controls=False,
                    extra_links=[
                        (
                            full_doc_section_href,
                            "Open this section in the full document",
                        ),
                    ],
                ),
                (
                    '<nav class="breadcrumb no-print" aria-label="Breadcrumb">'
                    '<ol class="breadcrumb__list">'
                    f'<li><a href="{self._escape_html(full_doc_href)}">Full document</a></li>'
                    f'<li aria-current="page">{self._escape_html(section.title)}</li>'
                    "</ol></nav>"
                ),
                f"<h1>{self._escape_html(title)}</h1>",
                pagination,
                section_html,
                pagination,
            ]
        )

        file_type_display = job.file_type.value.upper() if job.file_type else "Document"
        html = self._render_full_html(
            title=title,
            content=content,
            file_type=file_type_display,
            original_url=job.url,
        )
        return RenderedPage(
            page_key=section.page_key,
            kind="section",
            title=section.title,
            relative_path=companion_relative_path,
            html=html,
            source_page_range=section.source_page_range,
            section_slug=section.anchor,
        )

    def _render_document_controls(
        self,
        *,
        print_label: str,
        include_long_doc_controls: bool,
        extra_links: list[tuple[str, str]] | None = None,
    ) -> str:
        """Render print and long-document controls."""
        extra_links = extra_links or []
        link_html = "".join(
            f'<a class="document-controls__link" href="{self._escape_html(href)}">'
            f"{self._escape_html(label)}</a>"
            for href, label in extra_links
        )

        buttons = [
            (
                f'<button type="button" onclick="window.print()">'
                f"{self._escape_html(print_label)}</button>"
            )
        ]
        if include_long_doc_controls:
            buttons.extend(
                [
                    '<button type="button" data-action="expand-all">Expand all sections</button>',
                    '<button type="button" data-action="collapse-all">Collapse all sections</button>',
                ]
            )

        return (
            '<div class="document-controls no-print">'
            f'{"".join(buttons)}'
            f"{link_html}"
            "</div>"
        )

    def _render_long_document_nav(
        self,
        sections: list[_StructuredSection],
        canonical_relative_path: str,
        original_url: str,
    ) -> str:
        """Render section navigation for the canonical page."""
        items = []
        for section in sections:
            companion_path = self._section_relative_path(original_url, section.anchor)
            companion_href = self._relative_href(
                canonical_relative_path,
                companion_path,
            )
            page_note = (
                f'<span class="section-pages">{self._escape_html(section.source_page_range)}</span>'
                if section.source_page_range
                else ""
            )
            items.append(
                "<li>"
                f'<a href="#{self._escape_html(section.anchor)}">{self._escape_html(section.title)}</a>'
                f"{page_note}"
                f'<a class="section-subpage-link" href="{self._escape_html(companion_href)}">'
                "Section-only page</a>"
                "</li>"
            )

        return (
            '<nav class="document-section-nav no-print" aria-labelledby="document-section-nav-heading">'
            '<h2 id="document-section-nav-heading">Document sections</h2>'
            f"<ol>{''.join(items)}</ol>"
            "</nav>"
        )

    def _render_canonical_section(
        self,
        *,
        section: _StructuredSection,
        body_html: str,
        canonical_relative_path: str,
        companion_relative_path: str,
        include_disclosure: bool,
        section_index: int,
        section_count: int,
    ) -> str:
        """Render a major section for the canonical page."""
        section_content = self._render_section_content(section=section, body_html=body_html)

        if not include_disclosure:
            return section_content

        companion_href = self._relative_href(
            canonical_relative_path,
            companion_relative_path,
        )
        page_note = (
            f'<span class="section-pages">{self._escape_html(section.source_page_range)}</span>'
            if section.source_page_range
            else ""
        )
        open_attr = " open" if section_index == 1 else ""
        return (
            f'<details class="document-section" id="{self._escape_html(section.anchor)}-details"{open_attr}>'
            "<summary>"
            f'<span class="document-section__title">{self._escape_html(section.title)}</span>'
            f"{page_note}"
            "</summary>"
            '<div class="document-section__panel">'
            '<nav class="document-section__actions no-print" '
            f'aria-label="{self._escape_html(section.title)} section actions">'
            f'<a href="{self._escape_html(companion_href)}">Open section-only page</a>'
            "</nav>"
            f"{section_content}"
            "</div>"
            "</details>"
        )

    def _render_section_content(
        self,
        *,
        section: _StructuredSection,
        body_html: str,
    ) -> str:
        """Render the semantic section content shared by all page variants."""
        page_note = (
            f'<p class="section-pages">{self._escape_html(section.source_page_range)}</p>'
            if section.source_page_range
            else ""
        )
        return (
            f'<section id="{self._escape_html(section.anchor)}" '
            f'aria-labelledby="{self._escape_html(section.anchor)}-heading">'
            f'<h2 id="{self._escape_html(section.anchor)}-heading">'
            f"{self._escape_html(section.title)}</h2>"
            f"{page_note}"
            f"{body_html}"
            "</section>"
        )

    def _render_companion_pagination(
        self,
        *,
        current_relative_path: str,
        canonical_relative_path: str,
        previous_section: _StructuredSection | None,
        next_section: _StructuredSection | None,
        current_anchor: str,
    ) -> str:
        """Render prev/next navigation for a section-only page."""
        full_doc_href = self._relative_href(
            current_relative_path,
            f"{canonical_relative_path}#{current_anchor}",
        )

        prev_html = (
            f'<a href="{self._escape_html(self._relative_href(current_relative_path, self._section_relative_path_from_canonical(canonical_relative_path, previous_section.anchor)))}">'
            f"Previous: {self._escape_html(previous_section.title)}</a>"
            if previous_section
            else '<span aria-disabled="true">Beginning of document</span>'
        )
        next_html = (
            f'<a href="{self._escape_html(self._relative_href(current_relative_path, self._section_relative_path_from_canonical(canonical_relative_path, next_section.anchor)))}">'
            f"Next: {self._escape_html(next_section.title)}</a>"
            if next_section
            else '<span aria-disabled="true">End of document</span>'
        )

        return (
            '<nav class="companion-pagination no-print" aria-label="Section pagination">'
            f"{prev_html}"
            f'<a href="{self._escape_html(full_doc_href)}">Open in full document</a>'
            f"{next_html}"
            "</nav>"
        )

    @staticmethod
    def _render_long_document_script() -> str:
        """Render the accordion support script for long documents."""
        return """\
<script>
(() => {
  const details = Array.from(document.querySelectorAll('details.document-section'));
  if (!details.length) return;

  const expand = document.querySelector('[data-action="expand-all"]');
  const collapse = document.querySelector('[data-action="collapse-all"]');
  let printState = [];

  const setAll = (open) => {
    details.forEach((section) => {
      section.open = open;
    });
  };

  const openForHash = () => {
    const hash = window.location.hash;
    if (!hash) return;
    const target = document.querySelector(hash);
    if (!target) return;
    const owner = target.closest('details.document-section');
    if (owner) owner.open = true;
  };

  expand?.addEventListener('click', () => setAll(true));
  collapse?.addEventListener('click', () => setAll(false));

  window.addEventListener('hashchange', openForHash);
  openForHash();

  window.addEventListener('beforeprint', () => {
    printState = details.map((section) => section.open);
    setAll(true);
  });

  window.addEventListener('afterprint', () => {
    details.forEach((section, index) => {
      section.open = printState[index] ?? section.open;
    });
  });
})();
</script>"""

    @staticmethod
    def _join_html(parts: list[str]) -> str:
        """Join non-empty HTML fragments with line breaks."""
        return "\n\n".join(part.strip() for part in parts if part and part.strip())

    def _render_full_html(
        self,
        *,
        title: str,
        content: str,
        file_type: str,
        original_url: str,
    ) -> str:
        """Render a complete HTML document from the shared template."""
        return LAMC_HTML_TEMPLATE.format(
            title=self._escape_html(title),
            content=content,
            file_type=file_type,
            original_url=original_url,
        )

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _canonical_relative_path(self, url: str) -> str:
        """Return the canonical output path for a document URL."""
        parsed = urlparse(url)
        path = parsed.path.lstrip("/")
        for prefix in ("sites/lamc.edu/files/", "sites/default/files/"):
            if path.startswith(prefix):
                path = path[len(prefix):]
                break

        stem = PurePosixPath(path).stem
        parent = str(PurePosixPath(path).parent)
        slug = self._slugify(stem)
        if parent and parent != ".":
            return f"documents/{parent}/{slug}.html"
        return f"documents/{slug}.html"

    def _section_relative_path(self, url: str, section_slug: str) -> str:
        """Return the companion page path for a section."""
        canonical = self._canonical_relative_path(url)
        return self._section_relative_path_from_canonical(canonical, section_slug)

    @staticmethod
    def _section_relative_path_from_canonical(
        canonical_relative_path: str,
        section_slug: str,
    ) -> str:
        """Build a companion page path from a canonical page path."""
        pure = PurePosixPath(canonical_relative_path)
        return str(pure.parent / pure.stem / f"{section_slug}.html")

    @staticmethod
    def _relative_href(current_relative_path: str, target_relative_path: str) -> str:
        """Compute a relative href between two output artifacts."""
        target_path, _, fragment = target_relative_path.partition("#")
        current_dir = posixpath.dirname(current_relative_path) or "."
        relative = posixpath.relpath(target_path, start=current_dir)
        if fragment:
            return f"{relative}#{fragment}"
        return relative

    # ------------------------------------------------------------------
    # Text / fragment cleanup helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        """Remove markdown code fences that models sometimes wrap HTML in."""
        stripped = text.strip()
        if stripped.startswith("```html"):
            stripped = stripped[7:]
        elif stripped.startswith("```"):
            stripped = stripped[3:]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        return stripped.strip()

    def _clean_generated_fragment(self, fragment: str) -> str:
        """Strip code fences and any accidental full-page scaffolding."""
        cleaned = self._strip_code_fences(fragment)
        cleaned = re.sub(r"<!DOCTYPE[^>]*>", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"</?(?:html|head|body|main)[^>]*>",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r'<a[^>]+class="[^"]*skip-(?:nav|link)[^"]*"[^>]*>.*?</a>',
            "",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        return cleaned.strip()

    def _strip_duplicate_heading(self, body_html: str, heading_text: str) -> str:
        """Remove a repeated leading major heading from a section fragment."""
        heading_text = heading_text.strip()
        if not heading_text:
            return body_html

        escaped = re.escape(heading_text)
        pattern = re.compile(
            rf"^\s*<h[1-6][^>]*>\s*{escaped}\s*</h[1-6]>\s*",
            re.IGNORECASE,
        )
        return pattern.sub("", body_html, count=1).strip()

    @staticmethod
    def _derive_title(job: DocumentJob) -> str:
        """Extract a reasonable page title from job metadata."""
        if job.link_text and len(job.link_text.strip()) > 3:
            return job.link_text.strip()

        url_path = PurePosixPath(urlparse(job.url).path)
        name = url_path.stem.replace("-", " ").replace("_", " ")
        if name:
            return name.title()

        return "Document"

    @staticmethod
    def _document_is_form(job: DocumentJob) -> bool:
        """Best-effort heuristic for print-form labeling."""
        signal = " ".join(
            [
                job.link_text,
                job.link_context,
                job.html_plan[:2000],
                job.ocr_markdown[:1000],
            ]
        ).lower()
        return any(
            keyword in signal
            for keyword in (
                "fillable form",
                "print form",
                "application",
                "request form",
                "update form",
            )
        )

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert text into a stable lowercase slug."""
        slug = re.sub(r"[^\w\s-]", "", text).strip().lower()
        slug = re.sub(r"[-\s]+", "-", slug)
        return slug

    @staticmethod
    def _escape_html(text: str) -> str:
        """Minimal HTML entity escaping for template insertion."""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
