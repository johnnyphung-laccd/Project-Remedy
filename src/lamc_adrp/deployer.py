"""Stage 8: Output Organization & Deployment.

Organises validated HTML pages into a deployment-ready directory
structure that mirrors the original URL paths on lamission.edu.
Generates redirect manifests (JSON, CSV), server configs (.htaccess,
nginx), a master index page, a validation summary report, and the
shared LAMC accessible CSS stylesheet.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from lamc_adrp.config import PipelineConfig
from lamc_adrp.models import DocumentJob, JobStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OutputDeployer
# ---------------------------------------------------------------------------


class OutputDeployer:
    """Organises validated HTML into a deployment-ready file structure.

    Parameters
    ----------
    config:
        Pipeline configuration providing output directory paths.
    """

    def __init__(self, config: PipelineConfig) -> None:
        self._config = config
        self._output_dir = config.output.output_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def deploy_all(self, jobs: list[DocumentJob]) -> Path:
        """Deploy all completed jobs into the output directory structure.

        Creates the full directory tree, copies HTML files, generates
        redirect manifests, server configs, the index page, validation
        report, and shared CSS.

        Parameters
        ----------
        jobs:
            List of document jobs (typically VALIDATED or FLAGGED).

        Returns
        -------
        Path
            The root output directory.
        """
        root = self._output_dir.resolve()

        # Create directory structure
        (root / "assets" / "css").mkdir(parents=True, exist_ok=True)
        (root / "assets" / "images").mkdir(parents=True, exist_ok=True)
        (root / "assets" / "svg").mkdir(parents=True, exist_ok=True)
        (root / "documents").mkdir(parents=True, exist_ok=True)

        # Write shared CSS
        css_path = root / "assets" / "css" / "lamc-accessible.css"
        css_path.write_text(self.generate_css(), encoding="utf-8")
        logger.info("Wrote shared CSS: %s", css_path)

        # Deploy each job's HTML
        deployed_jobs: list[DocumentJob] = []
        for job in jobs:
            rendered_pages = job.get_rendered_pages()
            if not rendered_pages:
                logger.warning("Job %s has no generated HTML; skipping", job.id)
                continue

            canonical_path: Path | None = None
            for page in rendered_pages:
                html_rel_path = page.relative_path or self._url_to_html_path(job.url)
                html_abs_path = root / html_rel_path
                html_abs_path.parent.mkdir(parents=True, exist_ok=True)

                html_content = self._inject_css_link(page.html, html_rel_path)
                html_abs_path.write_text(html_content, encoding="utf-8")

                if page.kind == "canonical":
                    canonical_path = html_abs_path

                logger.debug(
                    "Deployed: %s [%s] -> %s",
                    job.url,
                    page.page_key,
                    html_rel_path,
                )

            if canonical_path is not None:
                job.final_html_path = str(canonical_path)
            deployed_jobs.append(job)

        # Generate redirect manifest (JSON + CSV)
        manifest = self.generate_redirect_manifest(deployed_jobs)
        manifest_json_path = root / "redirect-manifest.json"
        manifest_json_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        manifest_csv_path = root / "redirect-manifest.csv"
        manifest_csv_path.write_text(
            self._manifest_to_csv(manifest), encoding="utf-8"
        )

        # Server configs
        htaccess_path = root / ".htaccess"
        htaccess_path.write_text(
            self.generate_htaccess(deployed_jobs), encoding="utf-8"
        )

        nginx_path = root / "nginx-redirects.conf"
        nginx_path.write_text(
            self.generate_nginx_config(deployed_jobs), encoding="utf-8"
        )

        # Master index
        index_path = root / "index.html"
        index_path.write_text(
            self.generate_index_html(deployed_jobs), encoding="utf-8"
        )

        # Validation report
        report_path = root / "validation-report.html"
        report_path.write_text(
            self.generate_validation_report(jobs), encoding="utf-8"
        )

        logger.info(
            "Deployment complete: %d document(s) -> %s",
            len(deployed_jobs), root,
        )
        return root

    def generate_redirect_manifest(
        self, jobs: list[DocumentJob]
    ) -> dict[str, Any]:
        """Generate the redirect manifest as a dictionary.

        Parameters
        ----------
        jobs:
            Deployed document jobs.

        Returns
        -------
        dict
            Manifest with a ``redirects`` list.
        """
        redirects: list[dict[str, Any]] = []

        for job in jobs:
            original_path = urlparse(job.url).path
            html_path = "/" + self._url_to_html_path(job.url)

            violation_count = sum(
                len(r.violations) for r in job.validation_results
            )

            redirects.append({
                "original": original_path,
                "html_path": html_path,
                "document_type": job.file_type.value if job.file_type else "unknown",
                "conversion_date": job.updated_at.strftime("%Y-%m-%d"),
                "validation_status": (
                    "pass" if job.status == JobStatus.VALIDATED else "flagged"
                ),
                "wcag_violations": violation_count,
            })

        return {"redirects": redirects}

    def generate_htaccess(self, jobs: list[DocumentJob]) -> str:
        """Generate Apache .htaccess rewrite rules.

        Parameters
        ----------
        jobs:
            Deployed document jobs.

        Returns
        -------
        str
            Complete .htaccess file content with RewriteRule entries.
        """
        lines = [
            "# LAMC ADA Document Remediation Pipeline",
            "# Auto-generated redirect rules for accessible HTML replacements",
            f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
            "RewriteEngine On",
            "",
        ]

        for job in jobs:
            original_path = urlparse(job.url).path
            html_path = "/" + self._url_to_html_path(job.url)

            # Escape special regex characters in the original path
            escaped = re.escape(original_path.lstrip("/"))

            lines.append(
                f"# {job.link_text or job.url}"
            )
            lines.append(
                f"RewriteRule ^{escaped}$ {html_path} [R=301,L]"
            )
            lines.append("")

        return "\n".join(lines)

    def generate_nginx_config(self, jobs: list[DocumentJob]) -> str:
        """Generate Nginx redirect location blocks.

        Parameters
        ----------
        jobs:
            Deployed document jobs.

        Returns
        -------
        str
            Nginx config snippet with location blocks.
        """
        lines = [
            "# LAMC ADA Document Remediation Pipeline",
            "# Auto-generated Nginx redirect rules for accessible HTML replacements",
            f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
        ]

        for job in jobs:
            original_path = urlparse(job.url).path
            html_path = "/" + self._url_to_html_path(job.url)

            lines.extend([
                f"# {job.link_text or job.url}",
                f"location = {original_path} {{",
                f"    return 301 {html_path};",
                "}",
                "",
            ])

        return "\n".join(lines)

    def generate_index_html(self, jobs: list[DocumentJob]) -> str:
        """Generate a master index page linking to all converted documents.

        Parameters
        ----------
        jobs:
            Deployed document jobs.

        Returns
        -------
        str
            Complete HTML page with a table of all documents.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        total = len(jobs)
        passed = sum(1 for j in jobs if j.status == JobStatus.VALIDATED)
        flagged = sum(1 for j in jobs if j.status == JobStatus.FLAGGED)

        rows_html = []
        for job in sorted(jobs, key=lambda j: j.url):
            html_path = self._url_to_html_path(job.url)
            title = job.link_text or _filename_from_url(job.url)
            file_type = job.file_type.value.upper() if job.file_type else "?"
            status = "Pass" if job.status == JobStatus.VALIDATED else "Flagged"
            status_class = "status-pass" if status == "Pass" else "status-flagged"
            violation_count = sum(
                len(r.violations) for r in job.validation_results
            )

            rows_html.append(
                f'    <tr>\n'
                f'      <td><a href="{_escape(html_path)}">{_escape(title)}</a></td>\n'
                f'      <td>{file_type}</td>\n'
                f'      <td class="{status_class}">{status}</td>\n'
                f'      <td>{violation_count}</td>\n'
                f'      <td><a href="{_escape(job.url)}">Original</a></td>\n'
                f'    </tr>'
            )

        table_rows = "\n".join(rows_html)

        return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>LAMC Accessible Document Index</title>
  <link rel="stylesheet" href="assets/css/lamc-accessible.css">
