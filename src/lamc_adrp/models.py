"""Core data models for the LAMC ADA Document Remediation Pipeline."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


class JobStatus(enum.Enum):
    """Lifecycle status of a document remediation job."""

    DISCOVERED = "discovered"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    PLANNING = "planning"
    PLANNED = "planned"
    CONVERTING = "converting"
    CONVERTED = "converted"
    VALIDATING = "validating"
    VALIDATED = "validated"
    FAILED = "failed"
    FLAGGED = "flagged"


class FileType(enum.Enum):
    """Supported document file types."""

    PDF = "pdf"
    DOCX = "docx"
    DOC = "doc"
    PPTX = "pptx"
    PPT = "ppt"
    XLSX = "xlsx"
    XLS = "xls"


@dataclass
class ValidationResult:
    """Result from a single accessibility validation tool."""

    tool: str  # axe, pa11y, or lighthouse
    score: float | None = None  # Lighthouse accessibility score (0-100)
    violations: list[dict[str, Any]] = field(default_factory=list)
    passed: bool = False


@dataclass
class DocumentJob:
    """Represents a single document remediation job through the pipeline."""

    id: str = field(default_factory=lambda: uuid4().hex)
    url: str = ""
    source_page_url: str = ""
    link_text: str = ""
    link_context: str = ""
    file_type: FileType | None = None
    local_path: str = ""
    file_hash: str = ""
    file_size: int = 0
    status: JobStatus = JobStatus.DISCOVERED
    ocr_markdown: str = ""
    html_plan: str = ""
    generated_html: str = ""
    final_html_path: str = ""
    validation_results: list[ValidationResult] = field(default_factory=list)
    remediation_count: int = 0
    error_message: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Serialize the job to a dictionary for database storage."""
        return {
            "id": self.id,
            "url": self.url,
            "source_page_url": self.source_page_url,
            "link_text": self.link_text,
            "link_context": self.link_context,
            "file_type": self.file_type.value if self.file_type else None,
            "local_path": self.local_path,
            "file_hash": self.file_hash,
            "file_size": self.file_size,
            "status": self.status.value,
            "ocr_markdown": self.ocr_markdown,
            "html_plan": self.html_plan,
            "generated_html": self.generated_html,
            "final_html_path": self.final_html_path,
            "validation_results": _serialize_validation_results(
                self.validation_results
            ),
            "remediation_count": self.remediation_count,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocumentJob:
        """Deserialize a job from a database row dictionary."""
        import json

        validation_raw = data.get("validation_results", "[]")
        if isinstance(validation_raw, str):
            validation_raw = json.loads(validation_raw)

        validation_results = [
            ValidationResult(
                tool=v["tool"],
                score=v.get("score"),
                violations=v.get("violations", []),
                passed=v.get("passed", False),
            )
            for v in validation_raw
        ]

        file_type_raw = data.get("file_type")
        file_type = FileType(file_type_raw) if file_type_raw else None

        return cls(
            id=data["id"],
            url=data.get("url", ""),
            source_page_url=data.get("source_page_url", ""),
            link_text=data.get("link_text", ""),
            link_context=data.get("link_context", ""),
            file_type=file_type,
            local_path=data.get("local_path", ""),
            file_hash=data.get("file_hash", ""),
            file_size=data.get("file_size", 0),
            status=JobStatus(data.get("status", "discovered")),
            ocr_markdown=data.get("ocr_markdown", ""),
            html_plan=data.get("html_plan", ""),
            generated_html=data.get("generated_html", ""),
            final_html_path=data.get("final_html_path", ""),
            validation_results=validation_results,
            remediation_count=data.get("remediation_count", 0),
            error_message=data.get("error_message", ""),
            created_at=_parse_datetime(data.get("created_at", "")),
            updated_at=_parse_datetime(data.get("updated_at", "")),
        )


def _serialize_validation_results(results: list[ValidationResult]) -> str:
    """Serialize validation results to a JSON string for storage."""
    import json

    return json.dumps(
        [
            {
                "tool": r.tool,
                "score": r.score,
                "violations": r.violations,
                "passed": r.passed,
            }
            for r in results
        ]
    )


def _parse_datetime(value: str | datetime) -> datetime:
    """Parse an ISO datetime string or return the datetime as-is."""
    if isinstance(value, datetime):
        return value
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value)
