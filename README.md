# Project Remedy

**Automated ADA Document Remediation Pipeline**

Project Remedy discovers, extracts, and converts non-accessible documents (PDF, Word, PowerPoint, Excel) hosted on any website into WCAG 2.1 Level AA compliant HTML pages — automatically.

## Why This Exists

The [ADA Title II Final Rule](https://www.ada.gov/resources/web-rule-first-steps/) requires all public entities to make their web content accessible by **April 24, 2026**. Many institutions have thousands of documents on their websites that were created without accessibility in mind. Manual remediation at 30-60 minutes per document is prohibitively expensive. Project Remedy automates this at a fraction of the cost.

## How It Works

The pipeline runs in 8 stages:

```
[1] CRAWL         [2] DOWNLOAD       [3] EXTRACT        [4] PLAN
Crawl4AI deep     Fetch docs to      Vision model       LLM analyzes
crawl of target   local storage      extracts content    structure, plans
website           with dedup         as Markdown         HTML conversion

[5] GENERATE      [6] VISION         [7] VALIDATE       [8] DEPLOY
LLM produces      Vision model       axe-core + pa11y   Output folder
WCAG AA HTML      creates alt text   + Lighthouse        with redirects
with branding     and SVGs           (triple-layer)      ready for server
```

### Stage Details

1. **Crawl** — Uses [Crawl4AI](https://github.com/unclecode/crawl4ai) to deep-crawl the target website and discover all linked documents (.pdf, .doc, .docx, .ppt, .pptx, .xls, .xlsx). Captures link text and surrounding context from the referring page.

2. **Download** — Async downloads with SHA-256 deduplication. Legacy formats (.doc, .ppt, .xls) are converted to modern formats via headless LibreOffice.

3. **Extract** — PDFs are rendered page-by-page to images via PyMuPDF and sent to a vision model for OCR. Word/PowerPoint/Excel use native Python parsers (python-docx, python-pptx, openpyxl) with vision fallback.

4. **Plan** — An LLM with thinking mode analyzes the extracted content and creates a conversion plan: heading hierarchy, table identification, form handling, image processing needs, landmark regions.

5. **Generate** — The LLM generates semantic HTML following the plan, with institution branding, skip navigation, landmark roles, accessible forms, and all WCAG 2.1 AA requirements embedded in the prompt.

6. **Vision** — A vision model processes complex images: generates alt text, recreates charts as accessible SVGs with data tables, describes diagrams as structured HTML.

7. **Validate** — Triple-layer free validation:
   - **axe-core** (via Playwright) — industry-standard WCAG engine
   - **pa11y** (HTML_CodeSniffer) — independent rule set
   - **Lighthouse** — Google's accessibility scoring (target: 100/100)
   - Failed documents are auto-remediated up to 3 cycles by feeding violations back to the LLM.

8. **Deploy** — Organizes output into a deployment-ready folder structure mirroring the original URL paths, generates redirect manifests (JSON, CSV, .htaccess, nginx.conf), a master index, and a validation report.

## Tech Stack

| Component | Technology |
|---|---|
| Runtime | Python 3.11+ |
| Web Crawling | Crawl4AI |
| AI Models | OpenAI-compatible API (chat + vision) |
| PDF Rendering | PyMuPDF |
| Document Parsing | python-docx, python-pptx, openpyxl |
| WCAG Validation | axe-core, pa11y, Lighthouse |
| Database | SQLite (async via aiosqlite) |
| CLI | Click + Rich |

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js (for pa11y and Lighthouse)
- An API key for an OpenAI-compatible LLM provider

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
# Edit .env and add your API key and target site URL
```

### Usage

```bash
# Run the full pipeline
remedy run

# Or run stages individually
remedy crawl       # Discover documents
remedy process     # Extract + convert
remedy validate    # Run WCAG validation
remedy deploy      # Generate output structure

# Check progress
remedy status

# Retry failed documents
remedy retry-failed
```

### Configuration

Environment variables in `.env`:

```bash
API_KEY=your-api-key-here
API_BASE_URL=https://api.example.com/v1
CRAWL_START_URL=https://www.example.edu/
CRAWL_MAX_DEPTH=10
MAX_CONCURRENT_API_CALLS=5
```

See `config.example.yaml` for additional options.

## Output Structure

```
output/
├── index.html                    # Master index of all converted docs
├── assets/
│   ├── css/accessible.css       # Branded accessible stylesheet
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
- Brand colors meeting WCAG contrast ratios
- Keyboard navigable with visible focus indicators
- Print button (forms say "Print This Form", documents say "Print This Page")
- Responsive down to 320px width
- `lang="en"` with per-element language annotations
- Link to original document in footer

## Cost Estimate

For ~2,000 documents using a typical LLM API:

| Stage | Est. Total |
|---|---|
| OCR Extraction (vision model) | ~$12 |
| Planning (LLM) | ~$50 |
| HTML Generation (LLM) | ~$136 |
| Vision Processing (vision model) | ~$12 |
| Auto-remediation (LLM) | ~$8 |
| **Total** | **~$210** |

## Regulatory Context

- **Governing Law:** ADA Title II, 28 CFR § 35.200(b)
- **Technical Standard:** WCAG 2.1 Level AA
- **Compliance Deadline:** April 24, 2026
- **Enforcement:** Penalties up to $150,000 per violation

## License

MIT
