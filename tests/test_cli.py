"""CLI wiring: correct exit codes and no accidental network access."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from typer.testing import CliRunner

from hermes_auction.cli import app
from hermes_auction.models import AuctionHouse, LotOutcome, PriceBasis, RawLot, SaleRef

runner = CliRunner()


def write_snapshot(root, title="Craie Epsom Birkin Sellier 25 Gold Hardware, 2022"):
    raw = root / "data" / "raw"
    raw.mkdir(parents=True)
    lot = RawLot(
        house=AuctionHouse.CHRISTIES,
        lot_key="1",
        title=title,
        sale=SaleRef(
            house=AuctionHouse.CHRISTIES,
            sale_id="1",
            sale_name="Handbags & Accessories",
            sale_date=dt.date(2024, 5, 24),
            location="Hong Kong",
        ),
        currency="USD",
        price_realised=Decimal("25200"),
        price_basis=PriceBasis.PREMIUM_INCLUSIVE,
        outcome=LotOutcome.SOLD,
    )
    (raw / "christies.jsonl").write_text(lot.model_dump_json() + "\n", "utf-8")


def test_help_lists_every_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("harvest", "build", "gaps", "enrich"):
        assert command in result.output


def test_build_without_snapshots_fails_loudly(tmp_path):
    result = runner.invoke(app, ["build", "--root", str(tmp_path)])
    assert result.exit_code != 0


def test_gaps_before_build_explains_itself(tmp_path):
    result = runner.invoke(app, ["gaps", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "Run `build` first" in result.output


def test_enrich_before_build_explains_itself(tmp_path):
    result = runner.invoke(app, ["enrich", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "Run `build` first" in result.output


def test_every_declared_house_has_an_adapter():
    """The enum and the registry must not drift apart."""
    from hermes_auction.models import AuctionHouse
    from hermes_auction.sources import registry

    assert set(AuctionHouse) == set(registry())


def test_harvest_rejects_an_unknown_house(tmp_path):
    result = runner.invoke(app, ["harvest", "--house", "not-a-house", "--root", str(tmp_path)])
    assert result.exit_code == 2


def test_gaps_renders_the_report_written_by_build(tmp_path):
    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    (processed / "report.json").write_text(
        json.dumps({"vocabulary_gaps": {"colour": [["vanille", 12], ["garance", 7]]}}), "utf-8"
    )
    result = runner.invoke(app, ["gaps", "--root", str(tmp_path), "--top", "5"])
    assert result.exit_code == 0
    assert "vanille" in result.output
    assert "garance" in result.output
