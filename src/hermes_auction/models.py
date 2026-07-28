"""Domain models shared by every source adapter, the normaliser and the exporter.

Two layers:

``RawLot``
    What a source adapter emits. Deliberately permissive - it is a faithful record of
    what the house published, with only mechanical clean-up applied.

``NormalisedLot``
    ``RawLot`` plus parsed bag attributes and a CNY conversion struck at the sale date.
    This is what the published site consumes.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    field_validator,
    model_validator,
)

from hermes_auction.taxonomy import BagFamily

CurrencyCode = Annotated[str, Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")]

_HTTP_URL = TypeAdapter(HttpUrl)


def _validated_url(value: str) -> str:
    """Validate as an HTTP(S) URL but keep the original string.

    ``HttpUrl`` normalises on serialisation - notably appending a trailing slash to
    bare-host URLs - which would make lot links differ from what the house published.
    """
    _HTTP_URL.validate_python(value)
    return value


#: A string guaranteed to parse as an HTTP(S) URL, stored exactly as published.
UrlStr = Annotated[str, AfterValidator(_validated_url)]


class AuctionHouse(StrEnum):
    """Sources this project harvests. Adding one means adding an adapter, nothing else."""

    CHRISTIES = "christies"
    SOTHEBYS = "sothebys"
    PHILLIPS = "phillips"
    BONHAMS = "bonhams"
    HERITAGE = "heritage"
    ARTCURIAL = "artcurial"
    POLY_HK = "poly_hk"


HOUSE_DISPLAY_NAMES: dict[AuctionHouse, str] = {
    AuctionHouse.CHRISTIES: "Christie's",
    AuctionHouse.SOTHEBYS: "Sotheby's",
    AuctionHouse.PHILLIPS: "Phillips",
    AuctionHouse.BONHAMS: "Bonhams",
    AuctionHouse.HERITAGE: "Heritage Auctions",
    AuctionHouse.ARTCURIAL: "Artcurial",
    AuctionHouse.POLY_HK: "Poly Auction Hong Kong",
}


class LotOutcome(StrEnum):
    """What happened to the lot. ``RESULT_HIDDEN`` is distinct from ``UNSOLD``."""

    SOLD = "sold"
    UNSOLD = "unsold"
    WITHDRAWN = "withdrawn"
    #: The house ran the lot but does not publish the price without an account.
    RESULT_HIDDEN = "result_hidden"
    UNKNOWN = "unknown"


class PriceBasis(StrEnum):
    """Whether a realised price includes buyer's premium. Never guess this."""

    PREMIUM_INCLUSIVE = "premium_inclusive"
    HAMMER = "hammer"
    UNKNOWN = "unknown"


class Money(BaseModel):
    """An amount in a single currency. Decimal throughout - never float for money."""

    model_config = ConfigDict(frozen=True)

    amount: Decimal
    currency: CurrencyCode

    @field_validator("amount")
    @classmethod
    def _non_negative(cls, value: Decimal) -> Decimal:
        if value < 0:
            msg = f"monetary amount must be non-negative, got {value}"
            raise ValueError(msg)
        return value


class SaleRef(BaseModel):
    """The sale a lot belonged to."""

    model_config = ConfigDict(frozen=True)

    house: AuctionHouse
    sale_id: str
    sale_name: str
    sale_date: dt.date
    location: str | None = None
    sale_number: str | None = None
    url: UrlStr | None = None


class RawLot(BaseModel):
    """A lot exactly as the auction house published it, before interpretation."""

    model_config = ConfigDict(frozen=True)

    house: AuctionHouse
    #: Stable within the house. Combined with ``house`` this is the global primary key.
    lot_key: str
    lot_number: str | None = None
    title: str
    subtitle: str | None = None
    description: str | None = None

    sale: SaleRef

    currency: CurrencyCode
    estimate_low: Decimal | None = None
    estimate_high: Decimal | None = None
    price_realised: Decimal | None = None
    price_basis: PriceBasis = PriceBasis.UNKNOWN
    outcome: LotOutcome = LotOutcome.UNKNOWN

    lot_url: UrlStr | None = None
    image_url: UrlStr | None = None

    #: Anything source-specific worth keeping for auditability.
    extra: dict[str, str] = Field(default_factory=dict)

    @property
    def uid(self) -> str:
        return f"{self.house.value}:{self.lot_key}"

    @property
    def searchable_text(self) -> str:
        parts = [self.title, self.subtitle or "", self.description or ""]
        return " \n ".join(p for p in parts if p)

    @model_validator(mode="after")
    def _outcome_matches_price(self) -> Self:
        if self.outcome is LotOutcome.SOLD and self.price_realised is None:
            msg = f"lot {self.lot_key} marked sold but carries no realised price"
            raise ValueError(msg)
        return self


class BagAttributes(BaseModel):
    """Structured Hermes attributes parsed out of a lot's free text."""

    model_config = ConfigDict(frozen=True)

    family: BagFamily
    size_cm: int | None = None
    size_bucket: str
    leather: str | None = None
    leather_category: str | None = None
    colour: str | None = None
    colour_family: str | None = None
    colour_hex: str | None = None
    hardware: str | None = None
    hardware_abbrev: str | None = None
    construction: str | None = None
    special_editions: tuple[str, ...] = ()
    stamp_year: int | None = None
    condition_grade: str | None = None

    #: 0.0-1.0. Fraction of the attributes we care about that were resolved.
    completeness: float = 0.0
    #: Which fields the deterministic parser could not resolve. Feeds the AI pass.
    unresolved: tuple[str, ...] = ()
    #: ``rules`` or the model id of the LLM that filled the gaps.
    resolved_by: str = "rules"


class FxConversion(BaseModel):
    """A CNY conversion struck at (or as close as possible to) the sale date."""

    model_config = ConfigDict(frozen=True)

    amount_cny: Decimal
    rate: Decimal
    rate_date: dt.date
    #: True when the sale date fell on a weekend/holiday and we carried the prior rate.
    carried_forward: bool
    source: str


class NormalisedLot(BaseModel):
    """A raw lot enriched with parsed attributes and a CNY conversion."""

    model_config = ConfigDict(frozen=True)

    raw: RawLot
    attributes: BagAttributes
    realised_cny: FxConversion | None = None
    estimate_low_cny: FxConversion | None = None
    estimate_high_cny: FxConversion | None = None

    @property
    def uid(self) -> str:
        return self.raw.uid

    @property
    def sold(self) -> bool:
        return self.raw.outcome is LotOutcome.SOLD
