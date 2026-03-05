"""Configuration module for the LAMC ADA Document Remediation Pipeline.

Loads settings from .env files and config.yaml, exposing them as typed
dataclasses for use throughout the pipeline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Configuration dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CrawlConfig:
    """Settings that control the web crawler stage."""

    start_url: str = ""
    max_depth: int = 10
    max_pages: int = 10_000
    rate_limit: float = 2.0  # seconds between requests


@dataclass(frozen=True)
class APIConfig:
    """Settings for the upstream AI / OCR API."""

    api_key: str = ""
    base_url: str = ""
    max_concurrent_calls: int = 5
    max_retries: int = 3
    retry_backoff_base: float = 2.0


@dataclass(frozen=True)
class ProcessingConfig:
    """Settings that govern document processing behaviour."""

    max_concurrent_calls: int = 5
    max_retries: int = 3
    retry_backoff_base: float = 2.0


@dataclass(frozen=True)
class OutputConfig:
    """Paths for pipeline output artefacts."""

    output_dir: Path = Path("./output")
    log_dir: Path = Path("./logs")
    db_path: Path = Path("./pipeline.db")


@dataclass(frozen=True)
class ValidationConfig:
    """Validation / remediation loop settings."""

    max_remediation_cycles: int = 3
    fail_on_serious: bool = True


@dataclass
class PipelineConfig:
    """Top-level configuration container aggregating all sub-configs."""

    crawl: CrawlConfig = field(default_factory=CrawlConfig)
    api: APIConfig = field(default_factory=APIConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load and return a YAML file as a dictionary, or empty dict on failure."""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def _env(key: str, default: str = "") -> str:
    """Return an environment variable value or *default*."""
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return int(raw)


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return float(raw)


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes")


def load_config(
    env_path: Path | None = None,
    yaml_path: Path | None = None,
) -> PipelineConfig:
    """Build a ``PipelineConfig`` by merging .env and YAML sources.

    Resolution order (last wins):
        1. Compiled defaults in the dataclasses above
        2. Values from *config.yaml*
        3. Environment variables (loaded from *.env* if present)

    Parameters
    ----------
    env_path:
        Explicit path to a ``.env`` file.  Falls back to ``.env`` in cwd.
    yaml_path:
        Explicit path to a YAML config file.  Falls back to ``config.yaml``
        in cwd.
    """
    # --- Load .env ---------------------------------------------------------
    dotenv_file = env_path or Path(".env")
    load_dotenv(dotenv_file, override=True)

    # --- Load YAML ---------------------------------------------------------
    yaml_file = yaml_path or Path("config.yaml")
    yml: dict[str, Any] = _load_yaml(yaml_file)

    crawl_yml = yml.get("crawl", {})
    api_yml = yml.get("api", {})
    processing_yml = yml.get("processing", {})
    output_yml = yml.get("output", {})
    validation_yml = yml.get("validation", {})

    # --- Build sub-configs (env overrides yaml overrides defaults) ----------

    crawl = CrawlConfig(
        start_url=_env("CRAWL_START_URL", crawl_yml.get("start_url", "")),
        max_depth=_env_int("CRAWL_MAX_DEPTH", crawl_yml.get("max_depth", 10)),
        max_pages=_env_int("CRAWL_MAX_PAGES", crawl_yml.get("max_pages", 10_000)),
        rate_limit=_env_float("CRAWL_RATE_LIMIT", crawl_yml.get("rate_limit", 2.0)),
    )

    api = APIConfig(
        api_key=_env("ZAI_API_KEY", api_yml.get("api_key", "")),
        base_url=_env("ZAI_BASE_URL", api_yml.get("base_url", "")),
        max_concurrent_calls=_env_int(
            "MAX_CONCURRENT_API_CALLS",
            api_yml.get("max_concurrent_calls", 5),
        ),
        max_retries=_env_int(
            "MAX_RETRIES",
            api_yml.get("max_retries", 3),
        ),
        retry_backoff_base=_env_float(
            "RETRY_BACKOFF_BASE",
            api_yml.get("retry_backoff_base", 2.0),
        ),
    )

    processing = ProcessingConfig(
        max_concurrent_calls=_env_int(
            "MAX_CONCURRENT_API_CALLS",
            processing_yml.get("max_concurrent_calls", 5),
        ),
        max_retries=_env_int(
            "MAX_RETRIES",
            processing_yml.get("max_retries", 3),
        ),
        retry_backoff_base=_env_float(
            "RETRY_BACKOFF_BASE",
            processing_yml.get("retry_backoff_base", 2.0),
        ),
    )

    output = OutputConfig(
        output_dir=Path(
            _env("OUTPUT_DIR", str(output_yml.get("output_dir", "./output")))
        ),
        log_dir=Path(_env("LOG_DIR", str(output_yml.get("log_dir", "./logs")))),
        db_path=Path(
            _env("DB_PATH", str(output_yml.get("db_path", "./pipeline.db")))
        ),
    )

    validation = ValidationConfig(
        max_remediation_cycles=_env_int(
            "VALIDATION_MAX_REMEDIATION_CYCLES",
            validation_yml.get("max_remediation_cycles", 3),
        ),
        fail_on_serious=_env_bool(
            "VALIDATION_FAIL_ON_SERIOUS",
            validation_yml.get("fail_on_serious", True),
        ),
    )

    return PipelineConfig(
        crawl=crawl,
        api=api,
        processing=processing,
        output=output,
        validation=validation,
    )
