#!/usr/bin/env python3
"""Process the LAMC 2024 Annual Security Report through the ADA remediation pipeline."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import sys
from pathlib import Path

import fitz  # PyMuPDF
import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from lamc_adrp.config import load_config
from lamc_adrp.converter import HTMLConverter
from lamc_adrp.database import DatabaseManager
from lamc_adrp.models import DocumentJob, FileType, JobStatus
from lamc_adrp.zai_client import ZAIClient

DOC_URL = "https://www.lamission.edu/sites/lamc.edu/files/2024-09/2024-Annual-Security-Report.pdf"
SOURCE_PAGE = "https://lamission.edu/campus-life/campus-safety/sheriffs-office/crime-stats"
LINK_TEXT = "2024 Annual Security Report"
DEFAULT_OUTPUT_HTML = REPO_ROOT / "examples" / "test_security_report.html"
DOWNLOAD_DIR = REPO_ROOT / "output" / "downloads" / "pdf"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download the annual security report, extract OCR, and render the "
            "full accessible HTML example."
        ),
    )
    parser.add_argument(
        "--page-limit",
        type=int,
        default=None,
        help="Optional debug limit for OCR extraction. Defaults to the full document.",
    )
    parser.add_argument(
        "--output-html",
        type=Path,
        default=DEFAULT_OUTPUT_HTML,
        help="Canonical output HTML path. Section-only pages are written beside it.",
    )
    return parser.parse_args()


def _write_rendered_pages(output_html: Path, job: DocumentJob) -> None:
    output_html.parent.mkdir(parents=True, exist_ok=True)
    companion_dir = output_html.with_suffix("")

    for page in job.get_rendered_pages():
        if page.kind == "canonical":
            target = output_html
        else:
            slug = page.section_slug or page.page_key
            target = companion_dir / f"{slug}.html"

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page.html, encoding="utf-8")


async def main() -> None:
    args = _parse_args()

    print("=" * 70)
    print("STEP 1: Downloading PDF")
    print("=" * 70)
    print(f"  URL: {DOC_URL}")

    async with httpx.AsyncClient(
        follow_redirects=True,
        verify=False,
        timeout=60.0,
        headers=REQUEST_HEADERS,
    ) as http:
        await http.get(SOURCE_PAGE)
        resp = await http.get(DOC_URL)
        resp.raise_for_status()
        pdf_bytes = resp.content

    print(f"  Downloaded: {len(pdf_bytes):,} bytes ({len(pdf_bytes) / 1024 / 1024:.1f} MB)")

    print("\n" + "=" * 70)
    print("STEP 2: Saving PDF with SHA-256 prefix")
    print("=" * 70)

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_hash = hashlib.sha256(pdf_bytes).hexdigest()
    safe_name = "2024-Annual-Security-Report.pdf"
    saved_name = f"{file_hash[:16]}_{safe_name}"
    saved_path = DOWNLOAD_DIR / saved_name
    saved_path.write_bytes(pdf_bytes)

    print(f"  SHA-256: {file_hash}")
    print(f"  Saved to: {saved_path}")

    print("\n" + "=" * 70)
    print("STEP 3: Inspecting PDF with PyMuPDF")
    print("=" * 70)

    doc = fitz.open(str(saved_path))
    total_pages = len(doc)
    doc.close()

    pages_to_process = total_pages if args.page_limit is None else min(args.page_limit, total_pages)
    print(f"  Total pages: {total_pages}")
    if args.page_limit is None:
        print("  Processing: full document")
    else:
        print(f"  Processing: first {pages_to_process} page(s) for debugging")

    print("\n" + "=" * 70)
    print("STEP 4: OCR extraction via ZAIClient")
    print("=" * 70)

    cfg = load_config(
        env_path=REPO_ROOT / ".env",
        yaml_path=REPO_ROOT / "config.yaml",
    )
    zai = ZAIClient(cfg)
    await zai.start()

    try:
        doc = fitz.open(str(saved_path))
        all_markdown: list[str] = []

        for page_num in range(pages_to_process):
            page = doc[page_num]
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            b64 = base64.b64encode(img_bytes).decode()

            print(f"  Processing page {page_num + 1}/{pages_to_process}...", end=" ", flush=True)
            page_md = await zai._ocr_single_image(
                image_b64=b64,
                mime="image/png",
                page_hint=f"Page {page_num + 1} of {total_pages}",
            )
            all_markdown.append(f"<!-- Page {page_num + 1} -->\n{page_md}")

            preview = page_md[:150].replace("\n", " ")
            print(f"OK ({len(page_md)} chars)")
            print(f"    Preview: {preview}...")

        doc.close()

        combined_markdown = "\n\n---\n\n".join(all_markdown)
        print(f"\n  Total extracted markdown: {len(combined_markdown):,} chars")
        print("\n  --- MARKDOWN PREVIEW (first 500 chars) ---")
        print(combined_markdown[:500])
        print("  --- END PREVIEW ---")

        print("\n" + "=" * 70)
        print("STEP 5: Planning and generating accessible HTML")
        print("=" * 70)

        db = DatabaseManager(Path(cfg.output.db_path))
        await db.connect()

        try:
            job = DocumentJob(
                url=DOC_URL,
                source_page_url=SOURCE_PAGE,
                link_text=LINK_TEXT,
                link_context="Campus Safety / Sheriff's Office / Crime Stats page",
                file_type=FileType.PDF,
                local_path=str(saved_path),
                file_hash=file_hash,
                file_size=len(pdf_bytes),
                status=JobStatus.EXTRACTED,
                ocr_markdown=combined_markdown,
            )

            await db.create_job(job)
            print(f"  Job created: {job.id}")

            converter = HTMLConverter(cfg, zai, db)

            print("  Running planning stage (GLM-5 with thinking)...")
            plan = await converter.plan(job)
            print(f"  Plan generated: {len(plan)} chars")
            print("\n  --- PLAN PREVIEW (first 400 chars) ---")
            print(plan[:400])
            print("  --- END PLAN PREVIEW ---\n")

            print("  Running HTML generation stage (GLM-5 with thinking)...")
            html = await converter.generate(job)
            print(f"  HTML generated: {len(html):,} chars")

        finally:
            await db.close()

    finally:
        await zai.close()

    print("\n" + "=" * 70)
    print("STEP 6: Saving output HTML")
    print("=" * 70)

    _write_rendered_pages(args.output_html, job)
    section_count = sum(1 for page in job.get_rendered_pages() if page.kind == "section")
    print(f"  Canonical page: {args.output_html}")
    if section_count:
        print(f"  Section-only pages: {section_count} in {args.output_html.with_suffix('')}")

    print("\n" + "=" * 70)
    print("DONE - Token Usage Summary")
    print("=" * 70)
    print(f"  Input tokens:  {zai.total_input_tokens:,}")
    print(f"  Output tokens: {zai.total_output_tokens:,}")
    print(f"  Output file:   {args.output_html}")


if __name__ == "__main__":
    asyncio.run(main())
