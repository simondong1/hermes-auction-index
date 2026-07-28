/**
 * Heritage Auctions browser-assisted harvester.
 *
 * Heritage is the only source in this project that cannot be harvested by a plain HTTP
 * client. Two independent walls:
 *
 *   1. Realised prices are omitted server-side for anonymous visitors. The page carries
 *      Google's paywall markup declaring `.bot-price-data` non-free, and the price-bracket
 *      facet is silently disabled without a session. A free account unlocks them.
 *   2. DataDome (behind Cloudflare) 403s non-browser clients, and its cookie is a rotating
 *      session artefact. Requests must originate from a real, signed-in browser.
 *
 * So this runs *in the page*, on ha.com, in a tab where you are signed in.
 *
 * Hard-won operational notes:
 *   - robots.txt sets `Crawl-delay: 15`. Honour it. Pacing at 2s got roughly fifteen pages
 *     through before DataDome hard-blocked the whole session, and the block then survived
 *     both a real navigation and a 60-second cooldown.
 *   - Progress is checkpointed to localStorage after every page. A block, a navigation or
 *     a closed tab therefore costs at most the page in flight, not the whole run.
 *   - `sb=5` sorts date-descending, so the walk stops at the window boundary instead of
 *     paging through the entire all-time archive.
 *
 * Usage, in the DevTools console of a signed-in ha.com tab:
 *
 *     await HeritageHarvest.runAll();      // resumable; safe to re-run after a block
 *     HeritageHarvest.status();            // how far did it get?
 *     HeritageHarvest.copy();              // copy the JSON to the clipboard
 *
 * Then save the JSON to data/heritage-browser-dump.json and run:
 *
 *     uv run hermes-auction harvest --house heritage
 */

