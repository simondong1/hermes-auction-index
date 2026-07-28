import type { Lot } from "../types";

/** Summary statistics for one bucket of lots, all in CNY. */
export interface PriceStats {
  count: number;
  soldCount: number;
  hiddenCount: number;
  unsoldCount: number;
  /** Share of run lots that sold, excluding withdrawn and hidden-result lots. */
  sellThrough: number | null;
  min: number | null;
  p25: number | null;
  median: number | null;
  p75: number | null;
  max: number | null;
  total: number;
}

export const EMPTY_STATS: PriceStats = {
  count: 0,
  soldCount: 0,
  hiddenCount: 0,
  unsoldCount: 0,
  sellThrough: null,
  min: null,
  p25: null,
  median: null,
  p75: null,
  max: null,
  total: 0,
};

/** Linear-interpolated quantile over a pre-sorted ascending array. */
export function quantile(sorted: number[], q: number): number | null {
  if (sorted.length === 0) return null;
  if (sorted.length === 1) return sorted[0];
  const pos = (sorted.length - 1) * q;
  const lower = Math.floor(pos);
  const upper = Math.ceil(pos);
  if (lower === upper) return sorted[lower];
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (pos - lower);
}

export function computeStats(lots: Lot[]): PriceStats {
  const prices: number[] = [];
  let soldCount = 0;
  let hiddenCount = 0;
  let unsoldCount = 0;

  for (const lot of lots) {
    if (lot.outcome === "sold") soldCount += 1;
    else if (lot.outcome === "result_hidden") hiddenCount += 1;
    else if (lot.outcome === "unsold") unsoldCount += 1;
    if (lot.priceCny != null && lot.outcome === "sold") prices.push(lot.priceCny);
  }

  prices.sort((a, b) => a - b);
  // A lot whose result the house withheld tells us nothing about sell-through,
  // so it is excluded from the denominator rather than counted as a failure.
  const decided = soldCount + unsoldCount;

  return {
    count: lots.length,
    soldCount,
    hiddenCount,
    unsoldCount,
    sellThrough: decided > 0 ? soldCount / decided : null,
    min: prices.length ? prices[0] : null,
    p25: quantile(prices, 0.25),
    median: quantile(prices, 0.5),
    p75: quantile(prices, 0.75),
    max: prices.length ? prices[prices.length - 1] : null,
    total: prices.reduce((sum, value) => sum + value, 0),
  };
}

export interface TrendPoint {
  period: string;
  median: number | null;
  p25: number | null;
  p75: number | null;
  count: number;
}

/** Median CNY price per half-year, for the trend charts. */
export function halfYearTrend(lots: Lot[]): TrendPoint[] {
  const buckets = new Map<string, number[]>();

  for (const lot of lots) {
    if (lot.outcome !== "sold" || lot.priceCny == null) continue;
    const year = lot.saleDate.slice(0, 4);
    const half = Number(lot.saleDate.slice(5, 7)) <= 6 ? "H1" : "H2";
    const key = `${year} ${half}`;
    const bucket = buckets.get(key);
    if (bucket) bucket.push(lot.priceCny);
    else buckets.set(key, [lot.priceCny]);
  }

  return [...buckets.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([period, values]) => {
      values.sort((a, b) => a - b);
      return {
        period,
        median: quantile(values, 0.5),
        p25: quantile(values, 0.25),
        p75: quantile(values, 0.75),
        count: values.length,
      };
    });
}

/** Group lots by a key, dropping nulls into an explicit bucket the caller names. */
export function groupBy<T>(
  items: T[],
  key: (item: T) => string | null,
  nullLabel = "Unspecified",
): Map<string, T[]> {
  const out = new Map<string, T[]>();
  for (const item of items) {
    const raw = key(item);
    const label = raw ?? nullLabel;
    const bucket = out.get(label);
    if (bucket) bucket.push(item);
    else out.set(label, [item]);
  }
  return out;
}

export interface RankedBucket {
  label: string;
  lots: Lot[];
  stats: PriceStats;
}

/** Group, compute stats and rank by median price descending. */
export function rankByMedian(
  lots: Lot[],
  key: (lot: Lot) => string | null,
  { minCount = 1, nullLabel = "Unspecified" } = {},
): RankedBucket[] {
  return [...groupBy(lots, key, nullLabel).entries()]
    .map(([label, group]) => ({ label, lots: group, stats: computeStats(group) }))
    .filter((bucket) => bucket.stats.soldCount >= minCount)
    .sort((a, b) => (b.stats.median ?? 0) - (a.stats.median ?? 0));
}
