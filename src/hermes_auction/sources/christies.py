"""Christie's adapter.

Christie's global search gateway indexes every past lot across every sale and
department, which makes cross-sale keyword harvesting complete by construction - no
need to guess which sales might have contained a Birkin.

Endpoint (unauthenticated, but gated on three things that must all be present)::

    GET https://apim.christies.com/search-client
        ?keyword=birkin&page=1&is_past_lots=True&sortby=relevance
        &language=en&geocountrycode=US&show_on_loan=true
        &datasourceId=<uuid>
    x-api-key: <key>
    Accept: application/vnd.christies.v1+json
    source-application: gsrp

The API key and datasource id are public front-end configuration, embedded in
``window.chrComponents.searchResults`` on any /en/search page. They are re-harvested at
run time rather than hard-coded so a rotation does not silently break the harvest.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from collections.abc import Iterator, Sequence
from typing import Any, ClassVar, Final

from hermes_auction.models import AuctionHouse, LotOutcome, PriceBasis, RawLot, SaleRef
from hermes_auction.sources.base import (
    DEFAULT_QUERIES,
    AuctionSource,
    parse_date,
    parse_money,
    register,
)

logger = logging.getLogger(__name__)

_SEARCH_URL: Final = "https://apim.christies.com/search-client"
_CONFIG_PAGE: Final = "https://www.christies.com/en/search?keyword=hermes&entry=lots&tab=sold_lots"
_PAGE_SIZE: Final = 20

#: Fallback config, used only if the live harvest fails. Verified 2026-07.
_FALLBACK_API_KEY: Final = "w3etROpeli8m8bYEbfkpd2MflLigGa6cB0tk6b1g"
_FALLBACK_DATASOURCE: Final = "182f8bb2-d729-4a38-b539-7cf1a901cf2e"

_COMPONENT_RE: Final = re.compile(r"window\.chrComponents\.searchResults\s*=\s*\{")
_API_KEY_RE: Final = re.compile(r'"x-api-key"\s*:\s*"([A-Za-z0-9]+)"')
_DATASOURCE_RE: Final = re.compile(r'"datasource_id"\s*:\s*"([0-9a-f-]{36})"')

#: Christie's leaves the sale saleroom code only in the lot image path.
_SALEROOM_RE: Final = re.compile(r"/lotimages/\d{4}/([A-Z]{3})/")


def _extract_balanced_object(text: str, open_brace_index: int) -> str:
    """Return the JSON object starting at ``open_brace_index``, respecting strings."""
    depth = 0
    i = open_brace_index
    while i < len(text):
        ch = text[i]
        if ch == '"':
            i += 1
            while i < len(text) and (text[i] != '"' or text[i - 1] == "\\"):
                i += 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_index : i + 1]
        i += 1
    msg = "unbalanced JSON object in Christie's page config"
    raise ValueError(msg)


@register
class ChristiesSource(AuctionSource):
    """Harvests past Christie's lots via the global search gateway."""

    house: ClassVar[AuctionHouse] = AuctionHouse.CHRISTIES
    coverage_note: ClassVar[str] = (
        "Global cross-sale search over every past Christie's lot. Realised prices are "
        "published in full and include buyer's premium."
    )

    def __init__(self, client: Any, queries: Sequence[str] = DEFAULT_QUERIES) -> None:
        super().__init__(client)
        self.queries = tuple(queries)
        self._api_key: str | None = None
        self._datasource: str | None = None

    # -- front-end config ---------------------------------------------------------

    def _load_config(self) -> tuple[str, str]:
        if self._api_key and self._datasource:
            return self._api_key, self._datasource
        try:
            html = self.client.get_text(_CONFIG_PAGE)
            api_key = _API_KEY_RE.findall(html)
            datasource = _DATASOURCE_RE.search(html)
            # Two keys appear on the page; the apim one is the longer-lived of the pair
            # and is the one bound to search-client. Prefer an exact config read.
            match = _COMPONENT_RE.search(html)
            if match is not None:
                blob = json.loads(_extract_balanced_object(html, match.end() - 1))
                data = blob.get("data", {})
                if (ds := data.get("datasource_id")) and api_key:
                    self._datasource = str(ds)
                    self._api_key = api_key[0]
                    logger.info("harvested live Christie's search config")
                    return self._api_key, self._datasource
            if datasource and api_key:
                self._api_key, self._datasource = api_key[0], datasource.group(1)
                return self._api_key, self._datasource
        except Exception:
            logger.warning("could not harvest Christie's config; using pinned fallback")
        self._api_key, self._datasource = _FALLBACK_API_KEY, _FALLBACK_DATASOURCE
        return self._api_key, self._datasource

    def _fetch_page(self, keyword: str, page: int) -> dict[str, Any]:
        api_key, datasource = self._load_config()
        payload: Any = self.client.get_json(
            _SEARCH_URL,
            params={
                "keyword": keyword,
                "page": page,
                "is_past_lots": "True",
                "sortby": "relevance",
                "language": "en",
                "geocountrycode": "US",
                "show_on_loan": "true",
                "datasourceId": datasource,
            },
            headers={
                "Accept": "application/vnd.christies.v1+json",
                "source-application": "gsrp",
                "x-api-key": api_key,
                "Origin": "https://www.christies.com",
                "Referer": "https://www.christies.com/",
            },
        )
        return payload if isinstance(payload, dict) else {}

    # -- harvesting ---------------------------------------------------------------

    def iter_lots(self, since: dt.date, until: dt.date) -> Iterator[RawLot]:
        seen: set[str] = set()
        for keyword in self.queries:
            first = self._fetch_page(keyword, 1)
            total_pages = int(first.get("total_pages") or 0)
            logger.info(
                "christies: '%s' -> %d pages (~%d lots)",
                keyword,
                total_pages,
                total_pages * _PAGE_SIZE,
            )
            for page in range(1, total_pages + 1):
                raw = first if page == 1 else self._fetch_page(keyword, page)
                rows = raw.get("lots") or []
                if not rows:
                    break
                for row in rows:
                    lot = self._to_raw_lot(row)
                    if lot is None or lot.lot_key in seen:
                        continue
                    if not (since <= lot.sale.sale_date <= until):
                        continue
                    seen.add(lot.lot_key)
                    yield lot

    def _to_raw_lot(self, row: dict[str, Any]) -> RawLot | None:
        object_id = str(row.get("object_id") or "").strip()
        if not object_id:
            return None

        sale_date = parse_date(row.get("end_date")) or parse_date(row.get("start_date"))
        if sale_date is None:
            return None

        sale_info = row.get("sale") or {}
        realised = parse_money(row.get("price_realised_txt"))
        estimate = _parse_estimate_range(row.get("estimate_txt"))

        currency = (
            (realised[1] if realised else None) or (estimate[2] if estimate else None) or "USD"
        )

        if row.get("lot_withdrawn"):
            outcome = LotOutcome.WITHDRAWN
        elif realised is not None:
            outcome = LotOutcome.SOLD
        else:
            outcome = LotOutcome.UNSOLD

        image = (row.get("image") or {}).get("image_src")
        saleroom = None
        if image and (m := _SALEROOM_RE.search(image)):
            saleroom = m.group(1)

        sale = SaleRef(
            house=self.house,
            sale_id=str(sale_info.get("id") or sale_info.get("number") or "unknown"),
            sale_name=str(sale_info.get("name") or f"Sale {sale_info.get('number', '')}").strip(),
            sale_date=sale_date,
            location=sale_info.get("location"),
            sale_number=str(sale_info.get("number")) if sale_info.get("number") else None,
        )

        return RawLot(
            house=self.house,
            lot_key=object_id,
            lot_number=str(row.get("lot_id_txt") or "") or None,
            title=str(row.get("title_primary_txt") or "").strip(),
            subtitle=str(row.get("title_secondary_txt") or "").strip() or None,
            description=str(row.get("description_txt") or "").strip() or None,
            sale=sale,
            currency=currency,
            estimate_low=estimate[0] if estimate else None,
            estimate_high=estimate[1] if estimate else None,
            price_realised=realised[0] if realised else None,
            # Christie's publishes "Price realised" inclusive of buyer's premium.
            price_basis=PriceBasis.PREMIUM_INCLUSIVE if realised else PriceBasis.UNKNOWN,
            outcome=outcome,
            lot_url=str(row.get("url") or "").split("?")[0] or None,
            image_url=image,
            extra={
                k: str(v)
                for k, v in (
                    ("sale_type", sale_info.get("type")),
                    ("saleroom", saleroom),
                    ("price_realised_txt", row.get("price_realised_txt")),
                    ("estimate_txt", row.get("estimate_txt")),
                )
                if v
            },
        )


def _parse_estimate_range(text: str | None) -> tuple[Any, Any, str] | None:
    """Split "USD 40,000 - 50,000" into low, high and currency."""
    if not text:
        return None
    parts = re.split(r"\s*[-\u2013]\s*", text, maxsplit=1)
    low = parse_money(parts[0])
    if low is None:
        return None
    high = parse_money(parts[1], default_currency=low[1]) if len(parts) > 1 else None
    return low[0], (high[0] if high else low[0]), low[1]
