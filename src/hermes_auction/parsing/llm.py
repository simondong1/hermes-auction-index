"""Optional LLM pass that proposes vocabulary additions.

Hermès introduces new colours every season and revives old leathers without notice, so
a hand-maintained vocabulary decays. This module closes that loop without letting a
model anywhere near the numbers:

1. :func:`~hermes_auction.pipeline.find_vocabulary_gaps` collects the unnamed terms that
   actually appear in lots whose colour or leather could not be resolved.
2. A model is asked to classify **only those terms** into the existing schema.
3. Proposals are written to disk for review. They are never merged automatically and
   never touch a price, a date or an outcome.

That boundary is the point. A hallucinated colour name costs one mislabelled facet; a
hallucinated price would corrupt the dataset, so prices stay rules-only.

Providers are resolved from the environment, so the pass is a no-op on a machine with no
credentials rather than a hard failure.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from pydantic import BaseModel, Field

from hermes_auction.taxonomy import colour_vocab, leather_vocab

logger = logging.getLogger(__name__)

_COLOUR_FAMILIES: Final = (
    "Black & White",
    "Tan & Brown",
    "Grey & Taupe",
    "Red & Burgundy",
    "Pink & Purple",
    "Orange & Yellow",
    "Blue",
    "Green",
    "Special",
)

_MATERIAL_CATEGORIES: Final = (
    "calfskin",
    "goatskin",
    "bull-calf",
    "exotic",
    "suede",
    "canvas",
    "wicker",
    "other",
)


class ColourProposal(BaseModel):
    """A proposed addition to ``data/colours.json``."""

    canonical: str
    family: str = Field(description=f"One of: {', '.join(_COLOUR_FAMILIES)}")
    hex: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    aliases: list[str]
    confidence: float = Field(ge=0, le=1)
    reasoning: str = ""


class MaterialProposal(BaseModel):
    """A proposed addition to ``data/leathers.json``."""

    canonical: str
    category: str = Field(description=f"One of: {', '.join(_MATERIAL_CATEGORIES)}")
    aliases: list[str]
    confidence: float = Field(ge=0, le=1)
    reasoning: str = ""


class Proposals(BaseModel):
    """Everything a single enrichment run suggested, plus how it was produced."""

    provider: str
    colours: list[ColourProposal] = Field(default_factory=list)
    materials: list[MaterialProposal] = Field(default_factory=list)
    #: Terms the model declined to classify. Useful signal - often not Hermès terms.
    rejected: list[str] = Field(default_factory=list)


_PROMPT = """\
You are extending the reference vocabulary of a Hermès handbag auction dataset.