</head>
<body>
  <a href="#main-content" class="skip-nav">Skip to main content</a>

  <header role="banner">
    <h1>Los Angeles Mission College &mdash; Accessible Documents</h1>
    <p>ADA-compliant HTML replacements for campus documents</p>
  </header>

  <nav aria-label="Page navigation">
    <ul>
      <li><a href="#summary">Summary</a></li>
      <li><a href="#documents">Documents</a></li>
      <li><a href="validation-report.html">Validation Report</a></li>
    </ul>
  </nav>

  <main id="main-content">
    <section id="summary" aria-label="Conversion summary">
      <h2>Conversion Summary</h2>
      <dl class="summary-stats">
        <dt>Total Documents</dt>
        <dd>{total}</dd>
        <dt>Passed Validation</dt>
        <dd>{passed}</dd>
        <dt>Flagged for Review</dt>
        <dd>{flagged}</dd>
        <dt>Generated</dt>
        <dd>{now}</dd>
      </dl>
    </section>

    <section id="documents" aria-label="Document listing">
      <h2>All Documents</h2>
      <div class="table-wrapper">
        <table>
          <caption>Converted accessible documents</caption>
          <thead>
            <tr>
              <th scope="col">Document</th>
              <th scope="col">Type</th>
              <th scope="col">Status</th>
              <th scope="col">Violations</th>
              <th scope="col">Original</th>
            </tr>
          </thead>
          <tbody>
{table_rows}
          </tbody>
        </table>
      </div>
    </section>
  </main>

  <footer role="contentinfo">
    <p>Los Angeles Mission College &mdash; 13356 Eldridge Avenue, Sylmar, CA 91342</p>
    <p>Generated by the LAMC ADA Document Remediation Pipeline</p>
  </footer>
