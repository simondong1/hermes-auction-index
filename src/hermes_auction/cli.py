"""Command line entry point.

hermes-auction harvest --since 2021-07-01      # pull raw snapshots
hermes-auction build                           # normalise + convert + export
hermes-auction gaps                            # show unnamed vocabulary terms
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from hermes_auction.http import HttpClient
from hermes_auction.models import AuctionHouse
from hermes_auction.pipeline import build_dataset, harvest
from hermes_auction.sources import registry

app = typer.Typer(
    add_completion=False,
    help="Harvest, normalise and publish Hermes Birkin / Kelly auction results.",
    no_args_is_help=True,
)
console = Console()

DEFAULT_ROOT = Path.cwd()

HousesOpt = Annotated[
    list[AuctionHouse] | None,
    typer.Option("--house", "-H", help="Restrict to these houses. Repeatable."),
]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")]


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _resolve_houses(houses: list[AuctionHouse] | None) -> list[AuctionHouse]:
    available = sorted(registry(), key=lambda h: h.value)
    if not houses:
        return available
    unknown = [h for h in houses if h not in available]
    if unknown:
        names = ", ".join(h.value for h in unknown)
        console.print(f"[red]No adapter registered for: {names}[/red]")
        raise typer.Exit(code=2)
    return houses


@app.command("harvest")
def harvest_cmd(
    since: Annotated[
        dt.datetime, typer.Option("--since", formats=["%Y-%m-%d"], help="Earliest sale date.")
    ] = dt.datetime(dt.date.today().year - 5, 1, 1),  # noqa: B008
    until: Annotated[
        dt.datetime | None, typer.Option("--until", formats=["%Y-%m-%d"], help="Latest sale date.")
    ] = None,
    houses: HousesOpt = None,
    root: Annotated[Path, typer.Option("--root", help="Project root.")] = DEFAULT_ROOT,
    min_interval: Annotated[
        float, typer.Option("--min-interval", help="Seconds between requests to a host.")
    ] = 0.7,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Bypass the disk cache.")] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Pull raw lot snapshots from each auction house into ``data/raw/``."""
    _configure_logging(verbose)
    start = since.date()
    end = (until or dt.datetime.now(tz=dt.UTC)).date()
    targets = _resolve_houses(houses)

    console.print(
        f"Harvesting [bold]{', '.join(h.value for h in targets)}[/bold] for sales "
        f"between [cyan]{start}[/cyan] and [cyan]{end}[/cyan]"
    )

    with HttpClient(
        cache_dir=None if no_cache else root / "data" / "cache",
        min_interval=min_interval,
    ) as client:
        reports = harvest(targets, start, end, client=client, raw_dir=root / "data" / "raw")

    table = Table(title="Harvest", header_style="bold")
    table.add_column("House")
    table.add_column("Lots", justify="right")
    table.add_column("Status")
    for report in reports:
        status = "[red]" + report.failure + "[/red]" if report.failure else "[green]ok[/green]"
        table.add_row(report.house.value, str(report.lots), status)
    console.print(table)

    if any(r.failure for r in reports):
        raise typer.Exit(code=1)


@app.command()
def build(
    houses: HousesOpt = None,
    root: Annotated[Path, typer.Option("--root", help="Project root.")] = DEFAULT_ROOT,
    verbose: VerboseOpt = False,
) -> None:
    """Normalise snapshots, convert prices to CNY and write the site dataset."""
    _configure_logging(verbose)
    data = root / "data"
    with HttpClient(cache_dir=data / "cache") as client:
        lots, report = build_dataset(
            data / "raw",
            data / "processed",
            client=client,
            fx_cache=data / "fx-rates.json",
            houses=_resolve_houses(houses) if houses else None,
        )

    table = Table(title="Dataset", header_style="bold")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("raw lots read", f"{report.raw_lots:,}")
    table.add_row("in scope", f"{report.in_scope:,}")
    table.add_row("sold", f"{report.sold:,}")
    table.add_row("converted to CNY", f"{report.priced_in_cny:,}")
    table.add_row("FX carried forward", f"{report.carried_forward_rates:,}")
    for family, count in report.by_family.most_common():
        table.add_row(f"  {family}", f"{count:,}")
    console.print(table)

    if report.unresolved_fields:
        console.print(
            "[yellow]Unresolved attributes:[/yellow] "
            + ", ".join(f"{k}={v:,}" for k, v in report.unresolved_fields.most_common())
        )
    console.print(f"[green]Wrote[/green] {len(lots):,} records to data/processed/dataset.json")


