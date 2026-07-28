# Adding an auction house

Adding a house is one new module plus one line in `sources/__init__.py`. Nothing else in
the pipeline changes: normalisation, scope filtering, attribute parsing, FX conversion and
the published site all work off `RawLot`.

## 1. Find the data path

Every one of the five live sources was cracked the same way, and it is worth trying the
steps in order — the first one that works is usually the best one.

1. **Fetch a past-results or category page with a realistic desktop User-Agent.** Several
   houses 403 a default client but serve a browser identity happily.
2. **Grep the HTML for an embedded JSON blob.** In practice one of these hits:
   - `__NEXT_DATA__` — Next.js. Christie's config, Bonhams' Typesense key and Sotheby's
     Algolia key all live here.
   - `window.<something> = {...}` — Christie's lot lists arrive this way
     (`window.chrComponents.lots`).
   - `application/ld+json`, `__INITIAL_STATE__`, `apolloCache`.
3. **Look for a search backend.** An Algolia app id, a Typesense host, a GraphQL gateway
   or an `/api/...` path. These are the good outcomes: they give cross-sale search, so
   coverage is complete by construction instead of depending on guessing which sales might
   have contained a Birkin.
4. **If the blob is absent, watch the network.** Load the page in a browser and read
   `performance.getEntriesByType('resource')` for XHR URLs. This is how Christie's
   `datasourceId` parameter was found — without it the gateway returns a bare
   `404 Resource not found` with no hint that a parameter is missing.
5. **Confirm realised prices are actually public.** This is the step that decides whether
   a house is usable at all. Heritage passes every other test and still fails here; see
   [`sources.md`](sources.md).

## 2. Write the adapter

```python
from typing import ClassVar

from hermes_auction.models import AuctionHouse, LotOutcome, PriceBasis, RawLot, SaleRef
from hermes_auction.sources.base import AuctionSource, parse_date, parse_money, register


@register
class MyHouseSource(AuctionSource):
    house: ClassVar[AuctionHouse] = AuctionHouse.MY_HOUSE
    coverage_note: ClassVar[str] = "One sentence rendered in the site's methodology panel."

    def iter_lots(self, since, until):
        for row in self._fetch(since, until):
            yield RawLot(...)
```

Then add `AuctionHouse.MY_HOUSE` to the enum, a display name to `HOUSE_DISPLAY_NAMES`, and
import the module in `sources/__init__.py`.

### Rules an adapter must follow

- **Never fabricate a price.** If the house ran the lot but withheld the number, emit
  `LotOutcome.RESULT_HIDDEN` with `price_realised=None`. Do not fall back to the estimate,
  and do not record it as unsold — those are different facts and the site reports them
  differently.
- **Declare the price basis.** `PriceBasis.PREMIUM_INCLUSIVE` and `PriceBasis.HAMMER` must
  not be mixed silently. Where a house publishes both, prefer premium-inclusive, because
  that is what Christie's and Sotheby's publish.
- **Be generous about what you yield.** Do not try to decide whether a lot is a Birkin.
  Yield anything plausibly a handbag and let `parse_attributes` enforce scope — it is
  tested against real catalogue titles and it is the only place that decision lives.
- **Re-harvest keys, don't pin them.** Every embedded key observed so far rotates. Harvest
  at run time with a pinned fallback and a warning, as `christies.py` and `bonhams.py` do.
- **Assert row counts when a filter could be ignored.** Poly's API ignores unknown query
  parameters instead of rejecting them, and will hand back its entire 31,590-lot database
  while looking like a successful filtered response. `poly_hk.py` raises rather than
  attributing that to one sale.

## 3. Test it

Add a fixture row copied verbatim from the live API to `tests/test_sources.py` and assert
the `RawLot` mapping — especially currency, the price basis, and that a missing price does
not become a sale. `test_christies_row_maps_onto_a_raw_lot` is the pattern to follow.

If the house writes titles in a register the vocabulary has not seen, add a couple of real
titles to the corpus in `tests/test_attributes.py`.

## 4. Run it

```bash
uv run hermes-auction harvest --house my_house --since 2021-07-01
uv run hermes-auction build
uv run hermes-auction gaps            # any new colour or leather names to add?
```

`gaps` is the feedback loop: it ranks the words the vocabulary could not name, so a house
with unfamiliar nomenclature surfaces immediately instead of quietly producing lots with
`colour: null`. Either add the terms to `src/hermes_auction/data/*.json` by hand, or run
`hermes-auction enrich` to have an LLM propose them for review.

## Extending the attribute vocabulary

Hermès introduces colours every season, so the vocabularies in
`src/hermes_auction/data/` are data rather than code and are expected to grow.

`hermes-auction enrich` sends only the *unnamed terms* to a model and asks it to classify
them into the existing schema. It writes proposals to
`data/processed/vocab-proposals.json` for review and never merges them, and never touches
a price, a date or an outcome. That boundary is deliberate: a hallucinated colour costs one
mislabelled facet, whereas a hallucinated price would corrupt the dataset.

Providers are tried in order — the Cursor CLI (`cursor-agent`, no API key needed), then
`ANTHROPIC_API_KEY`, then `OPENAI_API_KEY`. With none configured the command explains what
to install instead of failing.