</body>
</html>"""

    def generate_validation_report(self, jobs: list[DocumentJob]) -> str:
        """Generate an HTML validation summary report.

        Parameters
        ----------
        jobs:
            All document jobs (including failed ones).

        Returns
        -------
        str
            Complete HTML page summarising validation results.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        total = len(jobs)
        validated = [j for j in jobs if j.status == JobStatus.VALIDATED]
        flagged = [j for j in jobs if j.status == JobStatus.FLAGGED]
        failed = [j for j in jobs if j.status == JobStatus.FAILED]

        # Compute average Lighthouse score across validated jobs
        lh_scores = []
        for job in jobs:
            for r in job.validation_results:
                if r.tool == "lighthouse" and r.score is not None:
                    lh_scores.append(r.score)
        avg_lh = sum(lh_scores) / len(lh_scores) if lh_scores else 0

        # Build per-document rows
        rows_html = []
        for job in sorted(jobs, key=lambda j: j.url):
            title = job.link_text or _filename_from_url(job.url)
            status = job.status.value.title()
            status_class = {
                "Validated": "status-pass",
                "Flagged": "status-flagged",
                "Failed": "status-failed",
            }.get(status, "")

            axe_count = pa11y_count = lh_count = 0
            lh_scores: list[float] = []
            for r in job.validation_results:
                if r.tool == "axe":
                    axe_count += len(r.violations)
                elif r.tool == "pa11y":
                    pa11y_count += len(r.violations)
                elif r.tool == "lighthouse":
                    lh_count += len(r.violations)
                    if r.score is not None:
                        lh_scores.append(r.score)

            lh_score = (
                f"{sum(lh_scores) / len(lh_scores):.0f}"
                if lh_scores
                else "N/A"
            )

            cycles = job.remediation_count

            rows_html.append(
                f'    <tr>\n'
                f'      <td>{_escape(title)}</td>\n'
                f'      <td class="{status_class}">{status}</td>\n'
                f'      <td>{axe_count}</td>\n'
                f'      <td>{pa11y_count}</td>\n'
                f'      <td>{lh_score}</td>\n'
                f'      <td>{lh_count}</td>\n'
                f'      <td>{cycles}</td>\n'
                f'    </tr>'
            )

        table_rows = "\n".join(rows_html)

        return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>LAMC Validation Report</title>
  <link rel="stylesheet" href="assets/css/lamc-accessible.css">
