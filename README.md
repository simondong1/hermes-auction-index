# Hermès Auction Index

A reproducible dataset and published visual index of **Hermès Birkin and Kelly auction
results** from the world's major auction houses, covering the last five years, with every
price converted to **CNY at the exchange rate on the day of the sale**.

The site organises results the way collectors actually shop:

```
Bag type  →  Size  →  Individual bag (leather × colour × hardware)
```

- **Mini Kelly** — 20 (and vintage 15)
- **Kelly Pochette** — one size
- **Birkin** — 25, 30, 35 (plus 20 / 40 and other sizes)
- **Kelly** — 25, 28, 32, 35 (plus 40 and other sizes)

> **On "Kelly 30":** Hermès has never produced one. The Kelly ladder steps
> 25 → 28 → 32 → 35. The site says so explicitly rather than silently returning nothing.

## Sources

| House | Realised prices | Notes |
|---|---|---|
| Christie's | public, premium-inclusive | Global cross-sale search gateway |
| Sotheby's | public except ~4% withheld | Algolia index + GraphQL gateway |
| Bonhams | public, hammer + premium | Typesense search proxy |
| Artcurial | public, hammer + premium | Hermès Vintage / Luxury Bags series |
| Poly Auction HK | public, hammer + premium | Handbag salerooms, HKD |
| Heritage Auctions | public once signed in | Browser-assisted harvest — see below |

Every record comes from the auction house's own published results. No aggregator, no
resale platform, and **no price is ever inferred**. A lot whose result the house declines
to publish is recorded as `result_hidden`, excluded from every median and from the
sell-through denominator — never counted as unsold.

[`docs/sources.md`](docs/sources.md) documents the access path, coverage and known
limitations of each house, including the four we deliberately skipped and why.

### Heritage: browser-assisted

Heritage has the largest Hermès handbag archive of any house (~1,300–1,600 in-scope lots for
this window) and is the only source that cannot be harvested by an HTTP client. Prices are
omitted server-side for anonymous visitors, and DataDome blocks non-browser clients —
including automated ones, which it fingerprints and 403s after a few pages regardless of
pacing.

The adapter and a checkpointed in-page harvester are both built and tested. To fill it in,
paste `scripts/heritage-browser-harvest.js` into the DevTools console of a signed-in
`ha.com` tab in your normal browser:

```js
await HeritageHarvest.runAll();   // ~24 pages at the 15s crawl delay robots.txt asks for
HeritageHarvest.copy();           // JSON to clipboard
```

Save it to `data/heritage-browser-dump.json`, then `harvest --house heritage` and `build`.
It checkpoints to `localStorage` after every page, so a block costs one page, not the run.
Full detail in [`docs/sources.md`](docs/sources.md).

## Layout

| Path | What lives there |
|---|---|
| `src/hermes_auction/` | Harvest → normalise → export pipeline (Python 3.12) |
| `src/hermes_auction/sources/` | One adapter per auction house, behind a shared protocol |
| `src/hermes_auction/data/` | Extensible leather / colour / hardware vocabularies (JSON) |
| `site/` | The published front end (Vite + React + TypeScript + Tailwind) |
| `data/raw/` | Immutable per-source harvest snapshots — the audit trail |
| `data/processed/` | Normalised dataset the site consumes, plus a coverage report |

## Quick start

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"

# Pull raw snapshots for the trailing five years
uv run hermes-auction harvest --since 2021-07-01

# Parse attributes, convert to CNY, write data/processed/dataset.json
uv run hermes-auction build

# Which colour and leather names did the vocabulary not recognise?
uv run hermes-auction gaps

# Optional: have an LLM propose vocabulary additions for review
uv run hermes-auction enrich

# Serve the site
cp data/processed/dataset.json site/public/
cd site && npm install && npm run dev
```

## How it stays current

`.github/workflows/publish.yml` re-harvests every Monday, rebuilds the dataset, commits
the refreshed snapshots and redeploys the site. New sales appear without anyone touching
the repository.

Adding a **new auction house** is one module plus one import — see
[`docs/adding-a-source.md`](docs/adding-a-source.md), which also records how each existing
source was reverse-engineered so the next one goes faster.

## Design principles

1. **Never invent a price.** Withheld results stay withheld and are counted separately.
   `RawLot` refuses to validate a lot marked sold with no realised price.
2. **Raw snapshots are immutable.** Re-normalising never touches the network, so any
   published figure traces back to the bytes the house served.
3. **Adding a house is adding one file.** Adapters implement a single protocol and
   register themselves.
4. **Vocabularies are data, not code.** New Hermès colours are a JSON edit. An optional
   LLM pass *proposes* additions for review — it is never allowed near a price, a date or
   an outcome, because a hallucinated colour costs one mislabelled facet whereas a
   hallucinated price would corrupt the dataset.
5. **Scope is enforced in one place.** "Kelly" is also a surname (Ellsworth Kelly), a
   jewellery line and several NBA players; the parser is the single gate that decides what
   is a handbag, and it is tested against real catalogue titles.

## Quality gates

```bash
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy                  # strict
uv run pytest --cov=hermes_auction
cd site && npx tsc --noEmit && npm run build
```

## Licence

MIT for the code. The underlying auction records remain the property of the respective
auction houses and are collected here for personal research use. Bag names and trade marks
belong to Hermès International.
