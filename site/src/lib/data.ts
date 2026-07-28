import type { Dataset, FamilyKey, Filters, Lot } from "../types";
import { EMPTY_FILTERS } from "../types";

/** Loads the dataset emitted by the Python pipeline. */
export async function loadDataset(): Promise<Dataset> {
  const response = await fetch(`${import.meta.env.BASE_URL}dataset.json`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`Could not load dataset.json (HTTP ${response.status})`);
  }
  return (await response.json()) as Dataset;
}

export function applyFilters(lots: Lot[], filters: Filters): Lot[] {
  const needle = filters.query.trim().toLowerCase();

  return lots.filter((lot) => {
    if (filters.soldOnly && lot.outcome !== "sold") return false;
    if (filters.houses.length && !filters.houses.includes(lot.house)) return false;
    if (filters.colourFamilies.length && !filters.colourFamilies.includes(lot.colourFamily ?? "")) {
      return false;
    }
    if (filters.leathers.length && !filters.leathers.includes(lot.leather ?? "")) return false;
    if (filters.hardware.length && !filters.hardware.includes(lot.hardware ?? "")) return false;
    if (filters.editions.length && !filters.editions.some((e) => lot.editions.includes(e))) {
      return false;
    }

    const year = Number(lot.saleDate.slice(0, 4));
    if (filters.yearFrom != null && year < filters.yearFrom) return false;
    if (filters.yearTo != null && year > filters.yearTo) return false;

    if (needle) {
      const haystack = `${lot.title} ${lot.saleName} ${lot.colour ?? ""} ${lot.leather ?? ""}`;
      if (!haystack.toLowerCase().includes(needle)) return false;
    }
    return true;
  });
}

/** Distinct values for a facet, ordered by how many lots carry them. */
export function facetValues(lots: Lot[], key: (lot: Lot) => string | string[] | null): string[] {
  const counts = new Map<string, number>();
  for (const lot of lots) {
    const value = key(lot);
    const values = value == null ? [] : Array.isArray(value) ? value : [value];
    for (const v of values) {
      if (v) counts.set(v, (counts.get(v) ?? 0) + 1);
    }
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([v]) => v);
}

/**
 * The "individual bag" identity: the combination a collector actually shops for.
 * Two lots share a key when they are, for pricing purposes, the same bag.
 */
export function variantKey(lot: Lot): string {
  return [
    lot.colour ?? "Unspecified colour",
    lot.leather ?? "Unspecified leather",
    lot.hardwareAbbrev ?? "—",
  ].join(" · ");
}

export function variantLabel(lot: Lot): string {
  const parts = [lot.colour, lot.leather].filter(Boolean);
  return parts.length ? parts.join(" ") : "Unspecified";
}

// --------------------------------------------------------------------------------
// URL state — every view is a shareable link
// --------------------------------------------------------------------------------

export interface Route {
  family: FamilyKey | null;
  bucket: string | null;
  variant: string | null;
}

export const EMPTY_ROUTE: Route = { family: null, bucket: null, variant: null };

const LIST_KEYS = ["houses", "colourFamilies", "leathers", "hardware", "editions"] as const;

export function readUrl(): { route: Route; filters: Filters } {
  const params = new URLSearchParams(window.location.hash.replace(/^#\/?/, ""));
  const filters: Filters = { ...EMPTY_FILTERS };

  for (const key of LIST_KEYS) {
    const raw = params.get(key);
    if (raw) filters[key] = raw.split("~").filter(Boolean);
  }
  const from = params.get("from");
  const to = params.get("to");
  if (from) filters.yearFrom = Number(from);
  if (to) filters.yearTo = Number(to);
  if (params.get("sold") === "1") filters.soldOnly = true;
  filters.query = params.get("q") ?? "";

  return {
    route: {
      family: (params.get("family") as FamilyKey | null) ?? null,
      bucket: params.get("bucket"),
      variant: params.get("variant"),
    },
    filters,
  };
}

/**
 * Sync the address bar.
 *
 * A drill-down is a navigation and should be undoable with the back button, so it
 * pushes. A filter tweak is not, so it replaces - otherwise typing in the search box
 * would bury the previous page under one history entry per keystroke.
 */
export function writeUrl(route: Route, filters: Filters, mode: "push" | "replace" = "replace"): void {
  const params = new URLSearchParams();
  if (route.family) params.set("family", route.family);
  if (route.bucket) params.set("bucket", route.bucket);
  if (route.variant) params.set("variant", route.variant);

  for (const key of LIST_KEYS) {
    if (filters[key].length) params.set(key, filters[key].join("~"));
  }
  if (filters.yearFrom != null) params.set("from", String(filters.yearFrom));
  if (filters.yearTo != null) params.set("to", String(filters.yearTo));
  if (filters.soldOnly) params.set("sold", "1");
  if (filters.query) params.set("q", filters.query);

  const next = `#/${params.toString()}`;
  if (next === window.location.hash) return;
  if (mode === "push") window.history.pushState(null, "", next);
  else window.history.replaceState(null, "", next);
}

export function sameRoute(a: Route, b: Route): boolean {
  return a.family === b.family && a.bucket === b.bucket && a.variant === b.variant;
}
