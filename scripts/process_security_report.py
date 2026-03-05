#!/usr/bin/env python3
"""Process the LAMC 2024 Annual Security Report through the ADA remediation pipeline.

Downloads the PDF, extracts pages 1-5 via OCR, then generates accessible HTML.
"""

import asyncio
import hashlib
import sys
from pathlib import Path

# Project imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import base64
import httpx
import fitz  # PyMuPDF

from lamc_adrp.config import load_config
from lamc_adrp.database import DatabaseManager
from lamc_adrp.models import DocumentJob, FileType, JobStatus
from lamc_adrp.zai_client import ZAIClient
from lamc_adrp.converter import HTMLConverter

# Constants
DOC_URL = "https://www.lamission.edu/sites/lamc.edu/files/2024-09/2024-Annual-Security-Report.pdf"
SOURCE_PAGE = "https://lamission.edu/campus-life/campus-safety/sheriffs-office/crime-stats"
LINK_TEXT = "2024 Annual Security Report"
PAGES_TO_PROCESS = 5
OUTPUT_HTML = Path(__file__).resolve().parent.parent / "output" / "test_security_report.html"
DOWNLOAD_DIR = Path(__file__).resolve().parent.parent / "output" / "downloads" / "pdf"


async def main() -> None:
    # ── Step 1: Download the PDF ──────────────────────────────────────
    print("=" * 70)
    print("STEP 1: Downloading PDF")
    print("=" * 70)
    print(f"  URL: {DOC_URL}")

    async with httpx.AsyncClient(follow_redirects=True, verify=False, timeout=60.0) as http:
        resp = await http.get(DOC_URL)
        resp.raise_for_status()
        pdf_bytes = resp.content

    print(f"  Downloaded: {len(pdf_bytes):,} bytes ({len(pdf_bytes)/1024/1024:.1f} MB)")

    # ── Step 2: Save with SHA-256 prefix ──────────────────────────────
    print("\n" + "=" * 70)
    print("STEP 2: Saving PDF with SHA-256 prefix")
    print("=" * 70)

    file_hash = hashlib.sha256(pdf_bytes).hexdigest()
    safe_name = "2024-Annual-Security-Report.pdf"
    saved_name = f"{file_hash[:16]}_{safe_name}"
    saved_path = DOWNLOAD_DIR / saved_name
    saved_path.write_bytes(pdf_bytes)

    print(f"  SHA-256: {file_hash}")
    print(f"  Saved to: {saved_path}")

    # ── Step 3: Check page count, confirm we process pages 1-5 ────────
    print("\n" + "=" * 70)
    print("STEP 3: Inspecting PDF with PyMuPDF")
    print("=" * 70)

    doc = fitz.open(str(saved_path))
    total_pages = len(doc)
    print(f"  Total pages: {total_pages}")
    print(f"  Processing pages: 1-{PAGES_TO_PROCESS} (cover, TOC, intro sections)")
    doc.close()

    # ── Step 4: OCR extraction via ZAIClient ──────────────────────────
    print("\n" + "=" * 70)
    print("STEP 4: OCR extraction via ZAIClient (pages 1-5)")
    print("=" * 70)

    cfg = load_config(
        env_path=Path(__file__).resolve().parent.parent / ".env",
        yaml_path=Path(__file__).resolve().parent.parent / "config.yaml",
    )
    zai = ZAIClient(cfg)
    await zai.start()

    try:
        doc = fitz.open(str(saved_path))
        all_markdown: list[str] = []

        for page_num in range(PAGES_TO_PROCESS):
            page = doc[page_num]
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            b64 = base64.b64encode(img_bytes).decode()

            print(f"  Processing page {page_num + 1}/{PAGES_TO_PROCESS}...", end=" ", flush=True)

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
        print(f"\n  --- MARKDOWN PREVIEW (first 500 chars) ---")
        print(combined_markdown[:500])
        print("  --- END PREVIEW ---")

        # ── Step 5: Plan and generate accessible HTML ─────────────────
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

            # Stage 4: Planning
            print("  Running planning stage (GLM-5 with thinking)...")
            plan = await converter.plan(job)
            print(f"  Plan generated: {len(plan)} chars")
            print(f"\n  --- PLAN PREVIEW (first 400 chars) ---")
            print(plan[:400])
            print("  --- END PLAN PREVIEW ---\n")

            # Stage 5: HTML Generation
            print("  Running HTML generation stage (GLM-5 with thinking)...")
            html = await converter.generate(job)
            print(f"  HTML generated: {len(html):,} chars")

        finally:
            await db.close()

    finally:
        await zai.close()

    # ── Step 6: Save output HTML ──────────────────────────────────────
    print("\n" + "=" * 70)
    print("STEP 6: Saving output HTML")
    print("=" * 70)

    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"  Saved to: {OUTPUT_HTML}")
    print(f"  File size: {OUTPUT_HTML.stat().st_size:,} bytes")

    # Token usage summary
    print("\n" + "=" * 70)
    print("DONE - Token Usage Summary")
    print("=" * 70)
    print(f"  Input tokens:  {zai.total_input_tokens:,}")
    print(f"  Output tokens: {zai.total_output_tokens:,}")
    print(f"  Output file:   {OUTPUT_HTML}")


if __name__ == "__main__":
    asyncio.run(main())
