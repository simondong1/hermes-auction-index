"""Sotheby's adapter.

Two public, unauthenticated surfaces are combined:

``prod_lots`` on Algolia (app ``KAR1UEUPJD``)
    The catalogue index. Carries titles, estimates, sale metadata and structured
    handbag attributes - but **not** realised prices.

``clientapi.prod.sothelabs.com/graphql``
    The lot-detail gateway, which carries the realised price under
    ``bidState.sold`` as either ``ResultVisible`` (price published) or
    ``ResultHidden`` (Sotheby's declines to publish without an account).

Two constraints shape the implementation:

*Pagination cap.* Algolia only serves ~1,337 hits per query regardless of ``nbHits``.
A keyword like "kelly" matches 3,117 lots, so a naive page walk silently loses half
the data. The fix is to facet on ``auctionId`` first and then page within each sale,
which is exhaustive because no single sale approaches the cap.

*Per-lot price lookups.* One GraphQL round trip per lot would mean thousands of
requests. The gateway accepts aliased batches, so lots are enriched in groups.
"""

from __future__ import annotations

import base64
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

logger = logging.getLogger(__name__)

_APP_ID: Final = "KAR1UEUPJD"
_INDEX: Final = "prod_lots"
_ALGOLIA_URL: Final = f"https://{_APP_ID}-dsn.algolia.net/1/indexes/{_INDEX}"
_GRAPHQL_URL: Final = "https://clientapi.prod.sothelabs.com/graphql"
#: A /BrowsePage surface; its Algolia key is catalogue-wide rather than sale-scoped.
_KEY_SOURCE_PAGE: Final = "https://www.sothebys.com/en/buy/fashion/handbag"

_NEXT_DATA_RE: Final = re.compile(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.+?)</script>', re.DOTALL)

_HITS_PER_PAGE: Final = 100
#: Lots per aliased GraphQL batch. Large enough to matter, small enough to stay well
#: inside the gateway's query-complexity budget.
_GRAPHQL_BATCH: Final = 20

#: Staging and QA records leak into the production index; they satisfy Sotheby's own
#: `isTestRecord` filter because the flag is not consistently set.
_TEST_SALE_RE: Final = re.compile(r"\b(TEST|COPY|QA|Clerk Test|IT Test|Test Sale)\b", re.IGNORECASE)

_LOT_FRAGMENT: Final = """
fragment LotDetail on LotV2 {
  lotId
  title
  subtitle
  description
  withdrawnState { state }
  bidState {
    sold {
      __typename
      ... on ResultVisible { isSold premiums { finalPriceV2 { amount } } }
    }
  }
  estimateV2 {
    ... on LowHighEstimateV2 {
      lowEstimate { amount currency }
      highEstimate { amount currency }
    }
  }
  auction { auctionId title currency }
  media(imageSizes: [Large]) { images { renditions { url imageSize } } }
}
"""


class SothebysAuthError(RuntimeError):
    """The rotating Algolia search key could not be harvested."""


