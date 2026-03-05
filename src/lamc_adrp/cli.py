"""Click CLI entry point for the LAMC ADA Document Remediation Pipeline."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from lamc_adrp import __version__
from lamc_adrp.config import PipelineConfig, load_config
from lamc_adrp.logging_config import setup_logging
from lamc_adrp.models import JobStatus

console = Console()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_cfg(env: str | None, config: str | None) -> PipelineConfig:
    """Load configuration from env and yaml paths."""
    env_path = Path(env) if env else None
    yaml_path = Path(config) if config else None
    return load_config(env_path=env_path, yaml_path=yaml_path)


def _run_async(coro) -> None:
    """Bridge a synchronous Click command to an async coroutine.

    Catches KeyboardInterrupt for graceful shutdown.
    """
    try:
        asyncio.run(coro)
    except KeyboardInterrupt:
        console.print("\n[yellow]Pipeline interrupted by user.[/yellow]")
        sys.exit(130)
    except Exception as exc:
        console.print(f"\n[bold red]Pipeline error:[/bold red] {exc}")
        logger.exception("Pipeline failed with unhandled exception.")
        sys.exit(1)


def _print_report(report) -> None:
    """Print a PipelineReport as a Rich panel."""
    from lamc_adrp.pipeline import PipelineReport

    lines = report.summary_lines()
    body = "\n".join(lines)

    if report.errors:
        body += "\n\n[bold red]Errors:[/bold red]"
        for err in report.errors[:20]:
            body += f"\n  - {err[:120]}"
        if len(report.errors) > 20:
            body += f"\n  ... and {len(report.errors) - 20} more"

    console.print(Panel(body, title="Pipeline Report", border_style="green"))


def _print_job_table(jobs, title: str = "Jobs") -> None:
    """Print a summary table of document jobs."""
    table = Table(title=title, show_lines=True)
    table.add_column("ID", style="dim", max_width=12)
    table.add_column("URL", max_width=50)
    table.add_column("Type", justify="center")
    table.add_column("Status", justify="center")

    status_styles = {
        JobStatus.VALIDATED: "green",
        JobStatus.FAILED: "red",
        JobStatus.FLAGGED: "yellow",
        JobStatus.CONVERTED: "cyan",
        JobStatus.DOWNLOADED: "blue",
    }

    for job in jobs[:50]:
        style = status_styles.get(job.status, "")
        status_text = f"[{style}]{job.status.value}[/{style}]" if style else job.status.value
        file_type = job.file_type.value.upper() if job.file_type else "?"
        url_short = job.url[-50:] if len(job.url) > 50 else job.url
        table.add_row(job.id[:12], url_short, file_type, status_text)

    if len(jobs) > 50:
        table.add_row("...", f"({len(jobs) - 50} more)", "", "")

    console.print(table)
    console.print(f"Total: {len(jobs)} job(s)")


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(version=__version__, prog_name="lamc-adrp")
def cli() -> None:
    """LAMC ADA Document Remediation Pipeline.

    Crawl, extract, remediate, and validate inaccessible documents
    from the LAMC website, producing WCAG 2.1 AA compliant HTML.
    """


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--env", default=None, help="Path to .env file.")
@click.option("--config", default=None, help="Path to config.yaml file.")
def crawl(env: str | None, config: str | None) -> None:
    """Stage 1-2: Crawl the site, discover and download documents."""

    async def _crawl() -> None:
        from lamc_adrp.pipeline import Pipeline

        try:
            cfg = _load_cfg(env, config)
        except Exception as exc:
            console.print(f"[bold red]Config error:[/bold red] {exc}")
            sys.exit(1)

        setup_logging(cfg.output.log_dir)

        if not cfg.crawl.start_url:
            console.print("[bold red]Error:[/bold red] crawl.start_url is not configured.")
            sys.exit(1)

        console.print(
            f"[bold green]Starting crawl[/bold green] from {cfg.crawl.start_url}"
        )
        console.print(
            f"  max_depth={cfg.crawl.max_depth}  "
            f"max_pages={cfg.crawl.max_pages}  "
            f"rate_limit={cfg.crawl.rate_limit}s"
        )

        pipeline = Pipeline(cfg)
        try:
            await pipeline.start()
            jobs = await pipeline.crawl()
            _print_job_table(jobs, "Crawl Results")
        finally:
            await pipeline.close()

    _run_async(_crawl())


@cli.command()
@click.option("--env", default=None, help="Path to .env file.")
@click.option("--config", default=None, help="Path to config.yaml file.")
def process(env: str | None, config: str | None) -> None:
    """Stage 3-6: Extract, plan, convert, and vision-process documents."""

    async def _process() -> None:
        from lamc_adrp.pipeline import Pipeline

        try:
            cfg = _load_cfg(env, config)
        except Exception as exc:
            console.print(f"[bold red]Config error:[/bold red] {exc}")
            sys.exit(1)

        setup_logging(cfg.output.log_dir)

        pipeline = Pipeline(cfg)
        try:
            await pipeline.start()

            downloaded = await pipeline._db.get_jobs_by_status(JobStatus.DOWNLOADED)
            console.print(
                f"[bold green]Processing[/bold green] {len(downloaded)} downloaded document(s)."
            )

            jobs = await pipeline.process()

            _print_job_table(jobs, "Processing Results")
        finally:
            await pipeline.close()

    _run_async(_process())


@cli.command()
@click.option("--env", default=None, help="Path to .env file.")
@click.option("--config", default=None, help="Path to config.yaml file.")
def validate(env: str | None, config: str | None) -> None:
    """Stage 7: Run accessibility validation on converted documents."""

    async def _validate() -> None:
        from lamc_adrp.pipeline import Pipeline

        try:
            cfg = _load_cfg(env, config)
        except Exception as exc:
            console.print(f"[bold red]Config error:[/bold red] {exc}")
            sys.exit(1)

        setup_logging(cfg.output.log_dir)

        pipeline = Pipeline(cfg)
        try:
            await pipeline.start()

            converted = await pipeline._db.get_jobs_by_status(JobStatus.CONVERTED)
            console.print(
                f"[bold green]Validating[/bold green] {len(converted)} converted document(s)."
            )

            jobs = await pipeline.validate_all()

            passed = sum(1 for j in jobs if j.status == JobStatus.VALIDATED)
            flagged = sum(1 for j in jobs if j.status == JobStatus.FLAGGED)
            console.print(
                f"Results: [green]{passed} passed[/green], "
                f"[yellow]{flagged} flagged[/yellow]"
            )
            _print_job_table(jobs, "Validation Results")
        finally:
            await pipeline.close()

    _run_async(_validate())


@cli.command()
@click.option("--env", default=None, help="Path to .env file.")
@click.option("--config", default=None, help="Path to config.yaml file.")
def deploy(env: str | None, config: str | None) -> None:
    """Stage 8: Deploy validated documents to output directory."""

    async def _deploy() -> None:
        from lamc_adrp.pipeline import Pipeline

        try:
            cfg = _load_cfg(env, config)
        except Exception as exc:
            console.print(f"[bold red]Config error:[/bold red] {exc}")
            sys.exit(1)

        setup_logging(cfg.output.log_dir)

        pipeline = Pipeline(cfg)
        try:
            await pipeline.start()

            validated = await pipeline._db.get_jobs_by_status(
                JobStatus.VALIDATED, JobStatus.FLAGGED
            )
            console.print(
                f"[bold green]Deploying[/bold green] {len(validated)} document(s)."
            )

            output_path = await pipeline.deploy()
            console.print(
                f"[bold green]Deployment complete:[/bold green] {output_path}"
            )
        finally:
            await pipeline.close()

    _run_async(_deploy())


@cli.command()
@click.option("--env", default=None, help="Path to .env file.")
@click.option("--config", default=None, help="Path to config.yaml file.")
def run(env: str | None, config: str | None) -> None:
    """Run the full pipeline (crawl -> process -> validate -> deploy)."""

    async def _run_all() -> None:
        from lamc_adrp.pipeline import Pipeline

        try:
            cfg = _load_cfg(env, config)
        except Exception as exc:
            console.print(f"[bold red]Config error:[/bold red] {exc}")
            sys.exit(1)

        setup_logging(cfg.output.log_dir)
        console.print("[bold green]Starting full pipeline run.[/bold green]")

        pipeline = Pipeline(cfg)
        try:
            await pipeline.start()
            report = await pipeline.run()
            _print_report(report)
        finally:
            await pipeline.close()

    _run_async(_run_all())


@cli.command()
@click.option("--env", default=None, help="Path to .env file.")
@click.option("--config", default=None, help="Path to config.yaml file.")
def status(env: str | None, config: str | None) -> None:
    """Show current pipeline progress."""

    async def _status() -> None:
        from lamc_adrp.database import DatabaseManager

        try:
            cfg = _load_cfg(env, config)
        except Exception as exc:
            console.print(f"[bold red]Config error:[/bold red] {exc}")
            sys.exit(1)

        setup_logging(cfg.output.log_dir)

        db = DatabaseManager(cfg.output.db_path)
        try:
            await db.connect()
            stats = await db.get_stats()
            total = sum(stats.values())

            table = Table(title="Pipeline Status", show_lines=True)
            table.add_column("Status", style="bold")
            table.add_column("Count", justify="right")
            table.add_column("Percentage", justify="right")

            for member in JobStatus:
                count = stats.get(member.value, 0)
                pct = f"{count / total * 100:.1f}%" if total else "0.0%"
                style = ""
                if member == JobStatus.VALIDATED:
                    style = "green"
                elif member == JobStatus.FAILED:
                    style = "red"
                elif member == JobStatus.FLAGGED:
                    style = "yellow"
                table.add_row(
                    f"[{style}]{member.value}[/{style}]" if style else member.value,
                    str(count),
                    pct,
                )

            table.add_row("[bold]TOTAL[/bold]", f"[bold]{total}[/bold]", "100.0%")
            console.print(table)
        finally:
            await db.close()

    _run_async(_status())


@cli.command("retry-failed")
@click.option("--env", default=None, help="Path to .env file.")
@click.option("--config", default=None, help="Path to config.yaml file.")
def retry_failed(env: str | None, config: str | None) -> None:
    """Reset failed jobs and reprocess them."""

    async def _retry() -> None:
        from lamc_adrp.pipeline import Pipeline

        try:
            cfg = _load_cfg(env, config)
        except Exception as exc:
            console.print(f"[bold red]Config error:[/bold red] {exc}")
            sys.exit(1)

        setup_logging(cfg.output.log_dir)

        pipeline = Pipeline(cfg)
        try:
            await pipeline.start()
            reset_jobs = await pipeline.retry_failed()

            if not reset_jobs:
                console.print("[green]No failed jobs to retry.[/green]")
                return

            console.print(
                f"[bold green]Reset {len(reset_jobs)} failed job(s) "
                f"to 'discovered'.[/bold green]"
            )
            _print_job_table(reset_jobs, "Reset Jobs")
        finally:
            await pipeline.close()

    _run_async(_retry())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
