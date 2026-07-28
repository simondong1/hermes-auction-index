import { cny, cnyCompact, count } from "../lib/format";
import { computeStats, halfYearTrend, rankByMedian } from "../lib/stats";
import type { FamilyMeta, Lot } from "../types";
import { TrendChart } from "./TrendChart";
import { EmptyState, MiniBar, Pill, SectionTitle, StatsRow } from "./primitives";

interface Props {
  family: FamilyMeta;
  lots: Lot[];
  sellThroughKnown: boolean;
  onSelectBucket: (bucket: string) => void;
}

/**
 * Second level: size buckets within one family.
 *
 * Headline sizes always render, even at zero lots, so a gap in the market is visible
 * rather than merely absent. Sizes outside the ladder collect into their own bucket.
 */
export function SizeBreakdown({ family, lots, sellThroughKnown, onSelectBucket }: Props) {
  const stats = computeStats(lots);
  const byBucket = new Map<string, Lot[]>();
  for (const lot of lots) {
    const bucket = byBucket.get(lot.sizeBucket);
    if (bucket) bucket.push(lot);
    else byBucket.set(lot.sizeBucket, [lot]);
  }

  const headline = family.sizeless
    ? ["One size"]
    : family.headlineSizes.map((size) => `${family.name} ${size}`);

  const extras = [...byBucket.keys()]
    .filter((bucket) => !headline.includes(bucket))
    .sort((a, b) => (byBucket.get(b)?.length ?? 0) - (byBucket.get(a)?.length ?? 0));

  const buckets = [...headline, ...extras].map((label) => {
    const bucketLots = byBucket.get(label) ?? [];
    return { label, lots: bucketLots, stats: computeStats(bucketLots), headline: headline.includes(label) };
  });

  const maxMedian = Math.max(...buckets.map((b) => b.stats.median ?? 0), 1);
  const topColours = rankByMedian(lots, (lot) => lot.colour, { minCount: 3 }).slice(0, 8);
  const topLeathers = rankByMedian(lots, (lot) => lot.leather, { minCount: 3 }).slice(0, 8);

  return (
    <div className="space-y-10">
      <section className="space-y-5">
        <SectionTitle
          eyebrow="Bag type"
          title={family.name}
          subtitle={
            family.nonexistentSizes.length > 0
              ? `Hermès has never produced a ${family.name} ${family.nonexistentSizes.join(" or ")}. The ladder steps ${family.headlineSizes.join(" → ")}.`
              : undefined
          }
        />
        <StatsRow stats={stats} sellThroughKnown={sellThroughKnown} />
        <div className="rounded-xl border hairline bg-white/50 p-4">
          <TrendChart data={halfYearTrend(lots)} height={220} />
        </div>
      </section>

      <section className="space-y-4">
        <SectionTitle eyebrow="Step two" title="Choose a size" />
        {buckets.every((b) => b.lots.length === 0) ? (
          <EmptyState message="No lots match the current filters." hint="Try widening the filters above." />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {buckets.map((bucket) => {
              const empty = bucket.lots.length === 0;
              return (
                <button
                  key={bucket.label}
                  type="button"
                  disabled={empty}
                  onClick={() => onSelectBucket(bucket.label)}
                  className={`rounded-xl border hairline p-4 text-left transition focus:outline-none focus-visible:ring-2 focus-visible:ring-orange/40 ${
                    empty
                      ? "cursor-not-allowed bg-paper-deep/40 opacity-60"
                      : "bg-white/60 hover:border-orange/40 hover:bg-white"
                  }`}
                >
                  <div className="flex items-baseline justify-between gap-2">
                    <h3 className="font-display text-xl text-ink">{bucket.label}</h3>
                    {!bucket.headline ? <Pill tone="muted">outside ladder</Pill> : null}
                  </div>
                  {empty ? (
                    <p className="mt-3 text-sm text-ink-faint">No lots</p>
                  ) : (
                    <>
                      <dl className="mt-3 flex items-baseline gap-4">
                        <div>
                          <dt className="text-[11px] uppercase tracking-[0.08em] text-ink-faint">
                            Median
                          </dt>
                          <dd className="tnum font-display text-xl text-ink">
                            {cny(bucket.stats.median)}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-[11px] uppercase tracking-[0.08em] text-ink-faint">
                            Top
                          </dt>
                          <dd className="tnum font-display text-base text-ink-soft">
                            {cnyCompact(bucket.stats.max)}
                          </dd>
                        </div>
                      </dl>
                      <div className="mt-3">
                        <MiniBar value={bucket.stats.median ?? 0} max={maxMedian} />
                      </div>
                      <p className="mt-2 text-[11px] text-ink-faint">
                        {count(bucket.stats.soldCount)} sold of {count(bucket.lots.length)}
                      </p>
                    </>
                  )}
                </button>
              );
            })}
          </div>
        )}
      </section>

      {topColours.length > 0 ? (
        <section className="grid gap-8 lg:grid-cols-2">
          <RankedList title="Highest median by colour" buckets={topColours} showSwatch />
          <RankedList title="Highest median by leather" buckets={topLeathers} />
        </section>
      ) : null}
    </div>
  );
}

function RankedList({
  title,
  buckets,
  showSwatch = false,
}: {
  title: string;
  buckets: { label: string; lots: Lot[]; stats: { median: number | null; soldCount: number } }[];
  showSwatch?: boolean;
}) {
  const max = Math.max(...buckets.map((b) => b.stats.median ?? 0), 1);
  return (
    <div className="space-y-3">
      <h3 className="border-b hairline pb-2 font-display text-lg text-ink">{title}</h3>
      <ul className="space-y-2.5">
        {buckets.map((bucket) => (
          <li key={bucket.label} className="grid grid-cols-[1fr_auto] items-center gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                {showSwatch ? (
                  <span
                    className="inline-block size-3 shrink-0 rounded-full ring-1 ring-black/15"
                    style={{ backgroundColor: bucket.lots[0]?.colourHex ?? "transparent" }}
                  />
                ) : null}
                <span className="truncate text-sm text-ink">{bucket.label}</span>
                <span className="text-[11px] text-ink-faint">
                  {count(bucket.stats.soldCount)}
                </span>
              </div>
              <div className="mt-1">
                <MiniBar value={bucket.stats.median ?? 0} max={max} />
              </div>
            </div>
            <span className="tnum text-sm text-ink-soft">{cnyCompact(bucket.stats.median)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
