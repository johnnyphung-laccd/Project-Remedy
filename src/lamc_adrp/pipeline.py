"""Main Pipeline Orchestrator for the LAMC ADA Document Remediation Pipeline.

Wires all eight stages together into a coherent, concurrent, fault-tolerant
pipeline that can be run end-to-end or stage-by-stage.

Stages:
    1. Crawl     -- discover document links on lamission.edu
    2. Download  -- fetch documents to local storage
    3. Extract   -- OCR / native-parse documents to Markdown
    4-5. Convert -- plan and generate WCAG 2.1 AA compliant HTML
    6. Vision    -- process images with alt text / SVG recreation
    7. Validate  -- triple-layer validation with auto-remediation
    8. Deploy    -- organise output, generate manifests
"""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lamc_adrp.config import PipelineConfig
from lamc_adrp.converter import HTMLConverter
from lamc_adrp.crawler import DocumentCrawler
from lamc_adrp.database import DatabaseManager
from lamc_adrp.deployer import OutputDeployer
from lamc_adrp.downloader import DocumentDownloader
from lamc_adrp.extractor import ContentExtractor
from lamc_adrp.models import DocumentJob, JobStatus
from lamc_adrp.validator import AccessibilityValidator
from lamc_adrp.vision import VisionProcessor
from lamc_adrp.zai_client import ZAIClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline report
# ---------------------------------------------------------------------------


@dataclass
class PipelineReport:
    """Summary statistics from a pipeline run."""

    total_documents: int = 0
    discovered: int = 0
    downloaded: int = 0
    extracted: int = 0
    converted: int = 0
    validated: int = 0
    failed: int = 0
    flagged: int = 0
    average_lighthouse_score: float = 0.0
    total_api_cost_estimate: float = 0.0
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        """Return a list of human-readable summary lines."""
        mins, secs = divmod(self.duration_seconds, 60)
        return [
            f"Total documents:          {self.total_documents}",
            f"Discovered:               {self.discovered}",
            f"Downloaded:               {self.downloaded}",
            f"Extracted:                {self.extracted}",
            f"Converted (HTML):         {self.converted}",
            f"Validated (passed):       {self.validated}",
            f"Flagged (needs review):   {self.flagged}",
            f"Failed:                   {self.failed}",
            f"Avg Lighthouse score:     {self.average_lighthouse_score:.1f}/100",
            f"Est. API cost:            ${self.total_api_cost_estimate:.2f}",
            f"Duration:                 {int(mins)}m {secs:.1f}s",
            f"Errors:                   {len(self.errors)}",
        ]


# ---------------------------------------------------------------------------
# Image placeholder regex for Stage 6
# ---------------------------------------------------------------------------

