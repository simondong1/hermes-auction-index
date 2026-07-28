"""Deterministic extraction of Hermes bag attributes from auction lot text.

Auction houses describe the same bag in wildly different registers::

    Christie's  A MATTE WHITE HIMALAYA NILOTICUS CROCODILE DIAMOND BIRKIN 30
                HERMES, 2014 / GRADE: 1
    Sotheby's   Craie Epsom Birkin Sellier 25 Gold Hardware, 2022
    Heritage    Hermes 25cm Gris Meyer Togo Leather Birkin Bag with Palladium Hardware

A single vocabulary-driven pass handles all of them. The order of operations matters
and is the main source of correctness here:

1. strip accessory boilerplate ("includes dustbag, box and ribbon") so ``box`` cannot
   be mistaken for Box Calf;
2. resolve and **mask** hardware phrases, so ``gold hardware`` cannot be mistaken for
   the colour Gold while a leading ``A GOLD TOGO BIRKIN`` still resolves correctly;
3. resolve colour, leather, construction, edition and size against the masked text.

Anything the rules cannot resolve is recorded in ``BagAttributes.unresolved`` rather
than guessed, which is what makes the optional LLM pass in :mod:`.llm` safe to bolt on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from hermes_auction.models import BagAttributes
from hermes_auction.taxonomy import (
    BAG_EVIDENCE,
    BIRKIN_EXCLUSIONS,
    CONSTRUCTIONS,
    FAMILY_SPECS,
    KELLY_EXCLUSIONS,
    NON_BAG_MARKERS,
    SPECIAL_EDITIONS,
    BagFamily,
    colour_vocab,
    hardware_vocab,
    leather_vocab,
    normalise,
    size_bucket,
)

#: Packaging / provenance chatter that would otherwise poison vocabulary matching.
#: ``box``, ``natural``, ``gold`` and ``lock`` all appear here and all collide with
#: real leather or colour names.
_BOILERPLATE_PATTERNS: Final[tuple[str, ...]] = (
    r"includes?\b[^.;<]{0,200}",
    r"accompanied by\b[^.;<]{0,200}",
    r"comes? with\b[^.;<]{0,200}",
    r"(?:with|and)\s+(?:its\s+)?(?:original\s+)?(?:dust\s?bag|dustbag|box|ribbon|"
    r"raincoat|clochette|cadena|padlock|keys?|care card|felt protector|shoulder strap)"
    r"(?:\s*,\s*[a-z ]+)*",
    r"\bcites\b[^.;<]{0,200}",
    # Latin species names only. These must NOT swallow trailing text: a Sotheby's title
    # reads "Natura Ombre Shiny Lizard Varanus Niloticus Birkin 25", so consuming the
    # rest of the line would eat the model and size.
    r"\bstruthio camelus\b",
    r"\bcrocodylus (?:niloticus|porosus)\b",
    r"\bvaranus niloticus\b",
    r"\balligator mississippiensis\b",
    r"\bannexe\s+[ivx]+\s*-?\s*[ab]?\b",
    r"\bthis lot (?:is|may|cannot|contains)\b[^.;<]{0,200}",
    r"\bplease note\b[^.;<]{0,200}",
    r"\bcondition report\b[^.;<]{0,200}",
    r"\bbuyer'?s premium\b[^.;<]{0,200}",
)

_BOILERPLATE_RE: Final = re.compile("|".join(_BOILERPLATE_PATTERNS), re.IGNORECASE)

#: ``2019`` in "HERMES, 2019" or "Birkin 25, 2024" - the atelier stamp year.
_YEAR_RE: Final = re.compile(r"(?<![0-9])(19[5-9][0-9]|20[0-4][0-9])(?![0-9])")

#: An artist attribution: "ELLSWORTH KELLY (1923-2015)", "John Melville Kelly (1879-1962)",
#: "Kelly Reichardt (b. 1964)". Several painters and printmakers are surnamed Kelly, so a
#: lifespan or birth year in the title is a reliable signal that the lot is their work
#: rather than a handbag. No Hermes catalogue entry carries one.
_ARTIST_LIFESPAN_RE: Final = re.compile(
    r"\(\s*(?:b\.?|born|c\.?|circa)?\s*1[6-9]\d{2}\s*[-\u2013]\s*(?:1[6-9]|20)\d{2}\s*\)"
    r"|\(\s*(?:b\.?|born)\s*(?:1[6-9]|20)\d{2}\s*\)",
    re.IGNORECASE,
)

#: Christie's publishes a numeric condition grade; other houses use prose.
#: Christie's prints "GRADE: 1"; Heritage prints "Condition: 3". Same 1-4 scale.
_GRADE_RE: Final = re.compile(r"\b(?:grade|condition)\s*:?\s*([1-4])\b", re.IGNORECASE)

_MASK_CHAR: Final = "\u0000"


@dataclass(frozen=True, slots=True)
class FamilyMatch:
    family: BagFamily
    #: Character span of the family keyword in the normalised text, used to anchor
    #: the size number to the right model when a title mentions several.
    span: tuple[int, int]


def _mask(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    chars = list(text)
    for start, end in spans:
        for i in range(start, min(end, len(chars))):
            chars[i] = _MASK_CHAR
    return "".join(chars)


def strip_boilerplate(text: str) -> str:
    """Blank out accessory/CITES chatter before any vocabulary matching."""
    return _BOILERPLATE_RE.sub(" ", text)


def detect_family(normalised: str) -> FamilyMatch | None:
    """Identify which of the four tracked families a lot belongs to.

    Returns ``None`` for anything out of scope, including Kelly-named objects that are
    not the Kelly handbag (Kelly Cut, Kelly Danse, Kelly Doll, ...).
    """
    # Kelly Pochette is checked before Mini Kelly and Kelly: it contains the word
    # "kelly" and would otherwise be swallowed by them.
    if (m := re.search(r"kelly\s+pochette|pochette\s+kelly", normalised)) is not None:
        return FamilyMatch(BagFamily.KELLY_POCHETTE, m.span())

    if any(excl in normalised for excl in (normalise(e) for e in KELLY_EXCLUSIONS)):
        return None

    if (m := re.search(r"mini\s+kelly|kelly\s+mini", normalised)) is not None:
        return FamilyMatch(BagFamily.MINI_KELLY, m.span())

    if (m := re.search(r"\bkelly\b", normalised)) is not None:
        # A "Kelly 20" is what the market calls a Mini Kelly.
        if re.search(r"kelly\s+(?:sellier\s+|retourne\s+)?20\b", normalised):
            return FamilyMatch(BagFamily.MINI_KELLY, m.span())
        return FamilyMatch(BagFamily.KELLY, m.span())

    if any(excl in normalised for excl in (normalise(e) for e in BIRKIN_EXCLUSIONS)):
        return None

    if (m := re.search(r"\bbirkin\b", normalised)) is not None:
        return FamilyMatch(BagFamily.BIRKIN, m.span())

    return None


def detect_size(normalised: str, match: FamilyMatch, *, infer_mini: bool = True) -> int | None:
    """Find the size in cm, preferring a number adjacent to the family keyword.

    Args:
        infer_mini: When ``True``, a Mini Kelly with no stated size falls back to 20cm,
            which is correct for the bag. Scope checking passes ``False`` so that
            inferred size is not mistaken for evidence that the lot is a bag at all.
    """
    spec = FAMILY_SPECS[match.family]
    if spec.sizeless:
        return None
    allowed = set(spec.known_sizes)

    modifier = r"(?:sellier|retourne|touch|cargo|shadow|faubourg|ghillies|verso|so black|)\s*"
    anchored = (
        # "birkin 30", "birkin sellier 25", "kelly ii 28"
        rf"{match.family.value.replace('_', ' ')}\s*(?:ii|i)?\s*{modifier}(\d{{2}})\b",
        rf"\b(?:birkin|kelly)\s*(?:ii|i)?\s*{modifier}(\d{{2}})\b",
        # "30cm birkin", "25 cm kelly"
        r"\b(\d{2})\s*cm\b",
        r"\b(\d{2})\b\s*(?:cm)?\s*(?:birkin|kelly)",
    )
    for pattern in anchored:
        for candidate in re.finditer(pattern, normalised):
            value = int(candidate.group(1))
            if value in allowed:
                return value

    # Special editions push the number away from the model name ("Kelly Teddy 35",
    # "Birkin 25 Faubourg"). Once the anchored patterns have had their chance, any
    # two-digit number that is on this family's real ladder is the size. Numbers off
    # the ladder are still refused, so an HAC 40 never becomes a Birkin.
    for candidate in re.finditer(r"(?<![a-z0-9])(\d{2})(?![a-z0-9])", normalised):
        value = int(candidate.group(1))
        if value in allowed:
            return value

    # Mini Kelly is always 20 unless the text says 15.
    if infer_mini and match.family is BagFamily.MINI_KELLY:
        return 15 if re.search(r"\b15\s*cm\b", normalised) else 20
    return None


#: Families whose name is unambiguously a handbag, so no further evidence is needed.
_SELF_EVIDENT_FAMILIES: Final = frozenset({BagFamily.BIRKIN, BagFamily.KELLY_POCHETTE})


def is_handbag(
    normalised: str, match: FamilyMatch, *, leather: str | None, stated_size: int | None
) -> bool:
    """Decide whether a "Kelly" match is really a Kelly handbag.

    Birkin and Kelly Pochette are self-evident: nothing else Hermès makes carries those
    names. Plain "Kelly" and "Mini Kelly" are not - "Kelly" is a surname (Ellsworth
    Kelly, Grace Kelly, several NBA players) and a jewellery line. For those, require one
    positive signal: an explicit bag noun, a size on the real ladder, or a named leather.
    """
    if _mentions_any(normalised, NON_BAG_MARKERS):
        return False
    if match.family in _SELF_EVIDENT_FAMILIES:
        return True
    if stated_size is not None or leather is not None:
        return True
    return _mentions_any(normalised, BAG_EVIDENCE)


def _mentions_any(normalised: str, words: tuple[str, ...]) -> bool:
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(normalise(word))}(?![a-z0-9])", normalised)
        for word in words
    )


def _detect_editions(normalised: str) -> tuple[str, ...]:
    found: list[str] = []
    for canonical, aliases in SPECIAL_EDITIONS:
        if any(
            re.search(rf"(?<![a-z0-9]){re.escape(normalise(a))}(?![a-z0-9])", normalised)
            for a in aliases
        ):
            found.append(canonical)
    return tuple(found)


def _detect_construction(normalised: str) -> str | None:
    for canonical, aliases in CONSTRUCTIONS:
        if any(re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", normalised) for a in aliases):
            return canonical
    return None


def _detect_stamp_year(raw_text: str) -> int | None:
    """Take the *last* plausible year: houses append it after the maker line."""
    years = [int(m.group(1)) for m in _YEAR_RE.finditer(raw_text)]
    return years[-1] if years else None


#: Fields the completeness score is measured against.
_SCORED_FIELDS: Final[tuple[str, ...]] = ("size_cm", "leather", "colour", "hardware")


def parse_attributes(
    title: str, subtitle: str | None = None, description: str | None = None
) -> BagAttributes | None:
    """Parse a lot's free text into structured attributes.

    Returns ``None`` when the lot is not one of the four tracked families.

    Title and subtitle are trusted for every field. The description is consulted only
    to fill gaps, because it carries condition prose and packaging lists that produce
    false positives.
    """
    if _ARTIST_LIFESPAN_RE.search(f"{title} {subtitle or ''}"):
        return None

    # Scope is decided on the lot title alone.
    #
    # Every house names the model in the primary title for a handbag lot - measured across
    # all 17,664 harvested lots, 10,224 name it there and exactly 8 name it only in the
    # subtitle, all 8 of which are artworks ("JEFF KOONS" / "Kelly Bag Ivory (Shelf)",
    # Sidney Nolan's Ned Kelly paintings, a photograph called "Kiddy Kelly"). Descriptions
    # are worse still: the Koons sculpture's note reads "includes a Hermes Kelly".
    #
    # So the title is the only trustworthy place to decide *whether* a lot is in scope.
    # Subtitle and description are still used to fill in attributes below.
    match = detect_family(normalise(strip_boilerplate(title)))
    if match is None:
        return None

    headline_raw = " ".join(p for p in (title, subtitle) if p)
    headline = normalise(strip_boilerplate(headline_raw))
    body = normalise(strip_boilerplate(description)) if description else ""

    # --- hardware, masked out before colour so "gold hardware" != colour Gold --------
    hw_vocab = hardware_vocab()
    hardware_term = hw_vocab.find_first(headline) or (hw_vocab.find_first(body) if body else None)

    def mask_hardware(text: str) -> str:
        spans: list[tuple[int, int]] = []
        for term in hw_vocab.terms:
            for alias in term.aliases:
                pattern = re.compile(rf"(?<![a-z0-9]){re.escape(normalise(alias))}(?![a-z0-9])")
                spans.extend(m.span() for m in pattern.finditer(text))
        return _mask(text, spans)

    headline_no_hw = mask_hardware(headline)
    body_no_hw = mask_hardware(body) if body else ""

    colours = colour_vocab()
    colour_term = colours.find_first(headline_no_hw) or (
        colours.find_first(body_no_hw) if body_no_hw else None
    )

    leathers = leather_vocab()
    headline_leather = leathers.find_first(headline)
    leather_term = headline_leather or (leathers.find_first(body) if body else None)

    # Resolve the *stated* size before anything is inferred: a Mini Kelly defaults to
    # 20cm, and treating that default as evidence would admit every Mini Kelly charm.
    headline_size = detect_size(headline, match, infer_mini=False)
    stated_size = headline_size
    if stated_size is None and body:
        stated_size = detect_size(body, match, infer_mini=False)

    # Scope evidence comes from the headline only. A painting's catalogue note can easily
    # contain a bag word, whereas a real bag lot names the model, the leather or a bag
    # noun in its own title.
    if not is_handbag(
        headline,
        match,
        leather=headline_leather.canonical if headline_leather else None,
        stated_size=headline_size,
    ):
        return None

    search_space = f"{headline} {body}".strip()
    size_cm = stated_size if stated_size is not None else detect_size(headline, match)

    editions = _detect_editions(search_space)
    construction = _detect_construction(search_space)
    stamp_year = _detect_stamp_year(headline_raw) or _detect_stamp_year(description or "")
    grade_match = _GRADE_RE.search(description or "") or _GRADE_RE.search(headline_raw)

    resolved = {
        "size_cm": size_cm,
        "leather": leather_term.canonical if leather_term else None,
        "colour": colour_term.canonical if colour_term else None,
        "hardware": hardware_term.canonical if hardware_term else None,
    }
    spec = FAMILY_SPECS[match.family]
    scored = [f for f in _SCORED_FIELDS if not (f == "size_cm" and spec.sizeless)]
    unresolved = tuple(f for f in scored if resolved[f] is None)
    completeness = round(1.0 - len(unresolved) / len(scored), 4) if scored else 1.0

    return BagAttributes(
        family=match.family,
        size_cm=size_cm,
        size_bucket=size_bucket(match.family, size_cm),
        leather=leather_term.canonical if leather_term else None,
        leather_category=leather_term.group if leather_term else None,
        colour=colour_term.canonical if colour_term else None,
        colour_family=colour_term.group if colour_term else None,
        colour_hex=colour_term.hex_colour if colour_term else None,
        hardware=hardware_term.canonical if hardware_term else None,
        hardware_abbrev=hardware_term.abbrev if hardware_term else None,
        construction=construction,
        special_editions=editions,
        stamp_year=stamp_year,
        condition_grade=grade_match.group(1) if grade_match else None,
        completeness=completeness,
        unresolved=unresolved,
        resolved_by="rules",
    )