The rules-based parser could not name the terms below. They were extracted from real \
auction catalogue titles for Birkin and Kelly bags, so each is *probably* a Hermès \
colour name, a leather/material name, or neither (a condition word, a place, a \
consignor's name, a stray adjective).

Classify each term into exactly one of three outcomes:
  - a Hermès COLOUR      -> add to `colours`
  - a Hermès MATERIAL    -> add to `materials`
  - neither              -> add the term to `rejected`

Rules you must follow:
  - Only propose a term you actually recognise as Hermès nomenclature. When unsure, \
reject it. A missing colour is harmless; a wrong one mislabels real sales.
  - `canonical` uses Hermès' own spelling without accents (e.g. "Vert Cypres", not \
"Vert Cyprès").
  - `aliases` must include the term as given, plus accented and English variants that \
appear in catalogues.
  - `hex` is your best approximation of the actual leather colour.
  - `family` must be exactly one of: {colour_families}
  - `category` must be exactly one of: {material_categories}
  - Never propose a term that already exists in the known vocabulary listed below.

Already known colours: {known_colours}

Already known materials: {known_materials}

Unnamed terms, with the number of lots each appears in:
{terms}

Reply with JSON only, matching this shape exactly:
{{"colours": [{{"canonical": "...", "family": "...", "hex": "#rrggbb", \
"aliases": ["..."], "confidence": 0.0, "reasoning": "..."}}],
 "materials": [{{"canonical": "...", "category": "...", "aliases": ["..."], \
"confidence": 0.0, "reasoning": "..."}}],
 "rejected": ["..."]}}
"""


def build_prompt(terms: Sequence[tuple[str, int]]) -> str:
    """Render the classification prompt for a set of ``(term, lot_count)`` pairs."""
    return _PROMPT.format(
        colour_families=", ".join(_COLOUR_FAMILIES),
        material_categories=", ".join(_MATERIAL_CATEGORIES),
        known_colours=", ".join(sorted(t.canonical for t in colour_vocab().terms)),
        known_materials=", ".join(sorted(t.canonical for t in leather_vocab().terms)),
        terms="\n".join(f"  - {term} ({count} lots)" for term, count in terms),
    )


class LlmProvider(ABC):
    """Anything that can turn a prompt into a JSON string."""

    name: str

    @abstractmethod
    def complete(self, prompt: str) -> str: ...

    @staticmethod
    @abstractmethod
    def available() -> bool: ...


class CursorAgentProvider(LlmProvider):
    """Shells out to the Cursor CLI, which carries its own authentication.

    Preferred on a developer machine: no API key to manage, and the model is whatever
    the user is already paying for.
    """

    name = "cursor-agent"

    @staticmethod
    def available() -> bool:
        return shutil.which("cursor-agent") is not None

    def complete(self, prompt: str) -> str:
        result = subprocess.run(
            [shutil.which("cursor-agent") or "cursor-agent", "-p", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            msg = f"cursor-agent exited {result.returncode}: {result.stderr[:300]}"
            raise RuntimeError(msg)
        return result.stdout


class OpenAiProvider(LlmProvider):
    """Uses ``OPENAI_API_KEY`` against the Chat Completions API."""

    name = "openai"
    _MODEL = os.environ.get("HERMES_LLM_MODEL", "gpt-4o-mini")

    @staticmethod
    def available() -> bool:
        return bool(os.environ.get("OPENAI_API_KEY"))

    def complete(self, prompt: str) -> str:
        import httpx

        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json={
                "model": self._MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
            timeout=180,
        )
        response.raise_for_status()
        content: str = response.json()["choices"][0]["message"]["content"]
        return content


class AnthropicProvider(LlmProvider):
    """Uses ``ANTHROPIC_API_KEY`` against the Messages API."""

    name = "anthropic"
    _MODEL = os.environ.get("HERMES_LLM_MODEL", "claude-sonnet-4-5")

    @staticmethod
    def available() -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    def complete(self, prompt: str) -> str:
        import httpx

        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": self._MODEL,
                "max_tokens": 4096,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=180,
        )
        response.raise_for_status()
        text: str = response.json()["content"][0]["text"]
        return text


#: Checked in order. First available wins.
PROVIDERS: Final[tuple[type[LlmProvider], ...]] = (
    CursorAgentProvider,
    AnthropicProvider,
    OpenAiProvider,
)


def resolve_provider(preferred: str | None = None) -> LlmProvider | None:
    """Return the first usable provider, or ``None`` if the machine has none."""
    candidates = PROVIDERS
    if preferred:
        candidates = tuple(p for p in PROVIDERS if p.name == preferred)
        if not candidates:
            msg = f"unknown provider {preferred!r}; expected one of {[p.name for p in PROVIDERS]}"
            raise ValueError(msg)
    for provider in candidates:
        if provider.available():
            return provider()
    return None


def _extract_json(text: str) -> dict[str, object]:
    """Pull the JSON object out of a reply that may be wrapped in prose or fences."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```")[1]
        stripped = stripped.removeprefix("json").strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end == -1:
        msg = f"no JSON object in model reply: {text[:200]!r}"
        raise ValueError(msg)
    parsed: dict[str, object] = json.loads(stripped[start : end + 1])
    return parsed


def propose_additions(
    gaps: dict[str, list[tuple[str, int]]],
    *,
    provider: LlmProvider | None = None,
    min_lots: int = 2,
) -> Proposals | None:
    """Ask a model to name the unresolved terms in ``gaps``.

    Args:
        gaps: The ``vocabulary_gaps`` section of a build report.
        provider: Override provider selection. Defaults to the first available.
        min_lots: Ignore terms appearing on fewer lots than this - the long tail is
            almost entirely typos and one-off consignor prose.

    Returns:
        The proposals, or ``None`` when no provider is configured or nothing qualifies.
    """
    terms = sorted(
        {
            term: count for rows in gaps.values() for term, count in rows if count >= min_lots
        }.items(),
        key=lambda item: -item[1],
    )
    if not terms:
        logger.info("no vocabulary gaps above the %d-lot threshold", min_lots)
        return None

    provider = provider or resolve_provider()
    if provider is None:
        logger.warning(
            "no LLM provider available (install the Cursor CLI, or set ANTHROPIC_API_KEY "
            "/ OPENAI_API_KEY); %d unnamed terms left for manual review",
            len(terms),
        )
        return None

    logger.info("asking %s to classify %d unnamed terms", provider.name, len(terms))
    payload = _extract_json(provider.complete(build_prompt(terms)))
    return Proposals.model_validate({**payload, "provider": provider.name})


def write_proposals(proposals: Proposals, out_dir: Path) -> Path:
    """Persist proposals for review. Deliberately does not edit the vocabularies."""
    out_dir.mkdir(parents=True, exist_ok=True)
    destination = out_dir / "vocab-proposals.json"
    destination.write_text(proposals.model_dump_json(indent=2), "utf-8")
    logger.info(
        "wrote %d colour and %d material proposals to %s (review before merging)",
        len(proposals.colours),
        len(proposals.materials),
        destination,
    )
    return destination
