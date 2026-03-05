"""Stage 1: Web Crawling & Document Discovery.

Uses Crawl4AI's AsyncWebCrawler with BFSDeepCrawlStrategy to deep-crawl
lamission.edu and discover all linked documents (PDF, Word, PowerPoint,
Excel).  Discovered document links are deduplicated and persisted as
DocumentJob entries in the pipeline database.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, unquote

from bs4 import BeautifulSoup, Tag

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
from crawl4ai.content_scraping_strategy import LXMLWebScrapingStrategy

from lamc_adrp.config import PipelineConfig
from lamc_adrp.database import DatabaseManager
from lamc_adrp.models import DocumentJob, FileType, JobStatus

logger = logging.getLogger(__name__)

# File extensions we want to discover, mapped to FileType enum values.
_DOCUMENT_EXTENSIONS: dict[str, FileType] = {
    ".pdf": FileType.PDF,
    ".doc": FileType.DOC,
    ".docx": FileType.DOCX,
    ".ppt": FileType.PPT,
    ".pptx": FileType.PPTX,
    ".xls": FileType.XLS,
    ".xlsx": FileType.XLSX,
}

# Regex that matches any of the target extensions at the end of a URL path
# (before any query string).
_EXT_PATTERN = re.compile(
    r"\.(?:pdf|docx?|pptx?|xlsx?)(?:\?.*)?$",
    re.IGNORECASE,
)

# Common CMS patterns for embedded document viewers / file fields.
_CMS_DOC_PATTERNS = [
    # Drupal file field: /sites/default/files/...
    re.compile(r"/sites/default/files/.*\.(?:pdf|docx?|pptx?|xlsx?)", re.IGNORECASE),
    # Generic /files/ or /documents/ directories
    re.compile(r"/(?:files|documents|uploads|media|assets)/.*\.(?:pdf|docx?|pptx?|xlsx?)", re.IGNORECASE),
    # Embedded Google Docs viewer links that reference a source document
    re.compile(r"docs\.google\.com/(?:viewer|gview)\?.*url=", re.IGNORECASE),
    # SharePoint / OneDrive embedded links
    re.compile(r"sharepoint\.com/.*\.(?:pdf|docx?|pptx?|xlsx?)", re.IGNORECASE),
]


def _file_type_from_url(url: str) -> FileType | None:
    """Determine the FileType from a URL's path extension."""
    parsed = urlparse(url)
    path = unquote(parsed.path).lower()
    for ext, ftype in _DOCUMENT_EXTENSIONS.items():
        if path.endswith(ext):
            return ftype
    return None


def _normalise_url(url: str) -> str:
    """Strip fragments and trailing whitespace for deduplication."""
    parsed = urlparse(url)
    # Reconstruct without fragment
    return parsed._replace(fragment="").geturl().strip()


def _get_surrounding_context(tag: Tag, max_chars: int = 200) -> str:
    """Extract text surrounding a link tag for accessibility context.

    Looks at the parent element's text content, trimmed to *max_chars*.
    """
    parent = tag.parent
    if parent is None:
        return ""
    # Walk up to find a block-level parent for better context
    block_tags = {"div", "p", "section", "article", "li", "td", "th", "dd"}
    for _ in range(5):
        if parent.name in block_tags:
            break
        if parent.parent is not None:
            parent = parent.parent
    text = parent.get_text(separator=" ", strip=True)
    if len(text) > max_chars:
        text = text[:max_chars] + "..."
    return text


