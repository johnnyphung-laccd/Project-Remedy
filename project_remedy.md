Got it — all four document types, accessible SVG/HTML recreation for complex visuals, and 2,000+ document scale. Let me build this comprehensive PRD now.Let me read the docx skill file for building a proper Word document PRD:I'll build the PRD as a comprehensive docx file using the skill system. Let me read the skill first:Now I have all the research I need. Let me build the comprehensive PRD as a Word document.I now have comprehensive research. Let me build the PRD document:It appears the file creation and bash tools aren't available in this session — only the Chrome browser tools are loaded. Let me create this PRD as a comprehensive Markdown artifact instead, which you can then download:

I'll build this as a rendered document artifact since the computer tools aren't available in this session. This will be a thorough, downloadable PRD.The Chrome extension is disconnected, so I'll create this as a comprehensive Markdown artifact that renders beautifully and can be downloaded. Given the depth of research I've done, this will be a fully detailed PRD.

---

# LAMC ADA Document Remediation Pipeline — PRD v1.0

**Los Angeles Mission College | Title II Compliance Deadline: April 24, 2026**
**Version 1.0 — March 4, 2026**

---

## 1. Executive Summary

This PRD defines the **LAMC ADA Document Remediation Pipeline (LAMC-ADRP)** — an automated tool that discovers, extracts, converts, and deploys WCAG 2.1 Level AA compliant HTML replacements for all PDF, Word, PowerPoint, and Excel documents currently hosted on lamission.edu.

Public entities don't have to comply with the rule until April 2026 or April 2027, depending on their size. As part of LACCD (population well over 50,000), LAMC's deadline is **April 24, 2026**. All digital materials—including websites, web and mobile applications, digital documents (PDFs, Word documents, and PowerPoint presentations)—must be accessible to people with disabilities.

With an estimated **2,000+ non-compliant documents** on the LAMC website, manual remediation is infeasible. LAMC-ADRP automates this by crawling the entire site with **Crawl4AI**, processing documents through **Z.AI's GLM model family** (GLM-OCR → GLM-5 with thinking → GLM-4.6V for vision), and outputting LAMC-branded, accessible HTML pages in a deployment-ready folder structure. Original document URLs redirect to the new HTML pages.

The tool runs headlessly from a **cloud code instance** (no frontend). Configuration is via `.env` and YAML.

---

## 2. Problem Statement & Regulatory Context

### 2.1 The Compliance Challenge

This rule requires all covered institutions' web content—including distance education courses, public-facing websites, and online resources—to comply with the Web Content Accessibility Guidelines (WCAG) 2.1 by April 24, 2026. Non-compliance risks federal penalties up to $150,000 per violation, private lawsuits, and failed public service.

LAMC currently hosts 2,000+ documents created without accessibility considerations — lacking heading structures, alt text, tagged content, reading order, and sufficient color contrast.

### 2.2 Why HTML Conversion

Remediating individual PDFs is labor-intensive (30–60 min/doc by a specialist). For 2,000+ docs, that's 1,000–2,000 hours of manual work ($75K–$200K at vendor rates). Converting to semantic HTML is a proven strategy because HTML is inherently more accessible: screen readers parse it natively, it reflows for different screen sizes, and WCAG conformance is automatically validatable.

### 2.3 Regulatory Requirements Summary

| Requirement | Detail |
|---|---|
| **Governing Law** | ADA Title II, 28 CFR § 35.200(b) |
| **Technical Standard** | WCAG 2.1 Level AA (Levels A and AA success criteria) |
| **Compliance Deadline** | April 24, 2026 (populations ≥ 50,000) |
| **Covered Content** | Websites, apps, digital docs (PDF, DOCX, PPTX, XLSX), video, audio, social media |
| **Enforcement** | DOJ investigations, private lawsuits, penalties up to $150K/violation |
| **Exceptions (Limited)** | Archived content (pre-deadline, stored separately, labeled), preexisting conventional docs, third-party content, password-protected individualized docs |

---

## 3. Goals & Success Criteria

### 3.1 Primary Goals