@app.command()
def gaps(
    root: Annotated[Path, typer.Option("--root", help="Project root.")] = DEFAULT_ROOT,
    top: Annotated[int, typer.Option("--top", help="Rows per field.")] = 25,
) -> None:
    """Show unnamed terms the vocabularies missed, ranked by frequency.

    These are the candidates for a vocabulary extension - by hand, or by the optional
    LLM pass, which proposes additions rather than editing the dataset directly.
    """
    report_path = root / "data" / "processed" / "report.json"
    if not report_path.exists():
        console.print("[red]No report found. Run `build` first.[/red]")
        raise typer.Exit(code=1)

    payload = json.loads(report_path.read_text("utf-8"))
    for field_name, rows in payload.get("vocabulary_gaps", {}).items():
        table = Table(title=f"Unnamed terms in lots missing '{field_name}'", header_style="bold")
        table.add_column("Term")
        table.add_column("Lots", justify="right")
        for term, count in rows[:top]:
            table.add_row(term, str(count))
        console.print(table)


@app.command()
def enrich(
    root: Annotated[Path, typer.Option("--root", help="Project root.")] = DEFAULT_ROOT,
    provider: Annotated[
        str | None, typer.Option("--provider", help="cursor-agent | anthropic | openai")
    ] = None,
    min_lots: Annotated[
        int, typer.Option("--min-lots", help="Ignore terms below this lot count.")
    ] = 2,
    verbose: VerboseOpt = False,
) -> None:
    """Ask an LLM to name the vocabulary terms the rules could not resolve.

    Writes reviewable proposals to ``data/processed/vocab-proposals.json``. Nothing is
    merged automatically and no price, date or outcome is ever touched by the model.
    """
    _configure_logging(verbose)
    from hermes_auction.parsing.llm import propose_additions, resolve_provider, write_proposals

    processed = root / "data" / "processed"
    report_path = processed / "report.json"
    if not report_path.exists():
        console.print("[red]No report found. Run `build` first.[/red]")
        raise typer.Exit(code=1)

    gaps_raw = json.loads(report_path.read_text("utf-8")).get("vocabulary_gaps", {})
    gaps = {field: [(term, int(n)) for term, n in rows] for field, rows in gaps_raw.items()}

    chosen = resolve_provider(provider)
    if chosen is None:
        console.print(
            "[yellow]No LLM provider available.[/yellow] Install the Cursor CLI or set "
            "ANTHROPIC_API_KEY / OPENAI_API_KEY, then re-run."
        )
        raise typer.Exit(code=1)

    proposals = propose_additions(gaps, provider=chosen, min_lots=min_lots)
    if proposals is None:
        console.print("[green]Nothing to propose.[/green] The vocabularies cover every term.")
        return

    destination = write_proposals(proposals, processed)

    table = Table(title=f"Proposals from {proposals.provider}", header_style="bold")
    table.add_column("Kind")
    table.add_column("Canonical")
    table.add_column("Group")
    table.add_column("Confidence", justify="right")
    for colour in proposals.colours:
        table.add_row("colour", colour.canonical, colour.family, f"{colour.confidence:.2f}")
    for material in proposals.materials:
        table.add_row(
            "material", material.canonical, material.category, f"{material.confidence:.2f}"
        )
    console.print(table)
    if proposals.rejected:
        console.print(f"[dim]Rejected as non-Hermès: {', '.join(proposals.rejected)}[/dim]")
    console.print(f"Review and merge into [bold]src/hermes_auction/data/[/bold] from {destination}")


def main() -> None:  # pragma: no cover - console-script shim
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
