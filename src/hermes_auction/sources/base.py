"""The contract every auction house adapter implements.

Adding a house is adding one module: subclass :class:`AuctionSource`, decorate it with
:func:`register`, implement :meth:`AuctionSource.iter_lots`. Nothing else in the
pipeline needs to change.

Adapters are responsible for *faithfulness*, not interpretation. They emit
:class:`~hermes_auction.models.RawLot` records that mirror what the house published;
attribute parsing, scope filtering and currency conversion all happen downstream.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from decimal import Decimal, InvalidOperation
from typing import ClassVar, Final, TypeVar

from hermes_auction.http import HttpClient
from hermes_auction.models import AuctionHouse, RawLot

logger = logging.getLogger(__name__)

#: Keywords that, between them, reach every bag in scope across every house.
#: Deliberately broader than the four families so a mis-titled lot still surfaces;
#: scope is enforced later by the parser, not by the query.
DEFAULT_QUERIES: Final[tuple[str, ...]] = (
    "birkin",
    "kelly",
    "kelly pochette",
    "mini kelly",
)


class AuctionSource(ABC):
    """Base class for a single auction house."""

    house: ClassVar[AuctionHouse]
    #: Human-readable note rendered in the site's methodology panel.
    coverage_note: ClassVar[str] = ""

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    @abstractmethod
    def iter_lots(self, since: dt.date, until: dt.date) -> Iterator[RawLot]:
        """Yield every past lot the house published whose sale falls in the window.

        Implementations should be generous: yield anything plausibly a handbag and let
        the downstream parser decide scope. They must never fabricate a price.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} house={self.house.value}>"


_REGISTRY: dict[AuctionHouse, type[AuctionSource]] = {}

TSource = TypeVar("TSource", bound=type[AuctionSource])


def register(cls: TSource) -> TSource:
    """Class decorator that makes an adapter discoverable by the pipeline."""
    if cls.house in _REGISTRY:
        msg = f"duplicate adapter registered for {cls.house}"
        raise RuntimeError(msg)
    _REGISTRY[cls.house] = cls
    return cls


def registry() -> dict[AuctionHouse, type[AuctionSource]]:
    """All registered adapters, keyed by house."""
    return dict(_REGISTRY)


def build(house: AuctionHouse, client: HttpClient) -> AuctionSource:
    """Instantiate the adapter for ``house``."""
    try:
        cls = _REGISTRY[house]
    except KeyError as exc:  # pragma: no cover - guarded by the CLI's enum
        msg = f"no adapter registered for {house}"
        raise LookupError(msg) from exc
    return cls(client)


# --------------------------------------------------------------------------------------
# Shared parsing helpers
# --------------------------------------------------------------------------------------

#: "HKD 1,500,000", "USD 100,800", "£ 12,500", "EUR 69 850"
_PRICE_RE: Final = re.compile(
    r"(?P<code>[A-Z]{3})?\s*(?P<symbol>[$£€¥])?\s*"
    r"(?P<amount>\d[\d,. \u00a0']*\d|\d)",
    re.UNICODE,
)

_SYMBOL_TO_CODE: Final[dict[str, str]] = {"£": "GBP", "€": "EUR", "$": "USD", "¥": "JPY"}


def parse_money(
    text: str | None, *, default_currency: str | None = None
) -> tuple[Decimal, str] | None:
    """Parse a house-formatted price string into ``(amount, ISO currency)``.

    Returns ``None`` for empty text, "Price on request", "Estimate on request" and any
    other non-numeric placeholder. Never raises on unparseable input - a missing price
    is data, a crash is not.
    """
    if not text:
        return None
    match = _PRICE_RE.search(text)
    if match is None:
        return None
    currency = (
        match.group("code") or _SYMBOL_TO_CODE.get(match.group("symbol") or "") or default_currency
    )
    if currency is None:
        return None
    digits = re.sub(r"[^\d.]", "", match.group("amount").replace(",", ""))
    if not digits:
        return None
    try:
        amount = Decimal(digits)
    except InvalidOperation:  # pragma: no cover - defensive
        return None
    return amount, currency


def parse_date(value: str | None) -> dt.date | None:
    """Parse the several date shapes the houses emit, returning ``None`` on failure."""
    if not value:
        return None
    cleaned = value.strip().replace("Z", "+00:00")
    for parser in (dt.datetime.fromisoformat, lambda s: dt.datetime.strptime(s, "%Y-%m-%d")):
        try:
            return parser(cleaned).date()
        except (ValueError, TypeError):
            continue
    # "2026-05-19T23:00Z" - minute-precision ISO without seconds.
    if (m := re.match(r"(\d{4})-(\d{2})-(\d{2})", cleaned)) is not None:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def paginate(
    fetch_page: Callable[[int], list[object]],
    *,
    first_page: int = 1,
    max_pages: int = 10_000,
    stop_on_empty: bool = True,
) -> Iterator[object]:
    """Walk pages until one comes back empty or ``max_pages`` is reached."""
    for page in range(first_page, first_page + max_pages):
        items = fetch_page(page)
        if not items and stop_on_empty:
            return
        yield from items
