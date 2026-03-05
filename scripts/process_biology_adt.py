#!/usr/bin/env python3
"""Process ADT - Biology 2025-2026 DOCX through the LAMC ADA remediation pipeline."""

import sys
sys.path.insert(0, "src")

import asyncio
import hashlib
from pathlib import Path

import httpx

from lamc_adrp.config import load_config
from lamc_adrp.models import DocumentJob, FileType, JobStatus
from lamc_adrp.database import DatabaseManager
from lamc_adrp.zai_client import ZAIClient
from lamc_adrp.converter import HTMLConverter

# --- Constants ---
DOC_URL = "https://lamission.edu/sites/lamc.edu/files/2026-01/ADT%20-%20Biology%2025-26_ADA.docx"
SOURCE_PAGE = "https://lamission.edu/student-services/counseling/associate-degrees-for-transfer"
LINK_TEXT = "ADT - Biology 2025-2026"
OUTPUT_HTML = Path("output/test_biology_adt.html")
DOWNLOAD_DIR = Path("output/downloads/docx")


async def main():
    # =====================================================================
    # Step 1: Download the DOCX
    # =====================================================================
    print("=" * 70)
    print("STEP 1: Downloading DOCX document")
    print("=" * 70)
    print(f"  URL: {DOC_URL}")

    async with httpx.AsyncClient(follow_redirects=True, verify=False, timeout=60.0) as client:
        response = await client.get(DOC_URL)
        response.raise_for_status()
        doc_bytes = response.content

    print(f"  Downloaded: {len(doc_bytes):,} bytes")
    print(f"  Status: {response.status_code}")
    print(f"  Content-Type: {response.headers.get('content-type', 'unknown')}")

    # =====================================================================
    # Step 2: Save with SHA-256 prefix
    # =====================================================================
    print("\n" + "=" * 70)
    print("STEP 2: Saving file with SHA-256 prefix")
    print("=" * 70)

    file_hash = hashlib.sha256(doc_bytes).hexdigest()
    filename = f"{file_hash[:16]}_ADT_Biology_25-26_ADA.docx"
    local_path = DOWNLOAD_DIR / filename
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(doc_bytes)

    print(f"  SHA-256: {file_hash}")
    print(f"  Saved to: {local_path}")
    print(f"  File size: {local_path.stat().st_size:,} bytes")

    # =====================================================================
    # Step 3: Native extraction with python-docx
    # =====================================================================
    print("\n" + "=" * 70)
    print("STEP 3: Native content extraction with python-docx")
    print("=" * 70)

    from docx import Document as DocxDocument
    import re

    doc = DocxDocument(str(local_path))

    # Count images and paragraphs
    image_count = sum(1 for rel in doc.part.rels.values() if "image" in rel.reltype)
    paragraph_count = len(doc.paragraphs)
    print(f"  Paragraphs: {paragraph_count}")
    print(f"  Images: {image_count}")
    print(f"  Image-heavy ratio: {image_count / max(paragraph_count, 1):.2f}")

    # Extract content natively
    parts = []
    for element in doc.element.body:
        tag = element.tag.split("}")[-1] if "}" in element.tag else element.tag

        if tag == "p":
            from docx.text.paragraph import Paragraph
            para = Paragraph(element, doc)
            text = ""
            for run in para.runs:
                run_text = run.text or ""
                if not run_text:
                    continue
                if run.bold:
                    run_text = f"**{run_text}**"
                if run.italic:
                    run_text = f"*{run_text}*"
                text += run_text
            text = text.strip()
            if not text:
                continue

            style_name = (para.style.name or "").lower() if para.style else ""
            if style_name.startswith("heading"):
                match = re.search(r"\d+", style_name)
                level = int(match.group()) if match else 1
                level = min(level, 6)
                parts.append(f"{'#' * level} {text}")
            elif style_name.startswith("list bullet") or "list" in style_name and "number" not in style_name:
                parts.append(f"- {text}")
            elif style_name.startswith("list number"):
                parts.append(f"1. {text}")
            else:
                parts.append(text)

        elif tag == "tbl":
            from docx.table import Table
            table = Table(element, doc)
            rows = table.rows
            if not rows:
                continue
            md_rows = []
            for i, row in enumerate(rows):
                cells = [cell.text.strip().replace("|", "\\|") for cell in row.cells]
                md_rows.append("| " + " | ".join(cells) + " |")
                if i == 0:
                    md_rows.append("| " + " | ".join("---" for _ in cells) + " |")
            parts.append("\n".join(md_rows))

    markdown_content = "\n\n".join(parts)

    print(f"\n  Extracted markdown length: {len(markdown_content):,} chars")
    print(f"  Non-empty parts: {len(parts)}")

    # Check quality of extraction
    use_ocr_fallback = False
    if len(markdown_content.strip()) < 100:
        print("  WARNING: Very short extraction, will use OCR fallback")
        use_ocr_fallback = True
    elif image_count > 0 and paragraph_count > 0 and image_count / paragraph_count > 0.5:
        print("  WARNING: Image-heavy document, will use OCR fallback")
        use_ocr_fallback = True

    print("\n  --- Extracted Markdown Preview (first 2000 chars) ---")
    print(markdown_content[:2000])
    print("  --- End Preview ---")

    # =====================================================================
    # Step 4-5: HTMLConverter - Plan and Generate accessible HTML
    # =====================================================================
    print("\n" + "=" * 70)
    print("STEP 4-5: Planning and generating accessible HTML via HTMLConverter")
    print("=" * 70)

    cfg = load_config()
    db = DatabaseManager(Path(cfg.output.db_path))
    await db.connect()

    zai = ZAIClient(cfg)
    await zai.start()

    try:
        # Create a DocumentJob
        job = DocumentJob(
            url=DOC_URL,
            source_page_url=SOURCE_PAGE,
            link_text=LINK_TEXT,
            link_context="Associate Degrees for Transfer - Counseling page",
            file_type=FileType.DOCX,
            local_path=str(local_path),
            file_hash=file_hash,
            file_size=len(doc_bytes),
            status=JobStatus.EXTRACTED,
            ocr_markdown=markdown_content,
        )

        if use_ocr_fallback:
            print("  Using OCR fallback for extraction...")
            ocr_md = await zai.ocr(file_path=local_path)
            job.ocr_markdown = ocr_md
            markdown_content = ocr_md
            print(f"  OCR markdown length: {len(ocr_md):,} chars")
            print("\n  --- OCR Markdown Preview (first 2000 chars) ---")
            print(ocr_md[:2000])
            print("  --- End OCR Preview ---")

        await db.create_job(job)
        print(f"  Job created: {job.id}")

        # Use HTMLConverter for planning + generation
        converter = HTMLConverter(cfg, zai, db)

        print("\n  Stage 4: Planning conversion...")
        plan = await converter.plan(job)
        print(f"  Plan generated: {len(plan):,} chars")
        print("\n  --- Conversion Plan Preview (first 1500 chars) ---")
        print(plan[:1500])
        print("  --- End Plan Preview ---")

        print("\n  Stage 5: Generating accessible HTML...")
        full_html = await converter.generate(job)
        print(f"  HTML generated: {len(full_html):,} chars")

        # =====================================================================
        # Step 6: Save output HTML
        # =====================================================================
        print("\n" + "=" * 70)
        print("STEP 6: Saving output HTML")
        print("=" * 70)

        OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_HTML.write_text(full_html, encoding="utf-8")
        print(f"  Saved to: {OUTPUT_HTML}")
        print(f"  File size: {OUTPUT_HTML.stat().st_size:,} bytes")

        # Print token usage
        print(f"\n  Token usage — input: {zai.total_input_tokens:,}, output: {zai.total_output_tokens:,}")
        print("\n  DONE!")

    finally:
        await zai.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
