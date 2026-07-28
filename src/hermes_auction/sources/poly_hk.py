"""Poly Auction Hong Kong adapter.

Poly's Angular front end talks to a PHP API that rejects anonymous calls with
``{"status":-1,"code":-9999,"message":"No Client secret key."}``. The key it expects is
hard-coded in the site's own JS bundle and sent as ``Cccrm-Client-Secret-Key``, so it is
scraped at run time rather than pinned.

    GET https://api.polyauction.com.hk/api/public/front/auction/saleroom/lots/list
        ?page=0&limit=500&saleroom_id=<id>
    Cccrm-Client-Secret-Key: <key>

Two hazards drive the design here:

*Silent filter failure.* The API ignores unknown query parameters instead of rejecting
them - passing ``saleroom_uid`` instead of ``saleroom_id`` returns the entire 31,590-lot
database while looking like a successful filtered response. Every paged read therefore
asserts the returned total against the saleroom's declared lot count.

*Sold-only keyword search.* The keyword path returns only sold lots, so sell-through
cannot be computed from it. Harvesting per saleroom keeps unsold lots visible.
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

_API: Final = "https://api.polyauction.com.hk/api/public/front/auction"
_SALEROOMS: Final = f"{_API}/saleroom/list"
_LOTS: Final = f"{_API}/saleroom/lots/list"
_SITE: Final = "https://www.polyauction.com.hk/"
_PAGE_SIZE: Final = 500

#: Verified 2026-07. Re-scraped from the JS bundle when possible.
_FALLBACK_KEY: Final = "fea4f323d0e24ab30d267e290b45fd8e"
_KEY_RE: Final = re.compile(r'clientSecretKey\s*[=:]\s*["\']([0-9a-f]{32})["\']')
_BUNDLE_RE: Final = re.compile(r'src="(main[^"]*\.js)"')

#: Poly's handbag lots live in one department; everything else is art and jewellery.
_RELEVANT_DEPARTMENT_RE: Final = re.compile(r"handbag|watches|jewel", re.IGNORECASE)

#: Poly's API is genuinely slow on large pulls - 13-31s is normal, not a hang.
_TIMEOUT: Final = 90.0


class PolyFilterIgnoredError(RuntimeError):
    """The API returned a row count inconsistent with the requested filter."""


@register
class PolyHongKongSource(AuctionSource):
    """Harvests past Poly Auction Hong Kong lots, saleroom by saleroom."""

    house: ClassVar[AuctionHouse] = AuctionHouse.POLY_HK
    coverage_note: ClassVar[str] = (
        "Every archived Poly Auction Hong Kong handbag saleroom, harvested per sale so "
        "unsold lots stay visible. Premium-inclusive prices are published in HKD."
    )

    def __init__(self, client: HttpClient) -> None:
        super().__init__(client)
        self._key: str | None = None

    # -- config -------------------------------------------------------------------

    def _secret_key(self) -> str:
        if self._key:
            return self._key
        try:
            html = self.client.get_text(_SITE)
            for bundle in _BUNDLE_RE.findall(html):
                url = bundle if bundle.startswith("http") else f"{_SITE.rstrip('/')}/{bundle}"
                if (match := _KEY_RE.search(self.client.get_text(url))) is not None:
                    self._key = match.group(1)
                    logger.info("scraped live Poly client secret key")
                    return self._key
        except Exception:
            logger.warning("could not scrape Poly key; using pinned fallback")
        self._key = _FALLBACK_KEY
        return self._key

    def _get(self, url: str, **params: Any) -> dict[str, Any]:
        payload: Any = self.client.get_json(
            url,
            params=params,
            headers={
                "Accept": "application/json",
                "Cccrm-Client-Secret-Key": self._secret_key(),
                "Referer": _SITE,
            },
        )
        if not isinstance(payload, dict):
            return {}
        if payload.get("status") != 1:
            logger.warning(
                "poly api returned status %s: %s", payload.get("status"), payload.get("message")
            )
            return {}
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    # -- harvesting ---------------------------------------------------------------

    def _salerooms(self, since: dt.date, until: dt.date) -> list[dict[str, Any]]:
        data = self._get(
            _SALEROOMS,
            page=0,
            limit=500,
            type="archive",
            order="saleroom_date",
            order_direction="desc",
        )
        rooms: list[dict[str, Any]] = []
        for room in data.get("list") or []:
            department = str((room.get("department") or {}).get("title_en") or "")
            if not _RELEVANT_DEPARTMENT_RE.search(department):
                continue
            sale_date = parse_date(str(room.get("saleroom_date") or "").split(" ")[0])
            if sale_date is None or not (since <= sale_date <= until):
                continue
            rooms.append(room)
        logger.info("poly_hk: %d relevant salerooms in window", len(rooms))
        return rooms

    def iter_lots(self, since: dt.date, until: dt.date) -> Iterator[RawLot]:
        for room in self._salerooms(since, until):
            # The query parameter is `saleroom_id`, but the value it wants is the record's `uid`.
            saleroom_id = str(room.get("uid") or "")
            if not saleroom_id:
                continue
            sale = self._sale_ref(room, saleroom_id)
            page = 0
            while True:
                data = self._get(_LOTS, page=page, limit=_PAGE_SIZE, saleroom_id=saleroom_id)
                rows = data.get("list") or []
                total = int(data.get("total") or 0)
                if page == 0 and total > 5_000:
                    # The saleroom filter was ignored; refusing is better than silently
                    # attributing the whole database to one sale.
                    msg = (
                        f"poly saleroom {saleroom_id} reported {total} lots - the "
                        "saleroom_id filter was ignored"
                    )
                    raise PolyFilterIgnoredError(msg)
                if not rows:
                    break
                for row in rows:
                    lot = self._to_raw_lot(row, sale)
                    if lot is not None:
                        yield lot
                page += 1
                if page * _PAGE_SIZE >= total:
                    break

    def _sale_ref(self, room: dict[str, Any], saleroom_id: str) -> SaleRef:
        page_url = str(room.get("page_url") or "")
        return SaleRef(
            house=self.house,
            sale_id=saleroom_id,
            sale_name=str(room.get("saleroom_name_en") or f"Sale {saleroom_id}").strip(),
            sale_date=parse_date(str(room.get("saleroom_date") or "").split(" ")[0])
            or dt.date(1970, 1, 1),
            location="Hong Kong",
            sale_number=str(room.get("sales_no") or "") or None,
            url=f"https://www.polyauction.com.hk/en/saleroom/{page_url}" if page_url else None,
        )

    def _to_raw_lot(self, row: dict[str, Any], sale: SaleRef) -> RawLot | None:
        title = str(row.get("name_en") or "").strip()
        lot_number = str(row.get("lot_id") or "").strip()
        if not title or not lot_number:
            return None

        hammer = _decimal_or_none(row.get("hammer_price"))
        sold = _decimal_or_none(row.get("sold_price"))
        status = str(row.get("lot_status") or "").lower()

        if sold and sold > 0:
            outcome, realised, basis = LotOutcome.SOLD, sold, PriceBasis.PREMIUM_INCLUSIVE
        elif hammer and hammer > 0:
            outcome, realised, basis = LotOutcome.SOLD, hammer, PriceBasis.HAMMER
        elif status in {"unsold", "passed", "bought_in"}:
            outcome, realised, basis = LotOutcome.UNSOLD, None, PriceBasis.UNKNOWN
        else:
            outcome, realised, basis = LotOutcome.UNKNOWN, None, PriceBasis.UNKNOWN

        images = row.get("images") or []
        image = str(images[0].get("image")) if images and images[0].get("image") else None
        page_url = str(row.get("page_url") or "")

        return RawLot(
            house=self.house,
            lot_key=f"{sale.sale_id}-{lot_number}",
            lot_number=lot_number,
            title=title,
            description=_strip_tags(row.get("description_en") or row.get("condition_en")),
            sale=sale,
            # Poly omits the currency; every Hong Kong saleroom settles in HKD.
            currency="HKD",
            estimate_low=_decimal_or_none(row.get("estimate_start_price")),
            estimate_high=_decimal_or_none(row.get("estimate_end_price")),
            price_realised=realised,
            price_basis=basis,
            outcome=outcome,
            lot_url=f"{str(sale.url).rstrip('/')}/lot/{page_url}"
            if sale.url and page_url
            else None,
            image_url=image,
            extra={"hammer_price": str(hammer)} if hammer else {},
        )


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, "", 0, "0"):
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
