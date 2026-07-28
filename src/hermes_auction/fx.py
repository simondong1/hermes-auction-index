"""Historical FX conversion to CNY, struck at the date of each sale.

Rates come from the **European Central Bank** daily reference rates, served by the
Frankfurter API. The ECB is the authoritative free source for this: rates are the
2.15 pm CET concertation fixing, published every TARGET business day, and revised
never - so a rebuild of the dataset a year from now reproduces the same numbers.

The ECB does not publish on weekends or TARGET holidays. A sale on such a day is
converted at the most recent prior publication and flagged ``carried_forward=True``
so the site can be honest about it.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Final

from hermes_auction.http import HttpClient
from hermes_auction.models import FxConversion

logger = logging.getLogger(__name__)

FRANKFURTER_BASE: Final = "https://api.frankfurter.dev/v1"
FX_SOURCE: Final = "ECB daily reference rate (via frankfurter.dev)"

#: Currencies the auction houses actually settle in.
SUPPORTED_CURRENCIES: Final = frozenset(
    {"USD", "GBP", "EUR", "HKD", "CHF", "JPY", "SGD", "AUD", "CAD", "SEK", "DKK", "NOK"}
)

#: How far back to walk looking for a published rate before giving up.
_MAX_CARRY_FORWARD_DAYS: Final = 10

_CENT = Decimal("0.01")


class FxError(RuntimeError):
    """No rate could be established for a currency/date pair."""


class FxConverter:
    """Fetches and caches ECB daily rates, then converts amounts to CNY.

    Rates are fetched one currency at a time over the full date span in a single
    request, which is both faster and kinder than a request per lot.
    """

    def __init__(self, client: HttpClient, cache_path: Path | None = None) -> None:
        self._client = client
        self._cache_path = cache_path
        self._rates: dict[str, dict[dt.date, Decimal]] = {}
        if cache_path is not None and cache_path.exists():
            self._load_cache(cache_path)

    # -- persistence -------------------------------------------------------------

    def _load_cache(self, path: Path) -> None:
        payload = json.loads(path.read_text("utf-8"))
        for currency, series in payload.items():
            self._rates[currency] = {
                dt.date.fromisoformat(day): Decimal(str(rate)) for day, rate in series.items()
            }
        logger.debug("loaded FX cache for %d currencies", len(self._rates))

    def save_cache(self) -> None:
        if self._cache_path is None:
            return
        payload = {
            currency: {day.isoformat(): str(rate) for day, rate in sorted(series.items())}
            for currency, series in sorted(self._rates.items())
        }
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(payload, indent=1, sort_keys=True), "utf-8")

    # -- fetching ----------------------------------------------------------------

    def prime(self, currencies: Iterable[str], start: dt.date, end: dt.date) -> None:
        """Pre-fetch the whole rate series for every currency we will need."""
        for currency in sorted(set(currencies)):
            if currency == "CNY":
                continue
            if currency not in SUPPORTED_CURRENCIES:
                logger.warning("no ECB series for %s - lots in it cannot be converted", currency)
                continue
            have = self._rates.get(currency, {})
            if have and min(have) <= start and max(have) >= end - dt.timedelta(days=7):
                continue
            self._fetch_series(currency, start, end)

    def _fetch_series(self, currency: str, start: dt.date, end: dt.date) -> None:
        # Reach back before `start` so a sale on the first day can still carry forward.
        padded = start - dt.timedelta(days=_MAX_CARRY_FORWARD_DAYS)
        url = f"{FRANKFURTER_BASE}/{padded.isoformat()}..{end.isoformat()}"
        payload = self._client.get_json(url, params={"base": currency, "symbols": "CNY"})
        series = self._rates.setdefault(currency, {})
        for day, rates in payload.get("rates", {}).items():
            if (value := rates.get("CNY")) is not None:
                series[dt.date.fromisoformat(day)] = Decimal(str(value))
        logger.info("fetched %d ECB %s/CNY rates", len(series), currency)

    # -- conversion --------------------------------------------------------------

    def rate_on(self, currency: str, on: dt.date) -> tuple[Decimal, dt.date, bool]:
        """Return ``(rate, effective_date, carried_forward)`` for ``currency`` on ``on``."""
        if currency == "CNY":
            return Decimal(1), on, False
        series = self._rates.get(currency)
        if not series:
            msg = f"no rate series loaded for {currency}"
            raise FxError(msg)
        for offset in range(_MAX_CARRY_FORWARD_DAYS + 1):
            day = on - dt.timedelta(days=offset)
            if (rate := series.get(day)) is not None:
                return rate, day, offset > 0
        msg = f"no {currency}/CNY rate within {_MAX_CARRY_FORWARD_DAYS} days of {on}"
        raise FxError(msg)

    def convert(self, amount: Decimal, currency: str, on: dt.date) -> FxConversion:
        """Convert ``amount`` of ``currency`` into CNY at the ``on`` date's rate."""
        rate, rate_date, carried = self.rate_on(currency, on)
        return FxConversion(
            amount_cny=(amount * rate).quantize(_CENT, rounding=ROUND_HALF_UP),
            rate=rate,
            rate_date=rate_date,
            carried_forward=carried,
            source=FX_SOURCE,
        )

    def try_convert(
        self, amount: Decimal | None, currency: str, on: dt.date
    ) -> FxConversion | None:
        """Same as :meth:`convert` but returns ``None`` instead of raising."""
        if amount is None:
            return None
        try:
            return self.convert(amount, currency, on)
        except FxError:
            logger.warning("could not convert %s %s on %s", amount, currency, on)
            return None