_IMAGE_PLACEHOLDER_RE = re.compile(
    r'!\[([^\]]*)\]\(([^)]+)\)|'                        # Markdown: ![alt](path)
    r'<img[^>]+alt="([^"]*)"[^>]+src="([^"]*)"[^>]*/?>|'  # HTML <img> with src
    r'alt="\[Image description pending[^"]*\]"',          # Pending vision markers
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class Pipeline:
    """Orchestrates the full 8-stage document remediation pipeline.

    Parameters
    ----------
    config:
        Fully resolved pipeline configuration.
    """

    def __init__(self, config: PipelineConfig) -> None:
        self._config = config

        # Components -- initialised lazily by start().
        self._db = DatabaseManager(config.output.db_path)
        self._zai = ZAIClient(config)
        self._crawler = DocumentCrawler(config, self._db)
        self._downloader = DocumentDownloader(
            config, self._db, max_concurrent=config.processing.max_concurrent_calls
        )
        self._extractor = ContentExtractor(config, self._zai, self._db)
        self._converter = HTMLConverter(config, self._zai, self._db)
        self._vision = VisionProcessor(self._zai)
        self._validator = AccessibilityValidator(config, self._zai, self._db)
        self._deployer = OutputDeployer(config)

        # Concurrency limiter for CPU/API-bound stages.
        self._semaphore = asyncio.Semaphore(config.processing.max_concurrent_calls)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Open database connection and initialise the API client."""
        await self._db.connect()
        await self._zai.start()
        logger.info("Pipeline started.")

    async def close(self) -> None:
        """Release all resources."""
        await self._zai.close()
        await self._db.close()
        logger.info("Pipeline closed.")

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    async def run(self) -> PipelineReport:
        """Execute the full 8-stage pipeline.

        Returns
        -------
        PipelineReport
            Summary of the entire run.
        """
        t0 = time.monotonic()
        report = PipelineReport()

        # Reset any jobs left mid-flight from a previous crash.
        reset = await self._db.reset_incomplete_jobs()
        if reset:
            logger.info("Reset %d incomplete jobs from previous run.", reset)

        # Stage 1: Crawl
        logger.info("=== Stage 1: Crawl ===")
        try:
            discovered = await self._crawler.crawl()
            report.discovered = len(discovered)
            logger.info("Crawl discovered %d documents.", len(discovered))
        except Exception as exc:
            msg = f"Crawl failed: {exc}"
            logger.exception(msg)
            report.errors.append(msg)

        # Stage 2: Download
        logger.info("=== Stage 2: Download ===")
        try:
            to_download = await self._db.get_jobs_by_status(JobStatus.DISCOVERED)
            if to_download:
                downloaded = await self._downloader.download_all(to_download)
                report.downloaded = sum(
                    1 for j in downloaded if j.status == JobStatus.DOWNLOADED
                )
            logger.info("Downloaded %d documents.", report.downloaded)
        except Exception as exc:
            msg = f"Download stage failed: {exc}"
            logger.exception(msg)
            report.errors.append(msg)

        # Stage 3: Extract
        logger.info("=== Stage 3: Extract ===")
        to_extract = await self._db.get_jobs_by_status(JobStatus.DOWNLOADED)
        extracted_jobs = await self._run_stage(
            to_extract, self._extract_one, "extraction", report
        )
        report.extracted = len(extracted_jobs)

        # Stage 4-5: Convert (plan + generate)
        logger.info("=== Stage 4-5: Convert ===")
        to_convert = await self._db.get_jobs_by_status(JobStatus.EXTRACTED)
        converted_jobs = await self._run_stage(
            to_convert, self._convert_one, "conversion", report
        )
        report.converted = len(converted_jobs)

        # Stage 6: Vision
        logger.info("=== Stage 6: Vision ===")
        to_vision = await self._db.get_jobs_by_status(JobStatus.CONVERTED)
        for job in to_vision:
            try:
                await self._vision_process_job(job)
            except Exception as exc:
                msg = f"Vision processing failed for {job.id}: {exc}"
                logger.warning(msg)
                report.errors.append(msg)

        # Stage 7: Validate
        logger.info("=== Stage 7: Validate ===")
        to_validate = await self._db.get_jobs_by_status(JobStatus.CONVERTED)
        validated_jobs = await self._run_stage(
            to_validate, self._validate_one, "validation", report
        )
        report.validated = sum(
            1 for j in validated_jobs if j.status == JobStatus.VALIDATED
        )
        report.flagged = sum(
            1 for j in validated_jobs if j.status == JobStatus.FLAGGED
        )

        # Stage 8: Deploy
        logger.info("=== Stage 8: Deploy ===")
        try:
            deploy_jobs = await self._db.get_jobs_by_status(
                JobStatus.VALIDATED, JobStatus.FLAGGED
            )
            if deploy_jobs:
                await self._deployer.deploy_all(deploy_jobs)
                logger.info("Deployed %d documents.", len(deploy_jobs))
        except Exception as exc:
            msg = f"Deploy stage failed: {exc}"
            logger.exception(msg)
            report.errors.append(msg)

        # Compute final stats.
        all_jobs = await self._db.get_all_jobs()
        report.total_documents = len(all_jobs)
        report.failed = sum(1 for j in all_jobs if j.status == JobStatus.FAILED)
        report.average_lighthouse_score = self._compute_avg_lighthouse(all_jobs)
        report.total_api_cost_estimate = self._estimate_api_cost()
        report.duration_seconds = time.monotonic() - t0

        logger.info("Pipeline run complete in %.1fs.", report.duration_seconds)
        return report

    # ------------------------------------------------------------------
    # Individual stage entry points (for CLI commands)
    # ------------------------------------------------------------------

    async def crawl(self) -> list[DocumentJob]:
        """Run Stages 1-2 only: crawl and download.

        Returns
        -------
        list[DocumentJob]
            All jobs after crawl + download.
        """
        await self._db.reset_incomplete_jobs()

        # Stage 1
        discovered = await self._crawler.crawl()
        logger.info("Crawl discovered %d documents.", len(discovered))

        # Stage 2
        to_download = await self._db.get_jobs_by_status(JobStatus.DISCOVERED)
        if to_download:
            await self._downloader.download_all(to_download)

        return await self._db.get_all_jobs()

    async def process(self) -> list[DocumentJob]:
        """Run Stages 3-6 only: extract, convert, vision.

        Picks up DOWNLOADED jobs and moves them through extraction,
        conversion, and vision processing.

        Returns
        -------
        list[DocumentJob]
            Jobs processed in this run.
        """
        await self._db.reset_incomplete_jobs()
        report = PipelineReport()
        processed: list[DocumentJob] = []

        # Stage 3: Extract
        to_extract = await self._db.get_jobs_by_status(JobStatus.DOWNLOADED)
        extracted = await self._run_stage(
            to_extract, self._extract_one, "extraction", report
        )
        processed.extend(extracted)

        # Stage 4-5: Convert
        to_convert = await self._db.get_jobs_by_status(JobStatus.EXTRACTED)
        converted = await self._run_stage(
            to_convert, self._convert_one, "conversion", report
        )
        processed.extend(converted)

        # Stage 6: Vision
        to_vision = await self._db.get_jobs_by_status(JobStatus.CONVERTED)
        for job in to_vision:
            try:
                await self._vision_process_job(job)
            except Exception as exc:
                logger.warning("Vision processing failed for %s: %s", job.id, exc)

        return processed

    async def validate_all(self) -> list[DocumentJob]:
        """Run Stage 7 only: validate converted jobs.

        Returns
        -------
        list[DocumentJob]
            Jobs that were validated in this run.
        """
        await self._db.reset_incomplete_jobs()
        report = PipelineReport()

        to_validate = await self._db.get_jobs_by_status(JobStatus.CONVERTED)
        return await self._run_stage(
            to_validate, self._validate_one, "validation", report
        )

    async def deploy(self) -> Path:
        """Run Stage 8 only: deploy validated jobs.

        Returns
        -------
        Path
            Root output directory.
        """
        jobs = await self._db.get_jobs_by_status(
            JobStatus.VALIDATED, JobStatus.FLAGGED
        )
        if not jobs:
            logger.warning("No validated or flagged jobs to deploy.")
            return self._config.output.output_dir

        return await self._deployer.deploy_all(jobs)

    async def retry_failed(self) -> list[DocumentJob]:
        """Reset FAILED jobs back to DISCOVERED and reprocess them.

        Returns
        -------
        list[DocumentJob]
            The reset jobs.
        """
        failed = await self._db.get_jobs_by_status(JobStatus.FAILED)
        if not failed:
            logger.info("No failed jobs to retry.")
            return []

        for job in failed:
            job.status = JobStatus.DISCOVERED
            job.error_message = ""
            await self._db.update_job(job)

        logger.info("Reset %d failed jobs to DISCOVERED.", len(failed))
        return failed

    # ------------------------------------------------------------------
    # Internal: per-job stage executors
    # ------------------------------------------------------------------

    async def _extract_one(self, job: DocumentJob) -> DocumentJob:
        """Extract content from a single downloaded document."""
        await self._extractor.extract(job)
        return job

    async def _convert_one(self, job: DocumentJob) -> DocumentJob:
        """Plan and generate HTML for a single extracted document."""
        await self._converter.convert(job)
        return job

    async def _validate_one(self, job: DocumentJob) -> DocumentJob:
        """Validate a single converted document with auto-remediation."""
        # Write the generated HTML to a file for validation tools.
        html_path = self._html_path_for_job(job)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(job.generated_html, encoding="utf-8")
        job.final_html_path = str(html_path)
        await self._db.update_job(job)

        # Update status.
        job.status = JobStatus.VALIDATING
        await self._db.update_job(job)

        # Run triple-layer validation.
        validation_report = await self._validator.validate(html_path)

        # Log initial validation results.
        for tool_result in [
            validation_report.axe_result,
            validation_report.pa11y_result,
            validation_report.lighthouse_result,
        ]:
            await self._db.log_validation(
                job_id=job.id,
                cycle=0,
                tool=tool_result.tool,
                score=tool_result.score,
                violations=tool_result.violations,
                passed=tool_result.passed,
            )

        # Auto-remediate if there are violations.
        if not validation_report.passed:
            job = await self._validator.auto_remediate(job, validation_report)
            # Re-write corrected HTML to disk.
            if job.generated_html:
                html_path.write_text(job.generated_html, encoding="utf-8")
        else:
            # Already passing -- mark as validated.
            job.validation_results = [
                validation_report.axe_result,
                validation_report.pa11y_result,
                validation_report.lighthouse_result,
            ]
            job.status = JobStatus.VALIDATED
            await self._db.update_job(job)

        return job

    async def _vision_process_job(self, job: DocumentJob) -> None:
        """Process image placeholders in a converted document's HTML.

        Scans the generated HTML for image placeholder patterns and
        replaces them with vision-generated alt text or HTML.
        """
        if not job.generated_html:
            return

        html = job.generated_html
        download_dir = self._config.output.output_dir / "downloads"

        # Find image references that need vision processing.
        matches = list(_IMAGE_PLACEHOLDER_RE.finditer(html))
        if not matches:
            return

        logger.info(
            "Vision: processing %d image placeholder(s) for job %s",
            len(matches),
            job.id,
        )

        for match in matches:
            # Try to determine the image path from the match groups.
            # Groups: (md_alt, md_path, html_alt, html_src)
            img_filename = match.group(2) or match.group(4)
            if not img_filename:
                continue

            # Look for the image in the downloads directory.
            img_path = download_dir / img_filename
            if not img_path.exists():
                # Try relative to the job's local path.
                if job.local_path:
                    img_path = Path(job.local_path).parent / img_filename
                if not img_path.exists():
                    logger.debug(
                        "Image file not found: %s (skipping vision)", img_filename
                    )
                    continue

            context = job.link_text or job.link_context or ""
            try:
                result = await self._vision.process_image(img_path, context)

                if result.html_replacement:
                    html = html.replace(match.group(0), result.html_replacement)
                elif result.alt_text:
                    # Replace just the alt text in the existing tag.
                    old_fragment = match.group(0)
                    if result.is_decorative:
                        new_fragment = (
                            f'<img src="{img_filename}" alt="" '
                            f'role="presentation">'
                        )
                    else:
                        new_fragment = (
                            f'<img src="{img_filename}" '
                            f'alt="{result.alt_text}">'
                        )
                    html = html.replace(old_fragment, new_fragment)
            except Exception as exc:
                logger.warning(
                    "Vision processing failed for image %s in job %s: %s",
                    img_filename,
                    job.id,
                    exc,
                )

        if html != job.generated_html:
            job.generated_html = html
            await self._db.update_job(job)

    # ------------------------------------------------------------------
    # Internal: concurrent stage runner
    # ------------------------------------------------------------------

    async def _run_stage(
        self,
        jobs: list[DocumentJob],
        handler,
        stage_name: str,
        report: PipelineReport,
    ) -> list[DocumentJob]:
        """Run *handler* concurrently on all *jobs* with semaphore control.

        Individual failures are caught and logged; they do not stop the
        pipeline for other documents.

        Returns
        -------
        list[DocumentJob]
            Successfully processed jobs.
        """
        if not jobs:
            logger.info("No jobs for %s stage.", stage_name)
            return []

        logger.info(
            "Running %s for %d job(s) (concurrency=%d).",
            stage_name,
            len(jobs),
            self._config.processing.max_concurrent_calls,
        )

        async def _guarded(job: DocumentJob) -> DocumentJob | None:
            async with self._semaphore:
                try:
                    return await handler(job)
                except Exception as exc:
                    msg = f"{stage_name} failed for job {job.id}: {exc}"
                    logger.error(msg)
                    report.errors.append(msg)
                    # Mark the job as failed if it is not already.
                    if job.status != JobStatus.FAILED:
                        job.status = JobStatus.FAILED
                        job.error_message = str(exc)
                        await self._db.update_job(job)
                    return None

        results = await asyncio.gather(
            *(_guarded(job) for job in jobs), return_exceptions=False
        )

        return [r for r in results if r is not None]

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    def _html_path_for_job(self, job: DocumentJob) -> Path:
        """Determine the HTML output path for a job."""
        if job.final_html_path:
            return Path(job.final_html_path)

        output_dir = self._config.output.output_dir / "html"
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_id = job.id[:12]
        return output_dir / f"{safe_id}.html"

    def _compute_avg_lighthouse(self, jobs: list[DocumentJob]) -> float:
        """Compute average Lighthouse accessibility score across all jobs."""
        scores: list[float] = []
        for job in jobs:
            for r in job.validation_results:
                if r.tool == "lighthouse" and r.score is not None:
                    scores.append(r.score)
        return sum(scores) / len(scores) if scores else 0.0

    def _estimate_api_cost(self) -> float:
        """Rough cost estimate based on cumulative token usage.

        Uses approximate pricing: $0.01 / 1K input tokens,
        $0.03 / 1K output tokens (conservative estimate).
        """
        input_cost = (self._zai.total_input_tokens / 1000) * 0.01
        output_cost = (self._zai.total_output_tokens / 1000) * 0.03
        return input_cost + output_cost
