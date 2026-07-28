"""Harvest -> normalise -> export.

The two stages are deliberately separable:

**harvest** writes one immutable JSONL snapshot per house under ``data/raw/``. This is
the audit trail: every published figure can be traced back to the bytes the house
served, and re-normalising never requires touching the network again.

**build** reads those snapshots, parses bag attributes, drops out-of-scope lots,
converts realised prices to CNY at the sale date, and emits the dataset the site
consumes plus a coverage report.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from hermes_auction.fx import FxConverter
from hermes_auction.http import HttpClient
from hermes_auction.models import (
    HOUSE_DISPLAY_NAMES,
    AuctionHouse,
    LotOutcome,
    NormalisedLot,
    RawLot,
)
from hermes_auction.parsing.attributes import parse_attributes
from hermes_auction.sources import build as build_source
from hermes_auction.sources import registry

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HarvestReport:
    """What a single house's harvest produced."""

    house: AuctionHouse
    lots: int = 0
    errors: int = 0
    path: Path | None = None
    failure: str | None = None


@dataclass(slots=True)
class BuildReport:
    """Coverage and quality statistics for a normalisation run."""

    raw_lots: int = 0
    in_scope: int = 0
    sold: int = 0
    priced_in_cny: int = 0
    by_house: Counter[str] = field(default_factory=Counter)
    by_family: Counter[str] = field(default_factory=Counter)
    by_outcome: Counter[str] = field(default_factory=Counter)
    unresolved_fields: Counter[str] = field(default_factory=Counter)
    #: Terms the vocabularies could not name, ranked by frequency. Feeds the AI pass.
    vocabulary_gaps: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    fx_failures: int = 0
    carried_forward_rates: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "raw_lots": self.raw_lots,
            "in_scope": self.in_scope,
            "sold": self.sold,
            "priced_in_cny": self.priced_in_cny,
            "by_house": dict(self.by_house),
            "by_family": dict(self.by_family),
            "by_outcome": dict(self.by_outcome),
            "unresolved_fields": dict(self.unresolved_fields),
            "vocabulary_gaps": dict(self.vocabulary_gaps),
            "fx_failures": self.fx_failures,
            "carried_forward_rates": self.carried_forward_rates,
        }


def raw_path(raw_dir: Path, house: AuctionHouse) -> Path:
    return raw_dir / f"{house.value}.jsonl"


# --------------------------------------------------------------------------------------
# Harvest
# --------------------------------------------------------------------------------------


