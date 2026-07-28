"""Parser tests, driven by titles copied verbatim from real auction catalogues.

Each house writes in a different register, so the corpus below deliberately mixes
Christie's block capitals, Sotheby's title case and Heritage's descriptive prose.
"""

from __future__ import annotations

import pytest

from hermes_auction.parsing.attributes import (
    detect_family,
    detect_size,
    parse_attributes,
    strip_boilerplate,
)
from hermes_auction.taxonomy import BagFamily, normalise

# title, subtitle, description, expected (family, size, colour, leather, hardware)
IN_SCOPE: list[
    tuple[str, str | None, str | None, tuple[BagFamily, int | None, str, str, str | None]]
] = [
    (
        "Craie Epsom Birkin Sellier 25 Gold Hardware, 2022",
        None,
        None,
        (BagFamily.BIRKIN, 25, "Craie", "Epsom", "Gold"),
    ),
    (
        "A GOLD TOGO LEATHER BIRKIN 30 WITH GOLD HARDWARE",
        "HERMÈS, 2021",
        None,
        (BagFamily.BIRKIN, 30, "Gold", "Togo", "Gold"),
    ),
    (
        "HERMÈS | BLEU MARINE BIRKIN 35CM IN TOGO LEATHER WITH PALLADIUM HARDWARE",
        None,
        None,
        (BagFamily.BIRKIN, 35, "Bleu Marine", "Togo", "Palladium"),
    ),
    (
        "A ROUGE H BOX LEATHER KELLY SELLIER 28 WITH GOLD HARDWARE",
        "HERMÈS, 2019",
        "includes dust bag and box",
        (BagFamily.KELLY, 28, "Rouge H", "Box Calf", "Gold"),
    ),
    (
        "Etoupe Clemence Kelly Retourne 32 Palladium Hardware, 2018",
        None,
        None,
        (BagFamily.KELLY, 32, "Etoupe", "Clemence", "Palladium"),
    ),
    (
        "Vert Cypres Chevre Mysore Mini Kelly 20 II Palladium Hardware, 2023",
        None,
        None,
        (BagFamily.MINI_KELLY, 20, "Vert Cypres", "Chevre", "Palladium"),
    ),
    (
        "A NOIR SWIFT KELLY POCHETTE WITH PALLADIUM HARDWARE",
        "HERMÈS, 2020",
        None,
        (BagFamily.KELLY_POCHETTE, None, "Noir", "Swift", "Palladium"),
    ),
    (
        "Hermes 25cm Gris Meyer Togo Leather Birkin Bag with Palladium Hardware",
        None,
        None,
        (BagFamily.BIRKIN, 25, "Gris Meyer", "Togo", "Palladium"),
    ),
    (
        "A MATTE WHITE HIMALAYA NILOTICUS CROCODILE DIAMOND BIRKIN 30",
        "HERMÈS, 2014",
        "GRADE: 1",
        (BagFamily.BIRKIN, 30, "Himalaya", "Niloticus Crocodile", "White Gold & Diamond"),
    ),
]

OUT_OF_SCOPE = [
    "A SHINY ROUGE BRAISE POROSUS CROCODILE KELLY CUT WITH GOLD HARDWARE",
    "A BLACK TOGO KELLY DANSE WITH PALLADIUM HARDWARE",
    "An Etoupe Clemence Kelly Doll bag",
    "A NOIR EPSOM KELLY DEPECHES 38 BRIEFCASE",
    "A BLEU NUIT TOGO EVELYNE III 29 WITH PALLADIUM HARDWARE",
    "A GOLD BARENIA CONSTANCE 24 WITH GOLD HARDWARE",
    "HERMÈS A silk twill scarf, Brides de Gala",
    "A MICRO KELLY BAG CHARM IN ROSE SAKURA",
]


@pytest.mark.parametrize(("title", "subtitle", "description", "expected"), IN_SCOPE)
def test_parses_real_catalogue_titles(title, subtitle, description, expected):
    attributes = parse_attributes(title, subtitle, description)
    assert attributes is not None, title
    family, size, colour, leather, hardware = expected
    assert attributes.family is family
    assert attributes.size_cm == size
    assert attributes.colour == colour
    assert attributes.leather == leather
    assert attributes.hardware == hardware


@pytest.mark.parametrize("title", OUT_OF_SCOPE)
def test_rejects_out_of_scope_lots(title):
    assert parse_attributes(title) is None


def test_gold_hardware_does_not_become_the_colour_gold():
    """The single most likely false positive in the whole vocabulary."""
    attributes = parse_attributes("A ROUGE H TOGO BIRKIN 30 WITH GOLD HARDWARE")
    assert attributes is not None
    assert attributes.colour == "Rouge H"
    assert attributes.hardware == "Gold"


def test_gold_colour_still_resolves_alongside_gold_hardware():
    attributes = parse_attributes("A GOLD TOGO BIRKIN 25 WITH GOLD HARDWARE")
    assert attributes is not None
    assert attributes.colour == "Gold"
    assert attributes.hardware == "Gold"


