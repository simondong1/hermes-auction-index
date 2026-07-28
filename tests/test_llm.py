"""The LLM enrichment pass.

These tests care about one thing above all: the model must only ever be able to propose
*vocabulary*, and its output must be validated before it is written anywhere.
"""

from __future__ import annotations

import json

import pytest

from hermes_auction.parsing.llm import (
    LlmProvider,
    Proposals,
    _extract_json,
    build_prompt,
    propose_additions,
    resolve_provider,
    write_proposals,
)

GAPS = {"colour": [("vanille", 12), ("garance", 7), ("zzz", 1)], "leather": [("sikkim", 4)]}


class StubProvider(LlmProvider):
    """Returns a canned reply and records the prompt it was given."""

    name = "stub"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompt: str | None = None

    @staticmethod
    def available() -> bool:
        return True

    def complete(self, prompt: str) -> str:
        self.prompt = prompt
        return self.reply


VALID_REPLY = json.dumps(
    {
        "colours": [
            {
                "canonical": "Vanille",
                "family": "Black & White",
                "hex": "#f0e2c0",
                "aliases": ["vanille", "vanilla"],
                "confidence": 0.9,
                "reasoning": "Hermès cream shade.",
            }
        ],
        "materials": [
            {
                "canonical": "Sikkim",
                "category": "calfskin",
                "aliases": ["sikkim"],
                "confidence": 0.8,
                "reasoning": "Hermès calfskin.",
            }
        ],
        "rejected": ["zzz"],
    }
)


def test_prompt_contains_the_gap_terms_and_the_known_vocabulary():
    prompt = build_prompt([("vanille", 12), ("garance", 7)])
    assert "vanille (12 lots)" in prompt
    assert "garance (7 lots)" in prompt
    # The model must be told what already exists so it cannot re-propose it.
    assert "Already known colours:" in prompt
    assert "Togo" in prompt
    assert "Noir" in prompt


def test_terms_below_the_threshold_are_not_sent():
    provider = StubProvider(VALID_REPLY)
    propose_additions(GAPS, provider=provider, min_lots=5)
    assert provider.prompt is not None
    assert "vanille" in provider.prompt
    assert "garance" in provider.prompt
    # "zzz" (1 lot) and "sikkim" (4 lots) fall below min_lots=5.
    assert "zzz" not in provider.prompt
    assert "sikkim" not in provider.prompt


def test_a_valid_reply_becomes_validated_proposals():
    proposals = propose_additions(GAPS, provider=StubProvider(VALID_REPLY), min_lots=1)
    assert proposals is not None
    assert proposals.provider == "stub"
    assert [c.canonical for c in proposals.colours] == ["Vanille"]
    assert [m.canonical for m in proposals.materials] == ["Sikkim"]
    assert proposals.rejected == ["zzz"]


def test_a_malformed_hex_is_rejected_rather_than_written():
    bad = json.dumps(
        {
            "colours": [
                {
                    "canonical": "Nope",
                    "family": "Blue",
                    "hex": "not-a-colour",
                    "aliases": ["nope"],
                    "confidence": 0.5,
                }
            ],
            "materials": [],
            "rejected": [],
        }
    )
    with pytest.raises(ValueError, match="hex"):
        propose_additions(GAPS, provider=StubProvider(bad), min_lots=1)


def test_no_gaps_means_no_call_at_all():
    provider = StubProvider(VALID_REPLY)
    assert propose_additions({}, provider=provider) is None
    assert provider.prompt is None


@pytest.mark.parametrize(
    "reply",
    [
        '{"colours": [], "materials": [], "rejected": []}',
        '```json\n{"colours": [], "materials": [], "rejected": []}\n```',
        'Sure! Here you go:\n{"colours": [], "materials": [], "rejected": []}\nHope that helps.',
    ],
)
def test_json_is_extracted_from_fences_and_surrounding_prose(reply):
    assert _extract_json(reply) == {"colours": [], "materials": [], "rejected": []}


def test_a_reply_with_no_json_raises():
    with pytest.raises(ValueError, match="no JSON object"):
        _extract_json("I could not classify any of those terms.")


def test_proposals_are_written_for_review_not_merged(tmp_path):
    """The vocabularies themselves must be untouched by the enrichment pass."""
    proposals = Proposals.model_validate({**json.loads(VALID_REPLY), "provider": "stub"})
    destination = write_proposals(proposals, tmp_path)

    assert destination.name == "vocab-proposals.json"
    written = json.loads(destination.read_text("utf-8"))
    assert written["colours"][0]["canonical"] == "Vanille"
    # Nothing else was created; in particular no vocabulary file was rewritten.
    assert [p.name for p in tmp_path.iterdir()] == ["vocab-proposals.json"]


def test_an_unknown_provider_name_is_rejected():
    with pytest.raises(ValueError, match="unknown provider"):
        resolve_provider("definitely-not-a-provider")


def test_resolve_provider_returns_none_when_nothing_is_configured(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert resolve_provider() is None


def test_propose_additions_is_a_no_op_without_a_provider(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert propose_additions(GAPS, min_lots=1) is None