window.HeritageHarvest = (function () {
  const STORE_KEY = "hermes:heritage:lots";
  const CURSOR_KEY = "hermes:heritage:cursor";

  /** Reach a little before the window start so the boundary page is never half-missed. */
  const SINCE = Date.parse("2021-06-25");

  /** Between them these reach every Birkin, Kelly, Mini Kelly and Kelly Pochette. */
  const TERMS = ["hermes birkin", "hermes kelly", "hermes mini kelly", "hermes kelly pochette"];

  const PAGE_SIZE = 72; // Heritage's maximum; larger values silently fall back to 48.
  const CRAWL_DELAY_MS = 15000; // robots.txt Crawl-delay: 15
  const MAX_PAGES_PER_TERM = 20;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();

  function load(key, fallback) {
    try {
      return JSON.parse(localStorage.getItem(key)) ?? fallback;
    } catch {
      return fallback;
    }
  }

  const store = () => new Map(Object.entries(load(STORE_KEY, {})));
  const saveStore = (map) => localStorage.setItem(STORE_KEY, JSON.stringify(Object.fromEntries(map)));
  const cursor = () => load(CURSOR_KEY, {});
  const saveCursor = (c) => localStorage.setItem(CURSOR_KEY, JSON.stringify(c));

  function searchUrl(term, page) {
    const params = new URLSearchParams({
      term,
      si: "2", // search titles + descriptions
      archive_state: "5327", // Auction Archives
      sold_status: "1526", // Sold only
      sb: "5", // sale date, newest first
      mode: "archive",
      page: `${PAGE_SIZE}~${page}`,
    });
    return `/c/search/results.zx?${params}`;
  }

  /**
   * One search-result row. Heritage nests the useful parts inside `a.item-title`:
   * `<b>` is the catalogue title, and the `<i>` siblings carry the blind stamp
   * ("P Square, 2012"), the condition grade ("Condition: 3") and the dimensions.
   */
  function parseRow(li) {
    const anchor = [...li.querySelectorAll('a[href*="/a/"]')].find((a) =>
      /\/a\/\d+-\d+\.s/.test(a.getAttribute("href") || ""),
    );
    if (!anchor) return null;

    const href = anchor.getAttribute("href").split("?")[0];
    const ids = href.match(/\/a\/(\d+)-(\d+)\.s/);
    if (!ids) return null;

    const titleEl = li.querySelector("a.item-title");
    const bold = titleEl && titleEl.querySelector("b");
    const notes = titleEl ? [...titleEl.querySelectorAll("i")].map((i) => clean(i.textContent)) : [];
    const lotLine = clean((li.querySelector("div.lotno") || {}).textContent);
    const dateMatch = lotLine.match(/([A-Z][a-z]{2,8}\s+\d{1,2},\s*\d{4})/);
    const priceEl = li.querySelector(".bot-price-data");
    const img = li.querySelector("img");

    return {
      lot_url: href,
      sale_no: ids[1],
      lot_no: ids[2],
      title: bold ? clean(bold.textContent) : clean(titleEl && titleEl.textContent).slice(0, 220),
      description: notes.join(" | ") || null,
      sale_date: dateMatch ? dateMatch[1] : null,
      price_text: priceEl ? clean(priceEl.textContent) : null,
      // Beyond roughly the first 24 rows Heritage lazy-loads thumbnails: `src` holds an
      // inline SVG placeholder and the real URL sits in `data-src`. Check that first.
      image_url: img
        ? img.getAttribute("data-src") || img.getAttribute("data-original") || img.getAttribute("src")
        : null,
    };
  }

  async function fetchPage(term, page) {
    const response = await fetch(searchUrl(term, page), { credentials: "include" });
    const body = await response.text();
    if (!response.ok) {
      const blocked = /captcha-delivery|datadome/i.test(body);
      const error = new Error(`HTTP ${response.status}${blocked ? " (DataDome block)" : ""}`);
      error.blocked = blocked;
      throw error;
    }
    const doc = new DOMParser().parseFromString(body, "text/html");
    return [...doc.querySelectorAll("li.item-block")].map(parseRow).filter(Boolean);
  }

  async function runTerm(term) {
    const map = store();
    const marks = cursor();
    const state = marks[term] || { page: 1, done: false };
    if (state.done) return { term, skipped: "already complete", total: map.size };

    const log = [];
    for (let page = state.page; page <= MAX_PAGES_PER_TERM; page++) {
      let rows;
      try {
        rows = await fetchPage(term, page);
      } catch (error) {
        // Checkpoint so a resume picks up exactly here.
        marks[term] = { page, done: false };
        saveCursor(marks);
        log.push(`p${page}: ${error.message} — stopped, resume with runAll()`);
        return { term, log, blocked: Boolean(error.blocked), total: map.size };
      }

      let kept = 0;
      let oldest = Infinity;
      for (const row of rows) {
        const stamp = row.sale_date ? Date.parse(row.sale_date) : NaN;
        if (!Number.isNaN(stamp)) oldest = Math.min(oldest, stamp);
        if (Number.isNaN(stamp) || stamp >= SINCE) {
          map.set(row.lot_url, row);
          kept++;
        }
      }

      // Persist after every page, not at the end. Losing a run to a block is what this
      // whole file exists to prevent.
      saveStore(map);
      marks[term] = { page: page + 1, done: rows.length === 0 || kept === 0 };
      saveCursor(marks);

      log.push(
        `p${page}: ${rows.length} rows, ${kept} in window, oldest ` +
          (Number.isFinite(oldest) ? new Date(oldest).toISOString().slice(0, 10) : "n/a"),
      );
      if (marks[term].done) break;
      await sleep(CRAWL_DELAY_MS);
    }
    return { term, log, total: map.size };
  }

  return {
    status() {
      const map = store();
      return { lots: map.size, cursor: cursor() };
    },

    /** Resumable across blocks, navigations and closed tabs. Safe to call repeatedly. */
    async runAll(terms = TERMS) {
      const results = [];
      for (const term of terms) {
        const result = await runTerm(term);
        results.push(result);
        console.log(term, result);
        if (result.blocked) {
          console.warn("DataDome blocked the session. Wait, reload ha.com, then re-run runAll().");
          break;
        }
        await sleep(CRAWL_DELAY_MS);
      }
      return { results, lots: store().size };
    },

    dump() {
      return [...store().values()];
    },

    copy() {
      const json = JSON.stringify(this.dump(), null, 1);
      return navigator.clipboard.writeText(json).then(() => `copied ${this.dump().length} lots`);
    },

    reset() {
      localStorage.removeItem(STORE_KEY);
      localStorage.removeItem(CURSOR_KEY);
      return "cleared";
    },
  };
})();