</head>
<body>
  <a href="#main-content" class="skip-nav">Skip to main content</a>

  <header role="banner">
    <h1>LAMC ADA Remediation &mdash; Validation Report</h1>
    <p>Generated: {now}</p>
  </header>

  <nav aria-label="Page navigation">
    <ul>
      <li><a href="index.html">Document Index</a></li>
      <li><a href="#overview">Overview</a></li>
      <li><a href="#details">Details</a></li>
    </ul>
  </nav>

  <main id="main-content">
    <section id="overview" aria-label="Validation overview">
      <h2>Overview</h2>
      <dl class="summary-stats">
        <dt>Total Documents</dt>
        <dd>{total}</dd>
        <dt>Passed (Validated)</dt>
        <dd>{len(validated)}</dd>
        <dt>Flagged for Review</dt>
        <dd>{len(flagged)}</dd>
        <dt>Failed</dt>
        <dd>{len(failed)}</dd>
        <dt>Average Lighthouse Score</dt>
        <dd>{avg_lh:.1f}/100</dd>
      </dl>
    </section>

    <section id="details" aria-label="Detailed validation results">
      <h2>Per-Document Results</h2>
      <div class="table-wrapper">
        <table>
          <caption>Validation results for all processed documents</caption>
          <thead>
            <tr>
              <th scope="col">Document</th>
              <th scope="col">Status</th>
              <th scope="col">axe-core</th>
              <th scope="col">pa11y</th>
              <th scope="col">LH Score</th>
              <th scope="col">LH Audits</th>
              <th scope="col">Cycles</th>
            </tr>
          </thead>
          <tbody>
{table_rows}
          </tbody>
        </table>
      </div>
    </section>
  </main>

  <footer role="contentinfo">
    <p>Los Angeles Mission College &mdash; 13356 Eldridge Avenue, Sylmar, CA 91342</p>
    <p>Generated by the LAMC ADA Document Remediation Pipeline</p>
  </footer>
</body>
</html>"""

    def generate_css(self) -> str:
        """Generate the LAMC accessible stylesheet.

        Implements the LAMC brand specifications with full WCAG 2.1 AA
        compliance: colour contrast, focus management, responsive design,
        print styles, high contrast mode, and reduced motion support.

        Returns
        -------
        str
            Complete CSS stylesheet content.
        """
        return """\
/* ==========================================================================
   LAMC Accessible Stylesheet — lamc-accessible.css
   Los Angeles Mission College ADA Document Remediation Pipeline
   WCAG 2.1 Level AA Compliant
   ========================================================================== */

/* --------------------------------------------------------------------------
   CSS Custom Properties (Design Tokens)
   -------------------------------------------------------------------------- */
:root {
  /* LAMC Brand Colours */
  --color-primary: #004590;          /* Congress Blue — headers, nav, links */
  --color-accent: #FF611A;           /* Orange — decorative, large text */
  --color-accent-dark: #D94E0F;      /* Darkened orange — small text (4.5:1) */
  --color-neutral: #718089;          /* Rolling Stone Gray — decorative */
  --color-neutral-dark: #5A6670;     /* Darkened neutral — text use (4.5:1) */

  /* Backgrounds */
  --color-bg: #FFFFFF;
  --color-bg-alt: #F5F7FA;

  /* Text */
  --color-text: #1A1A2E;
  --color-text-muted: #5A6670;

  /* Focus */
  --color-focus: #FF611A;
  --focus-width: 3px;

  /* Typography */
  --font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --font-size-base: 1rem;           /* 16px */
  --line-height: 1.6;
  --max-width: 72ch;                 /* ~800px comfortable reading */

  /* Spacing */
  --space-xs: 0.25rem;
  --space-sm: 0.5rem;
  --space-md: 1rem;
  --space-lg: 1.5rem;
  --space-xl: 2rem;
  --space-2xl: 3rem;
}

/* --------------------------------------------------------------------------
   Reset & Base
   -------------------------------------------------------------------------- */
*,
*::before,
*::after {
  box-sizing: border-box;
}

html {
  font-size: 100%;                   /* Respects user's browser setting */
  scroll-behavior: smooth;
}

body {
  margin: 0;
  padding: 0;
  font-family: var(--font-family);
  font-size: var(--font-size-base);
  line-height: var(--line-height);
  color: var(--color-text);
  background-color: var(--color-bg);
  -webkit-text-size-adjust: 100%;
}

/* --------------------------------------------------------------------------
   Skip Navigation
   -------------------------------------------------------------------------- */
.skip-nav {
  position: absolute;
  top: -100%;
  left: var(--space-md);
  z-index: 1000;
  padding: var(--space-sm) var(--space-md);
  background: var(--color-primary);
  color: #FFFFFF;
  font-weight: 700;
  text-decoration: none;
  border-radius: 0 0 4px 4px;
  transition: top 0.2s ease;
}

