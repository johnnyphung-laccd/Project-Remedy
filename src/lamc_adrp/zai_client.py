"""Async client for Z.AI API (GLM-OCR, GLM-5, GLM-4.6V).

Provides a unified interface for all Z.AI model interactions used in the
LAMC ADA Document Remediation Pipeline, including OCR layout parsing,
chat completions with thinking mode, and multimodal vision calls.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any

import httpx

from lamc_adrp.config import PipelineConfig

logger = logging.getLogger(__name__)

# Default base URL for the Z.AI API (Coding Plan endpoint).
_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/coding/paas/v4"

# HTTP status codes that trigger automatic retry.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503}


class ZAIClientError(Exception):
    """Raised when a Z.AI API call fails after all retries."""


class ZAIClient:
    """Reusable async client for all Z.AI API interactions.

    Parameters
    ----------
    config:
        Pipeline configuration providing API key, base URL, concurrency
        limits, and retry settings.
    """

    def __init__(self, config: PipelineConfig) -> None:
        self._api_key = config.api.api_key
        self._base_url = (config.api.base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._max_retries = config.api.max_retries
        self._backoff_base = config.api.retry_backoff_base
        self._semaphore = asyncio.Semaphore(config.api.max_concurrent_calls)

        # Cumulative token usage counters.
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0

        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Create the underlying HTTP client."""
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(300.0, connect=30.0),
        )
        logger.info("ZAIClient started (base_url=%s)", self._base_url)

    async def close(self) -> None:
        """Shut down the HTTP client and log cumulative token usage."""
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info(
            "ZAIClient closed. Cumulative tokens — input: %d, output: %d",
            self.total_input_tokens,
            self.total_output_tokens,
        )

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("ZAIClient not started. Call start() first.")
        return self._client

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def ocr(
        self,
        file_path: Path | None = None,
        file_url: str | None = None,
    ) -> str:
        """Extract content from a document using GLM-4.6V vision model.

        Uses GLM-4.6V (included in the Coding Plan) instead of the separate
        GLM-OCR layout_parsing endpoint.  For PDFs, pages are rendered to
        images first since GLM-4.6V only accepts image inputs.

        Parameters
        ----------
        file_path:
            Path to a local file (PDF or image).
        file_url:
            Public URL pointing to an image file.

        Returns
        -------
        str
            Extracted Markdown content.
        """
        if file_path is None and file_url is None:
            raise ValueError("Either file_path or file_url must be provided.")

        # Handle URL-based images directly.
        if file_url:
            return await self._ocr_single_image(image_url=file_url)

        # For local files, check if it's a PDF that needs page-by-page rendering.
        suffix = file_path.suffix.lstrip(".").lower()
        if suffix == "pdf":
            return await self._ocr_pdf(file_path)
        else:
            return await self._ocr_single_image(file_path=file_path)

    async def _ocr_pdf(self, pdf_path: Path) -> str:
        """Render PDF pages to images and extract content page by page."""
        import fitz  # PyMuPDF

        doc = fitz.open(str(pdf_path))
        page_count = len(doc)
        logger.info("OCR: rendering %d PDF pages from %s", page_count, pdf_path.name)

        all_markdown: list[str] = []
        for page_num in range(page_count):
            page = doc[page_num]
            # Render at 200 DPI for good OCR quality.
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            b64 = base64.b64encode(img_bytes).decode()

            logger.debug("OCR: processing page %d/%d", page_num + 1, page_count)
            page_md = await self._ocr_single_image(
                image_b64=b64,
                mime="image/png",
                page_hint=f"Page {page_num + 1} of {page_count}",
            )
            all_markdown.append(f"<!-- Page {page_num + 1} -->\n{page_md}")

        doc.close()
        return "\n\n---\n\n".join(all_markdown)

    async def _ocr_single_image(
        self,
        file_path: Path | None = None,
        image_url: str | None = None,
        image_b64: str | None = None,
        mime: str = "image/png",
        page_hint: str = "",
    ) -> str:
        """Send a single image to GLM-4.6V for content extraction."""
        content: list[dict[str, Any]] = []

        if image_url:
            content.append(
                {"type": "image_url", "image_url": {"url": image_url}}
            )
        elif image_b64:
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}}
            )
        elif file_path:
            raw = file_path.read_bytes()
            b64 = base64.b64encode(raw).decode()
            suffix = file_path.suffix.lstrip(".").lower()
            mime = {
                "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
                "tiff": "image/tiff", "tif": "image/tiff",
            }.get(suffix, "image/png")
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
            )

        hint = f" ({page_hint})" if page_hint else ""
        content.append({
            "type": "text",
            "text": (
                f"Extract ALL content from this document image{hint} as structured "
                "Markdown. Preserve heading levels, lists, tables (as Markdown "
                "tables), bold/italic emphasis, and reading order. For images or "
                "logos, describe them as ![description](image). Be thorough — do "
                "not skip any text, tables, or sections."
            ),
        })

        payload: dict[str, Any] = {
            "model": "glm-4.6v",
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 16384,
            "temperature": 0.1,
        }

        data = await self._post("/chat/completions", payload)
        return self._extract_text(data)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str = "glm-5",
        thinking: bool = False,
        max_tokens: int = 8192,
        temperature: float = 0.3,
    ) -> str:
        """Send a chat completion request to a GLM model.

        Parameters
        ----------
        messages:
            OpenAI-compatible list of ``{"role": ..., "content": ...}`` dicts.
        model:
            Model identifier (e.g. ``"glm-5"``).
        thinking:
            When *True*, enables chain-of-thought thinking mode.
        max_tokens:
            Maximum tokens for the response.
        temperature:
            Sampling temperature.

        Returns
        -------
        str
            The assistant's reply text.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if thinking:
            payload["thinking"] = {"type": "enabled"}

        data = await self._post("/chat/completions", payload)
        return self._extract_text(data)

    async def vision(
        self,
        image_path: Path | None = None,
        image_url: str | None = None,
        prompt: str = "",
    ) -> str:
        """Analyse an image using GLM-4.6V multimodal vision.

        Parameters
        ----------
        image_path:
            Path to a local image file.
        image_url:
            Public URL of the image.
        prompt:
            Text prompt to accompany the image.

        Returns
        -------
        str
            The model's description / analysis.
        """
        if image_path is None and image_url is None:
            raise ValueError("Either image_path or image_url must be provided.")

        content: list[dict[str, Any]] = []

        if image_url:
            content.append(
                {"type": "image_url", "image_url": {"url": image_url}}
            )
        elif image_path:
            raw = image_path.read_bytes()
            b64 = base64.b64encode(raw).decode()
            suffix = image_path.suffix.lstrip(".").lower()
            mime = {
                "png": "image/png",
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "gif": "image/gif",
                "webp": "image/webp",
                "bmp": "image/bmp",
            }.get(suffix, "image/png")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                }
            )

        if prompt:
            content.append({"type": "text", "text": prompt})

        payload: dict[str, Any] = {
            "model": "glm-4.6v",
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 4096,
            "temperature": 0.3,
        }

        data = await self._post("/chat/completions", payload)
        return self._extract_text(data)

    async def health_check(self) -> bool:
        """Validate API connectivity with a minimal chat request.

        Returns
        -------
        bool
            *True* if the API responded successfully, *False* otherwise.
        """
        try:
            result = await self.chat(
                messages=[{"role": "user", "content": "ping"}],
                model="glm-5",
                max_tokens=8,
                temperature=0.0,
            )
            logger.info("Z.AI health check passed (response: %s)", result[:60])
            return True
        except Exception as exc:
            logger.error("Z.AI health check failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute a POST request with retry logic and concurrency control.

        Parameters
        ----------
        endpoint:
            API path relative to the base URL (e.g. ``"/chat/completions"``).
        payload:
            JSON-serialisable request body.

        Returns
        -------
        dict
            Parsed JSON response.

        Raises
        ------
        ZAIClientError
            If the request fails after all retry attempts.
        """
        last_exc: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            async with self._semaphore:
                try:
                    response = await self.client.post(endpoint, json=payload)

                    if response.status_code in _RETRYABLE_STATUS_CODES:
                        wait = self._backoff_base ** attempt
                        logger.warning(
                            "Z.AI %s returned %d (attempt %d/%d). "
                            "Retrying in %.1fs...",
                            endpoint,
                            response.status_code,
                            attempt,
                            self._max_retries,
                            wait,
                        )
                        await asyncio.sleep(wait)
                        continue

                    response.raise_for_status()
                    data: dict[str, Any] = response.json()

                    # Track token usage if present.
                    usage = data.get("usage", {})
                    input_tokens = usage.get("prompt_tokens", 0)
                    output_tokens = usage.get("completion_tokens", 0)
                    if input_tokens or output_tokens:
                        self.total_input_tokens += input_tokens
                        self.total_output_tokens += output_tokens
                        logger.debug(
                            "Token usage for %s — in: %d, out: %d",
                            endpoint,
                            input_tokens,
                            output_tokens,
                        )

                    return data

                except httpx.HTTPStatusError as exc:
                    last_exc = exc
                    logger.error(
                        "Z.AI %s HTTP error %d on attempt %d: %s",
                        endpoint,
                        exc.response.status_code,
                        attempt,
                        exc,
                    )
                    if exc.response.status_code in _RETRYABLE_STATUS_CODES:
                        wait = self._backoff_base ** attempt
                        await asyncio.sleep(wait)
                        continue
                    raise ZAIClientError(
                        f"Non-retryable HTTP {exc.response.status_code} "
                        f"from {endpoint}: {exc}"
                    ) from exc

                except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
                    last_exc = exc
                    wait = self._backoff_base ** attempt
                    logger.warning(
                        "Z.AI %s network error on attempt %d/%d: %s. "
                        "Retrying in %.1fs...",
                        endpoint,
                        attempt,
                        self._max_retries,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)

        raise ZAIClientError(
            f"Z.AI {endpoint} failed after {self._max_retries} attempts: {last_exc}"
        )

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        """Pull the assistant message text from an API response.

        GLM-5 may return content in ``content`` and/or ``reasoning_content``.
        When thinking mode is active, the actual answer is typically in
        ``content`` while chain-of-thought is in ``reasoning_content``.
        If ``content`` is empty, fall back to ``reasoning_content``.
        """
        try:
            choices = data.get("choices", [])
            if not choices:
                logger.warning("Z.AI response contained no choices: %s", data)
                return ""
            message = choices[0].get("message", {})
            content = message.get("content") or ""
            if isinstance(content, str):
                content = content.strip()
            else:
                content = str(content)
            # Fall back to reasoning_content if content is empty.
            if not content:
                reasoning = message.get("reasoning_content") or ""
                if isinstance(reasoning, str):
                    content = reasoning.strip()
            return content
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Failed to extract text from Z.AI response: %s", exc)
            return ""