def test_packaging_box_is_not_box_calf():
    """'includes ... box and ribbon' must not resolve the leather to Box Calf."""
    attributes = parse_attributes(
        "A VERT AMANDE TOGO BIRKIN 25 WITH GOLD HARDWARE",
        "HERMÈS, 2023",
        "GRADE: 1<br> includes clochette, lock, keys, dustbag, box and ribbon",
    )
    assert attributes is not None
    assert attributes.leather == "Togo"


def test_kelly_20_is_classified_as_a_mini_kelly():
    attributes = parse_attributes("Noir Chevre Kelly Sellier 20 Palladium Hardware, 2022")
    assert attributes is not None
    assert attributes.family is BagFamily.MINI_KELLY
    assert attributes.size_cm == 20


def test_kelly_pochette_beats_the_plain_kelly_match():
    match = detect_family(normalise("A NOIR SWIFT KELLY POCHETTE"))
    assert match is not None
    assert match.family is BagFamily.KELLY_POCHETTE


def test_horseshoe_stamp_is_captured_as_an_edition():
    attributes = parse_attributes(
        "Black Swift and Orange Poppy HSS Kelly Pochette Palladium Hardware, 2023"
    )
    assert attributes is not None
    assert "Horseshoe Stamp (HSS)" in attributes.special_editions


def test_special_editions_and_construction():
    attributes = parse_attributes(
        "A NOIR TOGO BIRKIN CARGO 25 SELLIER WITH PALLADIUM HARDWARE", "HERMÈS, 2022"
    )
    assert attributes is not None
    assert attributes.construction == "Sellier"
    assert "Cargo" in attributes.special_editions


def test_completeness_and_unresolved_are_reported_not_guessed():
    attributes = parse_attributes("A BIRKIN 30")
    assert attributes is not None
    assert attributes.colour is None
    assert attributes.leather is None
    assert set(attributes.unresolved) == {"colour", "leather", "hardware"}
    assert attributes.completeness == pytest.approx(0.25)


def test_kelly_pochette_is_sizeless_and_never_scored_on_size():
    attributes = parse_attributes("A NOIR SWIFT KELLY POCHETTE WITH PALLADIUM HARDWARE")
    assert attributes is not None
    assert attributes.size_bucket == "One size"
    assert "size_cm" not in attributes.unresolved


def test_stamp_year_takes_the_trailing_year():
    attributes = parse_attributes(
        "Craie Epsom Birkin 25 Gold Hardware", "HERMÈS, 2022", "Produced in 2022"
    )
    assert attributes is not None
    assert attributes.stamp_year == 2022


def test_size_outside_the_ladder_is_ignored_rather_than_accepted():
    match = detect_family(normalise("a togo birkin 33"))
    assert match is not None
    assert detect_size(normalise("a togo birkin 33"), match) is None


def test_strip_boilerplate_removes_cites_and_accessory_prose():
    cleaned = strip_boilerplate(
        "A BIRKIN 25. includes clochette, lock, keys. Crocodylus porosus, Annexe CITES II-B"
    )
    assert "clochette" not in cleaned.lower()
    assert "cites" not in cleaned.lower()
    assert "BIRKIN 25" in cleaned


def test_accented_and_unaccented_forms_are_equivalent():
    with_accent = parse_attributes("A VERT CYPRÈS CHÈVRE KELLY 25 WITH GOLD HARDWARE")
    without = parse_attributes("A VERT CYPRES CHEVRE KELLY 25 WITH GOLD HARDWARE")
    assert with_accent is not None and without is not None
    assert with_accent.colour == without.colour == "Vert Cypres"
    assert with_accent.leather == without.leather == "Chevre"


# --- scope: "Kelly" is a surname and a jewellery line, not only a handbag ----------

KELLY_FALSE_POSITIVES = [
    "ELLSWORTH KELLY (1923-2015)",
    "Grace Kelly",
    "Ned Kelly at Glenrowan",
    "Kelly Olynyk San Antonio Spurs 2026 NBA Finals Game Issued Shorts",
    "A SET OF TWO: 18K ROSE GOLD & DIAMOND KELLY BRACELET & GALOP RING",
    "Diamond 'Kelly' Bangle",
    "Set of Three Steel and Diamond Quartz Kelly Watch",
    "Silver Kelly Gourmette Bracelet Very Small Model",
    "A RARE, STERLING SILVER MINI KELLY",
]


@pytest.mark.parametrize("title", KELLY_FALSE_POSITIVES)
def test_kelly_named_non_handbags_are_rejected(title):
    assert parse_attributes(title) is None, title


KELLY_TRUE_POSITIVES_WITHOUT_LEATHER = [
    # A stated ladder size is enough evidence on its own.
    ("A KELLY 25 WITH GOLD HARDWARE", 25),
    # So is an explicit bag noun.
    ("A NOIR KELLY HANDBAG WITH PALLADIUM HARDWARE", None),
    ("Vert Amande Kelly Sellier Palladium Hardware, 2023", None),
]


