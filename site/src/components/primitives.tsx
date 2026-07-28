import type { ReactNode } from "react";
import { cny, cnyCompact, count, percent } from "../lib/format";
import type { PriceStats } from "../lib/stats";

export function Swatch({ hex, label }: { hex: string | null; label?: string | null }) {
  return (
    <span
      className="inline-block size-3.5 shrink-0 rounded-full ring-1 ring-black/15"
      style={{ backgroundColor: hex ?? "transparent" }}
      title={label ?? undefined}
      aria-hidden={!label}
    />
  );
}

export function Pill({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "orange" | "moss" | "plum" | "muted";
}) {
  const tones: Record<string, string> = {
    neutral: "bg-paper-deep text-ink-soft ring-rule",
    orange: "bg-orange-soft text-orange ring-orange/20",
    moss: "bg-moss/10 text-moss ring-moss/20",
    plum: "bg-plum/10 text-plum ring-plum/20",
    muted: "bg-transparent text-ink-faint ring-rule",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

export function Stat({
  label,
  value,
  hint,
  emphasis = false,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  emphasis?: boolean;
}) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-[0.08em] text-ink-faint">{label}</dt>
      <dd
        className={`tnum font-display ${emphasis ? "text-2xl" : "text-lg"} leading-tight text-ink`}
      >
        {value}
      </dd>
      {hint ? <p className="mt-0.5 text-[11px] text-ink-faint">{hint}</p> : null}
    </div>
  );
}

/** The four-number summary used under every bucket heading. */
export function StatsRow({ stats, dense = false }: { stats: PriceStats; dense?: boolean }) {
  return (
    <dl className={`grid ${dense ? "grid-cols-4 gap-4" : "grid-cols-2 gap-5 sm:grid-cols-4"}`}>
      <Stat label="Median" value={cny(stats.median)} emphasis={!dense} />
      <Stat
        label="Range"
        value={
          stats.min == null ? "—" : `${cnyCompact(stats.min)} – ${cnyCompact(stats.max)}`
        }
      />
      <Stat label="Sold" value={count(stats.soldCount)} />
      <Stat
        label="Sell-through"
        value={percent(stats.sellThrough)}
        hint={stats.hiddenCount > 0 ? `${count(stats.hiddenCount)} withheld` : undefined}
      />
    </dl>
  );
}

export function SectionTitle({
  eyebrow,
  title,
  subtitle,
  right,
}: {
  eyebrow?: string;
  title: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4 border-b hairline pb-3">
      <div>
        {eyebrow ? (
          <p className="text-[11px] uppercase tracking-[0.14em] text-orange">{eyebrow}</p>
        ) : null}
        <h2 className="font-display text-2xl leading-tight text-ink sm:text-3xl">{title}</h2>
        {subtitle ? <p className="mt-1 text-sm text-ink-soft">{subtitle}</p> : null}
      </div>
      {right}
    </div>
  );
}

/** A minimal inline bar, used for distributions where a full chart would be noise. */
export function MiniBar({ value, max, tone = "orange" }: { value: number; max: number; tone?: string }) {
  const pct = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-paper-deep">
      <div
        className="h-full rounded-full"
        style={{ width: `${pct}%`, backgroundColor: `var(--color-${tone})` }}
      />
    </div>
  );
}

export function EmptyState({ message, hint }: { message: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-dashed hairline px-6 py-12 text-center">
      <p className="font-display text-lg text-ink-soft">{message}</p>
      {hint ? <p className="mt-1 text-sm text-ink-faint">{hint}</p> : null}
    </div>
  );
}
