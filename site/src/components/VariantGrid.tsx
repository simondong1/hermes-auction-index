import { useMemo } from "react";
import { variantKey } from "../lib/data";
import { cny, cnyCompact, count, percent } from "../lib/format";
import { computeStats, type PriceStats } from "../lib/stats";
import type { Lot } from "../types";
import { EmptyState, Pill, SectionTitle, Swatch } from "./primitives";

interface Variant {
  key: string;
  colour: string | null;
  colourHex: string | null;
  leather: string | null;
  hardware: string | null;
  hardwareAbbrev: string | null;
  image: string | null;
  lots: Lot[];
  stats: PriceStats;
}

type SortKey = "median" | "count" | "recent";

interface Props {
  bucket: string;
  lots: Lot[];
  sort: SortKey;
  onSortChange: (sort: SortKey) => void;
  onSelectVariant: (key: string) => void;
}

/**
 * Third level: the individual bags inside one size, keyed on the combination a
 * collector shops for — colour, leather and hardware.
 */
export function VariantGrid({ bucket, lots, sort, onSortChange, onSelectVariant }: Props) {
  const variants = useMemo(() => buildVariants(lots, sort), [lots, sort]);

  return (
    <div className="space-y-5">
      <SectionTitle
        eyebrow="Step three"
        title={bucket}
        subtitle={`${count(variants.length)} distinct leather / colour / hardware combinations across ${count(lots.length)} lots.`}
        right={
          <label className="flex items-center gap-2 text-sm text-ink-soft">
            <span className="text-[11px] uppercase tracking-[0.08em] text-ink-faint">Sort</span>
            <select
              value={sort}
              onChange={(event) => onSortChange(event.target.value as SortKey)}
              className="rounded-md border hairline bg-white px-2 py-1 text-sm text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-orange/40"
            >
              <option value="median">Median price</option>
              <option value="count">Most traded</option>
              <option value="recent">Most recent</option>
            </select>
          </label>
        }
      />

      {variants.length === 0 ? (
        <EmptyState message="No lots match the current filters." />
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {variants.map((variant) => (
            <li key={variant.key}>
              <button
                type="button"
                onClick={() => onSelectVariant(variant.key)}
                className="group flex h-full w-full gap-4 rounded-xl border hairline bg-white/60 p-3 text-left transition hover:border-orange/40 hover:bg-white focus:outline-none focus-visible:ring-2 focus-visible:ring-orange/40"
              >
                <div className="size-24 shrink-0 overflow-hidden rounded-lg bg-paper-deep">
                  {variant.image ? (
                    <img
                      src={variant.image}
                      alt=""
                      loading="lazy"
                      className="size-full object-cover transition duration-300 group-hover:scale-[1.04]"
                      onError={(event) => {
                        event.currentTarget.style.visibility = "hidden";
                      }}
                    />
                  ) : null}
                </div>

                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <Swatch hex={variant.colourHex} label={variant.colour} />
                    <h3 className="truncate font-display text-lg leading-tight text-ink">
                      {variant.colour ?? "Unspecified colour"}
                    </h3>
                  </div>
                  <p className="truncate text-sm text-ink-soft">
                    {variant.leather ?? "Unspecified leather"}
                    {variant.hardwareAbbrev ? ` · ${variant.hardwareAbbrev}` : ""}
                  </p>

                  <p className="tnum mt-2 font-display text-xl text-ink">
                    {cny(variant.stats.median)}
                  </p>
                  <p className="tnum text-[11px] text-ink-faint">
                    {variant.stats.soldCount > 1
                      ? `${count(variant.stats.soldCount)} sales · ${cnyCompact(variant.stats.min)} – ${cnyCompact(variant.stats.max)}`
                      : `${count(variant.stats.soldCount)} sale`}
                  </p>

                  {variant.stats.sellThrough != null && variant.stats.sellThrough < 1 ? (
                    <p className="mt-1.5">
                      <Pill tone="muted">{percent(variant.stats.sellThrough)} sell-through</Pill>
                    </p>
                  ) : null}
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function buildVariants(lots: Lot[], sort: SortKey): Variant[] {
  const grouped = new Map<string, Lot[]>();
  for (const lot of lots) {
    const key = variantKey(lot);
    const bucket = grouped.get(key);
    if (bucket) bucket.push(lot);
    else grouped.set(key, [lot]);
  }

  const variants: Variant[] = [...grouped.entries()].map(([key, group]) => {
    // Prefer a sold lot's photograph: unsold lots are sometimes shot differently.
    const representative = group.find((lot) => lot.image && lot.outcome === "sold") ?? group[0];
    return {
      key,
      colour: representative.colour,
      colourHex: representative.colourHex,
      leather: representative.leather,
      hardware: representative.hardware,
      hardwareAbbrev: representative.hardwareAbbrev,
      image: representative.image,
      lots: group,
      stats: computeStats(group),
    };
  });

  const comparators: Record<SortKey, (a: Variant, b: Variant) => number> = {
    median: (a, b) => (b.stats.median ?? -1) - (a.stats.median ?? -1),
    count: (a, b) => b.stats.soldCount - a.stats.soldCount || (b.stats.median ?? 0) - (a.stats.median ?? 0),
    recent: (a, b) => latest(b).localeCompare(latest(a)),
  };
  return variants.sort(comparators[sort]);
}

function latest(variant: Variant): string {
  return variant.lots.reduce((max, lot) => (lot.saleDate > max ? lot.saleDate : max), "");
}
