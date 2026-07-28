"""Bonhams adapter.

Bonhams' lot catalogue is a Typesense collection queried directly from the browser, so
a search-only key ships in the page. That key is re-harvested from
``__NEXT_DATA__.props.runtimeConfig.TYPESENSE`` at run time rather than pinned, because
it rotates with deploys.

    GET https://api01.bonhams.com/search-proxy/collections/lots-search/documents/search
        ?q=birkin&query_by=title,catalogDesc&per_page=250&page=1
        &filter_by=hammerTime.timestamp:[<from>..<to>]
    X-TYPESENSE-API-KEY: <key>

Prices come in two flavours and the distinction matters: ``price.hammerPrice`` is the
hammer, ``price.hammerPremium`` is the buyer's-premium-inclusive total. Only the latter
is comparable with Christie's and Sotheby's, so that is what this adapter reports.

``hammerPrice == 0`` means unsold *or* results not yet published, which is why the
adapter leans on ``flags.isResultPublished`` to tell those two apart.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from collections.abc import Iterator, Sequence
from decimal import Decimal
from typing import Any, ClassVar, Final

from hermes_auction.http import HttpClient
from hermes_auction.models import AuctionHouse, LotOutcome, PriceBasis, RawLot, SaleRef
from hermes_auction.sources.base import DEFAULT_QUERIES, AuctionSource, parse_date, register
from hermes_auction.taxonomy import strip_accents

logger = logging.getLogger(__name__)

_HOME: Final = "https://www.bonhams.com/"
_SEARCH_HOST: Final = "https://api01.bonhams.com/search-proxy/collections"
_LOTS_COLLECTION: Final = f"{_SEARCH_HOST}/lots-search/documents/search"
_AUCTIONS_COLLECTION: Final = f"{_SEARCH_HOST}/auctions-search/documents/search"
_PER_PAGE: Final = 250

_NEXT_DATA_RE: Final = re.compile(r'id="__NEXT_DATA__"[^>]*>(.+?)</script>', re.DOTALL)
#: Verified 2026-07. Only used if the live harvest fails.
_FALLBACK_KEY: Final = "7YZqOyG0twgst4ACc2VuCyZxpGAYzM0weFTLCC20FQY"

_SLUG_STRIP_RE: Final = re.compile(r"[^a-z0-9]+")


@register
class BonhamsSource(AuctionSource):
    """Harvests past Bonhams lots from the public Typesense search proxy."""

    house: ClassVar[AuctionHouse] = AuctionHouse.BONHAMS
    coverage_note: ClassVar[str] = (
        "Cross-sale search over the public Typesense lot index. Bonhams publishes both "
        "hammer and premium-inclusive prices; the premium-inclusive figure is used."
    )

    def __init__(self, client: HttpClient, queries: Sequence[str] = DEFAULT_QUERIES) -> None:
        super().__init__(client)
        self.queries = tuple(queries)
        self._key: str | None = None
        self._sale_names: dict[str, str] = {}

    # -- config -------------------------------------------------------------------

    def _api_key(self) -> str:
        if self._key:
            return self._key
        try:
            html = self.client.get_text(_HOME)
            match = _NEXT_DATA_RE.search(html)
            if match is not None:
                payload = json.loads(match.group(1))
                # `runtimeConfig` sits at the top level of __NEXT_DATA__, not under
                # `props`; check both so a Next.js upgrade does not silently pin the key.
                config = (
                    payload.get("runtimeConfig")
                    or payload.get("props", {}).get("runtimeConfig")
                    or {}
                ).get("TYPESENSE", {})
                api_key = config.get("apiKey")
                key = api_key.get("search") if isinstance(api_key, dict) else api_key
                if key:
                    self._key = str(key)
                    logger.info("harvested live Bonhams Typesense key")
                    return self._key
        except Exception:
            logger.warning("could not harvest Bonhams key; using pinned fallback")
        self._key = _FALLBACK_KEY
        return self._key

    def _search(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        payload: Any = self.client.get_json(
            url,
            params=params,
            headers={
                "X-TYPESENSE-API-KEY": self._api_key(),
                "Accept": "application/json",
                "Origin": "https://www.bonhams.com",
                "Referer": _HOME,
            },
        )
        return payload if isinstance(payload, dict) else {}

    def _sale_name(self, auction_id: str) -> str | None:
        """Lot documents carry only ``auctionId``; the title lives in a sibling index."""
        if auction_id in self._sale_names:
            return self._sale_names[auction_id]
        response = self._search(
            _AUCTIONS_COLLECTION,
            {"q": "*", "filter_by": f"id:={auction_id}", "per_page": 1},
        )
        hits = response.get("hits") or []
        document = hits[0].get("document", {}) if hits else {}
        name = str(document.get("auctionTitle") or "").strip() or None
        venue = ((document.get("venue") or {}).get("name")) if document else None
        self._sale_names[auction_id] = name or ""
        if venue:
            self._sale_names[f"{auction_id}:venue"] = str(venue)
        return name

    # -- harvesting ---------------------------------------------------------------

    def iter_lots(self, since: dt.date, until: dt.date) -> Iterator[RawLot]:
        window = (
            f"hammerTime.timestamp:"
            f"[{int(dt.datetime.combine(since, dt.time.min, tzinfo=dt.UTC).timestamp())}.."
            f"{int(dt.datetime.combine(until, dt.time.max, tzinfo=dt.UTC).timestamp())}]"
        )
        seen: set[str] = set()

        for keyword in self.queries:
            page = 1
            while True:
                response = self._search(
                    _LOTS_COLLECTION,
                    {
                        "q": keyword,
                        "query_by": "title,catalogDesc",
                        "filter_by": window,
                        "sort_by": "hammerTime.timestamp:desc",
                        "per_page": _PER_PAGE,
                        "page": page,
                    },
                )
                hits = response.get("hits") or []
                if not hits:
                    break
                if page == 1:
                    logger.info(
                        "bonhams: '%s' -> %s in-window lots",
                        keyword,
                        response.get("found", "?"),
                    )
                for hit in hits:
                    document = hit.get("document") or {}
                    lot = self._to_raw_lot(document, since, until)
                    if lot is None or lot.lot_key in seen:
                        continue
                    seen.add(lot.lot_key)
                    yield lot
                page += 1

    def _to_raw_lot(self, doc: dict[str, Any], since: dt.date, until: dt.date) -> RawLot | None:
        lot_key = str(doc.get("id") or doc.get("lotId") or "").strip()
        if not lot_key:
            return None

        sale_date = parse_date(str((doc.get("hammerTime") or {}).get("datetime") or ""))
        if sale_date is None or not (since <= sale_date <= until):
            return None

        price = doc.get("price") or {}
        hammer = _decimal_or_none(price.get("hammerPrice"))
        premium = _decimal_or_none(price.get("hammerPremium"))
        published = bool((doc.get("flags") or {}).get("isResultPublished"))

        # A zero hammer means "unsold" only when the result has actually been published;
        # before that it just means the sale has not settled.
        if premium and premium > 0:
            outcome, realised, basis = LotOutcome.SOLD, premium, PriceBasis.PREMIUM_INCLUSIVE
        elif hammer and hammer > 0:
            outcome, realised, basis = LotOutcome.SOLD, hammer, PriceBasis.HAMMER
        elif published:
            outcome, realised, basis = LotOutcome.UNSOLD, None, PriceBasis.UNKNOWN
        else:
            outcome, realised, basis = LotOutcome.UNKNOWN, None, PriceBasis.UNKNOWN

        auction_id = str(doc.get("auctionId") or "")
        title = str(doc.get("title") or "").strip()
        location = " ".join(
            part
            for part in (
                str((doc.get("region") or {}).get("name") or ""),
                str((doc.get("country") or {}).get("name") or ""),
            )
            if part
        ).strip()

        lot_number = str((doc.get("lotNo") or {}).get("full") or doc.get("lotNo") or "") or None

        return RawLot(
            house=self.house,
            lot_key=lot_key,
            lot_number=lot_number,
            title=title,
            description=_strip_tags(doc.get("catalogDesc")),
            sale=SaleRef(
                house=self.house,
                sale_id=auction_id or "unknown",
                sale_name=self._sale_name(auction_id) or f"Sale {auction_id}",
                sale_date=sale_date,
                location=location or None,
            ),
            currency=str((doc.get("currency") or {}).get("iso_code") or "GBP"),
            estimate_low=_decimal_or_none(price.get("estimateLow")),
            estimate_high=_decimal_or_none(price.get("estimateHigh")),
            price_realised=realised,
            price_basis=basis,
            outcome=outcome,
            lot_url=_lot_url(auction_id, lot_number, title),
            image_url=str((doc.get("image") or {}).get("url") or "") or None,
            extra={
                k: str(v)
                for k, v in (
                    ("department", (doc.get("department") or {}).get("code")),
                    ("hammer_price", hammer),
                    ("result_published", published),
                )
                if v not in (None, "")
            },
        )


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


def _lot_url(auction_id: str, lot_number: str | None, title: str) -> str | None:
    """Bonhams lot URLs are ``/auction/{id}/lot/{no}/{title-slug}/``.

    Accents must be folded before punctuation is stripped, or ``HERMÈS`` slugs to
    ``herm-s`` and the resulting URL 404s.
    """
    if not (auction_id and lot_number and title):
        return None
    slug = _SLUG_STRIP_RE.sub("-", strip_accents(title).lower()).strip("-")[:120].rstrip("-")
    return f"https://www.bonhams.com/auction/{auction_id}/lot/{lot_number}/{slug}/"
