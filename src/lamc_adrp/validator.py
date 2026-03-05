"""Stage 7: Triple-Layer WCAG 2.1 AA Validation.

Runs three independent, free accessibility validation tools — axe-core
(via Playwright), pa11y (CLI), and Lighthouse (CLI) — against every
generated HTML page.  Merges and deduplicates findings, and drives an
auto-remediation loop that feeds violations back to GLM-5 for
correction (up to ``max_remediation_cycles``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lamc_adrp.config import PipelineConfig
from lamc_adrp.database import DatabaseManager
from lamc_adrp.models import DocumentJob, JobStatus, ValidationResult
from lamc_adrp.zai_client import ZAIClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Report dataclass
# ---------------------------------------------------------------------------


@dataclass
class ValidationReport:
    """Aggregated results from all three validation tools."""

    axe_result: ValidationResult = field(
        default_factory=lambda: ValidationResult(tool="axe")
    )
    pa11y_result: ValidationResult = field(
        default_factory=lambda: ValidationResult(tool="pa11y")
    )
    lighthouse_result: ValidationResult = field(
        default_factory=lambda: ValidationResult(tool="lighthouse")
    )
    lighthouse_score: float = 0.0
    all_violations: list[dict[str, Any]] = field(default_factory=list)
    passed: bool = False
    summary: str = ""


# ---------------------------------------------------------------------------
# axe-core injection script
# ---------------------------------------------------------------------------

_AXE_CDN = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js"

_AXE_SCRIPT = """
async () => {
    await axe.run(document, {
        runOnly: {
            type: 'tag',
            values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']
        }
    }).then(results => {
        window.__axe_results = results;
    });
    return window.__axe_results;
}
"""

# ---------------------------------------------------------------------------
# Remediation prompt
# ---------------------------------------------------------------------------

_REMEDIATION_PROMPT = """\
You are an expert web accessibility specialist. The HTML document below has
WCAG 2.1 Level AA violations detected by automated scanning tools.

Fix ALL of the following violations while preserving the document's content
and structure. Do not remove any content. Only modify the HTML to resolve
the accessibility issues.

## Violations to fix:

{violations}

## Current HTML:

```html
{html}
```