def harvest(
    houses: Sequence[AuctionHouse],
    since: dt.date,
    until: dt.date,
    *,
    client: HttpClient,
    raw_dir: Path,
) -> list[HarvestReport]:
    """Run each adapter and write its snapshot. One house failing never stops the rest."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    reports: list[HarvestReport] = []

    for house in houses:
        report = HarvestReport(house=house)
        destination = raw_path(raw_dir, house)
        # Write to a temp file so a crash cannot leave a truncated snapshot in place.
        staging = destination.with_suffix(".jsonl.partial")
        try:
            source = build_source(house, client)
            with staging.open("w", encoding="utf-8") as handle:
                for lot in source.iter_lots(since, until):
                    handle.write(lot.model_dump_json() + "\n")
                    report.lots += 1
                    if report.lots % 250 == 0:
                        logger.info("%s: %d lots", house.value, report.lots)
            staging.replace(destination)
            report.path = destination
            logger.info("%s: harvested %d lots -> %s", house.value, report.lots, destination)
        except Exception as exc:
            report.failure = f"{type(exc).__name__}: {exc}"
            logger.exception("%s: harvest failed", house.value)
            staging.unlink(missing_ok=True)
        reports.append(report)

    return reports


def load_raw(raw_dir: Path, houses: Iterable[AuctionHouse] | None = None) -> Iterator[RawLot]:
    """Stream previously harvested lots back off disk."""
    wanted = list(houses) if houses is not None else list(registry())
    for house in wanted:
        path = raw_path(raw_dir, house)
        if not path.exists():
            logger.debug("no snapshot for %s", house.value)
            continue
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    yield RawLot.model_validate_json(line)
                except ValidationError:
                    logger.warning("%s:%d is not a valid RawLot; skipped", path, line_number)


# --------------------------------------------------------------------------------------
# Normalise
# --------------------------------------------------------------------------------------


def normalise_lots(
    raw_lots: Iterable[RawLot],
    converter: FxConverter,
    report: BuildReport,
) -> list[NormalisedLot]:
    """Parse attributes, drop out-of-scope lots and convert money to CNY."""
    normalised: list[NormalisedLot] = []

    for raw in raw_lots:
        report.raw_lots += 1
        attributes = parse_attributes(raw.title, raw.subtitle, raw.description)
        if attributes is None:
            continue

        report.in_scope += 1
        report.by_house[raw.house.value] += 1
        report.by_family[attributes.family.value] += 1
        report.by_outcome[raw.outcome.value] += 1
        for missing in attributes.unresolved:
            report.unresolved_fields[missing] += 1

        realised = converter.try_convert(raw.price_realised, raw.currency, raw.sale.sale_date)
        if raw.price_realised is not None and realised is None:
            report.fx_failures += 1
        if realised is not None:
            report.priced_in_cny += 1
            if realised.carried_forward:
                report.carried_forward_rates += 1
        if raw.outcome is LotOutcome.SOLD:
            report.sold += 1

        normalised.append(
            NormalisedLot(
                raw=raw,
                attributes=attributes,
                realised_cny=realised,
                estimate_low_cny=converter.try_convert(
                    raw.estimate_low, raw.currency, raw.sale.sale_date
                ),
                estimate_high_cny=converter.try_convert(
                    raw.estimate_high, raw.currency, raw.sale.sale_date
                ),
            )
        )

    normalised.sort(key=lambda lot: (lot.raw.sale.sale_date, lot.uid), reverse=True)
    return normalised


def currencies_and_span(raw_lots: Sequence[RawLot]) -> tuple[set[str], dt.date, dt.date]:
    """Everything :meth:`FxConverter.prime` needs, in one pass."""
    currencies = {lot.currency for lot in raw_lots}
    dates = [lot.sale.sale_date for lot in raw_lots]
    return currencies, min(dates), max(dates)


# --------------------------------------------------------------------------------------
# Vocabulary gap detection - the hook the AI enrichment pass consumes
# --------------------------------------------------------------------------------------

#: Words that are not attribute names: catalogue furniture, model names, hardware
#: finishes, construction and edition vocabulary, and the jewellery lines that share a
#: bag name. Excluding them is what makes the gap report readable - otherwise "birkin"
#: and "palladium" sit at the top of every list.
_STOPWORD_TEXT = """
    a an and the with in of for hermes bag handbag sac leather hardware includes grade
    cm w h d this lot see please note condition report accompanied by comes original
    dust dustbag box ribbon clochette cadena cadenas padlock lock keys key raincoat felt
    protector care card shoulder strap stamp square circle blind year circa signed
    interior exterior stitching corners scratches marks wear worn good very excellent
    pristine unworn store fresh some minor light surface consignor property from
    collection important private sold as is set two three suite double series pair
    model small large
    birkin kelly mini micro pochette sellier retourne cut danse doll lakis teddy
    palladium gold rose ruthenium permabrass brushed diamond diamonds 18k silver
    sterling argent enamel guilloche dore steel
    limited edition rare vintage custom special order horseshoe hss verso ghillies
    faubourg himalaya cargo shadow touch picnic padded grizzly tressage officier
    matte matt shiny quilted
    necklace bracelet bangle pendant pendent ring earring cuff brooch amulette amulettes
    bijou jewellery jewelry watch quartz gourmette galop precieux rock caviar
    jpg hac butler kellymorphose
    """

_STOPWORDS: Final[frozenset[str]] = frozenset(_STOPWORD_TEXT.split())


def find_vocabulary_gaps(
    lots: Sequence[NormalisedLot], *, top_n: int = 40
) -> dict[str, list[tuple[str, int]]]:
    """Rank the unnamed words appearing in lots whose colour or leather went unresolved.

    This is the input to the optional LLM pass: rather than letting a model rewrite the
    dataset, it is asked only to name these specific unknown terms, and its proposals
    land as reviewable additions to the JSON vocabularies.
    """
    from hermes_auction.taxonomy import colour_vocab, leather_vocab, normalise

    known = {
        normalise(alias)
        for vocab in (colour_vocab(), leather_vocab())
        for term in vocab.terms
        for alias in term.aliases
    }
    known_words = {word for alias in known for word in alias.split()}

    counters: dict[str, Counter[str]] = defaultdict(Counter)
    for lot in lots:
        for missing in lot.attributes.unresolved:
            if missing not in {"colour", "leather"}:
                continue
            for word in normalise(lot.raw.title).split():
                if word.isdigit() or len(word) < 3:
                    continue
                if word in known_words or word in _STOPWORDS:
                    continue
                counters[missing][word] += 1

    return {field_name: counter.most_common(top_n) for field_name, counter in counters.items()}


# --------------------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------------------


def build_dataset(
    raw_dir: Path,
    out_dir: Path,
    *,
    client: HttpClient,
    fx_cache: Path | None = None,
    houses: Sequence[AuctionHouse] | None = None,
) -> tuple[list[NormalisedLot], BuildReport]:
    """Read snapshots, normalise and write the site's dataset."""
    raw_lots = list(load_raw(raw_dir, houses))
    if not raw_lots:
        msg = f"no raw snapshots found under {raw_dir}; run `harvest` first"
        raise FileNotFoundError(msg)

    converter = FxConverter(client, cache_path=fx_cache)
    currencies, first, last = currencies_and_span(raw_lots)
    converter.prime(currencies, first, last)
    converter.save_cache()

    report = BuildReport()
    lots = normalise_lots(raw_lots, converter, report)
    report.vocabulary_gaps = find_vocabulary_gaps(lots)

    out_dir.mkdir(parents=True, exist_ok=True)
    write_site_payload(lots, report, out_dir)
    (out_dir / "report.json").write_text(
        json.dumps(report.as_dict(), indent=2, sort_keys=True), "utf-8"
    )
    return lots, report


