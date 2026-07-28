import { useEffect, useMemo, useRef, useState } from "react";
import { FamilyOverview } from "./components/FamilyOverview";
import { FilterBar } from "./components/FilterBar";
import { LotTable } from "./components/LotTable";
import { Methodology } from "./components/Methodology";
import { SizeBreakdown } from "./components/SizeBreakdown";
import { VariantGrid } from "./components/VariantGrid";
import { SectionTitle, StatsRow } from "./components/primitives";
import type { Route } from "./lib/data";
import {
  EMPTY_ROUTE,
  applyFilters,
  loadDataset,
  readUrl,
  sameRoute,
  variantKey,
  writeUrl,
} from "./lib/data";
import { count, shortDate } from "./lib/format";
import { computeStats, halfYearTrend } from "./lib/stats";
import { TrendChart } from "./components/TrendChart";
import type { Dataset, Filters } from "./types";

type SortKey = "median" | "count" | "recent";

export default function App() {
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [error, setError] = useState<string | null>(null);
  const initial = useMemo(() => readUrl(), []);
  const [route, setRoute] = useState<Route>(initial.route);
  const [filters, setFilters] = useState<Filters>(initial.filters);
  const [sort, setSort] = useState<SortKey>("median");

  useEffect(() => {
    loadDataset().then(setDataset).catch((cause: Error) => setError(cause.message));
  }, []);

  const previousRoute = useRef(initial.route);
  useEffect(() => {
    const isNavigation = !sameRoute(previousRoute.current, route);
    previousRoute.current = route;
    writeUrl(route, filters, isNavigation ? "push" : "replace");
  }, [route, filters]);

  // Keep the view in step with the address bar so deep links, the back button and a
  // hand-edited hash all work.
  useEffect(() => {
    const resync = () => {
      const next = readUrl();
      previousRoute.current = next.route;
      setRoute(next.route);
      setFilters(next.filters);
    };
    window.addEventListener("hashchange", resync);
    window.addEventListener("popstate", resync);
    return () => {
      window.removeEventListener("hashchange", resync);
      window.removeEventListener("popstate", resync);
    };
  }, []);

  const filtered = useMemo(
    () => (dataset ? applyFilters(dataset.lots, filters) : []),
    [dataset, filters],
  );

  if (error) {
    return (
      <Shell>
        <div className="rounded-xl border hairline bg-white/60 p-8 text-center">
          <h2 className="font-display text-xl text-ink">The dataset could not be loaded</h2>
          <p className="mt-2 text-sm text-ink-soft">{error}</p>
          <p className="mt-1 text-sm text-ink-faint">
            Run <code className="rounded bg-paper-deep px-1">hermes-auction build</code> to
            regenerate it.
          </p>
        </div>
      </Shell>
    );
  }

  if (!dataset) {
    return (
      <Shell>
        <div className="animate-pulse space-y-4">
          <div className="h-8 w-2/3 rounded bg-paper-deep" />
          <div className="h-40 rounded-xl bg-paper-deep" />
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="h-44 rounded-xl bg-paper-deep" />
            <div className="h-44 rounded-xl bg-paper-deep" />
          </div>
        </div>
      </Shell>
    );
  }

  const family = dataset.families.find((f) => f.key === route.family) ?? null;
  const familyLots = family ? filtered.filter((lot) => lot.family === family.key) : filtered;
  const bucketLots = route.bucket
    ? familyLots.filter((lot) => lot.sizeBucket === route.bucket)
    : familyLots;
  const variantLots = route.variant
    ? bucketLots.filter((lot) => variantKey(lot) === route.variant)
    : bucketLots;

  const crumbs: { label: string; onClick?: () => void }[] = [
    { label: "All bags", onClick: () => setRoute(EMPTY_ROUTE) },
    ...(family
      ? [{ label: family.name, onClick: () => setRoute({ ...EMPTY_ROUTE, family: family.key }) }]
      : []),
    ...(route.bucket
      ? [
          {
            label: route.bucket,
            onClick: () => setRoute({ family: route.family, bucket: route.bucket, variant: null }),
          },
        ]
      : []),
    ...(route.variant ? [{ label: route.variant }] : []),
  ];

  return (
    <Shell
      header={
        <FilterBar
          lots={dataset.lots}
          houses={dataset.houses}
          filters={filters}
          onChange={setFilters}
          resultCount={filtered.length}
        />
      }
      generatedAt={dataset.generatedAt}
    >
      <nav aria-label="Breadcrumb" className="mb-6 flex flex-wrap items-center gap-1.5 text-sm">
        {crumbs.map((crumb, index) => (
          <span key={crumb.label} className="flex items-center gap-1.5">
            {index > 0 ? <span className="text-ink-faint">/</span> : null}
            {crumb.onClick && index < crumbs.length - 1 ? (
              <button
                type="button"
                onClick={crumb.onClick}
                className="text-ink-soft underline decoration-rule underline-offset-2 hover:text-orange hover:decoration-orange"
              >
                {crumb.label}
              </button>
            ) : (
              <span className="text-ink">{crumb.label}</span>
            )}
          </span>
        ))}
      </nav>

      {!family ? (
        <FamilyOverview
          families={dataset.families}
          lots={filtered}
          allLots={dataset.lots}
          onSelect={(selected) => setRoute({ ...EMPTY_ROUTE, family: selected.key })}
        />
      ) : !route.bucket ? (
        <SizeBreakdown
          family={family}
          lots={familyLots}
          onSelectBucket={(bucket) => setRoute({ family: family.key, bucket, variant: null })}
        />
      ) : !route.variant ? (
        <VariantGrid
          bucket={route.bucket}
          lots={bucketLots}
          sort={sort}
          onSortChange={setSort}
          onSelectVariant={(variant) =>
            setRoute({ family: family.key, bucket: route.bucket, variant })
          }
        />
      ) : (
        <div className="space-y-6">
          <SectionTitle
            eyebrow={`${family.name} · ${route.bucket}`}
            title={route.variant}
            subtitle={`${count(variantLots.length)} lots, most recent ${
              variantLots.length ? shortDate(variantLots[0].saleDate) : "—"
            }.`}
          />
          <StatsRow stats={computeStats(variantLots)} />
          {variantLots.length >= 4 ? (
            <div className="rounded-xl border hairline bg-white/50 p-4">
              <TrendChart data={halfYearTrend(variantLots)} height={200} />
            </div>
          ) : null}
          <LotTable lots={variantLots} caption={`Sales of ${route.variant}`} />
        </div>
      )}

      {route.family && !route.variant ? (
        <details className="mt-10 rounded-xl border hairline bg-white/40 p-4">
          <summary className="cursor-pointer font-display text-lg text-ink">
            Every lot in this view ({count(bucketLots.length)})
          </summary>
          <div className="mt-4">
            <LotTable lots={bucketLots} />
          </div>
        </details>
      ) : null}

      <div className="mt-16">
        <Methodology dataset={dataset} />
      </div>
    </Shell>
  );
}