class DocumentCrawler:
    """Stage 1 -- crawl a website and discover document links.

    Parameters
    ----------
    config:
        Full pipeline configuration (uses ``config.crawl`` for crawl settings
        and ``config.output`` for state persistence paths).
    db:
        An already-connected :class:`DatabaseManager` instance.
    """

    def __init__(self, config: PipelineConfig, db: DatabaseManager) -> None:
        self._config = config
        self._crawl_cfg = config.crawl
        self._db = db

        # Track discovered document URLs to avoid duplicates within a run.
        self._seen_urls: set[str] = set()

        # Counters for progress logging.
        self._pages_crawled: int = 0
        self._docs_found: int = 0

        # Where to persist crawl state snapshots for resumability.
        self._state_path = config.output.output_dir / "crawl_state.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def crawl(self) -> list[DocumentJob]:
        """Execute the deep crawl and return all newly created DocumentJobs.

        The crawl:
        1. Loads any previously-seen URLs from the database so they are
           skipped (resumability).
        2. Runs Crawl4AI's BFS deep crawl from the configured start URL.
        3. For each crawled page, parses the HTML to find document links.
        4. Deduplicates and persists new DocumentJob entries.
        5. Saves crawl state for future resumption.

        Returns
        -------
        list[DocumentJob]
            All document jobs created during this crawl session.
        """
        logger.info(
            "Starting crawl from %s (max_depth=%d, max_pages=%d)",
            self._crawl_cfg.start_url,
            self._crawl_cfg.max_depth,
            self._crawl_cfg.max_pages,
        )

        # Pre-load already-known document URLs so we skip them.
        await self._load_existing_urls()

        # Run the Crawl4AI deep crawl.
        results = await self._run_crawl4ai()

        # Extract document links from each crawled page.
        new_jobs: list[DocumentJob] = []
        for result in results:
            page_jobs = await self._extract_document_links(result)
            new_jobs.extend(page_jobs)

        # Save crawl state for resumability.
        await self._save_state()

        logger.info(
            "Crawl complete: %d pages crawled, %d documents discovered.",
            self._pages_crawled,
            self._docs_found,
        )
        return new_jobs

    # ------------------------------------------------------------------
    # Crawl4AI integration
    # ------------------------------------------------------------------

    async def _run_crawl4ai(self) -> list[Any]:
        """Run the Crawl4AI async deep crawler and return result objects."""
        browser_config = BrowserConfig(
            headless=True,
            user_agent="LAMC-ADRP/1.0 (ADA Compliance)",
            verbose=False,
        )

        crawl_strategy = BFSDeepCrawlStrategy(
            max_depth=self._crawl_cfg.max_depth,
            include_external=False,
            max_pages=self._crawl_cfg.max_pages,
        )

        run_config = CrawlerRunConfig(
            deep_crawl_strategy=crawl_strategy,
            scraping_strategy=LXMLWebScrapingStrategy(),
            # Rate limiting: mean_delay controls average pause between requests.
            mean_delay=self._crawl_cfg.rate_limit,
            max_range=self._crawl_cfg.rate_limit * 0.5,
            verbose=True,
        )

        results: list[Any] = []
        try:
            async with AsyncWebCrawler(config=browser_config) as crawler:
                crawl_results = await crawler.arun(
                    self._crawl_cfg.start_url,
                    config=run_config,
                )

                # arun returns a list when using deep_crawl_strategy.
                if isinstance(crawl_results, list):
                    results = crawl_results
                else:
                    results = [crawl_results]

                self._pages_crawled = len(results)
                logger.info("Crawl4AI returned %d page results.", len(results))

        except Exception:
            logger.exception("Crawl4AI crawl failed.")
            raise

        return results

    # ------------------------------------------------------------------
    # Document link extraction
    # ------------------------------------------------------------------

    async def _extract_document_links(self, result: Any) -> list[DocumentJob]:
        """Parse a single crawl result page and extract document links.

        Looks at:
        - Standard ``<a href>`` tags with document extensions.
        - ``<iframe>`` / ``<embed>`` / ``<object>`` elements that reference
          documents (common in CMS-embedded viewers).
        - Common CMS file-field URL patterns (Drupal, WordPress, etc.).

        Returns a list of newly-created DocumentJob instances.
        """
        new_jobs: list[DocumentJob] = []
        page_url: str = getattr(result, "url", "")
        html: str = getattr(result, "html", "")

        if not html:
            return new_jobs

        soup = BeautifulSoup(html, "html.parser")

        # Track crawl state for the page itself.
        depth = 0
        metadata = getattr(result, "metadata", None)
        if isinstance(metadata, dict):
            depth = metadata.get("depth", 0)
        await self._db.mark_url_discovered(page_url, depth)
        await self._db.mark_url_visited(page_url)

        # --- Standard <a href> links -----------------------------------------
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            if not href:
                continue
            abs_url = urljoin(page_url, href)
            abs_url = _normalise_url(abs_url)

            if not _EXT_PATTERN.search(abs_url):
                continue

            file_type = _file_type_from_url(abs_url)
            if file_type is None:
                continue

            link_text = anchor.get_text(strip=True)
            link_context = _get_surrounding_context(anchor)

            job = await self._register_document(
                url=abs_url,
                source_page_url=page_url,
                link_text=link_text,
                link_context=link_context,
                file_type=file_type,
            )
            if job is not None:
                new_jobs.append(job)

        # --- Embedded viewers: <iframe>, <embed>, <object> --------------------
        for tag_name, attr in [("iframe", "src"), ("embed", "src"), ("object", "data")]:
            for tag in soup.find_all(tag_name, attrs={attr: True}):
                src = tag[attr].strip()
                if not src:
                    continue
                abs_url = urljoin(page_url, src)
                abs_url = _normalise_url(abs_url)

                # Check for Google Docs viewer wrapping a real document URL.
                extracted = self._extract_viewer_url(abs_url)
                if extracted:
                    abs_url = extracted

                if not _EXT_PATTERN.search(abs_url):
                    continue

                file_type = _file_type_from_url(abs_url)
                if file_type is None:
                    continue

                job = await self._register_document(
                    url=abs_url,
                    source_page_url=page_url,
                    link_text=tag.get("title", ""),
                    link_context=_get_surrounding_context(tag),
                    file_type=file_type,
                )
                if job is not None:
                    new_jobs.append(job)

        # --- CMS-specific patterns in all href/src attributes -----------------
        for tag in soup.find_all(True, attrs={"href": True}):
            href = tag["href"].strip()
            abs_url = urljoin(page_url, href)
            abs_url = _normalise_url(abs_url)
            if abs_url in self._seen_urls:
                continue
            for pattern in _CMS_DOC_PATTERNS:
                if pattern.search(abs_url):
                    file_type = _file_type_from_url(abs_url)
                    if file_type is None:
                        continue
                    job = await self._register_document(
                        url=abs_url,
                        source_page_url=page_url,
                        link_text=tag.get_text(strip=True),
                        link_context=_get_surrounding_context(tag),
                        file_type=file_type,
                    )
                    if job is not None:
                        new_jobs.append(job)
                    break  # Only match first CMS pattern per URL.

        return new_jobs

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_viewer_url(url: str) -> str | None:
        """If *url* is a Google Docs viewer link, extract the target URL."""
        parsed = urlparse(url)
        if "docs.google.com" not in parsed.netloc:
            return None
        from urllib.parse import parse_qs
        params = parse_qs(parsed.query)
        target = params.get("url", [None])[0]
        return target

    async def _register_document(
        self,
        *,
        url: str,
        source_page_url: str,
        link_text: str,
        link_context: str,
        file_type: FileType,
    ) -> DocumentJob | None:
        """Create a DocumentJob if the URL has not been seen before.

        Returns the new job, or ``None`` if it was a duplicate.
        """
        if url in self._seen_urls:
            return None
        self._seen_urls.add(url)

        # Also check the database in case of a resumed crawl.
        if await self._db.job_exists_for_url(url):
            logger.debug("Skipping already-known document: %s", url)
            return None

        job = DocumentJob(
            url=url,
            source_page_url=source_page_url,
            link_text=link_text[:500] if link_text else "",
            link_context=link_context[:1000] if link_context else "",
            file_type=file_type,
            status=JobStatus.DISCOVERED,
        )

        await self._db.create_job(job)
        self._docs_found += 1

        if self._docs_found % 50 == 0:
            logger.info(
                "Progress: %d documents discovered across %d pages.",
                self._docs_found,
                self._pages_crawled,
            )

        return job

    async def _load_existing_urls(self) -> None:
        """Pre-populate ``_seen_urls`` from previously stored jobs."""
        existing_jobs = await self._db.get_all_jobs()
        for job in existing_jobs:
            if job.url:
                self._seen_urls.add(job.url)
        if self._seen_urls:
            logger.info(
                "Loaded %d previously discovered URLs for deduplication.",
                len(self._seen_urls),
            )

    async def _save_state(self) -> None:
        """Persist a snapshot of crawl progress for resumability."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pages_crawled": self._pages_crawled,
            "documents_found": self._docs_found,
            "seen_urls_count": len(self._seen_urls),
        }
        self._state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        logger.info("Crawl state saved to %s", self._state_path)