.skip-nav:focus {
  top: 0;
  outline: var(--focus-width) solid var(--color-focus);
  outline-offset: 2px;
}

/* --------------------------------------------------------------------------
   Layout
   -------------------------------------------------------------------------- */
body > header,
header[role="banner"],
main,
body > footer,
footer[role="contentinfo"] {
  max-width: var(--max-width);
  margin-inline: auto;
  padding: var(--space-lg) var(--space-md);
}

main {
  min-height: 60vh;
}

/* --------------------------------------------------------------------------
   Header
   -------------------------------------------------------------------------- */
body > header,
header[role="banner"] {
  border-bottom: 3px solid var(--color-primary);
  padding-bottom: var(--space-md);
  margin-bottom: var(--space-lg);
}

body > header h1,
header[role="banner"] h1 {
  color: var(--color-primary);
  margin: 0 0 var(--space-xs);
}

body > header p,
header[role="banner"] p {
  color: var(--color-text-muted);
  margin: 0;
}

header nav[aria-label="Institution"] {
  max-width: none;
  margin: 0 0 var(--space-sm);
  padding: 0;
  border: 0;
}

/* --------------------------------------------------------------------------
   Navigation
   -------------------------------------------------------------------------- */
body > nav {
  max-width: var(--max-width);
  margin-inline: auto;
  padding: var(--space-sm) var(--space-md);
  border-bottom: 1px solid var(--color-bg-alt);
}

body > nav ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-md);
}

body > nav a {
  color: var(--color-primary);
  text-decoration: underline;
  font-weight: 600;
}

body > nav a:hover {
  color: var(--color-accent-dark);
}

/* --------------------------------------------------------------------------
   Typography
   -------------------------------------------------------------------------- */
h1, h2, h3, h4, h5, h6 {
  color: var(--color-primary);
  line-height: 1.3;
  margin-top: var(--space-xl);
  margin-bottom: var(--space-sm);
}

h1 { font-size: 2rem; }
h2 { font-size: 1.5rem; }
h3 { font-size: 1.25rem; }
h4 { font-size: 1.125rem; }
h5 { font-size: 1rem; font-weight: 700; }
h6 { font-size: 1rem; font-weight: 600; font-style: italic; }

p {
  margin-top: 0;
  margin-bottom: var(--space-md);
}

/* --------------------------------------------------------------------------
   Links
   -------------------------------------------------------------------------- */
a {
  color: var(--color-primary);
  text-decoration: underline;
  text-underline-offset: 2px;
}

a:hover {
  color: var(--color-accent-dark);
}

a:focus-visible {
  outline: var(--focus-width) solid var(--color-focus);
  outline-offset: 2px;
  border-radius: 2px;
}

/* --------------------------------------------------------------------------
   Focus Styles (all interactive elements)
   -------------------------------------------------------------------------- */
:focus-visible {
  outline: var(--focus-width) solid var(--color-focus);
  outline-offset: 2px;
}

button:focus-visible,
input:focus-visible,
select:focus-visible,
textarea:focus-visible,
[tabindex]:focus-visible {
  outline: var(--focus-width) solid var(--color-focus);
  outline-offset: 2px;
}

/* --------------------------------------------------------------------------
   Lists
   -------------------------------------------------------------------------- */
ul, ol {
  padding-left: var(--space-xl);
  margin-bottom: var(--space-md);
}

li {
  margin-bottom: var(--space-xs);
}

dl {
  margin-bottom: var(--space-md);
}

dt {
  font-weight: 700;
  color: var(--color-primary);
  margin-top: var(--space-sm);
}

dd {
  margin-left: var(--space-lg);
  margin-bottom: var(--space-xs);
}

/* --------------------------------------------------------------------------
   Tables
   -------------------------------------------------------------------------- */
.table-wrapper {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  margin-bottom: var(--space-lg);
}

table {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: var(--space-md);
}

caption {
  font-weight: 700;
  text-align: left;
  padding: var(--space-sm) 0;
  color: var(--color-primary);
}

th, td {
  padding: var(--space-sm) var(--space-md);
  text-align: left;
  border: 1px solid #D1D5DB;
}

th {
  background-color: var(--color-primary);
  color: #FFFFFF;
  font-weight: 700;
}

