#!/usr/bin/env python3
"""Process the LAMC Commencement Program 2025 PDF through the ADA remediation pipeline."""

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

PDF_URL = "https://www.lamission.edu/sites/lamc.edu/files/2025-05/LAMC-Commencement-Program-2025.pdf"
SOURCE_PAGE = "https://lamission.edu/commencement/"
LINK_TEXT = "LAMC Commencement Program 2025"
DEFAULT_OUTPUT_HTML = REPO_ROOT / "examples" / "test_commencement.html"
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
            "Download the commencement program, extract OCR, and render the "
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

    print("[1/5] Loading configuration...")
    cfg = load_config(
        env_path=REPO_ROOT / ".env",
        yaml_path=REPO_ROOT / "config.yaml",
    )
    print(f"       Output dir: {cfg.output.output_dir}")
    print(f"       DB path:    {cfg.output.db_path}")

    print(f"\n[2/5] Downloading PDF from:\n       {PDF_URL}")
    async with httpx.AsyncClient(
        follow_redirects=True,
        verify=False,
        timeout=60,
        headers=REQUEST_HEADERS,
    ) as client:
        await client.get(SOURCE_PAGE)
        resp = await client.get(PDF_URL)
        resp.raise_for_status()
        pdf_bytes = resp.content
    print(f"       Downloaded {len(pdf_bytes):,} bytes")

    file_hash = hashlib.sha256(pdf_bytes).hexdigest()
    download_dir = REPO_ROOT / "output" / "downloads" / "pdf"
    download_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{file_hash[:16]}_LAMC-Commencement-Program-2025.pdf"
    local_path = download_dir / filename
    local_path.write_bytes(pdf_bytes)
    print(f"       Saved to: {local_path}")
    print(f"       SHA-256:  {file_hash[:16]}...")

    doc = fitz.open(str(local_path))
    total_pages = len(doc)
    doc.close()
    pages_to_process = total_pages if args.page_limit is None else min(args.page_limit, total_pages)
    print(f"       Total pages in PDF: {total_pages}")
    if args.page_limit is None:
        print("       Processing the full document")
    else:
        print(f"       Processing first {pages_to_process} page(s) for debugging")

    print("\n[3/5] Extracting content via OCR...")
    zai = ZAIClient(cfg)
    await zai.start()

    try:
        doc = fitz.open(str(local_path))
        all_markdown: list[str] = []

        for page_num in range(pages_to_process):
            page = doc[page_num]
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            b64 = base64.b64encode(img_bytes).decode()

            print(f"       Processing page {page_num + 1}/{pages_to_process}...")
            page_md = await zai._ocr_single_image(
                image_b64=b64,
                mime="image/png",
                page_hint=f"Page {page_num + 1} of {total_pages}",
            )
            all_markdown.append(f"<!-- Page {page_num + 1} -->\n{page_md}")

        doc.close()
        ocr_markdown = "\n\n---\n\n".join(all_markdown)

        print(f"       Extracted {len(ocr_markdown):,} characters of Markdown")
        print(
            f"       Token usage — input: {zai.total_input_tokens:,}, "
            f"output: {zai.total_output_tokens:,}"
        )

        print("\n[4/5] Planning and generating accessible HTML...")
        db = DatabaseManager(Path(cfg.output.db_path))
        await db.connect()

        try:
            job = DocumentJob(
                url=PDF_URL,
                source_page_url=SOURCE_PAGE,
                link_text=LINK_TEXT,
                file_type=FileType.PDF,
                local_path=str(local_path),
                file_hash=file_hash,
                file_size=len(pdf_bytes),
                status=JobStatus.EXTRACTED,
                ocr_markdown=ocr_markdown,
            )
            await db.create_job(job)

            converter = HTMLConverter(cfg, zai, db)

            print("       Phase 1: Creating conversion plan...")
            plan = await converter.plan(job)
            print(f"       Plan created ({len(plan):,} chars)")

            print("       Phase 2: Generating WCAG 2.1 AA HTML...")
            html = await converter.generate(job)
            print(f"       HTML generated ({len(html):,} chars)")

        finally:
            await db.close()
    finally:
        await zai.close()

    _write_rendered_pages(args.output_html, job)
    section_count = sum(1 for page in job.get_rendered_pages() if page.kind == "section")

    print(f"\n[5/5] Canonical output saved to: {args.output_html}")
    if section_count:
        print(f"       Section-only pages: {section_count} in {args.output_html.with_suffix('')}")
    print(
        f"       Final token usage — input: {zai.total_input_tokens:,}, "
        f"output: {zai.total_output_tokens:,}"
    )
    print("\n       Done!")


if __name__ == "__main__":
    asyncio.run(main())
