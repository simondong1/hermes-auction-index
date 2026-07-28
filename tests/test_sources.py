"""Adapter contract and the shared money/date parsing helpers."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TypeVar, cast

import pytest

from hermes_auction.http import HttpClient
from hermes_auction.models import AuctionHouse, LotOutcome, PriceBasis
from hermes_auction.parsing.attributes import parse_attributes
from hermes_auction.sources import registry
from hermes_auction.sources.artcurial import ArtcurialSource
from hermes_auction.sources.base import AuctionSource, parse_date, parse_money
from hermes_auction.sources.bonhams import BonhamsSource
from hermes_auction.sources.christies import ChristiesSource
from hermes_auction.sources.heritage import HeritageSource
from hermes_auction.sources.poly_hk import PolyHongKongSource
from hermes_auction.sources.sothebys import _read_result, _sale_url

TSource = TypeVar("TSource", bound=AuctionSource)


def offline(cls: type[TSource]) -> TSource:
    """Build an adapter for pure row-mapping tests.

    ``_to_raw_lot`` and ``_sale_ref`` never touch the network, so no client is needed.
    The cast keeps the production signature honest (adapters do require a client) while
    letting these tests stay free of HTTP mocking.
    """
    return cls(cast(HttpClient, None))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("HKD 1,500,000", (Decimal("1500000"), "HKD")),
        ("USD 100,800", (Decimal("100800"), "USD")),
        ("EUR 69,850", (Decimal("69850"), "EUR")),
        ("£ 12,500", (Decimal("12500"), "GBP")),
        ("GBP 8,000", (Decimal("8000"), "GBP")),
        ("", None),
        (None, None),
        ("Price on request", None),
        ("Estimate on request", None),
    ],
)
def test_parse_money(text, expected):
    assert parse_money(text) == expected


def test_parse_money_falls_back_to_a_supplied_currency():
    assert parse_money("50,000", default_currency="HKD") == (Decimal("50000"), "HKD")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2024-05-25T00:00Z", dt.date(2024, 5, 25)),
        ("2021-05-21T00:00:00", dt.date(2021, 5, 21)),
        ("2023-11-08", dt.date(2023, 11, 8)),
        ("not a date", None),
        (None, None),
    ],
)
def test_parse_date(text, expected):
    assert parse_date(text) == expected


def test_every_adapter_declares_a_house_and_a_coverage_note():
    assert registry(), "no adapters registered"
    for house, cls in registry().items():
        assert cls.house is house
        assert cls.coverage_note, f"{house} must document its coverage"


CHRISTIES_ROW = {
    "object_id": "6319552",
    "lot_id_txt": "3976",
    "end_date": "2021-05-21T00:00:00",
    "start_date": "2021-05-21T00:00:00",
    "url": "https://www.christies.com/en/lot/lot-6319552?ldp_breadcrumb=back",
    "title_primary_txt": "A SHINY JADE POROSUS CROCODILE BIRKIN 25 WITH GOLD HARDWARE",
    "title_secondary_txt": "HERMÈS, 2020",
    "description_txt": "GRADE: 1",
    "estimate_txt": "HKD 300,000 - 350,000",
    "price_realised_txt": "HKD 1,500,000",
    "sale": {"id": "29050", "number": "19861", "location": "Hong Kong", "type": "NormalSale"},
    "image": {
        "image_src": "https://www.christies.com/img/lotimages/2021/HGK/2021_HGK_19861_3976_000(x).jpg"
    },
}


def test_christies_row_maps_onto_a_raw_lot():
    lot = offline(ChristiesSource)._to_raw_lot(CHRISTIES_ROW)
    assert lot is not None
    assert lot.house is AuctionHouse.CHRISTIES
    assert lot.lot_key == "6319552"
    assert lot.currency == "HKD"
    assert lot.estimate_low == Decimal("300000")
    assert lot.estimate_high == Decimal("350000")
    assert lot.price_realised == Decimal("1500000")
    assert lot.price_basis is PriceBasis.PREMIUM_INCLUSIVE
    assert lot.outcome is LotOutcome.SOLD
    assert lot.sale.sale_date == dt.date(2021, 5, 21)
    assert lot.sale.location == "Hong Kong"
    assert lot.extra["saleroom"] == "HGK"
    # The tracking query string is dropped so lot URLs are stable across runs.
    assert str(lot.lot_url) == "https://www.christies.com/en/lot/lot-6319552"


def test_christies_lot_without_a_realised_price_is_unsold_not_sold():
    row = {**CHRISTIES_ROW, "price_realised_txt": ""}
    lot = offline(ChristiesSource)._to_raw_lot(row)
    assert lot is not None
    assert lot.outcome is LotOutcome.UNSOLD
    assert lot.price_realised is None
    # Currency must still be recoverable from the estimate.
    assert lot.currency == "HKD"


def test_christies_row_without_a_date_is_dropped():
    row = {**CHRISTIES_ROW, "end_date": None, "start_date": None}
    assert offline(ChristiesSource)._to_raw_lot(row) is None


@pytest.mark.parametrize(
    ("detail", "expected_outcome", "expected_price"),
    [
        (
            {
                "bidState": {
                    "sold": {
                        "__typename": "ResultVisible",
                        "isSold": True,
                        "premiums": {"finalPriceV2": {"amount": "25200"}},
                    }
                }
            },
            LotOutcome.SOLD,
            Decimal("25200"),
        ),
        ({"bidState": {"sold": {"__typename": "ResultHidden"}}}, LotOutcome.RESULT_HIDDEN, None),
        (
            {"bidState": {"sold": {"__typename": "ResultVisible", "isSold": False}}},
            LotOutcome.UNSOLD,
            None,
        ),
        ({"withdrawnState": {"state": "Withdrawn"}}, LotOutcome.WITHDRAWN, None),
        ({}, LotOutcome.UNKNOWN, None),
    ],
)
def test_sothebys_result_union_never_invents_a_price(detail, expected_outcome, expected_price):
    outcome, price = _read_result(detail)
    assert outcome is expected_outcome
    assert price == expected_price


def test_sothebys_sale_url_is_the_lot_slug_minus_its_last_segment():
    assert (
        _sale_url("/en/buy/auction/2025/handbags-accessories/gris-meyer-togo-birkin-25")
        == "https://www.sothebys.com/en/buy/auction/2025/handbags-accessories"
    )
    assert _sale_url(None) is None


# --- Bonhams ------------------------------------------------------------------------

BONHAMS_DOC = {
    "id": "25403012",
    "lotNo": {"full": "1201"},
    "title": "HERMÈS: BETON MATTE ALLIGATOR BIRKIN 25 WITH GOLD HARDWARE",
    "catalogDesc": "<p>Includes padlock, keys, clochette</p>",
    "auctionId": "28696",
    "hammerTime": {"datetime": "2023-09-20T07:33:00+00:00"},
    "country": {"name": "Hong Kong SAR, China"},
    "region": {"name": "Asia-Pacific"},
    "currency": {"iso_code": "HKD"},
    "price": {
        "estimateLow": 450000.0,
        "estimateHigh": 600000.0,
        "hammerPrice": 450000.0,
        "hammerPremium": 575500.0,
    },
    "flags": {"isResultPublished": True},
    "image": {"url": "https://images3.bonhams.com/image?src=Images/live/2023-09/11/x.jpg"},
    "department": {"code": "HBS"},
}


def _bonhams_source() -> BonhamsSource:
    source = offline(BonhamsSource)
    # Sale-name lookup needs the network; pre-seed the cache instead.
    source._sale_names["28696"] = "Luxury Online: Watches, Handbags, Pens"
    return source


def test_bonhams_prefers_the_premium_inclusive_price():
    lot = _bonhams_source()._to_raw_lot(BONHAMS_DOC, dt.date(2021, 1, 1), dt.date(2026, 12, 31))
    assert lot is not None
    assert lot.price_realised == Decimal("575500.0")
    assert lot.price_basis is PriceBasis.PREMIUM_INCLUSIVE
    assert lot.extra["hammer_price"] == "450000.0"
    assert lot.currency == "HKD"
    assert lot.sale.sale_name == "Luxury Online: Watches, Handbags, Pens"
    assert lot.lot_url is not None and lot.lot_url.startswith(
        "https://www.bonhams.com/auction/28696/lot/1201/hermes-beton-matte-alligator"
    )


def test_bonhams_zero_hammer_is_unsold_only_once_results_are_published():
    """Before a sale settles, a zero is 'not yet known', not 'did not sell'."""
    source = _bonhams_source()
    window = (dt.date(2021, 1, 1), dt.date(2026, 12, 31))

    published = {**BONHAMS_DOC, "price": {"hammerPrice": 0, "hammerPremium": 0}}
    lot = source._to_raw_lot(published, *window)
    assert lot is not None and lot.outcome is LotOutcome.UNSOLD

    pending = {**published, "flags": {"isResultPublished": False}}
    lot = source._to_raw_lot(pending, *window)
    assert lot is not None and lot.outcome is LotOutcome.UNKNOWN
    assert lot.price_realised is None


def test_bonhams_lot_outside_the_window_is_dropped():
    lot = _bonhams_source()._to_raw_lot(BONHAMS_DOC, dt.date(2024, 1, 1), dt.date(2026, 12, 31))
    assert lot is None


# --- Artcurial ----------------------------------------------------------------------

ARTCURIAL_SALE = {
    "ref": "MC-6028",
    "name": "Hermès & Luxury Bags",
    "validity": {"beginDate": "2026-07-08T09:00:00Z"},
    "vacations": [{"address": {"label": "Hôtel Hermitage, Monte-Carlo"}}],
}

ARTCURIAL_ITEM = {
    "index": 581,
    "subIndex": "a",
    "adjudicationPrice": 19000.0,
    "finalPrice": 25156.0,
    "low": 14000.0,
    "high": 18000.0,
    "currency": "EUR",
    "adjudicationDate": "2026-07-08T09:06:05.995199Z",
    "status": "SOLD",
    "descriptions": {
        "ENGLISH": {
            "titleWithHtml": "HERMÈS KELLY II sellier 25 Bag",
            "descriptionWithHtml": "<p>In black Box calfskin</p>",
            "state": "<p>New condition</p>",
        }
    },
    "pictures": [{"document": {"servingUrl": "https://storage.googleapis.com/x/042.jpg"}}],
}


def test_artcurial_item_maps_with_the_premium_inclusive_price():
    source = offline(ArtcurialSource)
    sale = source._sale_ref(ARTCURIAL_SALE, "MC-6028")
    assert sale.sale_date == dt.date(2026, 7, 8)
    assert sale.location == "Hôtel Hermitage, Monte-Carlo"

    lot = source._to_raw_lot(ARTCURIAL_ITEM, sale)
    assert lot is not None
    assert lot.lot_key == "MC-6028-581a"
    assert lot.price_realised == Decimal("25156.0")
    assert lot.price_basis is PriceBasis.PREMIUM_INCLUSIVE
    assert lot.currency == "EUR"
    # HTML is stripped so the parser is not fed markup.
    assert lot.description is not None and "<p>" not in lot.description
    assert "Box calfskin" in lot.description


def test_artcurial_unsold_lot_carries_no_price():
    source = offline(ArtcurialSource)
    sale = source._sale_ref(ARTCURIAL_SALE, "MC-6028")
    row = {**ARTCURIAL_ITEM, "adjudicationPrice": None, "finalPrice": None, "status": "UNSOLD"}
    lot = source._to_raw_lot(row, sale)
    assert lot is not None
    assert lot.outcome is LotOutcome.UNSOLD
    assert lot.price_realised is None


# --- Poly Auction Hong Kong ---------------------------------------------------------

POLY_ROOM = {
    "uid": "MoIHuIaTQCs",
    "saleroom_name_en": "Jewels, Watches and Handbags Online Auction",
    "saleroom_date": "2024-05-23 12:00:00",
    "sales_no": "HKO2624MAY",
    "page_url": "HKO2624MAY",
    "department": {"title_en": "Jewels, Watches and Handbags"},
}

POLY_LOT = {
    "lot_id": "1384",
    "name_en": "A CELESTE EPSOM LEATHER SELLIER KELLY 25 WITH PALLADIUM HARDWARE",
    "description_en": "<div>This bag is done in Celeste Epsom Leather</div>",
    "estimate_start_price": 135000,
    "estimate_end_price": 160000,
    "hammer_price": 135000,
    "sold_price": 162000,
    "lot_status": "sold",
    "page_url": "A-CELESTE-EPSOM-KELLY-25-661092",
    "images": [{"image": "https://www.polyauctionhk.com/lib/uploads/01384.jpg"}],
}


def test_poly_lot_maps_with_hkd_hardcoded():
    source = offline(PolyHongKongSource)
    sale = source._sale_ref(POLY_ROOM, "MoIHuIaTQCs")
    lot = source._to_raw_lot(POLY_LOT, sale)
    assert lot is not None
    # Poly omits the currency entirely; every Hong Kong saleroom settles in HKD.
    assert lot.currency == "HKD"
    assert lot.price_realised == Decimal("162000")
    assert lot.price_basis is PriceBasis.PREMIUM_INCLUSIVE
    assert lot.sale.sale_date == dt.date(2024, 5, 23)
    assert lot.lot_number == "1384"


# --- Heritage (browser-assisted dump) -----------------------------------------------

HERITAGE_ROW = {
    "lot_url": "https://jewelry.HA.com/itm/luxury-accessories/bags/hermes-birkin-30cm-fauve-grizzly-suede-and-barenia-leather-ghillies-birkin-with-gold-hardware/a/5539-58118.s",
    "sale_no": "5539",
    "lot_no": "58118",
    "title": (
        "Hermès Birkin 30cm Fauve Grizzly Suede and Barenia Leather "
        "Ghillies Birkin with Gold Hardware"
    ),
    "description": 'P Square, 2012 | Condition: 3 | 12" Width x 8" Height x 6" Depth',
    "sale_date": "May 4, 2023",
    "price_text": "$15,000.00",
    "image_url": "https://dyn1.heritagestatic.com/ha?p=2-7-9-3-6-27936445&w=200&h=400&it=product",
}


def test_heritage_row_maps_onto_a_raw_lot():
    lot = offline(HeritageSource)._to_raw_lot(HERITAGE_ROW)
    assert lot is not None
    assert lot.house is AuctionHouse.HERITAGE
    assert lot.lot_key == "5539-58118"
    assert lot.sale.sale_date == dt.date(2023, 5, 4)
    assert lot.currency == "USD"
    assert lot.price_realised == Decimal("15000.00")
    # Heritage's "Sold For" is the total the buyer paid.
    assert lot.price_basis is PriceBasis.PREMIUM_INCLUSIVE
    assert lot.outcome is LotOutcome.SOLD
    # Heritage does not publish estimates for this category; never invent one.
    assert lot.estimate_low is None and lot.estimate_high is None
    # Host casing is normalised so lot keys and URLs stay stable across runs.
    assert lot.lot_url is not None and lot.lot_url.startswith("https://jewelry.ha.com/")


def test_heritage_lot_without_a_visible_price_is_not_recorded_as_sold():
    """Anonymous rows render "Sold For: Sign-in"; that is unknown, not unsold."""
    row = {**HERITAGE_ROW, "price_text": "Sold For: Sign-in or Join (free & quick)"}
    lot = offline(HeritageSource)._to_raw_lot(row)
    assert lot is not None
    assert lot.price_realised is None
    assert lot.outcome is LotOutcome.UNKNOWN


def test_heritage_blind_stamp_and_condition_reach_the_parser():
    lot = offline(HeritageSource)._to_raw_lot(HERITAGE_ROW)
    assert lot is not None
    attributes = parse_attributes(lot.title, lot.subtitle, lot.description)
    assert attributes is not None
    assert attributes.size_cm == 30
    # This lot is genuinely bi-material ("Grizzly Suede and Barenia Leather"), so either
    # is a correct primary; what matters is that a real leather resolves.
    assert attributes.leather in {"Barenia", "Grizzly"}
    assert attributes.hardware == "Gold"
    # Heritage writes "Condition: 3" where Christie's writes "GRADE: 1".
    assert attributes.condition_grade == "3"
    assert attributes.stamp_year == 2012
    assert "Ghillies" in attributes.special_editions


def test_heritage_yields_nothing_and_explains_itself_without_a_dump(tmp_path, caplog):
    source = HeritageSource(cast(HttpClient, None), dump_path=tmp_path / "missing.json")
    with caplog.at_level("WARNING"):
        assert list(source.iter_lots(dt.date(2021, 7, 1), dt.date(2026, 7, 28))) == []
    assert "no Heritage dump" in caplog.text


def test_heritage_lazy_load_placeholder_is_not_treated_as_an_image():
    """Rows whose thumbnail had not loaded carry an inline data: URI, not a URL."""
    row = {
        **HERITAGE_ROW,
        "image_url": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E",
    }
    lot = offline(HeritageSource)._to_raw_lot(row)
    assert lot is not None
    assert lot.image_url is None


def test_heritage_host_casing_is_normalised():
    lot = offline(HeritageSource)._to_raw_lot(HERITAGE_ROW)
    assert lot is not None
    assert lot.lot_url is not None and "HA.com" not in lot.lot_url