function Shell({
  children,
  header,
  generatedAt,
}: {
  children: React.ReactNode;
  header?: React.ReactNode;
  generatedAt?: string;
}) {
  return (
    <div className="min-h-dvh">
      <header className="border-b hairline">
        <div className="mx-auto flex max-w-6xl flex-wrap items-end justify-between gap-3 px-4 py-6 sm:px-6">
          <div>
            <p className="text-[11px] uppercase tracking-[0.18em] text-orange">
              Hermès at auction
            </p>
            <h1 className="font-display text-3xl leading-tight text-ink sm:text-4xl">
              Birkin &amp; Kelly Price Index
            </h1>
            <p className="mt-1 max-w-2xl text-sm text-ink-soft">
              Five years of results from the major international auction houses, priced in
              renminbi at the exchange rate on the day of each sale.
            </p>
          </div>
          <a
            href="#methodology"
            className="text-sm text-ink-soft underline decoration-rule underline-offset-4 hover:text-orange hover:decoration-orange"
          >
            Methodology
          </a>
        </div>
      </header>

      {header}

      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">{children}</main>

      <footer className="mx-auto max-w-6xl px-4 pb-12 text-[11px] text-ink-faint sm:px-6">
        <p>
          Compiled from the auction houses' own published results for personal research.
          Bag names and trade marks belong to Hermès International.
          {generatedAt ? ` Data generated ${generatedAt.replace("T", " ")}.` : ""}
        </p>
      </footer>
    </div>
  );
}
