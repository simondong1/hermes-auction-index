"""Pipeline: scope filtering, CNY conversion, export shape and the gap report."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest

from hermes_auction.fx import FxConverter
from hermes_auction.http import HttpClient
from hermes_auction.models import (
    AuctionHouse,
    LotOutcome,
    PriceBasis,
    RawLot,
    SaleRef,
)
from hermes_auction.pipeline import (
    BuildReport,
    find_vocabulary_gaps,
    load_raw,
    normalise_lots,
    write_site_payload,
)


def make_lot(
    key: str,
    title: str,
    *,
    price: Decimal | None = Decimal("100000"),
    currency: str = "USD",
    outcome: LotOutcome = LotOutcome.SOLD,
    sale_date: dt.date = dt.date(2024, 5, 24),
) -> RawLot:
    return RawLot(
        house=AuctionHouse.CHRISTIES,
        lot_key=key,
        title=title,
        sale=SaleRef(
            house=AuctionHouse.CHRISTIES,
            sale_id="1",
            sale_name="Handbags & Accessories",
            sale_date=sale_date,
            location="Hong Kong",
        ),
        currency=currency,
        price_realised=price,
        price_basis=PriceBasis.PREMIUM_INCLUSIVE if price else PriceBasis.UNKNOWN,
        outcome=outcome,
    )


class StubConverter:
    """A converter with a fixed rate, so pipeline tests never touch the network."""

    def try_convert(self, amount, _currency, on):
        if amount is None:
            return None
        from hermes_auction.models import FxConversion

        return FxConversion(
            amount_cny=amount * Decimal("7.2"),
            rate=Decimal("7.2"),
            rate_date=on,
            carried_forward=False,
            source="stub",
        )


def test_out_of_scope_lots_are_dropped_and_counted():
    raw = [
        make_lot("1", "A GOLD TOGO BIRKIN 30 WITH GOLD HARDWARE"),
        make_lot("2", "A BLACK TOGO KELLY DANSE WITH PALLADIUM HARDWARE"),
        make_lot("3", "HERMÈS A silk twill scarf"),
    ]
    report = BuildReport()
    lots = normalise_lots(raw, StubConverter(), report)  # type: ignore[arg-type]

    assert len(lots) == 1
    assert report.raw_lots == 3
    assert report.in_scope == 1
    assert report.by_family == {"birkin": 1}


def test_prices_are_converted_to_cny():
    report = BuildReport()
    lots = normalise_lots(
        [make_lot("1", "Craie Epsom Birkin 25 Gold Hardware, 2022")],
        StubConverter(),  # type: ignore[arg-type]
        report,
    )
    assert lots[0].realised_cny is not None
    assert lots[0].realised_cny.amount_cny == Decimal("720000.0")
    assert report.priced_in_cny == 1


def test_a_withheld_result_is_not_counted_as_sold():
    report = BuildReport()
    normalise_lots(
        [
            make_lot(
                "1",
                "Craie Epsom Birkin 25 Gold Hardware, 2022",
                price=None,
                outcome=LotOutcome.RESULT_HIDDEN,
            )
        ],
        StubConverter(),  # type: ignore[arg-type]
        report,
    )
    assert report.sold == 0
    assert report.by_outcome == {"result_hidden": 1}


def test_lots_are_sorted_most_recent_first():
    report = BuildReport()
    lots = normalise_lots(
        [
            make_lot("old", "Noir Togo Birkin 30 Gold Hardware", sale_date=dt.date(2022, 1, 1)),
            make_lot("new", "Craie Epsom Birkin 25 Gold Hardware", sale_date=dt.date(2025, 1, 1)),
        ],
        StubConverter(),  # type: ignore[arg-type]
        report,
    )
    assert [lot.raw.lot_key for lot in lots] == ["new", "old"]


def test_a_sold_lot_must_carry_a_price():
    """The model refuses the one inconsistency that would silently corrupt a median."""
    with pytest.raises(ValueError, match="marked sold but carries no realised price"):
        make_lot("1", "Birkin 25", price=None, outcome=LotOutcome.SOLD)


def test_vocabulary_gaps_surface_unknown_terms():
    """A colour the vocabulary has never seen is reported, not silently dropped."""
    report = BuildReport()
    lots = normalise_lots(
        [make_lot("1", "A MATTE ZZZFICTIONAL ALLIGATOR BIRKIN 25 WITH GOLD HARDWARE")],
        StubConverter(),  # type: ignore[arg-type]
        report,
    )
    assert lots[0].attributes.colour is None
    assert "colour" in lots[0].attributes.unresolved
    assert "zzzfictional" in dict(find_vocabulary_gaps(lots)["colour"])


def test_gap_report_ignores_model_and_hardware_words():
    """Otherwise 'birkin' and 'palladium' top every list and hide the real gaps."""
    report = BuildReport()
    lots = normalise_lots(
        [make_lot("1", "A LIMITED EDITION ZZZFICTIONAL BIRKIN 25 WITH PALLADIUM HARDWARE")],
        StubConverter(),  # type: ignore[arg-type]
        report,
    )
    terms = dict(find_vocabulary_gaps(lots)["colour"])
    assert "zzzfictional" in terms
    assert not {"birkin", "palladium", "limited", "edition"} & terms.keys()


def test_site_payload_is_valid_json_with_the_expected_keys(tmp_path):
    report = BuildReport()
    lots = normalise_lots(
        [make_lot("1", "Craie Epsom Birkin Sellier 25 Gold Hardware, 2022")],
        StubConverter(),  # type: ignore[arg-type]
        report,
    )
    write_site_payload(lots, report, tmp_path)

    payload = json.loads((tmp_path / "dataset.json").read_text("utf-8"))
    assert {"generatedAt", "families", "houses", "fxSource", "summary", "lots"} <= set(payload)
    assert [f["key"] for f in payload["families"]] == [
        "mini_kelly",
        "kelly_pochette",
        "birkin",
        "kelly",
    ]
    record = payload["lots"][0]
    assert record["sizeBucket"] == "Birkin 25"
    assert record["colour"] == "Craie"
    assert record["priceCny"] == 720000.0
    assert record["priceBasis"] == "premium_inclusive"


def test_raw_snapshots_round_trip_through_disk(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    original = make_lot("1", "Craie Epsom Birkin 25 Gold Hardware, 2022")
    (raw_dir / "christies.jsonl").write_text(original.model_dump_json() + "\n", "utf-8")

    restored = list(load_raw(raw_dir, [AuctionHouse.CHRISTIES]))
    assert len(restored) == 1
    assert restored[0] == original


def test_a_corrupt_snapshot_line_is_skipped_not_fatal(tmp_path, caplog):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    good = make_lot("1", "Craie Epsom Birkin 25 Gold Hardware, 2022")
    (raw_dir / "christies.jsonl").write_text(
        '{"not":"a lot"}\n' + good.model_dump_json() + "\n", "utf-8"
    )
    restored = list(load_raw(raw_dir, [AuctionHouse.CHRISTIES]))
    assert [lot.lot_key for lot in restored] == ["1"]


def test_fx_converter_and_http_client_close_cleanly():
    client = HttpClient(cache_dir=None, min_interval=0.0)
    FxConverter(client)
    client.close()


def test_cache_key_can_ignore_a_rotating_credential(tmp_path):
    """Sotheby's Algolia key lives in the URL and rotates every few hours."""
    client = HttpClient(cache_dir=tmp_path, min_interval=0.0)
    try:
        base = "https://example.test/search?query=birkin&x-algolia-api-key="
        first = client._cache_path("GET", base + "KEY_ONE", None, ("x-algolia-api-key",))
        second = client._cache_path("GET", base + "KEY_TWO", None, ("x-algolia-api-key",))
        assert first == second

        # Without the exemption the two keys must produce different cache entries.
        assert client._cache_path("GET", base + "KEY_ONE", None) != client._cache_path(
            "GET", base + "KEY_TWO", None
        )
    finally:
        client.close()
