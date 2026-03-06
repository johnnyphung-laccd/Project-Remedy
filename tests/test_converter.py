from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

if "aiosqlite" not in sys.modules:
    aiosqlite_stub = types.ModuleType("aiosqlite")
    aiosqlite_stub.Connection = object
    sys.modules["aiosqlite"] = aiosqlite_stub

from lamc_adrp.converter import HTMLConverter, _StructuredDocument, _StructuredSection
from lamc_adrp.models import DocumentJob, FileType


def _make_converter() -> HTMLConverter:
    converter = HTMLConverter.__new__(HTMLConverter)
    converter._config = None
    converter._zai = None
    converter._db = None
    return converter


def _make_job(
    url: str = "https://www.lamission.edu/sites/lamc.edu/files/policies/sample-document.pdf",
) -> DocumentJob:
    return DocumentJob(
        url=url,
        source_page_url="https://www.lamission.edu/example",
        link_text="Sample Document",
        link_context="Example context",
        file_type=FileType.PDF,
        ocr_markdown="",
        html_plan="Preserve all sections and render them accessibly.",
    )


class HTMLConverterTests(unittest.TestCase):
    def test_structure_document_splits_headed_markdown_into_sections(self) -> None:
        converter = _make_converter()
        markdown = """
# Sample Security Report

Introductory overview text.

<!-- Page 1 -->
## Overview

Campus safety overview.

<!-- Page 2 -->
## Crime Statistics

Reported incidents for the year.

<!-- Page 3 -->
## Prevention Programs

Workshops and response procedures.
""".strip()

        structured = converter._structure_document(markdown, "Sample Security Report")

        self.assertTrue(structured.front_matter_markdown.startswith("# Sample Security Report"))
        self.assertIn("Introductory overview text.", structured.front_matter_markdown)
        self.assertEqual(
            [section.title for section in structured.sections],
            ["Overview", "Crime Statistics", "Prevention Programs"],
        )
        self.assertEqual(structured.sections[0].source_page_range, "Source page 1")
        self.assertEqual(
            structured.sections[1].body_markdown,
            "Reported incidents for the year.",
        )

    def test_structure_document_detects_article_and_roman_headings(self) -> None:
        converter = _make_converter()
        markdown = """
# Board Policies

Preface text.

<!-- Page 1 -->
ARTICLE I PURPOSE

This article defines the purpose.

<!-- Page 2 -->
II. ELIGIBILITY

This section defines eligibility.
""".strip()

        structured = converter._structure_document(markdown, "Board Policies")

        self.assertEqual(
            [section.title for section in structured.sections],
            ["ARTICLE I PURPOSE", "II. ELIGIBILITY"],
        )
        self.assertEqual(
            structured.sections[0].body_markdown,
            "This article defines the purpose.",
        )
        self.assertEqual(structured.sections[1].source_page_range, "Source page 2")

    def test_structure_document_falls_back_to_page_grouping_for_weak_markdown(self) -> None:
        converter = _make_converter()
        markdown = """
<!-- Page 1 -->
Sample Security Report

<!-- Page 2 -->
Table of Contents

<!-- Page 3 -->
Campus safety overview and context.

<!-- Page 4 -->
Detailed reporting requirements.

<!-- Page 5 -->
Emergency response procedures.

<!-- Page 6 -->
Annual statistics appendix.
""".strip()

        structured = converter._structure_document(markdown, "Sample Security Report")

        self.assertTrue(structured.front_matter_markdown.startswith("# Sample Security Report"))
        self.assertIn("Table of Contents", structured.front_matter_markdown)
        self.assertEqual(
            [section.title for section in structured.sections],
            ["Pages 3-5", "Page 6"],
        )
        self.assertIn(
            "Emergency response procedures.",
            structured.sections[0].body_markdown,
        )
        self.assertEqual(structured.sections[1].source_page_range, "Source page 6")

    def test_assemble_rendered_pages_keeps_all_sections_and_builds_companions(self) -> None:
        converter = _make_converter()
        job = _make_job(
            "https://www.lamission.edu/sites/lamc.edu/files/2024-09/annual-security-report.pdf"
        )
        structured = _StructuredDocument(
            title="Annual Security Report",
            front_matter_markdown="# Annual Security Report",
            sections=[
                _StructuredSection(
                    page_key="section-001-overview",
                    title="Overview",
                    anchor="overview",
                    heading_level=2,
                    body_markdown="Overview body",
                    full_markdown="## Overview\n\nOverview body",
                    page_start=1,
                    page_end=2,
                ),
                _StructuredSection(
                    page_key="section-002-statistics",
                    title="Statistics",
                    anchor="statistics",
                    heading_level=2,
                    body_markdown="Statistics body",
                    full_markdown="## Statistics\n\nStatistics body",
                    page_start=3,
                    page_end=4,
                ),
            ],
            page_count=22,
            is_long_document=True,
        )

        rendered_pages = converter._assemble_rendered_pages(
            job,
            structured,
            front_html="<h1>Annual Security Report</h1><p>Intro text.</p>",
            section_bodies={
                "section-001-overview": "<p>Overview body</p>",
                "section-002-statistics": "<p>Statistics body</p>",
            },
        )

        canonical = rendered_pages[0]
        section_pages = rendered_pages[1:]

        self.assertEqual(canonical.kind, "canonical")
        self.assertEqual(
            canonical.relative_path,
            "documents/2024-09/annual-security-report.html",
        )
        self.assertIn("<p>Overview body</p>", canonical.html)
        self.assertIn("<p>Statistics body</p>", canonical.html)
        self.assertNotIn("Content continues in full document", canonical.html)
        self.assertIn('details class="document-section"', canonical.html)
        self.assertIn("Expand all sections", canonical.html)
        self.assertIn("Document sections", canonical.html)

        self.assertEqual(
            [page.relative_path for page in section_pages],
            [
                "documents/2024-09/annual-security-report/overview.html",
                "documents/2024-09/annual-security-report/statistics.html",
            ],
        )
        self.assertIn("Open this section in the full document", section_pages[0].html)
        self.assertIn("Previous: Overview", section_pages[1].html)
        self.assertIn("Next: Statistics", section_pages[0].html)

    def test_security_report_example_uses_full_document_layout(self) -> None:
        example = (
            REPO_ROOT
            / "examples"
            / "test_security_report.html"
        ).read_text(encoding="utf-8")

        self.assertNotIn("Content continues in full document", example)
        self.assertNotIn("Map continues in full document", example)
        self.assertIn('class="skip-nav"', example)
        self.assertIn("Expand all sections", example)
        self.assertIn("Section-only page", example)


if __name__ == "__main__":
    unittest.main()
