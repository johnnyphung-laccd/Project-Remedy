# Project Remedy

**Automated ADA Document Remediation Pipeline for Los Angeles Mission College**

Project Remedy discovers, extracts, and converts non-accessible documents (PDF, Word, PowerPoint, Excel) hosted on [lamission.edu](https://www.lamission.edu/) into WCAG 2.1 Level AA compliant HTML pages — automatically.

## Why This Exists

The [ADA Title II Final Rule](https://www.ada.gov/resources/web-rule-first-steps/) requires all public entities to make their web content accessible by **April 24, 2026**. LAMC has 2,000+ documents on its website that were created without accessibility in mind. Manual remediation at 30-60 minutes per document would cost $75K-$200K. Project Remedy automates this for roughly **$210 in API costs**.

## How It Works

The pipeline runs in 8 stages:

```
[1] CRAWL         [2] DOWNLOAD       [3] EXTRACT        [4] PLAN
Crawl4AI deep     Fetch docs to      GLM-4.6V vision    GLM-5 analyzes
crawl of          local storage      extracts content    structure, plans
lamission.edu     with dedup         as Markdown         HTML conversion

[5] GENERATE      [6] VISION         [7] VALIDATE       [8] DEPLOY
GLM-5 produces    GLM-4.6V creates   axe-core + pa11y   Output folder
WCAG AA HTML      alt text, SVGs     + Lighthouse        with redirects
with LAMC brand   for images         (triple-layer)      ready for server
```

### Stage Details

1. **Crawl** — Uses [Crawl4AI](https://github.com/unclecode/crawl4ai) to deep-crawl lamission.edu and discover all linked documents (.pdf, .doc, .docx, .ppt, .pptx, .xls, .xlsx). Captures link text and surrounding context from the referring page.

2. **Download** — Async downloads with SHA-256 deduplication. Legacy formats (.doc, .ppt, .xls) are converted to modern formats via headless LibreOffice.

3. **Extract** — PDFs are rendered page-by-page to images via PyMuPDF and sent to GLM-4.6V for vision-based OCR. Word/PowerPoint/Excel use native Python parsers (python-docx, python-pptx, openpyxl) with GLM-4.6V as fallback.

4. **Plan** — GLM-5 with thinking mode analyzes the extracted content and creates a conversion plan: heading hierarchy, table identification, form handling, image processing needs, landmark regions.

5. **Generate** — GLM-5 generates semantic HTML following the plan, with LAMC branding (Congress Blue #004590, Orange #FF611A), skip navigation, landmark roles, accessible forms, and all WCAG 2.1 AA requirements embedded in the prompt.

6. **Vision** — GLM-4.6V processes complex images: generates alt text, recreates charts as accessible SVGs with data tables, describes diagrams as structured HTML.

7. **Validate** — Triple-layer free validation:
   - **axe-core** (via Playwright) — industry-standard WCAG engine
   - **pa11y** (HTML_CodeSniffer) — independent rule set
   - **Lighthouse** — Google's accessibility scoring (target: 100/100)
   - Failed documents are auto-remediated up to 3 cycles by feeding violations back to GLM-5.

8. **Deploy** — Organizes output into a deployment-ready folder structure mirroring the original URL paths, generates redirect manifests (JSON, CSV, .htaccess, nginx.conf), a master index, and a validation report.

## Tech Stack

| Component | Technology |
|---|---|
| Runtime | Python 3.11+ |
| Web Crawling | Crawl4AI |
| AI Models | ZhipuAI GLM-5, GLM-4.6V (via Coding Plan) |
| PDF Rendering | PyMuPDF |
| Document Parsing | python-docx, python-pptx, openpyxl |
| WCAG Validation | axe-core, pa11y, Lighthouse |
| Database | SQLite (async via aiosqlite) |
| CLI | Click + Rich |

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js (for pa11y and Lighthouse)
- A [ZhipuAI](https://open.bigmodel.cn/) API key (Coding Plan or pay-as-you-go)

### Installation

```bash
# Clone the repo
git clone https://github.com/johnnyphung-laccd/Project-Remedy.git
cd Project-Remedy

# Install Python dependencies
pip install -e .

# Install Playwright browser
playwright install chromium

# Install accessibility validators
npm install -g pa11y lighthouse

# Configure
cp .env.example .env
# Edit .env and add your ZAI_API_KEY
```

### Usage

```bash
# Run the full pipeline
lamc-adrp run

# Or run stages individually
lamc-adrp crawl       # Discover documents
lamc-adrp process     # Extract + convert
lamc-adrp validate    # Run WCAG validation
lamc-adrp deploy      # Generate output structure

# Check progress
lamc-adrp status

# Retry failed documents
lamc-adrp retry-failed
```

### Configuration

Environment variables in `.env`:

```bash
ZAI_API_KEY=your-api-key-here
ZAI_BASE_URL=https://open.bigmodel.cn/api/coding/paas/v4
CRAWL_START_URL=https://www.lamission.edu/
CRAWL_MAX_DEPTH=10
MAX_CONCURRENT_API_CALLS=5
```

See `config.example.yaml` for additional options.

## Output Structure

```
output/
├── index.html                    # Master index of all converted docs
├── assets/
│   ├── css/lamc-accessible.css  # LAMC-branded accessible stylesheet
│   ├── images/                  # Extracted images
│   └── svg/                     # Recreated accessible charts
├── documents/                   # Converted HTML (mirrors site structure)
├── redirect-manifest.json       # URL mapping for server redirects
├── .htaccess                    # Apache redirect rules
├── nginx-redirects.conf         # Nginx redirect rules
└── validation-report.html       # WCAG validation summary
```

## Accessibility Features

Every generated HTML page includes:

- Skip-to-content navigation
- Semantic landmarks (`<header>`, `<nav>`, `<main>`, `<footer>`)
- Proper heading hierarchy (single H1, logical nesting)
- Data tables with `<caption>`, `<thead>`, `<th scope>`
- Descriptive alt text (no "image of..." prefixes)
- LAMC brand colors meeting WCAG contrast ratios
- Keyboard navigable with visible focus indicators
- Print button (forms say "Print This Form", documents say "Print This Page")
- Responsive down to 320px width
- `lang="en"` with per-element language annotations
- Link to original document in footer

## Cost Estimate

For 2,000 documents on the ZhipuAI Coding Plan:

| Stage | Est. Total |
|---|---|
| OCR Extraction (GLM-4.6V) | ~$12 |
| Planning (GLM-5) | ~$50 |
| HTML Generation (GLM-5) | ~$136 |
| Vision Processing (GLM-4.6V) | ~$12 |
| Auto-remediation (GLM-5) | ~$8 |
| **Total** | **~$210** |

## Regulatory Context

- **Governing Law:** ADA Title II, 28 CFR § 35.200(b)
- **Technical Standard:** WCAG 2.1 Level AA
- **Compliance Deadline:** April 24, 2026
- **Enforcement:** Penalties up to $150,000 per violation

## License

MIT
