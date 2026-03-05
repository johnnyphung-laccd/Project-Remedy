#!/usr/bin/env python3
"""Process the LAMC Commencement Program 2025 PDF through the ADA remediation pipeline."""

import asyncio
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, "src")

import httpx
import fitz  # PyMuPDF

from lamc_adrp.config import load_config
from lamc_adrp.database import DatabaseManager
from lamc_adrp.models import DocumentJob, FileType, JobStatus
from lamc_adrp.zai_client import ZAIClient
from lamc_adrp.converter import HTMLConverter

PDF_URL = "https://www.lamission.edu/sites/lamc.edu/files/2025-05/LAMC-Commencement-Program-2025.pdf"
SOURCE_PAGE = "https://lamission.edu/commencement/"
LINK_TEXT = "LAMC Commencement Program 2025"
MAX_PAGES = 5


async def main():
    # ---- Step 1: Load config ----
    print("[1/5] Loading configuration...")
    cfg = load_config()
    print(f"       Output dir: {cfg.output.output_dir}")
    print(f"       DB path:    {cfg.output.db_path}")

    # ---- Step 2: Download PDF ----
    print(f"\n[2/5] Downloading PDF from:\n       {PDF_URL}")
    async with httpx.AsyncClient(follow_redirects=True, verify=False, timeout=60) as client:
        resp = await client.get(PDF_URL)
        resp.raise_for_status()
        pdf_bytes = resp.content
    print(f"       Downloaded {len(pdf_bytes):,} bytes")

    # Compute SHA-256 and save
    file_hash = hashlib.sha256(pdf_bytes).hexdigest()
    download_dir = Path("output/downloads/pdf")
    download_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{file_hash[:16]}_LAMC-Commencement-Program-2025.pdf"
    local_path = download_dir / filename
    local_path.write_bytes(pdf_bytes)
    print(f"       Saved to: {local_path}")
    print(f"       SHA-256:  {file_hash[:16]}...")

    # Check page count
    doc = fitz.open(str(local_path))
    total_pages = len(doc)
    doc.close()
    print(f"       Total pages in PDF: {total_pages}")
    if total_pages > MAX_PAGES:
        print(f"       NOTE: Only processing first {MAX_PAGES} of {total_pages} pages")

    # ---- Step 3: OCR extraction (first 5 pages) ----
    print(f"\n[3/5] Extracting content via OCR (GLM-4.6V)...")
    zai = ZAIClient(cfg)
    await zai.start()

    try:
        if total_pages <= MAX_PAGES:
            # Process all pages normally
            ocr_markdown = await zai.ocr(file_path=local_path)
        else:
            # Manually process only first MAX_PAGES pages
            import base64

            doc = fitz.open(str(local_path))
            all_markdown = []
            for page_num in range(MAX_PAGES):
                page = doc[page_num]
                pix = page.get_pixmap(dpi=200)
                img_bytes = pix.tobytes("png")
                b64 = base64.b64encode(img_bytes).decode()

                print(f"       Processing page {page_num + 1}/{MAX_PAGES}...")
                page_md = await zai._ocr_single_image(
                    image_b64=b64,
                    mime="image/png",
                    page_hint=f"Page {page_num + 1} of {total_pages}",
                )
                all_markdown.append(f"<!-- Page {page_num + 1} -->\n{page_md}")
            doc.close()
            ocr_markdown = "\n\n---\n\n".join(all_markdown)

        print(f"       Extracted {len(ocr_markdown):,} characters of Markdown")
        print(f"       Token usage — input: {zai.total_input_tokens:,}, output: {zai.total_output_tokens:,}")

        # ---- Step 4: Plan + Generate HTML ----
        print(f"\n[4/5] Planning and generating accessible HTML...")
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

            # Plan
            print("       Phase 1: Creating conversion plan...")
            plan = await converter.plan(job)
            print(f"       Plan created ({len(plan):,} chars)")

            # Generate
            print("       Phase 2: Generating WCAG 2.1 AA HTML...")
            html = await converter.generate(job)
            print(f"       HTML generated ({len(html):,} chars)")

            # ---- Step 5: Save output ----
            output_path = Path("output/test_commencement.html")
            output_path.write_text(html, encoding="utf-8")
            print(f"\n[5/5] Output saved to: {output_path}")
            print(f"       Final token usage — input: {zai.total_input_tokens:,}, output: {zai.total_output_tokens:,}")

            if total_pages > MAX_PAGES:
                print(f"\n       NOTE: This HTML contains content from pages 1-{MAX_PAGES} of {total_pages}.")
                print(f"       The remaining {total_pages - MAX_PAGES} pages were not processed.")

            print("\n       Done!")

        finally:
            await db.close()
    finally:
        await zai.close()


if __name__ == "__main__":
    asyncio.run(main())
