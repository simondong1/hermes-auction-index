"""Hermes bag taxonomy: families, size ladders and attribute vocabularies.

The *structure* (which families exist, which sizes each family ships in) lives here
as typed constants because it is stable and drives the published hierarchy.

The *open vocabularies* (leathers, colours, hardware) live in ``data/*.json`` so they
can be extended - by a human or by an LLM enrichment pass - without touching code.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cache, lru_cache
from importlib import resources
from typing import Final


class BagFamily(StrEnum):
    """Top-level bag types tracked by this project."""

    BIRKIN = "birkin"
    KELLY = "kelly"
    MINI_KELLY = "mini_kelly"
    KELLY_POCHETTE = "kelly_pochette"


@dataclass(frozen=True, slots=True)
class FamilySpec:
    """Everything needed to recognise a family and lay out its sizes."""

    family: BagFamily
    display_name: str
    #: Sizes the site renders as first-class buckets, in ladder order.
    headline_sizes: tuple[int, ...]
    #: Every size Hermes has produced for this family; anything else lands in "Other".
    known_sizes: tuple[int, ...]
    #: Sizes explicitly requested by the brief that Hermes does not actually produce.
    #: Kept so the UI can explain the gap rather than silently dropping the request.
    nonexistent_sizes: tuple[int, ...] = ()
    #: A family with no size axis (a clutch) renders a single "one size" bucket.
    sizeless: bool = False


FAMILY_SPECS: Final[Mapping[BagFamily, FamilySpec]] = {
    BagFamily.MINI_KELLY: FamilySpec(
        family=BagFamily.MINI_KELLY,
        display_name="Mini Kelly",
        headline_sizes=(20,),
        known_sizes=(15, 20),
    ),
    BagFamily.KELLY_POCHETTE: FamilySpec(
        family=BagFamily.KELLY_POCHETTE,
        display_name="Kelly Pochette",
        headline_sizes=(),
        known_sizes=(),
        sizeless=True,
    ),
    BagFamily.BIRKIN: FamilySpec(
        family=BagFamily.BIRKIN,
        display_name="Birkin",
        headline_sizes=(25, 30, 35),
        known_sizes=(20, 25, 30, 35, 40, 45, 50, 55),
    ),
    BagFamily.KELLY: FamilySpec(
        family=BagFamily.KELLY,
        display_name="Kelly",
        headline_sizes=(25, 28, 32, 35),
        known_sizes=(25, 28, 32, 35, 40, 50),
        # The brief asked for "Kelly 30". Hermes has never made one: the ladder steps
        # 25 -> 28 -> 32 -> 35. Surfaced in the UI instead of quietly ignored.
        nonexistent_sizes=(30,),
    ),
}

#: Render order for the top level of the published hierarchy, matching the brief.
FAMILY_ORDER: Final[tuple[BagFamily, ...]] = (
    BagFamily.MINI_KELLY,
    BagFamily.KELLY_POCHETTE,
    BagFamily.BIRKIN,
    BagFamily.KELLY,
)

#: Objects that are never handbags, whatever model name they borrow. Checked for every
#: family, because Hermes sells Birkin- and Kelly-named jewellery, watches and charms:
#: "SILVER MINI BIRKIN AMULETTE PENDANT NECKLACE" is a necklace, not a Birkin 20.
NON_BAG_MARKERS: Final[tuple[str, ...]] = (
    "necklace",
    "pendant",
    "bracelet",
    "bangle",
    "earring",
    "brooch",
    "amulette",
    "amulettes",
    "charm",
    "keyring",
    "key ring",
    "watch",
    "cufflink",
    "scarf",
    "twilly",
    "wallet",
    "card holder",
    "notebook",
    "lithograph",
    "screenprint",
    "sculpture",
    "porcelain",
    "pill box",
    "pillbox",
    "tray",
    "ashtray",
    "paperweight",
)

#: "Kelly" is a common surname and a jewellery line, so a bare match is not enough
#: evidence that a lot is a handbag. These phrases are what a real bag lot contains:
#: an explicit bag noun. Combined with a resolved leather or an in-ladder size, this is
#: the gate that keeps Ellsworth Kelly canvases and Kelly bracelets out of the dataset.
BAG_EVIDENCE: Final[tuple[str, ...]] = (
    "bag",
    "handbag",
    "sac",
    "purse",
    "pochette",
    "birkin",
    "retourne",
    "sellier",
)

#: Kelly-named bags that are *not* the Kelly handbag. Checked before the plain
#: "kelly" match so a Kelly Danse never pollutes the Kelly 25/28/32/35 buckets.
KELLY_EXCLUSIONS: Final[tuple[str, ...]] = (
    # Named people. Ellsworth Kelly alone accounted for 73 false positives.
    "ellsworth kelly",
    "grace kelly",
    "ned kelly",
    "kelly olynyk",
    "kelly oubre",
    "miles kelly",
    # The Kelly jewellery and watch lines share the name but nothing else.
    "kelly bracelet",
    "kelly bangle",
    "kelly watch",
    "kelly necklace",
    "kelly pendant",
    "kelly ring",
    "kelly earring",
    "kelly cuff",
    "kelly chaine",
    "kelly chaîne",
    "kelly gourmette",
    "kelly clou",
    "kelly cut",
    "kelly danse",
    "kelly doll",
    "kelly lakis",
    "kelly ado",
    "kelly depeche",
    "kelly dépêche",
    "kelly elan",
    "kelly élan",
    "kelly flat",
    "kelly move",
    "kelly twilly",
    "kelly moove",
    "kelly en desordre",
    "kelly en désordre",
    "kelly idole",
    "quelle idole",
    "kelly sport",
    "kelly relax",
    "kelly retourne bag charm",
    "mini kelly twilly",
    "micro kelly",
    "kelly wallet",
    "kelly classique",
    "kelly to go",
    "kelly pocket",
    "kelly caleche",
    "kelly calèche",
    "kellydole",
    # Distinct silhouettes that share the Kelly name but not the size ladder.
    "kelly messenger",
    "kelly longue",
    "kelly desordre",
    "kelly désordre",
    "kelly mini pochette",
    "so kelly",
    "kelly cadenas",
    "kelly clochette",
    "kelly muff",
    "minaudiere",
    "minaudière",
    "bag charm",
    "micro mini",
)

#: Birkin-named objects that are not the Birkin handbag.
#:
#: The Haut a Courroies is the Birkin's ancestor and is catalogued as "HAC Birkin 32/40";
#: it has its own size ladder and price level, so it is excluded rather than filed under
#: a Birkin size that does not exist. Same for the Jean Paul Gaultier-era shoulder Birkin
#: (a 42cm shoulder bag) and the Birkin Cut clutch.
BIRKIN_EXCLUSIONS: Final[tuple[str, ...]] = (
    "birkin bag charm",
    "micro birkin",
    "birkin shoulder",
    "shoulder birkin",
    "birkin cut",
    "birkin light",
    "jpg shoulder",
    "haut a courroies",
    "haut à courroies",
    "hac",
)

#: Construction styles worth faceting on.
CONSTRUCTIONS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("Sellier", ("sellier",)),
    ("Retourne", ("retourne", "retourné")),
)

#: Named special editions that materially move price. Longest phrase wins.
SPECIAL_EDITIONS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    # A special-order bag: the horseshoe stamp beside the blind stamp commands a
    # substantial premium, so it is tracked as a first-class attribute.
    ("Horseshoe Stamp (HSS)", ("horseshoe stamp", "horse shoe stamp", "hss", "special order")),
    ("Himalaya", ("himalaya", "himalayan")),
    ("Faubourg", ("faubourg",)),
    ("So Black", ("so black", "so-black")),
    ("Ghillies", ("ghillies",)),
    ("Cargo", ("cargo",)),
    ("Shadow", ("shadow",)),
    ("Touch", ("touch",)),
    ("3-in-1", ("3-in-1", "3 en 1", "trois en un")),
    ("Club", ("birkin club", "kelly club")),
    ("Picnic", ("picnic", "pique-nique")),
    ("Padded", ("padded", "matelasse", "matelassé")),
    ("Verso", ("verso",)),
    ("Grizzly", ("grizzly",)),
    ("In & Out", ("in and out", "in & out", "in&out")),
    ("Tressage", ("tressage", "tressee", "tressée", "braided")),
    ("Officier", ("officier",)),
    ("Amazone", ("amazone",)),
    ("Colormatic", ("colormatic",)),
    ("Chamkila", ("chamkila", "chamkilight")),
    ("Dalmatian", ("dalmatien", "dalmatian")),
    ("Special Order", ("commande speciale", "commande spéciale")),
)


# --------------------------------------------------------------------------------------
# Text normalisation
# --------------------------------------------------------------------------------------

_WS_RE: Final = re.compile(r"\s+")
_TAG_RE: Final = re.compile(r"<[^>]+>")


def strip_accents(text: str) -> str:
    """Fold accented Latin characters to ASCII so ``Chevre`` matches ``Chèvre``."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalise(text: str) -> str:
    """Lower-case, de-accent, strip HTML tags and collapse whitespace/punctuation.

    The result is what every vocabulary match runs against. Punctuation becomes a
    single space so ``BIRKIN-30`` and ``Birkin, 30 cm`` both reduce to ``birkin 30 cm``.
    """
    text = _TAG_RE.sub(" ", text)
    text = strip_accents(text).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return _WS_RE.sub(" ", text).strip()


