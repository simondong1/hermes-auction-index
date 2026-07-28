# Sources

Every figure in this project comes from an auction house's own published results. No
aggregator, no resale platform, and no inferred prices.

Each house below was reverse-engineered from its public front end: the browser has to be
able to render the data, so the data is reachable without an account. Where that is *not*
true, it is recorded as a limitation rather than worked around.

## Summary

| House | Status | Access | Realised prices | Approx. in-scope lots, 2021-07 → 2026-07 |
|---|---|---|---|---|
| Christie's | live | Global search gateway (JSON) | public, premium-inclusive | ~4,500 |
| Sotheby's | live | Algolia index + GraphQL gateway | public except ~4% withheld | ~3,600 |
| Bonhams | live | Typesense search proxy | public, hammer + premium | ~900 |
| Artcurial | live | Open `/ace` JSON REST API | public, hammer + premium | ~355 |
| Poly Auction HK | live | PHP API + static client key | public, hammer + premium | ~207 |
| **Heritage Auctions** | **blocked** | metadata reachable; **prices need a free account** | **account-walled** | ~1,300–1,600 |
| Phillips | skipped | excellent API, no handbag department | public | ~0 |
| Julien's | skipped | hard Cloudflare bot challenge | public | unknown |
| Rago / Wright | skipped | `robots.txt` disallows every needed path | unknown | low |
| Doyle | skipped | no past-lot search; brute-force only | public | low tens |

---

## Christie's

**Endpoint.** A single cross-sale search gateway indexes every past lot in every
department, which makes keyword harvesting complete by construction — there is no need to
guess which sales might have contained a Birkin.

```
GET https://apim.christies.com/search-client
    ?keyword=birkin&page=1&is_past_lots=True&sortby=relevance
    &language=en&geocountrycode=US&show_on_loan=true&datasourceId=<uuid>
x-api-key: <key>
Accept: application/vnd.christies.v1+json
source-application: gsrp
```

**Gotcha that costs an hour.** Omitting `datasourceId` returns `404 Resource not found`
from the API gateway — not a helpful validation error. The key *and* the datasource id are
public front-end configuration embedded in `window.chrComponents.searchResults` on any
`/en/search` page, and both are re-harvested at run time rather than pinned.

**Shape.** `{lots: [...20], filters: [...], total_pages: N}`. Prices arrive as formatted
strings (`"HKD 1,500,000"`), so `parse_money` handles the currency prefix. `price_realised`
is premium-inclusive.

**Pagination.** 20 per page, `total_pages` supplied, verified to page 276. About 690
requests covers every Birkin and Kelly they have ever sold.

---

## Sotheby's

Two surfaces are combined because neither is sufficient alone.

**Catalogue index — Algolia.** App `KAR1UEUPJD`, index `prod_lots`. The search-only key
rotates per page load and is harvested from `__NEXT_DATA__.props.pageProps.algoliaSearchKey`
on any `/en/buy/*` browse page; it carries a `validUntil` roughly 3.8 hours out.

**Prices — GraphQL.** `POST https://clientapi.prod.sothelabs.com/graphql`, no auth.
Realised prices are *not* in the Algolia index (`price` is null on essentially every
closed lot).

**Two constraints shape the adapter:**

1. **Pagination cap.** Algolia serves only ~1,337 hits per query regardless of `nbHits`.
   "kelly" matches 3,117 lots, so a naive page walk silently loses half the data. The fix
   is to facet on `auctionId` first and page within each sale, which is exhaustive because
   no single sale approaches the cap.
2. **Batching.** One GraphQL round trip per lot would mean thousands of requests. The
   gateway accepts aliased batches, so lots are priced 20 at a time.

**`auctionState` semantics are counter-intuitive.** `LIVE` does *not* mean live now — it
means a completed live-auctioneer sale. Past results are `LIVE OR CLOSED`; upcoming is
`PUBLISHED`.

**Withheld results.** `bidState.sold` is a union. `ResultVisible` carries
`premiums.finalPriceV2`; `ResultHidden` means Sotheby's has not authorised publication.
Roughly 4% of in-scope lots are withheld. There is no public route to those prices, and
they are recorded as `result_hidden` — never as unsold, and never excluded silently.

**Test records leak into the production index.** Sale names matching `TEST`, `COPY`, `QA`
and similar are filtered client-side.

---

## Bonhams

**Endpoint.** A Typesense collection queried straight from the browser.

```
GET https://api01.bonhams.com/search-proxy/collections/lots-search/documents/search
    ?q=birkin&query_by=title,catalogDesc&per_page=250&page=1
    &filter_by=hammerTime.timestamp:[<unix_from>..<unix_to>]
X-TYPESENSE-API-KEY: <key>
```

The key is harvested from `__NEXT_DATA__.props.runtimeConfig.TYPESENSE`.

**Two price fields, and the difference matters.** `price.hammerPrice` is the hammer;
`price.hammerPremium` is the premium-inclusive total. Only the latter is comparable with
Christie's and Sotheby's.

**`hammerPrice == 0` is ambiguous** — it means unsold *or* results not yet published. The
adapter uses `flags.isResultPublished` to tell those apart rather than assuming unsold.

**Fuzzy matching.** Typesense multi-word queries match loosely, so a search for "birkin"
returns unrelated lots (film scripts, autographs). Those are dropped downstream by the
scope parser rather than by tightening the query, which keeps recall high.

Sale titles live in a sibling `auctions-search` collection, joined on `auctionId`.

---

## Artcurial

The most permissive API of the five: open JSON REST, no key, no page-size cap.