/* Striped rows */
tbody tr:nth-child(even) {
  background-color: var(--color-bg-alt);
}

tbody tr:hover {
  background-color: #E8ECF0;
}

/* --------------------------------------------------------------------------
   Summary / Stats
   -------------------------------------------------------------------------- */
.summary-stats {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: var(--space-xs) var(--space-lg);
  margin-bottom: var(--space-lg);
  padding: var(--space-md);
  background: var(--color-bg-alt);
  border-radius: 4px;
  border-left: 4px solid var(--color-primary);
}

.summary-stats dt {
  margin: 0;
  color: var(--color-text-muted);
  font-weight: 600;
}

.summary-stats dd {
  margin: 0;
  font-weight: 700;
}

/* --------------------------------------------------------------------------
   Status Indicators
   -------------------------------------------------------------------------- */
.status-pass {
  color: #166534;
  font-weight: 700;
}

.status-flagged {
  color: #92400E;
  font-weight: 700;
}

.status-failed {
  color: #991B1B;
  font-weight: 700;
}

/* --------------------------------------------------------------------------
   Images
   -------------------------------------------------------------------------- */
img {
  max-width: 100%;
  height: auto;
  display: block;
}

figure {
  margin: var(--space-lg) 0;
  padding: 0;
}

figcaption {
  font-size: 0.875rem;
  color: var(--color-text-muted);
  margin-top: var(--space-xs);
  font-style: italic;
}

/* --------------------------------------------------------------------------
   Chart / Diagram Descriptions
   -------------------------------------------------------------------------- */
.chart-data-table {
  font-size: 0.875rem;
  margin-top: var(--space-sm);
}

.chart-summary,
.diagram-summary {
  font-style: italic;
  color: var(--color-text-muted);
  margin-top: var(--space-sm);
}

.diagram-description,
.infographic-description {
  padding: var(--space-md);
  background: var(--color-bg-alt);
  border: 1px solid #D1D5DB;
  border-radius: 4px;
  margin: var(--space-md) 0;
}

/* --------------------------------------------------------------------------
   Forms
   -------------------------------------------------------------------------- */
label {
  display: block;
  font-weight: 600;
  margin-bottom: var(--space-xs);
}

input,
select,
textarea {
  display: block;
  width: 100%;
  max-width: 40ch;
  padding: var(--space-sm);
  border: 2px solid #9CA3AF;
  border-radius: 4px;
  font-family: inherit;
  font-size: var(--font-size-base);
  color: var(--color-text);
  background: var(--color-bg);
  margin-bottom: var(--space-md);
}

input:focus,
select:focus,
textarea:focus {
  border-color: var(--color-primary);
  outline: var(--focus-width) solid var(--color-focus);
  outline-offset: 2px;
}

button,
[type="submit"],
[type="button"] {
  display: inline-block;
  padding: var(--space-sm) var(--space-lg);
  background: var(--color-primary);
  color: #FFFFFF;
  border: 2px solid var(--color-primary);
  border-radius: 4px;
  font-family: inherit;
  font-size: var(--font-size-base);
  font-weight: 700;
  cursor: pointer;
  min-height: 44px;
  min-width: 44px;
}

button:hover,
[type="submit"]:hover,
[type="button"]:hover {
  background: #003570;
  border-color: #003570;
}

/* --------------------------------------------------------------------------
   Footer
   -------------------------------------------------------------------------- */
body > footer,
footer[role="contentinfo"] {
  border-top: 3px solid var(--color-primary);
  margin-top: var(--space-2xl);
  padding-top: var(--space-lg);
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

body > footer a,
footer a {
  color: var(--color-primary);
}

/* --------------------------------------------------------------------------
   Long Document Controls
   -------------------------------------------------------------------------- */
.no-print {
  display: initial;
}

.document-controls {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-sm);
  align-items: center;
  margin-bottom: var(--space-lg);
}

.document-controls__link {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  padding: var(--space-sm) var(--space-md);
  border: 2px solid var(--color-primary);
  border-radius: 4px;
  font-weight: 700;
  text-decoration: none;
}

.document-controls__link:hover {
  background: var(--color-bg-alt);
}

.document-section-nav {
  margin: 0 0 var(--space-xl);
  padding: var(--space-lg);
  background: var(--color-bg-alt);
  border: 1px solid #D1D5DB;
  border-radius: 4px;
}

