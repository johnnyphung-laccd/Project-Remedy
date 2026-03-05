"""Stages 4-5: Planning and Accessible HTML Generation.

Uses GLM-5 with thinking mode to:
  1. Analyse extracted content and produce a structured conversion plan.
  2. Generate a complete WCAG 2.1 AA compliant HTML document with LAMC branding.
"""

from __future__ import annotations

import logging
from typing import Any

from lamc_adrp.config import PipelineConfig
from lamc_adrp.database import DatabaseManager
from lamc_adrp.models import DocumentJob, JobStatus
from lamc_adrp.zai_client import ZAIClient, ZAIClientError

logger = logging.getLogger(__name__)


class ConversionError(Exception):
    """Raised when HTML planning or generation fails."""


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
    .skip-link {{
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
    .skip-link:focus {{
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
  </style>
</head>
<body>
  <a href="#main-content" class="skip-link">Skip to main content</a>

  <header role="banner">
    <nav aria-label="Institution">
      <strong>Los Angeles Mission College</strong>
      &nbsp;|&nbsp;
      <span>Los Angeles Community College District</span>
    </nav>
    <p>{title}</p>
  </header>

  <main id="main-content" role="main">
{content}
  </main>

  <footer role="contentinfo">
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
# WCAG Requirements System Prompt (embedded in generation)
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
- Do NOT add redundant ARIA roles to semantic elements (e.g., no role="banner" on <header>).

SKIP NAVIGATION:
- "Skip to main content" must be the first focusable element on the page.

TEXT FORMATTING:
- Do NOT use text-align: justify (causes uneven word spacing, harms readability for dyslexic users).
- Use <em> and <strong> instead of <i> and <b> for emphasis.
- Paragraph text must be at least 1rem (16px).
"""


# ======================================================================
# Planning prompt
# ======================================================================

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


# ======================================================================
# Generation prompt
# ======================================================================

_GENERATION_SYSTEM_PROMPT = """\
You are an expert HTML accessibility engineer. Generate a complete, valid \
HTML page body (the content that goes inside <main>) for an LAMC document \
that has been converted from its original format to accessible HTML.

""" + _WCAG_REQUIREMENTS + """

CRITICAL INSTRUCTIONS:
- Output ONLY the inner HTML content that goes inside the <main> element.
- Do NOT include <!DOCTYPE>, <html>, <head>, <body>, <header>, <footer>, \
<main> tags, or the skip-link — those are in the page template already.
- Start with an <h1> containing the document title.
- Produce semantic, clean HTML. No inline styles except when absolutely \
necessary for accessibility.
- Preserve ALL content from the source document — do not summarise or omit.
- If the document contains images, use descriptive alt text. For images you \
cannot see, use alt="[Image description pending vision processing]".
- For tables, always include <caption>, <thead>, <th scope>.
- Use LAMC brand colours only via CSS classes, not inline styles.
- Ensure the output is well-formed HTML that can be directly inserted into \
the template's <main> element.

DOCUMENT TYPE HANDLING:
- PRINT FORMS (fillable forms, applications, update forms): These are documents \
that users print, fill out by hand, and submit in person. Do NOT add a "Submit" \
button with a server action. Instead: use onsubmit="window.print(); return false;" \
on the <form> tag, add a "Print This Form" button using \
<button type="button" onclick="window.print()">Print This Form</button>, \
add a <p> note explaining the form should be printed and submitted in person, \
and give print-related elements the class "no-print" so they hide when printing.
- INFORMATIONAL DOCUMENTS (schedules, catalogs, reports, policies): Render as \
readable HTML content. Add a "Print This Page" button at the top with class "no-print".
- ALL DOCUMENTS: Always include a print option. Users expect to be able to print \
accessible HTML documents just like they could print the original PDF/Word file.

PRINT CSS NOTE: The page template already includes @media print rules that hide \
elements with class "no-print". Use this class on print buttons and instructional \
notes that should not appear on the printed page.
"""


_GENERATION_USER_TEMPLATE = """\
CONVERSION PLAN:
{plan}

--- EXTRACTED CONTENT ---
{content}
--- END CONTENT ---

Document type: {file_type}
Original URL: {url}
Link text: {link_text}
Context: {link_context}

Generate the complete HTML body content (inside <main>) for this document. \
Follow the conversion plan and all WCAG requirements exactly.
"""


class HTMLConverter:
    """Converts extracted document content to accessible HTML via GLM-5.

    Implements a two-phase approach:
      1. **Planning** (Stage 4): GLM-5 with thinking analyses the document
         and produces a structured conversion plan.
      2. **Generation** (Stage 5): GLM-5 with thinking produces the final
         WCAG 2.1 AA compliant HTML using the plan, content, and template.

    Parameters
    ----------
    config:
        Pipeline configuration.
    zai:
        Initialised Z.AI API client.
    db:
        Database manager for persisting job state.
    """

    def __init__(
        self,
        config: PipelineConfig,
        zai: ZAIClient,
        db: DatabaseManager,
    ) -> None:
        self._config = config
        self._zai = zai
        self._db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def convert(self, job: DocumentJob) -> str:
        """Run the full plan-then-generate pipeline for a document job.

        Returns the complete HTML document string.
        """
        await self.plan(job)
        return await self.generate(job)

    async def plan(self, job: DocumentJob) -> str:
        """Stage 4: Analyse extracted content and produce a conversion plan.

        Updates ``job.html_plan`` and sets status to PLANNED.

        Returns
        -------
        str
            The structured Markdown conversion plan.
        """
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
        """Stage 5: Generate WCAG 2.1 AA compliant HTML from the plan.

        Updates ``job.generated_html`` and sets status to CONVERTED.

        Returns
        -------
        str
            The complete HTML document (template + generated content).
        """
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
            user_content = _GENERATION_USER_TEMPLATE.format(
                plan=job.html_plan,
                content=job.ocr_markdown,
                file_type=job.file_type.value if job.file_type else "unknown",
                url=job.url,
                link_text=job.link_text or "(not available)",
                link_context=job.link_context or "(not available)",
            )

            main_content = await self._zai.chat(
                messages=[
                    {"role": "system", "content": _GENERATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                model="glm-5",
                thinking=True,
                max_tokens=32768,
                temperature=0.2,
            )

            if not main_content.strip():
                raise ConversionError("GLM-5 returned empty HTML content.")

            # Clean up: strip markdown code fences if the model wrapped output.
            main_content = self._strip_code_fences(main_content)

            # Derive a page title from the plan or link text.
            title = self._derive_title(job)

            # Assemble the full HTML document from the template.
            file_type_display = (
                job.file_type.value.upper() if job.file_type else "Document"
            )

            full_html = LAMC_HTML_TEMPLATE.format(
                title=self._escape_html(title),
                content=main_content,
                file_type=file_type_display,
                original_url=job.url,
            )

            job.generated_html = full_html
            job.status = JobStatus.CONVERTED
            await self._db.update_job(job)
            logger.info(
                "HTML generation complete for job %s — %d chars",
                job.id,
                len(full_html),
            )
            return full_html

        except ZAIClientError as exc:
            error_msg = f"HTML generation failed: {exc}"
            logger.error("Job %s: %s", job.id, error_msg)
            job.status = JobStatus.FAILED
            job.error_message = error_msg
            await self._db.update_job(job)
            raise ConversionError(error_msg) from exc

    # ------------------------------------------------------------------
    # Helpers
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

    @staticmethod
    def _derive_title(job: DocumentJob) -> str:
        """Extract a reasonable page title from the job metadata."""
        # Prefer the link text from the referring page.
        if job.link_text and len(job.link_text.strip()) > 3:
            return job.link_text.strip()

        # Fall back to the filename from the URL.
        from pathlib import PurePosixPath

        url_path = PurePosixPath(job.url)
        name = url_path.stem
        # Convert kebab/snake/camel to title case.
        name = name.replace("-", " ").replace("_", " ")
        if name:
            return name.title()

        return "Document"

    @staticmethod
    def _escape_html(text: str) -> str:
        """Minimal HTML entity escaping for template insertion."""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
