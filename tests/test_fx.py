"""FX conversion: correct rate, correct date, honest carry-forward."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import httpx
import pytest
import respx

from hermes_auction.fx import FRANKFURTER_BASE, FxConverter, FxError
from hermes_auction.http import HttpClient

# A Friday, a gap over the weekend, then the following Monday.
RATES = {
    "2024-05-23": {"CNY": 7.2100},
    "2024-05-24": {"CNY": 7.2200},
    "2024-05-27": {"CNY": 7.2300},
}


@pytest.fixture
def converter(tmp_path):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__startswith=FRANKFURTER_BASE).mock(
            return_value=httpx.Response(200, json={"base": "USD", "rates": RATES})
        )
        client = HttpClient(cache_dir=None, min_interval=0.0)
        fx = FxConverter(client, cache_path=tmp_path / "fx.json")
        fx.prime(["USD"], dt.date(2024, 5, 23), dt.date(2024, 5, 27))
        yield fx
        client.close()


def test_converts_at_the_sale_date_rate(converter):
    result = converter.convert(Decimal("100000"), "USD", dt.date(2024, 5, 24))
    assert result.rate == Decimal("7.22")
    assert result.rate_date == dt.date(2024, 5, 24)
    assert result.carried_forward is False
    assert result.amount_cny == Decimal("722000.00")


def test_weekend_sale_carries_the_prior_business_day_and_says_so(converter):
    saturday = dt.date(2024, 5, 25)
    result = converter.convert(Decimal("1000"), "USD", saturday)
    assert result.rate_date == dt.date(2024, 5, 24)
    assert result.carried_forward is True


def test_cny_is_a_no_op(converter):
    result = converter.convert(Decimal("500"), "CNY", dt.date(2024, 5, 24))
    assert result.rate == Decimal(1)
    assert result.amount_cny == Decimal("500.00")


def test_missing_series_raises_rather_than_guessing(converter):
    with pytest.raises(FxError):
        converter.convert(Decimal("1"), "ZAR", dt.date(2024, 5, 24))


def test_try_convert_swallows_the_error(converter):
    assert converter.try_convert(Decimal("1"), "ZAR", dt.date(2024, 5, 24)) is None
    assert converter.try_convert(None, "USD", dt.date(2024, 5, 24)) is None


def test_rates_round_trip_through_the_cache(tmp_path):
    path = tmp_path / "fx.json"
    path.write_text(json.dumps({"GBP": {"2024-05-24": "9.1234"}}), "utf-8")
    client = HttpClient(cache_dir=None, min_interval=0.0)
    try:
        fx = FxConverter(client, cache_path=path)
        rate, rate_date, carried = fx.rate_on("GBP", dt.date(2024, 5, 24))
        assert rate == Decimal("9.1234")
        assert rate_date == dt.date(2024, 5, 24)
        assert carried is False
    finally:
        client.close()


def test_amounts_are_rounded_half_up_to_the_fen(converter):
    # 3.333 * 7.22 = 24.06426 -> 24.06
    assert converter.convert(Decimal("3.333"), "USD", dt.date(2024, 5, 24)).amount_cny == Decimal(
        "24.06"
    )
    # 1.5 * 7.22 = 10.83 exactly; 0.125 * 7.22 = 0.9025 -> half-up gives 0.90
    assert converter.convert(Decimal("1.5"), "USD", dt.date(2024, 5, 24)).amount_cny == Decimal(
        "10.83"
    )