.document-section-nav h2 {
  margin-top: 0;
}

.document-section-nav ol {
  margin: 0;
  padding-left: var(--space-lg);
  display: grid;
  gap: var(--space-sm);
}

.document-section-nav li {
  margin-bottom: 0;
  display: grid;
  gap: var(--space-xs);
}

.section-pages {
  display: inline-flex;
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.section-subpage-link {
  font-size: 0.875rem;
  font-weight: 600;
}

.document-section {
  margin-bottom: var(--space-md);
  border: 1px solid #D1D5DB;
  border-radius: 6px;
  background: var(--color-bg);
  overflow: clip;
}

.document-section[open] {
  box-shadow: 0 0 0 1px rgba(0, 69, 144, 0.15);
}

.document-section summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-sm);
  min-height: 44px;
  padding: var(--space-md);
  cursor: pointer;
  background: var(--color-bg-alt);
  font-weight: 700;
}

.document-section summary::-webkit-details-marker {
  display: none;
}

.document-section summary::after {
  content: "Expand";
  font-size: 0.875rem;
  color: var(--color-primary);
}

.document-section[open] summary::after {
  content: "Collapse";
}

.document-section__title {
  color: var(--color-primary);
  font-size: 1.125rem;
}

.document-section__panel {
  padding: var(--space-lg) var(--space-md);
}

.document-section__panel > section {
  margin: 0;
}

.document-section__actions {
  display: flex;
  justify-content: flex-end;
  margin-bottom: var(--space-md);
}

.breadcrumb {
  margin: 0 0 var(--space-lg);
}

.breadcrumb__list {
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-xs);
  margin: 0;
  padding: 0;
  color: var(--color-text-muted);
}

.breadcrumb__list li + li::before {
  content: "/";
  margin-right: var(--space-xs);
  color: var(--color-text-muted);
}

.companion-pagination {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  align-items: center;
  gap: var(--space-sm);
  margin: var(--space-lg) 0;
  padding: var(--space-md);
  background: var(--color-bg-alt);
  border: 1px solid #D1D5DB;
  border-radius: 4px;
}

.companion-pagination > :nth-child(2) {
  justify-self: center;
  text-align: center;
}

.companion-pagination > :last-child {
  justify-self: end;
  text-align: right;
}

.companion-pagination [aria-disabled="true"] {
  color: var(--color-text-muted);
}

/* --------------------------------------------------------------------------
   Sections (alternating backgrounds)
   -------------------------------------------------------------------------- */
main > section:nth-of-type(even) {
  background-color: var(--color-bg-alt);
  padding: var(--space-lg) var(--space-md);
  margin-inline: calc(-1 * var(--space-md));
  padding-inline: var(--space-md);
  border-radius: 4px;
}

/* --------------------------------------------------------------------------
   Print Styles
   -------------------------------------------------------------------------- */
@media print {
  *,
  *::before,
  *::after {
    background: transparent !important;
    color: #000 !important;
    box-shadow: none !important;
    text-shadow: none !important;
  }

  body {
    font-size: 12pt;
    line-height: 1.5;
  }

  a,
  a:visited {
    text-decoration: underline;
  }

  a[href^="http"]::after {
    content: " (" attr(href) ")";
    font-size: 0.8em;
  }

  h1, h2, h3 {
    page-break-after: avoid;
  }

  table, figure, img {
    page-break-inside: avoid;
  }

  .skip-nav,
  .no-print,
  body > nav {
    display: none !important;
  }

  body > header,
  header[role="banner"],
  main,
  body > footer,
  footer[role="contentinfo"] {
    max-width: 100%;
    padding: 0;
  }

  .document-section {
    border: 0;
    box-shadow: none;
    margin-bottom: var(--space-lg);
  }

  .document-section summary {
    background: transparent;
    padding: 0 0 var(--space-sm);
  }

  .document-section summary::after {
    content: "";
  }

  .document-section__panel {
    padding: 0;
  }

  th {
    background-color: #E5E7EB !important;
    color: #000 !important;
  }
}

/* --------------------------------------------------------------------------
   High Contrast Mode
   -------------------------------------------------------------------------- */
@media (forced-colors: active) {
  a {
    text-decoration: underline;
  }

  .skip-nav:focus {
    outline: 3px solid LinkText;
  }

  th {
    border: 2px solid CanvasText;
  }

  button,
  [type="submit"],
  [type="button"] {
    border: 2px solid ButtonText;
  }
}