# --------------------------------------------------------------------------------------
# Vocabulary loading + matching
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VocabTerm:
    """A canonical vocabulary entry with its match aliases."""

    canonical: str
    aliases: tuple[str, ...]
    group: str | None = None
    hex_colour: str | None = None
    abbrev: str | None = None


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """Longest-alias-first matcher over a set of canonical terms."""

    name: str
    terms: tuple[VocabTerm, ...]

    @property
    def by_canonical(self) -> Mapping[str, VocabTerm]:
        return {t.canonical: t for t in self.terms}

    def _ordered_aliases(self) -> Iterator[tuple[str, VocabTerm]]:
        pairs = [(normalise(a), t) for t in self.terms for a in t.aliases]
        # Longest alias first so "rouge casaque" beats "rouge", "bleu nuit" beats "bleu".
        pairs.sort(key=lambda p: (-len(p[0]), p[0]))
        yield from pairs

    @cache  # noqa: B019 - instances are module-level singletons
    def _patterns(self) -> tuple[tuple[re.Pattern[str], VocabTerm], ...]:
        return tuple(
            (re.compile(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"), term)
            for alias, term in self._ordered_aliases()
        )

    def find_first(self, normalised_text: str) -> VocabTerm | None:
        """Return the single best (longest-alias) match, or ``None``."""
        for pattern, term in self._patterns():
            if pattern.search(normalised_text):
                return term
        return None

    def find_all(self, normalised_text: str) -> tuple[VocabTerm, ...]:
        """Return every distinct term present, longest-alias-first, without overlaps."""
        found: list[VocabTerm] = []
        consumed = bytearray(len(normalised_text))
        for pattern, term in self._patterns():
            if term in found:
                continue
            for match in pattern.finditer(normalised_text):
                if any(consumed[match.start() : match.end()]):
                    continue
                consumed[match.start() : match.end()] = b"\x01" * (match.end() - match.start())
                found.append(term)
                break
        return tuple(found)


def _load_json(filename: str) -> dict[str, object]:
    payload = resources.files("hermes_auction.data").joinpath(filename).read_text("utf-8")
    parsed: dict[str, object] = json.loads(payload)
    return parsed


def _build_vocab(filename: str, key: str, *, group_field: str | None) -> Vocabulary:
    raw = _load_json(filename)
    rows = raw[key]
    assert isinstance(rows, list)
    terms: list[VocabTerm] = []
    for row in rows:
        assert isinstance(row, dict)
        terms.append(
            VocabTerm(
                canonical=str(row["canonical"]),
                aliases=tuple(str(a) for a in row["aliases"]),
                group=str(row[group_field]) if group_field and group_field in row else None,
                hex_colour=str(row["hex"]) if "hex" in row else None,
                abbrev=str(row["abbrev"]) if "abbrev" in row else None,
            )
        )
    return Vocabulary(name=key, terms=tuple(terms))


@lru_cache(maxsize=1)
def leather_vocab() -> Vocabulary:
    return _build_vocab("leathers.json", "materials", group_field="category")


@lru_cache(maxsize=1)
def colour_vocab() -> Vocabulary:
    return _build_vocab("colours.json", "colours", group_field="family")


@lru_cache(maxsize=1)
def hardware_vocab() -> Vocabulary:
    return _build_vocab("hardware.json", "hardware", group_field=None)


def size_bucket(family: BagFamily, size_cm: int | None) -> str:
    """Map a parsed size onto the bucket label the site groups by."""
    spec = FAMILY_SPECS[family]
    if spec.sizeless:
        return "One size"
    if size_cm is None:
        return "Size unknown"
    if size_cm in spec.headline_sizes:
        return f"{spec.display_name} {size_cm}"
    return f"{spec.display_name} {size_cm} (other)"


def headline_bucket_labels(family: BagFamily) -> Sequence[str]:
    spec = FAMILY_SPECS[family]
    if spec.sizeless:
        return ["One size"]
    return [f"{spec.display_name} {s}" for s in spec.headline_sizes]
