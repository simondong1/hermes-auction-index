"""Artcurial adapter.

Artcurial runs 2-4 dedicated "Hermes Vintage" / "Hermes & Luxury Bags" sales a year in
Paris and Monaco, and exposes a completely open JSON API with no key and no page cap::

    GET https://www.artcurial.com/ace/sales/results?page=0&size=3000   # every past sale
    GET https://www.artcurial.com/ace/sales/{saleRef}/items?size=500   # that sale's lots

Harvesting sale-by-sale rather than through the global ``/search`` endpoint is deliberate:
``/search`` returns HTTP 500 as soon as a second filter is supplied, so date filtering has
to happen client-side anyway, and the per-sale route makes the row count assertable.

``adjudicationPrice`` is the hammer; ``finalPrice`` includes the buyer's premium.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Iterator
from decimal import Decimal
from typing import Any, ClassVar, Final

from hermes_auction.http import HttpClient
from hermes_auction.models import AuctionHouse, LotOutcome, PriceBasis, RawLot, SaleRef
from hermes_auction.sources.base import AuctionSource, parse_date, register

logger = logging.getLogger(__name__)

_API: Final = "https://www.artcurial.com/ace"
_SALES: Final = f"{_API}/sales/results"
_PAGE_SIZE: Final = 500

#: Sales worth opening. Artcurial runs ~2,500 sales; only the Hermes and luxury-bag
#: series can contain a Birkin, and opening the rest would be thousands of wasted calls.
_RELEVANT_SALE_RE: Final = re.compile(
    r"herm[eè]s|luxury\s+bags?|luxur[ey]\s+item|sacs?\s+et\s+accessoires|"
    r"handbags?|maroquinerie|vintage",
    re.IGNORECASE,
)


@register
class ArtcurialSource(AuctionSource):
    """Harvests past Artcurial lots from the open ``/ace`` JSON API."""

    house: ClassVar[AuctionHouse] = AuctionHouse.ARTCURIAL
    coverage_note: ClassVar[str] = (
        "Every Artcurial sale in the Hermes Vintage and Luxury Bags series, harvested "
        "sale by sale from their open JSON API. Premium-inclusive prices are published."
    )

    def __init__(self, client: HttpClient) -> None:
        super().__init__(client)

    def _get(self, url: str, **params: Any) -> dict[str, Any]:
        payload: Any = self.client.get_json(
            url, params=params, headers={"Accept": "application/json"}
        )
        return payload if isinstance(payload, dict) else {}

    def _relevant_sales(self, since: dt.date, until: dt.date) -> list[dict[str, Any]]:
        response = self._get(_SALES, page=0, size=3000)
        sales: list[dict[str, Any]] = []
        for sale in response.get("content") or []:
            name = str(sale.get("name") or "")
            if not _RELEVANT_SALE_RE.search(name):
                continue
            sale_date = _sale_date(sale)
            if sale_date is None or not (since <= sale_date <= until):
                continue
            sales.append(sale)
        logger.info(
            "artcurial: %d relevant sales in window (of %d total)",
            len(sales),
            len(response.get("content") or []),
        )
        return sales

    def iter_lots(self, since: dt.date, until: dt.date) -> Iterator[RawLot]:
        for sale in self._relevant_sales(since, until):
            sale_ref = str(sale.get("ref") or "")
            if not sale_ref:
                continue
            sale_meta = self._sale_ref(sale, sale_ref)
            page = 0
            while True:
                response = self._get(f"{_API}/sales/{sale_ref}/items", page=page, size=_PAGE_SIZE)
                rows = response.get("content") or []
                if not rows:
                    break
                for row in rows:
                    lot = self._to_raw_lot(row, sale_meta)
                    if lot is not None:
                        yield lot
                page += 1
                if page >= int(response.get("totalPages") or 0):
                    break

    def _sale_ref(self, sale: dict[str, Any], sale_ref: str) -> SaleRef:
        vacations = sale.get("vacations") or [{}]
        address = (vacations[0].get("address") or {}) if vacations else {}
        return SaleRef(
            house=self.house,
            sale_id=sale_ref,
            sale_name=str(sale.get("name") or f"Sale {sale_ref}").strip(),
            sale_date=_sale_date(sale) or dt.date(1970, 1, 1),
            location=str(address.get("label") or "") or None,
            url=f"https://www.artcurial.com/en/sales/{sale_ref}",
        )

    def _to_raw_lot(self, row: dict[str, Any], sale: SaleRef) -> RawLot | None:
        index = row.get("index")
        if index is None:
            return None
        sub = str(row.get("subIndex") or "")
        lot_number = f"{index}{sub}"
        lot_key = f"{sale.sale_id}-{lot_number}"

        english = (row.get("descriptions") or {}).get("ENGLISH") or {}
        french = (row.get("descriptions") or {}).get("FRENCH") or {}
        title = _strip_tags(english.get("titleWithHtml") or french.get("titleWithHtml"))
        if not title:
            return None

        description = " ".join(
            filter(
                None,
                (
                    _strip_tags(
                        english.get("descriptionWithHtml") or french.get("descriptionWithHtml")
                    ),
                    _strip_tags(english.get("state") or french.get("state")),
                ),
            )
        )

        hammer = _decimal_or_none(row.get("adjudicationPrice"))
        final = _decimal_or_none(row.get("finalPrice"))
        if final and final > 0:
            outcome, realised, basis = LotOutcome.SOLD, final, PriceBasis.PREMIUM_INCLUSIVE
        elif hammer and hammer > 0:
            outcome, realised, basis = LotOutcome.SOLD, hammer, PriceBasis.HAMMER
        else:
            outcome, realised, basis = LotOutcome.UNSOLD, None, PriceBasis.UNKNOWN

        # A lot can be adjudicated on a different day to the sale opening.
        sale_date = parse_date(str(row.get("adjudicationDate") or "")) or sale.sale_date

        pictures = row.get("pictures") or []
        image = None
        if pictures:
            image = ((pictures[0].get("document") or {}).get("servingUrl")) or None

        return RawLot(
            house=self.house,
            lot_key=lot_key,
            lot_number=lot_number,
            title=title,
            description=description or None,
            sale=sale.model_copy(update={"sale_date": sale_date}),
            currency=str(row.get("currency") or "EUR"),
            estimate_low=_decimal_or_none(row.get("low")),
            estimate_high=_decimal_or_none(row.get("high")),
            price_realised=realised,
            price_basis=basis,
            outcome=outcome,
            lot_url=f"https://www.artcurial.com/en/sales/{sale.sale_id}/lots/{index}-{sub}"
            if sub
            else f"https://www.artcurial.com/en/sales/{sale.sale_id}/lots/{index}",
            image_url=str(image) if image else None,
            extra={"hammer_price": str(hammer)} if hammer else {},
        )


def _sale_date(sale: dict[str, Any]) -> dt.date | None:
    """Sale dates live on ``validity.beginDate``, at the sale or the session level."""
    candidates = [(sale.get("validity") or {}).get("beginDate")]
    candidates += [
        (vacation.get("validity") or {}).get("beginDate")
        for vacation in (sale.get("vacations") or [])
    ]
    for candidate in candidates:
        if (parsed := parse_date(str(candidate or ""))) is not None:
            return parsed
    return None


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, ArithmeticError):
        return None


def _strip_tags(html: Any) -> str | None:
    if not html:
        return None
    text = re.sub(r"<[^>]+>", " ", str(html))
    return re.sub(r"\s+", " ", text).strip() or None