/* --------------------------------------------------------------------------
   Prefers Reduced Motion
   -------------------------------------------------------------------------- */
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}

/* --------------------------------------------------------------------------
   Prefers Contrast (more)
   -------------------------------------------------------------------------- */
@media (prefers-contrast: more) {
  :root {
    --color-text: #000000;
    --color-text-muted: #1A1A2E;
    --color-neutral-dark: #333333;
  }

  a {
    text-decoration-thickness: 2px;
  }

  th, td {
    border-width: 2px;
    border-color: #000;
  }
}

/* --------------------------------------------------------------------------
   Responsive — Reflow at 320px
   -------------------------------------------------------------------------- */
@media (max-width: 40rem) {
  h1 { font-size: 1.5rem; }
  h2 { font-size: 1.25rem; }
  h3 { font-size: 1.125rem; }

  th, td {
    padding: var(--space-xs) var(--space-sm);
    font-size: 0.875rem;
  }

  body > nav ul {
    flex-direction: column;
    gap: var(--space-sm);
  }

  .summary-stats {
    grid-template-columns: 1fr;
  }

  .document-controls,
  .companion-pagination {
    grid-template-columns: 1fr;
  }

  .companion-pagination > :nth-child(2),
  .companion-pagination > :last-child {
    justify-self: start;
    text-align: left;
  }
}
"""

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _url_to_html_path(self, url: str) -> str:
        """Convert a document URL to a relative HTML file path.

        Mirrors the site's URL structure under ``documents/``.

        Examples
        --------
        >>> d = OutputDeployer.__new__(OutputDeployer)
        >>> d._url_to_html_path("https://www.lamission.edu/academics/schedule.pdf")
        'documents/academics/schedule.html'
        """
        parsed = urlparse(url)
        path = parsed.path.lstrip("/")

        # Remove common path prefixes from CMS (Drupal, etc.)
        for prefix in ("sites/lamc.edu/files/", "sites/default/files/"):
            if path.startswith(prefix):
                path = path[len(prefix):]
                break

        # Replace the extension with .html
        stem = Path(path).stem
        parent = str(Path(path).parent)

        # Slugify the filename
        slug = re.sub(r"[^\w\-/]", "-", stem).strip("-").lower()
        slug = re.sub(r"-{2,}", "-", slug)

        if parent and parent != ".":
            return f"documents/{parent}/{slug}.html"
        return f"documents/{slug}.html"

    def _inject_css_link(self, html: str, html_rel_path: str) -> str:
        """Inject a relative link to the shared CSS if not already present."""
        if "lamc-accessible.css" in html:
            return html

        # Calculate relative path from the HTML file to the CSS
        html_depth = html_rel_path.count("/")
        css_rel = "../" * html_depth + "assets/css/lamc-accessible.css"

        css_tag = f'  <link rel="stylesheet" href="{css_rel}">'

        # Insert after <head> or before </head>
        if "</head>" in html:
            return html.replace("</head>", f"{css_tag}\n</head>", 1)
        if "<head>" in html:
            return html.replace("<head>", f"<head>\n{css_tag}", 1)

        # Fallback: prepend
        return f'<link rel="stylesheet" href="{css_rel}">\n{html}'

    @staticmethod
    def _manifest_to_csv(manifest: dict[str, Any]) -> str:
        """Convert the redirect manifest to CSV format."""
        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow([
            "original_url",
            "html_path",
            "document_type",
            "conversion_date",
            "validation_status",
            "wcag_violations",
        ])

        for entry in manifest.get("redirects", []):
            writer.writerow([
                entry.get("original", ""),
                entry.get("html_path", ""),
                entry.get("document_type", ""),
                entry.get("conversion_date", ""),
                entry.get("validation_status", ""),
                entry.get("wcag_violations", 0),
            ])

        return output.getvalue()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _escape(text: str) -> str:
    """Minimal HTML entity escaping."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _filename_from_url(url: str) -> str:
    """Extract a readable filename from a URL."""
    path = urlparse(url).path
    name = Path(path).stem
    # Convert hyphens/underscores to spaces and title-case
    name = re.sub(r"[-_]+", " ", name)
    return name.title() if name else "Untitled"