```
GET https://www.artcurial.com/ace/sales/results?page=0&size=3000   # every past sale
GET https://www.artcurial.com/ace/sales/{ref}/items?page=0&size=500
```

Artcurial runs the "Hermès Vintage" (Paris) and "Hermès & Luxury Bags" (Monaco) series
two to four times a year, so the adapter filters the ~2,554-sale list down to the relevant
series and then opens those sales — roughly 30 requests for a complete harvest.

**Field names are not the obvious ones.** The sale identifier is `ref` (e.g. `MC-6028`),
not `reference` or `id`, and the date is `validity.beginDate`. `adjudicationPrice` is the
hammer; `finalPrice` includes the premium.

**The global `/search` endpoint returns HTTP 500 as soon as a second filter is supplied**,
so date filtering happens client-side and the per-sale route is preferred.

Titles are inconsistently formatted (`HERMÈS KELLY sellier 25 Bag` alongside
`HERMÈS - KELLY Sellier Mini 20`), which the vocabulary parser absorbs.

---

## Poly Auction Hong Kong

```
GET https://api.polyauction.com.hk/api/public/front/auction/saleroom/lots/list
    ?page=0&limit=500&saleroom_id=<uid>
Cccrm-Client-Secret-Key: <key>
```

The key is hard-coded in Poly's own JS bundle as `clientSecretKey` and is re-scraped at
run time. Without the header the API returns
`{"status":-1,"code":-9999,"message":"No Client secret key."}`.

**The dangerous behaviour: unknown query parameters are ignored, not rejected.** Passing
`saleroom_uid` instead of `saleroom_id` returns the entire 31,590-lot database while
looking like a successful filtered response. The adapter asserts the returned total
against a plausible per-sale bound and raises `PolyFilterIgnoredError` rather than
attributing 31,590 lots to one sale.

Two further quirks: the confusingly-named `saleroom_id` parameter wants the record's `uid`
field, and the keyword-search path returns only sold lots — so harvesting per saleroom is
what keeps unsold lots visible for sell-through. Currency is absent from the payload;
every Hong Kong saleroom settles in HKD.

The API is genuinely slow (13–31 s on large pulls), which is why the client timeout is 90 s.

---

## Heritage Auctions — blocked on prices

Heritage has the largest Hermès handbag archive of any house here (~2,500 Birkin and
~2,200 Kelly lots all-time, of which roughly 1,300–1,600 fall in our window), so this is
the most significant gap in the dataset.

**What is reachable anonymously:** lot URL, sale and lot number, sale date, title,
description, category and image. Pagination is `page={size}~{page}` with a maximum size of
72, and `auction_year=YYYY` filters by year. There is also a clean JSON facet endpoint at
`/c/webservices/search/guided-navigation-archive.zx` that returns per-year counts.

**What is not:** the realised price. This is confirmed rather than assumed:

1. Every lot row renders `Sold For: [Sign-in] or [Join]`. The number is **omitted
   server-side** — not CSS-hidden, not lazy-loaded, so it cannot be recovered from the
   HTML.
2. Every page carries Google's paywall markup declaring the price element non-free:
   `{"@type":"WebPageElement","isAccessibleForFree":false,"cssSelector":".bot-price-data"}`.
3. `/c/print-prices-realized.zx` 302-redirects to the login page and is `Disallow`ed in
   `robots.txt`.
4. The price-bracketing workaround does not work. The `price_realized` range facet is
   exposed anonymously with bucket counts, but the filter is silently disabled for
   anonymous users — the server echoes it back as `{"value":[]}`. An A/B against a bracket
   that the facet said only 19 lots satisfy returned a byte-identical 72-lot result set.
   The aggregate histogram is learnable; an individual lot's bracket is not.

Access is also gated by **DataDome** behind Cloudflare. Browser-like headers are not
enough; only the edge-cached homepage returns 200 to a plain client.

**Status: awaiting a decision.** Registration is free, so this is an account problem
rather than a technical one. Adding Heritage requires a logged-in session cookie, which
crosses the read-only, no-authentication boundary the rest of this project observes. The
enumeration mechanics above would all still apply — only the `.bot-price-data` element
changes.

---

## Houses deliberately skipped

**Phillips** has the cleanest data layer of any house surveyed and is useless here: its
seven departments are Contemporary, Online, Design, Editions, Watches, Photographs and
Jewels. There is no handbag department, and every keyword hit for "handbag" or "fashion"
turned out to be a *fashion photography* sale. There is also no cross-sale lot search.

**Julien's Auctions** returns HTTP 403 with `cf-mitigated: challenge` on every request,
including the sitemap its own `robots.txt` advertises. Defeating that needs a stealth
headless browser, which is both a materially larger effort and a different legal posture,
for a celebrity-memorabilia house with modest Hermès volume.

**Rago / Wright** disallows `/items/item/*`, `/auctions/view/*` and `/search/*` in
`robots.txt` — that is every path required. This is a compliance decision, not a technical
one, and their Hermès handbag volume is low.

**Doyle** publishes prices in plain HTML but its site search indexes only current
inventory, so past lots can only be found by walking all 54+ past auctions. Expected
volume is low tens.

## FX

Prices are converted to CNY at the **European Central Bank daily reference rate** for the
day of the sale, served by [frankfurter.dev](https://frankfurter.dev). The ECB fixing is
published every TARGET business day and is never revised, so a rebuild a year from now
reproduces the same numbers.

The ECB does not publish at weekends or on TARGET holidays. A sale on such a day is
converted at the most recent prior publication and flagged `carried_forward`, which the
site reports rather than hides.
