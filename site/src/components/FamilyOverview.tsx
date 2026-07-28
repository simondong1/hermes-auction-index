import { cny, cnyCompact, count, plural } from "../lib/format";
import { computeStats, halfYearTrend } from "../lib/stats";
import type { FamilyMeta, Lot } from "../types";
import { TrendChart } from "./TrendChart";
import { MiniBar, Pill, SectionTitle, Stat } from "./primitives";

interface Props {
  families: FamilyMeta[];
  lots: Lot[];
  /** Unfiltered, so the headline count is the size of the dataset rather than the view. */
  allLots: Lot[];
  onSelect: (family: FamilyMeta) => void;
}

/** Top level of the hierarchy: the four bag types, side by side. */
export function FamilyOverview({ families, lots, allLots, onSelect }: Props) {
  const overall = computeStats(lots);
  const tracked = computeStats(allLots);
  const trend = halfYearTrend(lots);

  const cards = families.map((family) => {
    const familyLots = lots.filter((lot) => lot.family === family.key);
    return { family, lots: familyLots, stats: computeStats(familyLots) };
  });
  const maxMedian = Math.max(...cards.map((card) => card.stats.median ?? 0), 1);

  return (
    <div className="space-y-10">
      <section className="space-y-5">
        <SectionTitle
          eyebrow="All houses, trailing five years"
          title="Every Birkin and Kelly at auction"
          subtitle="Prices are the realised total including buyer's premium, converted to renminbi at the European Central Bank reference rate on the day of the sale."
        />
        <dl className="grid grid-cols-2 gap-6 sm:grid-cols-4">
          <Stat
            label="Lots tracked"
            value={count(tracked.count)}
            emphasis
            hint={
              overall.count !== tracked.count
                ? `${count(overall.count)} match your filters`
                : "Across every house"
            }
          />
          <Stat
            label="Sold"
            value={count(overall.soldCount)}
            emphasis
            hint={
              tracked.hiddenCount > 0
                ? `${count(tracked.hiddenCount)} results withheld by the house`
                : undefined
            }
          />
          <Stat label="Median price" value={cny(overall.median)} emphasis hint="Realised, in CNY" />
          <Stat
            label="Total realised"
            value={cnyCompact(overall.total)}
            emphasis
            hint="Sold lots with a published price"
          />
        </dl>
        <div className="rounded-xl border hairline bg-white/50 p-4">
          <TrendChart data={trend} height={230} />
        </div>
      </section>

      <section className="space-y-4">
        <SectionTitle
          eyebrow="Step one"
          title="Choose a bag type"
          subtitle="Then narrow by size, then by the individual bag — leather, colour and hardware."
        />
        <div className="grid gap-4 sm:grid-cols-2">
          {cards.map(({ family, lots: familyLots, stats }) => (
            <button
              key={family.key}
              type="button"
              onClick={() => onSelect(family)}
              className="group rounded-xl border hairline bg-white/60 p-5 text-left transition hover:border-orange/40 hover:bg-white focus:outline-none focus-visible:ring-2 focus-visible:ring-orange/40"
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="font-display text-2xl leading-tight text-ink">{family.name}</h3>
                  <p className="mt-0.5 text-sm text-ink-faint">
                    {family.sizeless
                      ? "One size"
                      : `Sizes ${family.headlineSizes.join(" · ")}`}
                  </p>
                </div>
                <span className="mt-1 text-ink-faint transition group-hover:translate-x-0.5 group-hover:text-orange">
                  →
                </span>
              </div>

              <dl className="mt-4 grid grid-cols-3 gap-3">
                <Stat label="Median" value={cny(stats.median)} />
                <Stat label="Sold" value={count(stats.soldCount)} />
                <Stat label="Top price" value={cnyCompact(stats.max)} />
              </dl>

              <div className="mt-4">
                <MiniBar value={stats.median ?? 0} max={maxMedian} />
              </div>

              <div className="mt-3 flex flex-wrap gap-1.5">
                <Pill>{plural(familyLots.length, "lot")}</Pill>
                {stats.hiddenCount > 0 ? (
                  <Pill tone="muted">{count(stats.hiddenCount)} results withheld</Pill>
                ) : null}
                {family.nonexistentSizes.length > 0 ? (
                  <Pill tone="orange">
                    No {family.name} {family.nonexistentSizes.join("/")} exists
                  </Pill>
                ) : null}
              </div>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