def write_site_payload(lots: Sequence[NormalisedLot], report: BuildReport, out_dir: Path) -> None:
    """Emit the compact JSON the front end loads.

    Field names are shortened because this file is downloaded by every visitor; the
    long-form records stay in ``data/raw`` for anyone doing analysis.
    """
    from hermes_auction.taxonomy import FAMILY_ORDER, FAMILY_SPECS

    records = [
        {
            "id": lot.uid,
            "house": lot.raw.house.value,
            "houseName": HOUSE_DISPLAY_NAMES[lot.raw.house],
            "saleName": lot.raw.sale.sale_name,
            "saleDate": lot.raw.sale.sale_date.isoformat(),
            "location": lot.raw.sale.location,
            "lotNumber": lot.raw.lot_number,
            "title": lot.raw.title,
            "url": lot.raw.lot_url,
            "image": lot.raw.image_url,
            "family": lot.attributes.family.value,
            "size": lot.attributes.size_cm,
            "sizeBucket": lot.attributes.size_bucket,
            "leather": lot.attributes.leather,
            "leatherCategory": lot.attributes.leather_category,
            "colour": lot.attributes.colour,
            "colourFamily": lot.attributes.colour_family,
            "colourHex": lot.attributes.colour_hex,
            "hardware": lot.attributes.hardware,
            "hardwareAbbrev": lot.attributes.hardware_abbrev,
            "construction": lot.attributes.construction,
            "editions": list(lot.attributes.special_editions),
            "stampYear": lot.attributes.stamp_year,
            "grade": lot.attributes.condition_grade,
            "outcome": lot.raw.outcome.value,
            "currency": lot.raw.currency,
            "estLow": _num(lot.raw.estimate_low),
            "estHigh": _num(lot.raw.estimate_high),
            "price": _num(lot.raw.price_realised),
            "priceCny": _num(lot.realised_cny.amount_cny) if lot.realised_cny else None,
            "estLowCny": _num(lot.estimate_low_cny.amount_cny) if lot.estimate_low_cny else None,
            "estHighCny": _num(lot.estimate_high_cny.amount_cny) if lot.estimate_high_cny else None,
            "fxRate": float(lot.realised_cny.rate) if lot.realised_cny else None,
            "fxDate": lot.realised_cny.rate_date.isoformat() if lot.realised_cny else None,
            "priceBasis": lot.raw.price_basis.value,
        }
        for lot in lots
    ]

    payload = {
        "generatedAt": dt.datetime.now(tz=dt.UTC).isoformat(timespec="seconds"),
        "families": [
            {
                "key": family.value,
                "name": FAMILY_SPECS[family].display_name,
                "headlineSizes": list(FAMILY_SPECS[family].headline_sizes),
                "nonexistentSizes": list(FAMILY_SPECS[family].nonexistent_sizes),
                "sizeless": FAMILY_SPECS[family].sizeless,
            }
            for family in FAMILY_ORDER
        ],
        "houses": [
            {
                "key": house.value,
                "name": HOUSE_DISPLAY_NAMES[house],
                "note": cls.coverage_note,
                "lots": report.by_house.get(house.value, 0),
            }
            for house, cls in sorted(registry().items(), key=lambda kv: kv[0].value)
        ],
        "fxSource": "ECB daily reference rate (via frankfurter.dev)",
        "summary": report.as_dict(),
        "lots": records,
    }
    (out_dir / "dataset.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8"
    )
    logger.info("wrote %d records to %s", len(records), out_dir / "dataset.json")


def _num(value: object) -> float | None:
    return float(value) if value is not None else None  # type: ignore[arg-type]
