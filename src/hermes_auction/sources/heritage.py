"""Heritage Auctions adapter.

Heritage is the one source here that a plain HTTP client cannot reach, for two
independent reasons:

*Prices are account-walled.* For anonymous visitors the realised price is omitted
server-side - the row renders "Sold For: Sign-in or Join" and the page carries Google's
paywalled-content markup declaring ``.bot-price-data`` non-free. It is not CSS-hidden or
lazy-loaded, so there is nothing to recover from the HTML. The price-bracket facet is
exposed but silently disabled, so a lot's price cannot even be bracketed. Registration is
free and unlocks the number.

*Requests must come from a real browser.* DataDome, behind Cloudflare, 403s anything else,
and its cookie is a rotating session artefact.

So the harvest itself runs in the browser via ``scripts/heritage-browser-harvest.js``, and
this adapter converts that dump into ``RawLot`` records so Heritage flows through exactly
the same normalisation, scope filtering and FX conversion as every other house.

When the dump is missing the adapter yields nothing and says why, rather than failing the
whole run.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, Final

from hermes_auction.http import HttpClient
from hermes_auction.models import AuctionHouse, LotOutcome, PriceBasis, RawLot, SaleRef
from hermes_auction.sources.base import AuctionSource, parse_money, register

logger = logging.getLogger(__name__)

#: Produced by ``scripts/heritage-browser-harvest.js``; path relative to the project root.
DEFAULT_DUMP = Path("data/heritage-browser-dump.json")

#: "May 4, 2023" / "Apr 16, 2013"
_DATE_FORMATS: Final = ("%b %d, %Y", "%B %d, %Y")

#: Heritage encodes the production year in the blind stamp: "P Square, 2012".
_STAMP_YEAR_RE: Final = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


@register
class HeritageSource(AuctionSource):
    """Converts a browser-assisted Heritage dump into the common lot shape."""

    house: ClassVar[AuctionHouse] = AuctionHouse.HERITAGE
    coverage_note: ClassVar[str] = (
        "Harvested through a signed-in browser session: Heritage omits realised prices "
        "server-side for anonymous visitors and blocks non-browser clients. Prices are "
        "premium-inclusive and quoted in USD."
    )

    def __init__(self, client: HttpClient, dump_path: Path | None = None) -> None:
        super().__init__(client)
        self.dump_path = dump_path or DEFAULT_DUMP

    def iter_lots(self, since: dt.date, until: dt.date) -> Iterator[RawLot]:
        if not self.dump_path.exists():
            logger.warning(
                "no Heritage dump at %s - run scripts/heritage-browser-harvest.js in a "
                "signed-in ha.com tab, save the JSON there, then re-run this harvest",
                self.dump_path,
            )
            return

        rows: Any = json.loads(self.dump_path.read_text("utf-8"))
        if not isinstance(rows, list):
            logger.error("%s should contain a JSON array of lot rows", self.dump_path)
            return

        logger.info("heritage: %d rows in %s", len(rows), self.dump_path)
        seen: set[str] = set()
        for row in rows:
            lot = self._to_raw_lot(row)
            if lot is None or lot.lot_key in seen:
                continue
            if not (since <= lot.sale.sale_date <= until):
                continue
            seen.add(lot.lot_key)
            yield lot

    def _to_raw_lot(self, row: dict[str, Any]) -> RawLot | None:
        sale_no = str(row.get("sale_no") or "").strip()
        lot_no = str(row.get("lot_no") or "").strip()
        title = str(row.get("title") or "").strip()
        if not (sale_no and lot_no and title):
            return None

        sale_date = _parse_us_date(row.get("sale_date"))
        if sale_date is None:
            return None

        realised = parse_money(row.get("price_text"), default_currency="USD")
        outcome = LotOutcome.SOLD if realised else LotOutcome.UNKNOWN

        return RawLot(
            house=self.house,
            lot_key=f"{sale_no}-{lot_no}",
            lot_number=lot_no,
            title=title,
            description=str(row.get("description") or "") or None,
            sale=SaleRef(
                house=self.house,
                sale_id=sale_no,
                sale_name=f"Heritage sale {sale_no}",
                sale_date=sale_date,
                # Heritage runs its luxury accessories sales out of Dallas and New York;
                # the search rows do not say which, so the field is left unset rather
                # than guessed.
                location=None,
                sale_number=sale_no,
            ),
            currency="USD",
            # Heritage does not publish estimates for this category.
            estimate_low=None,
            estimate_high=None,
            price_realised=_two_places(realised[0]) if realised else None,
            # Heritage's "Sold For" is the total including buyer's premium.
            price_basis=PriceBasis.PREMIUM_INCLUSIVE if realised else PriceBasis.UNKNOWN,
            outcome=outcome,
            lot_url=_https(row.get("lot_url")),
            image_url=_https(row.get("image_url")),
            extra={"blind_stamp": str(row["description"])} if row.get("description") else {},
        )


def _parse_us_date(value: Any) -> dt.date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).replace(tzinfo=dt.UTC).date()
        except ValueError:
            continue
    return None


def _two_places(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"))


def _https(url: Any) -> str | None:
    """Normalise a Heritage URL, or return ``None`` if it is not a real one.

    Two quirks to absorb: Heritage mixes host casing (``jewelry.HA.com``), which would
    otherwise destabilise lot keys across runs; and rows whose thumbnail had not been
    lazy-loaded carry an inline ``data:image/svg+xml`` placeholder rather than a URL.
    """
    if not url:
        return None
    text = str(url).strip()
    if text.startswith("//"):
        text = f"https:{text}"
    if not text.lower().startswith(("http://", "https://")):
        return None
    return re.sub(r"^(https?://)([^/]+)", lambda m: m.group(1) + m.group(2).lower(), text)