@pytest.mark.parametrize(("title", "size"), KELLY_TRUE_POSITIVES_WITHOUT_LEATHER)
def test_kelly_handbags_survive_the_scope_gate(title, size):
    attributes = parse_attributes(title)
    assert attributes is not None, title
    assert attributes.size_cm == size


def test_birkin_needs_no_extra_evidence_because_nothing_else_is_called_birkin():
    assert parse_attributes("BIRKIN 30") is not None


NON_BAG_OBJECTS = [
    "HERMÈS: SILVER MINI BIRKIN AMULETTE PENDANT NECKLACE",
    "A ROSE GOLD AND DIAMOND BIRKIN CHARM",
    "Black Tadelakt Micro Mini Twilly Kelly Bag Charm Palladium Hardware, 2021",
    "AN 18K ROSE GOLD AND DIAMOND KELLY CLOCHETTE WATCH",
    "A SILK TWILLY AND BIRKIN 25 KEYRING",
]


@pytest.mark.parametrize("title", NON_BAG_OBJECTS)
def test_birkin_and_kelly_named_jewellery_is_rejected(title):
    """Hermès sells Birkin-shaped necklaces. They are not Birkins."""
    assert parse_attributes(title) is None, title


ARTIST_ATTRIBUTIONS = [
    "ELLSWORTH KELLY (1923-2015)",
    "John Melville Kelly (1879-1962); Old Hawaii (Outrigger);",
    "John Melville Kelly (1879-1962) Leilani on the Beach 20 1/2 x 15 1/8 in.",
    "Kelly Reichardt (b. 1964)",
]


@pytest.mark.parametrize("title", ARTIST_ATTRIBUTIONS)
def test_artist_lifespan_in_the_title_is_never_a_handbag(title):
    """Several painters are surnamed Kelly; no Hermès lot title carries a lifespan."""
    assert parse_attributes(title) is None, title


def test_generic_french_colour_words_resolve_but_lose_to_specific_names():
    generic = parse_attributes("HERMÈS 2015 Sac BIRKIN 35 Veau Togo taupe Garniture palladié")
    assert generic is not None
    assert generic.colour == "Taupe"
    assert generic.leather == "Togo"
    assert generic.hardware == "Palladium"

    # A specific name must still beat the generic alias it contains.
    specific = parse_attributes("A BLEU NUIT TOGO BIRKIN 30 WITH PALLADIUM HARDWARE")
    assert specific is not None
    assert specific.colour == "Bleu Nuit"


def test_description_alone_cannot_admit_a_lot():
    """A sculpture whose note mentions an included Kelly is not a Kelly sale."""
    assert (
        parse_attributes(
            "JEFF KOONS",
            None,
            "<i>Kelly Bag Ivory (Shelf), 2014</i> Handbag, mirror and acrylic. "
            "Includes a Hermès Kelly with lock, keys, clochette, strap.",
        )
        is None
    )


def test_model_named_only_in_the_subtitle_is_out_of_scope():
    """Measured across the whole corpus, this pattern is always an artwork."""
    assert parse_attributes("JEFF KOONS", "Kelly Bag Ivory (Shelf), 2014") is None
    assert parse_attributes("SIR SIDNEY NOLAN, O.M., R.A.", "Kelly in Swamp") is None
    assert parse_attributes("WEEGEE", "Kiddy Kelly, c. 1940s") is None


def test_subtitle_still_fills_attributes_for_a_real_bag():
    """Christie's puts the maker and stamp year in the subtitle; that must still be read."""
    attributes = parse_attributes(
        "A GOLD TOGO LEATHER BIRKIN 30 WITH GOLD HARDWARE", "HERMÈS, 2021"
    )
    assert attributes is not None
    assert attributes.stamp_year == 2021
    assert attributes.colour == "Gold"


def test_electrum_hardware_and_french_plating_phrases_resolve():
    electrum = parse_attributes("Craie Epsom Kelly 25 Sellier Electrum Hardware, 2025")
    assert electrum is not None
    assert electrum.hardware == "Electrum"

    french = parse_attributes("HERMÈS 2015 Sac BIRKIN 35 Veau Togo noir Garniture métal plaqué or")
    assert french is not None
    assert french.hardware == "Gold"
    assert french.colour == "Noir"


def test_catalogue_typos_still_resolve():
    """Real Christie's and Poly titles contain these misspellings."""
    typo = parse_attributes("A BLACK CLÉMENCE LEATHER BIRKIN 30 WITH GOLD HARDARE")
    assert typo is not None and typo.hardware == "Gold"

    poly = parse_attributes(
        "A LIMITED EDITION BLUE CELESTE EPSOM LEATHER BIRKIN 25 WITH PALLDAIUM HARDWARE"
    )
    assert poly is not None and poly.hardware == "Palladium"


def test_silver_kelly_pill_box_is_not_a_handbag():
    assert parse_attributes("HERMÈS: SILVER KELLY PILL BOX 40 GRAMS") is None