@register
class SothebysSource(AuctionSource):
    """Harvests past Sotheby's lots from Algolia, priced via the GraphQL gateway."""

    house: ClassVar[AuctionHouse] = AuctionHouse.SOTHEBYS
    coverage_note: ClassVar[str] = (
        "Full catalogue index via Algolia, priced per lot via the public GraphQL "
        "gateway. Sotheby's withholds the realised price on a meaningful share of "
        "lots; those are reported as 'result withheld', never as unsold."
    )

    def __init__(self, client: HttpClient, queries: Sequence[str] = DEFAULT_QUERIES) -> None:
        super().__init__(client)
        self.queries = tuple(queries)
        self._key: str | None = None
        self._key_expires: dt.datetime | None = None

    # -- Algolia ------------------------------------------------------------------

    def _search_key(self) -> str:
        """Harvest (and cache until expiry) the rotating search-only Algolia key."""
        now = dt.datetime.now(tz=dt.UTC)
        if self._key and self._key_expires and now < self._key_expires:
            return self._key

        # Never cached on disk: the key is short-lived and a stale one fails opaquely.
        html = self.client.get_text(_KEY_SOURCE_PAGE, use_cache=False)
        match = _NEXT_DATA_RE.search(html)
        if match is None:
            msg = "Sotheby's page did not contain __NEXT_DATA__"
            raise SothebysAuthError(msg)
        key = json.loads(match.group(1))["props"]["pageProps"].get("algoliaSearchKey")
        if not key:
            msg = "Sotheby's __NEXT_DATA__ did not contain algoliaSearchKey"
            raise SothebysAuthError(msg)

        self._key = str(key)
        self._key_expires = now + dt.timedelta(hours=3)
        if (valid_until := _decode_key_expiry(self._key)) is not None:
            self._key_expires = min(self._key_expires, valid_until)
        logger.info("harvested Sotheby's Algolia key, valid until %s", self._key_expires)
        return self._key

    def _algolia(self, params: dict[str, Any]) -> dict[str, Any]:
        merged = {
            **params,
            "x-algolia-application-id": _APP_ID,
            "x-algolia-api-key": self._search_key(),
        }
        payload: Any = self.client.get_json(
            _ALGOLIA_URL,
            params=merged,
            # The key rotates every few hours; keying the cache on it would mean a full
            # re-harvest on every run.
            cache_ignore_params=("x-algolia-api-key",),
        )
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _facet_filters(extra: list[list[str]] | None = None) -> str:
        base = [
            # LIVE and CLOSED are both "past"; see the state-semantics note in docs.
            ["auctionState:LIVE", "auctionState:CLOSED"],
            ["withdrawn:false"],
        ]
        return json.dumps(base + (extra or []))

    def _auction_ids(self, keyword: str) -> list[str]:
        """Every past sale containing a hit for ``keyword``."""
        response = self._algolia(
            {
                "query": keyword,
                "hitsPerPage": 0,
                "maxValuesPerFacet": 1000,
                "facets": json.dumps(["auctionId"]),
                "facetFilters": self._facet_filters(),
            }
        )
        return list(response.get("facets", {}).get("auctionId", {}))

    def _hits_for_auction(self, keyword: str, auction_id: str) -> Iterator[dict[str, Any]]:
        page = 0
        while True:
            response = self._algolia(
                {
                    "query": keyword,
                    "hitsPerPage": _HITS_PER_PAGE,
                    "page": page,
                    "facetFilters": self._facet_filters([[f"auctionId:{auction_id}"]]),
                }
            )
            hits = response.get("hits") or []
            yield from hits
            page += 1
            if page >= int(response.get("nbPages") or 0):
                return

    # -- GraphQL ------------------------------------------------------------------

    def _fetch_details(self, lot_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Resolve realised prices for a batch of lot ids in one round trip."""
        aliases = " ".join(
            f'l{i}: lotV2(lotId: "{lot_id}", countryOfOrigin: "US", language: ENGLISH)'
            " { ...LotDetail }"
            for i, lot_id in enumerate(lot_ids)
        )
        response = self.client.post_json(
            _GRAPHQL_URL,
            {"query": f"query LotBatch {{ {aliases} }}{_LOT_FRAGMENT}"},
            headers={"Origin": "https://www.sothebys.com"},
        )
        if errors := response.get("errors"):
            logger.warning("sothebys graphql batch errors: %s", json.dumps(errors)[:400])
        data = response.get("data") or {}
        return {
            lot_id: data.get(f"l{i}") or {} for i, lot_id in enumerate(lot_ids) if data.get(f"l{i}")
        }

    # -- harvesting ---------------------------------------------------------------

    def iter_lots(self, since: dt.date, until: dt.date) -> Iterator[RawLot]:
        in_window: dict[str, dict[str, Any]] = {}
        for keyword in self.queries:
            auction_ids = self._auction_ids(keyword)
            logger.info("sothebys: '%s' spans %d past sales", keyword, len(auction_ids))
            for auction_id in auction_ids:
                for hit in self._hits_for_auction(keyword, auction_id):
                    sale_date = parse_date(hit.get("auctionDate"))
                    if sale_date is None or not (since <= sale_date <= until):
                        continue
                    if hit.get("isTestLot") or _TEST_SALE_RE.search(
                        str(hit.get("auctionName", ""))
                    ):
                        continue
                    in_window[str(hit["objectID"])] = hit

        logger.info("sothebys: %d in-window lots to price", len(in_window))
        ids = list(in_window)
        for start in range(0, len(ids), _GRAPHQL_BATCH):
            batch = ids[start : start + _GRAPHQL_BATCH]
            details = self._fetch_details(batch)
            for lot_id in batch:
                lot = self._to_raw_lot(in_window[lot_id], details.get(lot_id, {}))
                if lot is not None:
                    yield lot

    def _to_raw_lot(self, hit: dict[str, Any], detail: dict[str, Any]) -> RawLot | None:
        sale_date = parse_date(hit.get("auctionDate"))
        if sale_date is None:
            return None

        currency = str(
            hit.get("currency") or (detail.get("auction") or {}).get("currency") or "USD"
        )
        outcome, realised = _read_result(detail)

        slug = hit.get("slug")
        image = _first_image(detail)

        sale = SaleRef(
            house=self.house,
            sale_id=str(hit.get("auctionId") or "unknown"),
            sale_name=str(hit.get("auctionName") or "").strip() or "Unknown sale",
            sale_date=sale_date,
            location=hit.get("auctionLocation"),
            url=_sale_url(slug),
        )

        return RawLot(
            house=self.house,
            lot_key=str(hit["objectID"]),
            lot_number=str(hit.get("lotDisplayNumber") or "") or None,
            title=str(hit.get("title") or detail.get("title") or "").strip(),
            subtitle=str(hit.get("subtitle") or detail.get("subtitle") or "").strip() or None,
            description=_clean_description(detail.get("description")),
            sale=sale,
            currency=currency,
            estimate_low=_decimal_or_none(hit.get("lowEstimate")),
            estimate_high=_decimal_or_none(hit.get("highEstimate")),
            price_realised=realised,
            # Sotheby's "final price" is the hammer plus buyer's premium.
            price_basis=PriceBasis.PREMIUM_INCLUSIVE
            if realised is not None
            else PriceBasis.UNKNOWN,
            outcome=outcome,
            lot_url=f"https://www.sothebys.com{slug}" if slug else None,
            image_url=image,
            extra={
                k: str(v)
                for k, v in (
                    ("sale_type", hit.get("auctionType")),
                    ("lot_state", hit.get("lotState")),
                    ("collection", hit.get("collection")),
                    ("materials", ", ".join(hit.get("Materials", []) or [])),
                    ("special_feature", ", ".join(hit.get("Special Feature", []) or [])),
                    ("handbag_size_cm", hit.get("Handbag Size")),
                )
                if v
            },
        )


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _decode_key_expiry(key: str) -> dt.datetime | None:
    """Read ``validUntil`` out of the base64 Algolia secured key."""
    try:
        decoded = base64.b64decode(key).decode("utf-8", "replace")
    except (ValueError, TypeError):
        return None
    if (match := re.search(r"validUntil=(\d+)", decoded)) is None:
        return None
    return dt.datetime.fromtimestamp(int(match.group(1)), tz=dt.UTC)


def _decimal_or_none(value: Any) -> Decimal | None:
    # Sotheby's uses -1 as a "no estimate" sentinel.
    if value is None or (isinstance(value, (int, float)) and value < 0):
        return None
    try:
        return Decimal(str(value))
    except (ValueError, ArithmeticError):
        return None


def _read_result(detail: dict[str, Any]) -> tuple[LotOutcome, Decimal | None]:
    """Map the GraphQL result union onto an outcome, never inventing a price."""
    if (detail.get("withdrawnState") or {}).get("state") == "Withdrawn":
        return LotOutcome.WITHDRAWN, None
    sold = (detail.get("bidState") or {}).get("sold")
    if not sold:
        return LotOutcome.UNKNOWN, None
    typename = sold.get("__typename")
    if typename == "ResultHidden":
        return LotOutcome.RESULT_HIDDEN, None
    if typename == "ResultVisible":
        if not sold.get("isSold"):
            return LotOutcome.UNSOLD, None
        amount = ((sold.get("premiums") or {}).get("finalPriceV2") or {}).get("amount")
        value = _decimal_or_none(amount)
        return (LotOutcome.SOLD, value) if value is not None else (LotOutcome.RESULT_HIDDEN, None)
    return LotOutcome.UNKNOWN, None


def _first_image(detail: dict[str, Any]) -> str | None:
    images = (detail.get("media") or {}).get("images") or []
    for image in images:
        for rendition in image.get("renditions") or []:
            if url := rendition.get("url"):
                return str(url)
    return None


def _sale_url(slug: str | None) -> str | None:
    """A lot slug is the sale path plus one segment; trim it to get the sale."""
    if not slug:
        return None
    trimmed = slug.rstrip("/").rsplit("/", 1)[0]
    return f"https://www.sothebys.com{trimmed}" if trimmed else None


def _clean_description(html: str | None) -> str | None:
    """Keep the English half of bilingual catalogue copy."""
    if not html:
        return None
    english = re.split(r"-{4,}", html, maxsplit=1)[0]
    return english.strip() or None