Return ONLY the corrected, complete HTML document. No explanation, no
markdown fences — just the raw HTML starting with <!DOCTYPE html>.
"""


# ---------------------------------------------------------------------------
# AccessibilityValidator
# ---------------------------------------------------------------------------


class AccessibilityValidator:
    """Triple-layer WCAG 2.1 AA validation with auto-remediation.

    Uses axe-core (Playwright), pa11y (CLI), and Lighthouse (CLI) to
    validate every generated HTML page.  When violations are found, they
    are formatted and sent back to GLM-5 for automated correction, up
    to ``max_remediation_cycles`` times.

    Parameters
    ----------
    config:
        Pipeline configuration (provides remediation cycle limit).
    zai_client:
        An initialised :class:`ZAIClient` for remediation chat calls.
    db:
        Database manager for logging validation cycles.
    """

    def __init__(
        self,
        config: PipelineConfig,
        zai_client: ZAIClient,
        db: DatabaseManager,
    ) -> None:
        self._config = config
        self._zai = zai_client
        self._db = db
        self._max_cycles = config.validation.max_remediation_cycles
        self._fail_on_serious = config.validation.fail_on_serious

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def validate(self, html_path: Path) -> ValidationReport:
        """Run all three validation tools and return a merged report.

        Parameters
        ----------
        html_path:
            Path to the HTML file to validate.

        Returns
        -------
        ValidationReport
            Aggregated, deduplicated results from axe-core, pa11y,
            and Lighthouse.
        """
        axe, pa11y, lh = await asyncio.gather(
            self.validate_with_axe(html_path),
            self.validate_with_pa11y(html_path),
            self.validate_with_lighthouse(html_path),
            return_exceptions=True,
        )

        # Handle individual tool failures gracefully
        if isinstance(axe, Exception):
            logger.error("axe-core validation failed: %s", axe)
            axe = ValidationResult(tool="axe", violations=[], passed=True)
        if isinstance(pa11y, Exception):
            logger.error("pa11y validation failed: %s", pa11y)
            pa11y = ValidationResult(tool="pa11y", violations=[], passed=True)
        if isinstance(lh, Exception):
            logger.error("Lighthouse validation failed: %s", lh)
            lh = ValidationResult(tool="lighthouse", score=0, violations=[], passed=True)

        # Merge and deduplicate violations
        all_violations = self._merge_violations(axe, pa11y, lh)

        # Determine pass/fail
        critical_serious = [
            v for v in all_violations
            if v.get("impact") in ("critical", "serious")
        ]
        passed = len(critical_serious) == 0

        lh_score = lh.score if lh.score is not None else 0.0

        # Build summary
        summary_parts = [
            f"axe-core: {len(axe.violations)} violation(s)",
            f"pa11y: {len(pa11y.violations)} violation(s)",
            f"Lighthouse score: {lh_score}/100 ({len(lh.violations)} audit failure(s))",
            f"Total unique violations: {len(all_violations)}",
            f"Critical/serious: {len(critical_serious)}",
            f"Result: {'PASS' if passed else 'FAIL'}",
        ]

        return ValidationReport(
            axe_result=axe,
            pa11y_result=pa11y,
            lighthouse_result=lh,
            lighthouse_score=lh_score,
            all_violations=all_violations,
            passed=passed,
            summary=" | ".join(summary_parts),
        )

    async def validate_with_axe(self, html_path: Path) -> ValidationResult:
        """Run axe-core validation via Playwright.

        Loads the HTML file in a headless Chromium browser, injects
        axe-core from CDN, runs ``axe.run()``, and parses violations.

        Parameters
        ----------
        html_path:
            Path to the HTML file.

        Returns
        -------
        ValidationResult
            axe-core results with violations categorised by impact.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error(
                "playwright is not installed. "
                "Install with: pip install playwright && playwright install chromium"
            )
            return ValidationResult(tool="axe", passed=False)

        violations: list[dict[str, Any]] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()

            file_url = html_path.resolve().as_uri()
            await page.goto(file_url, wait_until="networkidle")

            # Inject axe-core
            await page.add_script_tag(url=_AXE_CDN)
            await page.wait_for_function("typeof axe !== 'undefined'", timeout=15000)

            # Run axe
            raw_results = await page.evaluate(_AXE_SCRIPT)
            await browser.close()

        if raw_results and isinstance(raw_results, dict):
            for v in raw_results.get("violations", []):
                violations.append({
                    "tool": "axe",
                    "id": v.get("id", ""),
                    "impact": v.get("impact", "minor"),
                    "description": v.get("description", ""),
                    "help": v.get("help", ""),
                    "help_url": v.get("helpUrl", ""),
                    "wcag": _extract_wcag_tags(v.get("tags", [])),
                    "nodes_count": len(v.get("nodes", [])),
                    "html_snippets": [
                        n.get("html", "")[:200]
                        for n in v.get("nodes", [])[:3]
                    ],
                })

        has_critical = any(
            v.get("impact") in ("critical", "serious") for v in violations
        )
        passed = not has_critical

        logger.info(
            "axe-core: %d violation(s), passed=%s", len(violations), passed
        )
        return ValidationResult(
            tool="axe", violations=violations, passed=passed
        )

    async def validate_with_pa11y(self, html_path: Path) -> ValidationResult:
        """Run pa11y CLI as an async subprocess.

        Parameters
        ----------
        html_path:
            Path to the HTML file.

        Returns
        -------
        ValidationResult
            pa11y results mapped to WCAG success criteria.
        """
        file_url = html_path.resolve().as_uri()
        cmd = [
            "pa11y",
            "--reporter", "json",
            "--standard", "WCAG2AA",
            "--timeout", "30000",
            file_url,
        ]

        violations: list[dict[str, Any]] = []

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=60
            )

            # pa11y exits with code 2 when issues are found — that is expected
            if stdout:
                raw = json.loads(stdout.decode("utf-8", errors="replace"))
                if isinstance(raw, list):
                    for issue in raw:
                        impact = _pa11y_type_to_impact(
                            issue.get("type", "notice")
                        )
                        violations.append({
                            "tool": "pa11y",
                            "id": issue.get("code", ""),
                            "impact": impact,
                            "description": issue.get("message", ""),
                            "help": issue.get("code", ""),
                            "wcag": _extract_wcag_from_code(
                                issue.get("code", "")
                            ),
                            "selector": issue.get("selector", ""),
                            "context": issue.get("context", "")[:200],
                        })

        except FileNotFoundError:
            logger.warning(
                "pa11y CLI not found. Install with: npm install -g pa11y"
            )
        except asyncio.TimeoutError:
            logger.warning("pa11y timed out after 60s for %s", html_path.name)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("pa11y output parse error: %s", exc)

        has_critical = any(
            v.get("impact") in ("critical", "serious") for v in violations
        )
        passed = not has_critical

        logger.info(
            "pa11y: %d violation(s), passed=%s", len(violations), passed
        )
        return ValidationResult(
            tool="pa11y", violations=violations, passed=passed
        )

    async def validate_with_lighthouse(
        self, html_path: Path
    ) -> ValidationResult:
        """Run Lighthouse CLI as an async subprocess.

        Targets the accessibility category only, aiming for a 100/100 score.

        Parameters
        ----------
        html_path:
            Path to the HTML file.

        Returns
        -------
        ValidationResult
            Lighthouse accessibility score and failing audits.
        """
        file_url = html_path.resolve().as_uri()

        # Write Lighthouse JSON output to a temporary file
        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w"
        ) as tmp:
            output_path = tmp.name

        cmd = [
            "lighthouse",
            file_url,
            "--only-categories=accessibility",
            "--output=json",
            f"--output-path={output_path}",
            "--chrome-flags=--headless --no-sandbox --disable-gpu",
            "--quiet",
        ]

        violations: list[dict[str, Any]] = []
        score: float = 0.0

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=120)

            output_file = Path(output_path)
            if output_file.exists():
                raw = json.loads(output_file.read_text(encoding="utf-8"))
                output_file.unlink(missing_ok=True)

                # Extract accessibility score
                categories = raw.get("categories", {})
                acc_cat = categories.get("accessibility", {})
                score = (acc_cat.get("score") or 0) * 100

                # Extract failing audits
                audits = raw.get("audits", {})
                for audit_id, audit in audits.items():
                    audit_score = audit.get("score")
                    # score=null means not applicable, score=1 means passed
                    if audit_score is not None and audit_score < 1:
                        violations.append({
                            "tool": "lighthouse",
                            "id": audit_id,
                            "impact": "serious" if audit_score == 0 else "moderate",
                            "description": audit.get("title", ""),
                            "help": audit.get("description", ""),
                            "score": audit_score,
                        })

        except FileNotFoundError:
            logger.warning(
                "lighthouse CLI not found. "
                "Install with: npm install -g lighthouse"
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Lighthouse timed out after 120s for %s", html_path.name
            )
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Lighthouse output parse error: %s", exc)
        finally:
            Path(output_path).unlink(missing_ok=True)

        has_critical = any(
            v.get("impact") in ("critical", "serious") for v in violations
        )
        passed = not has_critical

        logger.info(
            "Lighthouse: score=%.0f/100, %d failing audit(s), passed=%s",
            score, len(violations), passed,
        )
        return ValidationResult(
            tool="lighthouse",
            score=score,
            violations=violations,
            passed=passed,
        )

    async def auto_remediate(
        self, job: DocumentJob, report: ValidationReport
    ) -> DocumentJob:
        """Feed validation errors back to GLM-5 for correction.

        Runs up to ``max_remediation_cycles`` (from config), re-validating
        after each correction.  Each cycle is logged to the database.

        Parameters
        ----------
        job:
            The document job containing the generated HTML.
        report:
            The initial validation report with violations.

        Returns
        -------
        DocumentJob
            Updated job — HTML corrected if possible, remediation count
            incremented, status set to VALIDATED or FLAGGED.
        """
        current_html = job.generated_html
        current_report = report
        cycle = 0

        while not current_report.passed and cycle < self._max_cycles:
            cycle += 1
            logger.info(
                "Auto-remediation cycle %d/%d for job %s",
                cycle, self._max_cycles, job.id,
            )

            # Format violations for GLM-5
            violation_text = self._format_violations_for_llm(
                current_report.all_violations
            )

            prompt = _REMEDIATION_PROMPT.format(
                violations=violation_text,
                html=current_html,
            )

            # Send to GLM-5 for correction
            corrected = await self._zai.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert web accessibility remediation "
                            "specialist. Fix the WCAG violations in the HTML. "
                            "Return only the corrected HTML."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                model="glm-5",
                thinking=True,
                max_tokens=32768,
                temperature=0.2,
            )

            # Clean up the response — strip markdown fences if present
            corrected = corrected.strip()
            corrected = re.sub(r"^```(?:html)?\s*", "", corrected)
            corrected = re.sub(r"\s*```$", "", corrected)
            corrected = corrected.strip()

            if not corrected or len(corrected) < 50:
                logger.warning(
                    "Remediation cycle %d returned empty/short HTML; "
                    "keeping previous version",
                    cycle,
                )
                break

            current_html = corrected
            job.generated_html = current_html
            job.remediation_count = cycle

            # Write corrected HTML to file for re-validation
            html_path = Path(job.final_html_path) if job.final_html_path else None
            if html_path:
                html_path.write_text(current_html, encoding="utf-8")
            else:
                # Write to a temp file for validation
                with tempfile.NamedTemporaryFile(
                    suffix=".html", delete=False, mode="w", encoding="utf-8"
                ) as tmp:
                    tmp.write(current_html)
                    html_path = Path(tmp.name)

            # Log this cycle to the database
            current_report = await self.validate(html_path)

            for tool_result in [
                current_report.axe_result,
                current_report.pa11y_result,
                current_report.lighthouse_result,
            ]:
                await self._db.log_validation(
                    job_id=job.id,
                    cycle=cycle,
                    tool=tool_result.tool,
                    score=tool_result.score,
                    violations=tool_result.violations,
                    passed=tool_result.passed,
                )

            logger.info(
                "Remediation cycle %d result: %s (violations: %d)",
                cycle,
                "PASS" if current_report.passed else "FAIL",
                len(current_report.all_violations),
            )

        # Update job status
        job.validation_results = [
            current_report.axe_result,
            current_report.pa11y_result,
            current_report.lighthouse_result,
        ]

        if current_report.passed:
            job.status = JobStatus.VALIDATED
            logger.info("Job %s PASSED validation after %d cycle(s)", job.id, cycle)
        else:
            job.status = JobStatus.FLAGGED
            job.error_message = (
                f"Failed validation after {cycle} remediation cycle(s). "
                f"{len(current_report.all_violations)} violation(s) remain."
            )
            logger.warning(
                "Job %s FLAGGED after %d remediation cycle(s) with %d violation(s)",
                job.id, cycle, len(current_report.all_violations),
            )

        await self._db.update_job(job)
        return job

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_violations(
        axe: ValidationResult,
        pa11y: ValidationResult,
        lh: ValidationResult,
    ) -> list[dict[str, Any]]:
        """Merge and deduplicate violations from all three tools."""
        seen: set[str] = set()
        merged: list[dict[str, Any]] = []

        for result in [axe, pa11y, lh]:
            for v in result.violations:
                # Dedup key: tool + id + first selector/snippet
                key_parts = [
                    v.get("tool", ""),
                    v.get("id", ""),
                    v.get("selector", ""),
                ]
                snippets = v.get("html_snippets", [])
                if snippets:
                    key_parts.append(snippets[0][:80])
                dedup_key = "|".join(key_parts)

                if dedup_key not in seen:
                    seen.add(dedup_key)
                    merged.append(v)

        # Sort by severity: critical > serious > moderate > minor
        severity_order = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}
        merged.sort(key=lambda v: severity_order.get(v.get("impact", "minor"), 3))

        return merged

    @staticmethod
    def _format_violations_for_llm(
        violations: list[dict[str, Any]],
    ) -> str:
        """Format violations into a clear, numbered list for GLM-5."""
        if not violations:
            return "No violations found."

        lines: list[str] = []
        for i, v in enumerate(violations, 1):
            tool = v.get("tool", "unknown")
            vid = v.get("id", "unknown")
            impact = v.get("impact", "unknown")
            desc = v.get("description", "")
            help_text = v.get("help", "")
            wcag = v.get("wcag", "")
            selector = v.get("selector", "")
            snippets = v.get("html_snippets", [])

            parts = [f"{i}. [{tool}] {vid} ({impact})"]
            if wcag:
                parts.append(f"   WCAG: {wcag}")
            parts.append(f"   Issue: {desc}")
            if help_text and help_text != desc:
                parts.append(f"   Fix: {help_text}")
            if selector:
                parts.append(f"   Selector: {selector}")
            if snippets:
                parts.append(f"   HTML: {snippets[0][:150]}")

            lines.append("\n".join(parts))

        return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _extract_wcag_tags(tags: list[str]) -> str:
    """Extract WCAG success criteria from axe-core tags."""
    wcag_tags = [t for t in tags if t.startswith("wcag")]
    return ", ".join(wcag_tags) if wcag_tags else ""


def _extract_wcag_from_code(code: str) -> str:
    """Extract WCAG criteria reference from a pa11y issue code."""
    # pa11y codes like "WCAG2AA.Principle1.Guideline1_1.1_1_1.H37"
    match = re.search(r"Guideline(\d+_\d+)\.(\d+_\d+_\d+)", code)
    if match:
        sc = match.group(2).replace("_", ".")
        return f"WCAG {sc}"
    return ""


def _pa11y_type_to_impact(pa11y_type: str) -> str:
    """Map pa11y issue types to axe-compatible impact levels."""
    mapping = {
        "error": "serious",
        "warning": "moderate",
        "notice": "minor",
    }
    return mapping.get(pa11y_type.lower(), "minor")