- **G1 — Complete Discovery:** Crawl and identify 100% of PDF, DOCX, DOC, PPTX, PPT, XLSX, and XLS files on lamission.edu
- **G2 — Accurate Extraction:** ≥95% content fidelity using GLM-OCR
- **G3 — WCAG 2.1 AA Compliant HTML:** Zero critical violations, minimal warnings via axe-core + pa11y
- **G4 — LAMC Brand Consistency:** Congress Blue (#004590), Orange (#FF611A), Gray (#718089) styling on all output
- **G5 — Deployment-Ready Output:** Folder structure mirroring original URL paths, ready for web server upload
- **G6 — URL Preservation:** JSON/CSV redirect manifest for server-side redirects from original URLs to new HTML

### 3.2 Success Metrics

| Metric | Target | Measurement |
|---|---|---|
| Document Discovery Rate | ≥ 99% | Discovered vs. manual audit sample |
| OCR Accuracy | ≥ 95% | Character-level comparison on 50-doc sample |
| WCAG AA Pass Rate | 100% critical, ≥ 95% all | axe-core + pa11y scan of all output |
| Visual Fidelity | ≥ 90% | Human review of 50-doc sample |
| Processing Throughput | ≥ 100 docs/hour | Pipeline timing logs |
| Cost per Document | ≤ $0.15 avg | Z.AI billing / total docs |

---

## 4. Scope & Document Types

### 4.1 In Scope

- All **PDF** (.pdf), **Word** (.doc, .docx), **PowerPoint** (.ppt, .pptx), **Excel** (.xls, .xlsx) files on lamission.edu
- Documents on lamission.edu subdomains and LACCD shared domains (configurable)
- Complex visual conversion to **accessible SVG/HTML** (charts, diagrams, infographics)
- Image alt text generation via GLM-4.6V
- LAMC-branded HTML template, URL redirect manifest generation

### 4.2 Out of Scope

- Video/audio accessibility (captioning, transcripts) — separate initiative
- Web page HTML remediation (vs. linked documents)
- Mobile app / social media accessibility
- Real-time monitoring (future enhancement)
- Frontend dashboard — headless CLI only

### 4.3 Processing Strategy by File Type

| File Type | Extensions | Primary Parser | OCR Fallback | Conversion | Vision |
|---|---|---|---|---|---|
| PDF | .pdf | GLM-OCR | — | GLM-5 (thinking) | GLM-4.6V |
| Word | .doc, .docx | python-docx | GLM-OCR | GLM-5 (thinking) | GLM-4.6V |
| PowerPoint | .ppt, .pptx | python-pptx | GLM-OCR | GLM-5 (thinking) | GLM-4.6V |
| Excel | .xls, .xlsx | openpyxl | GLM-OCR | GLM-5 (thinking) | N/A |

---

## 5. System Architecture

### 5.1 High-Level Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                      LAMC-ADRP Pipeline                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  [1] CRAWL4AI         [2] DOWNLOAD        [3] GLM-OCR              │
│  Deep Crawl           Fetch & Store       Extract Content           │
│  lamission.edu ─────► Documents to  ────► Text, Tables,             │
│  Discover doc links   local storage       Images, Structure         │
│                                                                     │
│  [4] GLM-5 PLANNING   [5] GLM-5 CONVERT  [6] GLM-4.6V VISION      │
│  Analyze structure     Generate semantic   Process complex           │
│  Plan HTML layout      WCAG 2.1 AA HTML   images/charts to          │
│  (thinking mode) ────► with LAMC brand ──► accessible SVG/HTML      │
│                                                                     │
│  [7] VALIDATION        [8] DEPLOY                                   │
│  axe-core + pa11y     Output folder  ───► Web Server                │
│  Auto-remediate        structure +         (lamission.edu)           │
│  up to 3 cycles        redirect manifest                            │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.2 Data Flow

Each document flows as a **DocumentJob** object accumulating: original URL → local file path → OCR markdown → HTML plan → generated HTML → validation results → final output path. A **SQLite database** tracks job status for resumability — if interrupted, it resumes from the last completed stage.

---

## 6. Technology Stack

| Component | Technology | Version | Purpose |
|---|---|---|---|
| Runtime | Python | 3.11+ | Core application |
| Web Crawling | Crawl4AI | 0.8.x | Deep crawl for document discovery |
| OCR | Z.AI GLM-OCR | API | Text/table/formula extraction from PDFs & images |
| Conversion & Planning | Z.AI GLM-5 | API | Thinking-mode HTML planning + generation |
| Vision | Z.AI GLM-4.6V | API | Complex image analysis, alt text, SVG recreation |
| Word Parsing | python-docx | 1.x | Native .docx extraction |
| PPT Parsing | python-pptx | 0.6.x | Native .pptx extraction |
| Excel Parsing | openpyxl | 3.x | Native .xlsx extraction |
| Legacy Formats | LibreOffice (headless) | 7.x+ | .doc/.ppt/.xls → modern format conversion |
| WCAG Validation | axe-core (Playwright) | 4.x | Primary WCAG 2.1 AA scanning |
| WCAG Validation | pa11y | 7.x | Secondary accessibility validation |
| WCAG Validation | Lighthouse (CLI) | 12.x | Google accessibility scoring (100/100 target) |
| HTML Cleanup | Beautiful Soup | latest | Validate/sanitize generated HTML |
| Database | SQLite | 3.x | Job tracking, resumability, audit |
| Config | python-dotenv + YAML | — | Environment & pipeline configuration |
| Concurrency | asyncio + aiohttp | — | Async crawling + parallel API calls |

---

## 7. Pipeline Stages (Detailed)

### 7.1 Stage 1: Web Crawling & Document Discovery

Uses Crawl4AI's **BFSDeepCrawlStrategy** for breadth-first crawl of the entire lamission.edu domain.

**Configuration:**

| Parameter | Value | Description |
|---|---|---|
| start_url | `https://www.lamission.edu/` | Entry point |
| max_depth | 10 | Max link depth |
| include_external | False | Stay within domain |
| max_pages | 10,000 | Max pages to visit |
| file_extensions | .pdf, .doc, .docx, .ppt, .pptx, .xls, .xlsx | Target types |
| rate_limit | 2 req/sec | Polite crawling |
| user_agent | `LAMC-ADRP/1.0 (ADA Compliance)` | Purpose identification |
| resume_state | SQLite | Crash recovery |

**Output:** Deduplicated manifest of document URLs with metadata (source page, file type, size, last-modified, link text/context). The **link anchor text and surrounding context** from the referring page is captured — this is passed to GLM-5 during planning to help understand document purpose (e.g., "Spring 2025 Class Schedule" tells the model it's a schedule).

### 7.2 Stage 2: Document Download & Inventory

- **Download:** Async HTTP with retry (3x, exponential backoff), concurrent downloads capped at 10
- **Validation:** MIME type matches extension, non-zero size, PDFs parseable
- **Deduplication:** SHA-256 hash; same doc linked from multiple pages → process once, map all URLs
- **Legacy Conversion:** .doc → .docx, .ppt → .pptx, .xls → .xlsx via headless LibreOffice
- **Inventory:** Update SQLite with file path, hash, size, type, conversion status

### 7.3 Stage 3: OCR & Content Extraction (GLM-OCR)

**PDFs** go directly to GLM-OCR's layout parsing endpoint. GLM-OCR is a lightweight professional OCR model with parameters as small as 0.9B, yet it achieves state-of-the-art performance across multiple capabilities. Handles text, tables, formulas, complex layouts, code, handwriting, and mixed content. PDFs up to 50MB and 100 pages per request.

**Word/PPT/Excel** use native Python parsers first (python-docx, python-pptx, openpyxl) preserving structure. GLM-OCR as fallback for image-heavy or complex formatting that native parsers can't capture.

**API Details:**
- **Endpoint:** `POST https://api.z.ai/api/paas/v4/layout_parsing`
- **Model:** `glm-ocr`
- **Input:** File URL or base64
- **Output:** Structured Markdown with text, tables (HTML), formulas, image links
- **Cost:** $0.03/1M tokens (input and output) — extremely cheap for bulk processing

### 7.4 Stage 4: Planning & Thinking Phase (GLM-5)

This is the **intelligence differentiator**. Before generating HTML, GLM-5 with thinking mode analyzes each document and creates a conversion plan. The prompt includes extracted content, document type, link context from the referring page, the LAMC template, and WCAG requirements.

**GLM-5 thinks through:**
- Document purpose and audience (schedule? form? report? flyer?)
- Optimal heading hierarchy (H1–H6)
- What should be data tables vs. prose
- Images requiring GLM-4.6V processing
- Complex visuals to recreate as accessible SVG/HTML
- Reading order for multi-column layouts
- Form fields → accessible HTML forms
- Navigation landmarks and ARIA regions
- Content that can't be accurately converted (flagged for manual review)

**API Config:**

| Parameter | Value | Rationale |
|---|---|---|
| model | glm-5 | Best reasoning capability |
| thinking.type | enabled | Chain-of-thought for better planning |
| max_tokens | 8,192 | Detailed conversion plan |
| temperature | 0.3 | Deterministic planning |

**Cost:** $1.00/1M input, $3.20/1M output

### 7.5 Stage 5: Accessible HTML Generation (GLM-5)

Using the plan from Stage 4, GLM-5 generates the final HTML. The prompt includes the plan, extracted content, LAMC template, and explicit WCAG requirements as system instructions.

**HTML Requirements (embedded in prompt):**

- **Semantic Structure:** `<header>`, `<nav>`, `<main>`, `<article>`, `<section>`, `<footer>` landmarks. Single H1, logical H2–H6 nesting. No consecutive headings without intervening content. No skipped heading levels (e.g., H2 → H4)
- **Text Alternatives:** All images get descriptive `alt`. Decorative images: `alt=""` + `role="presentation"`. Complex images: long descriptions. No "image of..." or "picture of..." prefixes. Alt text must not duplicate adjacent caption or link text
- **Tables:** `<thead>`, `<th scope>`, `<caption>`. No layout tables. No empty table headers
- **Contrast:** Text 4.5:1, large text 3:1. Color never sole means of info
- **Keyboard:** All interactive elements navigable. Visible focus indicators. Tap targets ≥ 44x44px
- **Language:** `<html lang="en">`. Other languages use `lang` attributes
- **Responsive:** Reflow at 320px. No horizontal scroll at 400% zoom
- **Links:** Unique, descriptive text in context (no "click here", no duplicate "Read More" / "Learn More"). External links noted with `aria-label` or visible indicator
- **Forms:** `<label>` for all inputs. Descriptive error messages
- **ARIA:** Only when native HTML semantics insufficient. No redundant ARIA roles on semantic elements (e.g., no `role="banner"` on `<header>`)
- **Skip Nav:** "Skip to main content" as first focusable element
- **Text Formatting:** No `text-align: justify` (causes uneven word spacing for dyslexic users). Use `<em>` / `<strong>` instead of `<i>` / `<b>`. Paragraph text ≥ 1rem
- **Crawlable Links:** All links use `<a href>`, never JavaScript-only navigation (`onclick` without `href`)
- **Orphaned Content:** No floating text outside semantic containers. All content within landmarks

**API Config:** model=glm-5, thinking=enabled, max_tokens=32768, temperature=0.2

### 7.6 Stage 6: Image & Visual Processing (GLM-4.6V)

Documents with complex visuals are processed by GLM-4.6V (128K context, SOTA multimodal vision).

**Processing Modes:**

- **Alt Text:** Photographs/simple graphics → concise descriptive alt text
- **Chart Recreation:** Bar/line/pie/scatter charts → GLM-4.6V extracts data → pipeline generates accessible SVG with ARIA labels + HTML data table + text summary
- **Diagram → Described HTML:** Flowcharts/org charts → identifies components and relationships → structured description list or nested list with ARIA roles
- **Infographic Decomposition:** Complex infographics → broken into logical sections, each described separately
- **Decorative Detection:** Purely decorative images → `alt=""` + `role="presentation"`

**Cost:** $0.30/1M input, $0.90/1M output

### 7.7 Stage 7: WCAG Validation & QA (Triple-Layer)

Every generated HTML page is validated by **three independent tools** before acceptance:

1. Run **axe-core** scan (via Playwright) — industry-standard WCAG 2.1 AA engine
2. Run **pa11y** scan (HTML_CodeSniffer) — independent second opinion, catches different rule sets
3. Run **Lighthouse accessibility audit** (via Playwright/CLI) — Google's scoring engine (uses axe-core internally but applies additional checks: tap target sizing, crawlable links, document structure). Target: **100/100 accessibility score**
4. Merge and deduplicate findings from all three tools
5. Zero critical/serious violations across all three → **PASS** → Stage 8
6. Violations found → **AUTO-REMEDIATE:** feed errors back to GLM-5 with WCAG success criteria codes, tool-specific error messages, and fix guidance. Request corrected HTML
7. Re-validate with all three tools (max **3 remediation cycles**)
8. Still failing after 3 → **FLAG** for manual review, include with warning banner
9. All results logged to SQLite for audit trail — per-tool scores stored for reporting

**Why three tools:** Each has different rule coverage and severity thresholds. axe-core and pa11y together catch ~85% of automated WCAG issues. Lighthouse adds scoring context and additional checks (tap targets, crawlable links, font sizing). This triple-layer approach approximates what a WAVE scan would catch — without the per-page cost.

### 7.8 Stage 8: Output Organization & Deployment

```
output/
├── index.html                          # Master index of all converted docs
├── assets/
│   ├── css/lamc-accessible.css         # Shared LAMC-branded stylesheet
│   ├── images/                         # Extracted images with alt text
│   └── svg/                            # Recreated accessible charts/diagrams
├── documents/
│   ├── academics/
│   │   ├── spring-2025-schedule.html
│   │   └── course-catalog-2024.html
│   ├── admissions/
│   │   └── financial-aid-faq.html
│   └── ... (mirrors site structure)
├── redirect-manifest.json              # URL mapping: original → new
├── redirect-manifest.csv               # CSV for server config
├── .htaccess                           # Apache redirects (auto-generated)
├── nginx-redirects.conf                # Nginx redirects (auto-generated)
└── validation-report.html              # Summary of all validation results
```

**Redirect Manifest Example:**
```json
{
  "redirects": [
    {
      "original": "/sites/lamc.edu/files/2023-09/catalog-2023-2024.pdf",
      "html_path": "/documents/academics/catalog-2023-2024.html",
      "document_type": "pdf",
      "conversion_date": "2026-03-04",
      "validation_status": "pass",
      "wcag_violations": 0
    }
  ]
}
```

---

## 8. LAMC Brand Styling Specifications

All output HTML uses a shared `lamc-accessible.css` file encoding these specs while maintaining WCAG 2.1 AA contrast:

| Element | Specification | WCAG Note |
|---|---|---|
| Primary Color | #004590 (Congress Blue) | 4.5:1+ on white ✓ |
| Accent Color | #FF611A (Orange) | Darkened to #D94E0F for small text (4.5:1) |
| Neutral | #718089 → #5A6670 (darkened) | Meets 4.5:1 on white |
| Background | #FFFFFF | Primary |
| Alt Background | #F5F7FA | Section alternation |
| Text Color | #1A1A2E | Max contrast |
| Font | system-ui, -apple-system, Segoe UI, Roboto, sans-serif | System fonts |
| Body Size | 1rem (16px) | Resizable to 200% |
| Line Height | 1.6 | WCAG 1.4.12 |
| Max Width | 72ch (~800px) | Comfortable reading |
| Links | Underlined + color; visible focus ring | Not color-only |
| Skip Nav | "Skip to main content" — first focusable | WCAG 2.4.1 |
| Header | LAMC logo (with alt), page title, breadcrumb | WCAG 2.4.2/2.4.8 |
| Footer | Contact info, accessibility statement link, original doc link | WCAG 3.2.4 |

---

## 9. WCAG 2.1 AA Compliance Checklist (for Generated Output)

Embedded in the GLM-5 system prompt and validated in Stage 7. Key criteria:

| SC | Level | Criterion | Implementation |
|---|---|---|---|
| 1.1.1 | A | Non-text Content | Alt text on all images; empty alt for decorative |
| 1.3.1 | A | Info & Relationships | Headings, lists, tables, labels convey structure |
| 1.3.2 | A | Meaningful Sequence | DOM order = visual reading order |
| 1.4.1 | A | Use of Color | Color never sole means of info |
| 1.4.3 | AA | Contrast (Minimum) | Text 4.5:1, large text 3:1 |
| 1.4.4 | AA | Resize Text | 200% without loss |
| 1.4.5 | AA | Images of Text | Real text, not images of text |
| 1.4.10 | AA | Reflow | 320px width, no horizontal scroll |
| 1.4.11 | AA | Non-text Contrast | UI/graphics 3:1 |
| 1.4.12 | AA | Text Spacing | Works with increased spacing |
| 2.1.1 | A | Keyboard | All functionality via keyboard |
| 2.4.1 | A | Bypass Blocks | Skip nav link |
| 2.4.2 | A | Page Titled | Descriptive `<title>` |
| 2.4.6 | AA | Headings & Labels | Descriptive headings/labels |
| 2.4.7 | AA | Focus Visible | Visible focus indicator |
| 3.1.1 | A | Language of Page | `lang="en"` on `<html>` |
| 3.1.2 | AA | Language of Parts | `lang` on foreign-language content |
| 4.1.2 | A | Name, Role, Value | All UI components have accessible names |
| 4.1.3 | AA | Status Messages | ARIA live regions for status |

---

## 10. Configuration & Environment

### .env File

```bash
# Z.AI API Configuration
ZAI_API_KEY=your-z-ai-api-key-here
ZAI_BASE_URL=https://api.z.ai/api/paas/v4

# Crawl Configuration
CRAWL_START_URL=https://www.lamission.edu/
CRAWL_MAX_DEPTH=10
CRAWL_MAX_PAGES=10000
CRAWL_RATE_LIMIT=2

# Processing
MAX_CONCURRENT_API_CALLS=5
MAX_RETRIES=3
RETRY_BACKOFF_BASE=2

# Output
OUTPUT_DIR=./output
LOG_DIR=./logs
DB_PATH=./pipeline.db

# Validation
VALIDATION_MAX_REMEDIATION_CYCLES=3
VALIDATION_FAIL_ON_SERIOUS=true
```

A **YAML config** provides additional settings: file type handling, brand overrides, domain include/exclude lists, custom prompt templates — allowing adaptation for other LACCD campuses.

---

## 11. Error Handling & Logging

| Category | Example | Handling |
|---|---|---|
| Crawl Errors | 403/404, timeout | Log, skip, retry 3x, report as 'unreachable' |
| Download Errors | Corrupt/zero-byte file | Retry 3x; flag 'download_failed' |
| OCR Errors | API timeout, unreadable | Retry with backoff; flag for manual review |
| Conversion Errors | Invalid HTML, token overflow | Retry simplified; split long docs |
| Validation Errors | WCAG violations | Auto-remediate up to 3 cycles; then flag |
| Vision Errors | GLM-4.6V can't interpret | Generic alt text; flag manual review |
| System Errors | Disk full, crash | Resume from SQLite checkpoint |

Logging: console (structured, colorized) + rotating JSON log files. Summary report at completion.

---

## 12. Cost Estimation

| Stage | Model | Avg Tokens/Doc | Cost/1M Tokens | Est./Doc | Est. Total (2,000) |
|---|---|---|---|---|---|
| OCR Extraction | GLM-OCR | ~50K in + 20K out | $0.03 (both) | $0.002 | $4 |
| Planning | GLM-5 | ~15K in + 3K out | $1.00 / $3.20 | $0.025 | $50 |
| HTML Generation | GLM-5 | ~20K in + 15K out | $1.00 / $3.20 | $0.068 | $136 |
| Vision (30% of docs) | GLM-4.6V | ~5K in + 2K out | $0.30 / $0.90 | $0.006 | $12 |
| Remediation (10%) | GLM-5 | ~10K in + 10K out | $1.00 / $3.20 | $0.004 | $8 |
| **TOTAL** | — | — | — | **~$0.105** | **~$210** |

**Estimated total API cost: ~$210** — orders of magnitude cheaper than manual remediation ($75K–$200K) or commercial tools ($40K–$100K). Compute costs for the cloud instance: ~$10–$30 additional.

---

## 13. Timeline & Milestones

| Phase | Duration | Milestone | Deliverable |
|---|---|---|---|
| Setup & Crawl | Week 1 | Pipeline infra, Crawl4AI integration | Working crawler + manifest |
| OCR Integration | Week 1–2 | GLM-OCR pipeline | Extracted Markdown for all types |
| Planning & Conversion | Week 2–3 | GLM-5 planning + generation | HTML for 50-doc sample batch |
| Vision & Styling | Week 3 | GLM-4.6V + LAMC CSS | Styled, image-processed pages |
| Validation | Week 3–4 | axe-core + pa11y + auto-remediation | Validated WCAG AA output |
| Full Run | Week 4 | Process all 2,000+ docs | Complete output directory |
| QA & Deploy | Week 4–5 | Manual flagged-doc review, server deploy | Live on lamission.edu |

**Total: 4–5 weeks from kickoff to deployment.**

---

## 14. Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| GLM-5 generates non-compliant HTML | High | Medium | Multi-layer validation + 3-cycle auto-remediation. Manual queue for persistent failures |
| Poor-quality scanned PDFs | Medium | Medium | GLM-OCR handles degraded inputs (SOTA); GLM-4.6V fallback; flag unreadable docs |
| Z.AI API rate limits/outages | High | Low | Exponential backoff, concurrency cap, resumability. Monitor status |
| Complex layouts lose meaning | Medium | Medium | GLM-5 thinking mode plans before generating. Link context provides semantics |
| Pipeline crash mid-run | Medium | Low | SQLite state tracking + Crawl4AI crash recovery |
| Brand mismatch | Low | Low | CSS template in shared stylesheet; GLM-5 generates semantic HTML only |
| Deadline pressure | High | Medium | 100+ docs/hour throughput. 24/7 cloud execution. Parallel processing |

---

## 15. Future Enhancements

- **Real-time Monitoring:** Webhook/Slack alerts when new non-compliant docs are uploaded
- **Continuous Compliance:** Weekly crawls to detect and process new/updated documents
- **Multi-campus Support:** Parameterize for other LACCD campuses (LACC, ELAC, WLAC) with campus branding
- **WCAG 2.2 Upgrade:** Update validation and prompts for future-proofing
- **Human Review Dashboard:** Web UI for accessibility specialists to review flagged docs
- **Source Remediation:** Option to remediate source PDFs (tagged PDF) for frequently updated docs
- **Spanish Localization:** Bilingual handling (critical for LAMC demographics)
- **Analytics:** Track converted doc access to prioritize high-traffic content

---

## 16. Appendices

### Appendix A: API References

- Z.AI GLM-OCR: https://docs.z.ai/guides/vlm/glm-ocr
- Z.AI GLM-5: https://docs.z.ai/guides/llm/glm-5
- Z.AI GLM-4.6V: https://docs.z.ai/guides/vlm/glm-4.6v
- Z.AI Pricing: https://docs.z.ai/guides/overview/pricing
- Crawl4AI: https://docs.crawl4ai.com/
- WCAG 2.1: https://www.w3.org/TR/WCAG21/
- ADA Title II Rule: https://www.ada.gov/resources/web-rule-first-steps/

### Appendix B: LAMC Website

| Property | Value |
|---|---|
| URL | https://www.lamission.edu/ |
| Institution | Los Angeles Mission College |
| District | LACCD |
| Address | 13356 Eldridge Avenue, Sylmar, CA 91342 |
| Primary | #004590 (Congress Blue) |
| Accent | #FF611A (Orange) |
| Neutral | #718089 (Rolling Stone Gray) |
| Mascot | Eagles |

### Appendix C: .env Setup Instructions

Once ready to begin development and testing, create a `.env` file in the project root with your Z.AI API key. You can insert the key at any point — the pipeline will validate the key on startup and run a health check against the GLM-OCR endpoint before beginning the full crawl.

---

*End of Document — LAMC-ADRP PRD v1.0*

---

Johnny, here's your comprehensive PRD. It covers all 8 pipeline stages in detail, from Crawl4AI deep crawling through GLM-OCR extraction, GLM-5 thinking-mode planning and HTML generation, GLM-4.6V vision processing, automated WCAG validation with auto-remediation loops, and deployment-ready output with redirect manifests.

A few things to note about the `.env` file for testing: whenever you're ready, just create a `.env` file with your `ZAI_API_KEY` and I can start building the actual pipeline code and testing it against a small sample of pages from lamission.edu. The GLM-OCR endpoint is the cheapest to test with ($0.03/M tokens), so we can validate the full extraction → conversion flow on a handful of documents before committing to the full 2,000+ doc run.

Would you like me to start building the actual Python pipeline code next, or would you prefer to refine any section of the PRD first?