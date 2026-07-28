import { cny, nativeMoney, shortDate } from "../lib/format";
import type { Lot, Outcome } from "../types";
import { EmptyState, Pill } from "./primitives";

const OUTCOME_LABEL: Record<Outcome, string> = {
  sold: "Sold",
  unsold: "Bought in",
  withdrawn: "Withdrawn",
  result_hidden: "Result withheld",
  unknown: "Unknown",
};

const OUTCOME_TONE: Record<Outcome, "neutral" | "orange" | "moss" | "plum" | "muted"> = {
  sold: "moss",
  unsold: "muted",
  withdrawn: "muted",
  result_hidden: "plum",
  unknown: "muted",
};

/** The leaf view: every individual sale record behind a bucket. */
export function LotTable({ lots, caption }: { lots: Lot[]; caption?: string }) {
  if (lots.length === 0) {
    return <EmptyState message="No lots match the current filters." />;
  }

  const sorted = [...lots].sort((a, b) => b.saleDate.localeCompare(a.saleDate));

  return (
    <div className="overflow-hidden rounded-xl border hairline bg-white/60">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[52rem] border-collapse text-sm">
          {caption ? <caption className="sr-only">{caption}</caption> : null}
          <thead>
            <tr className="border-b hairline bg-paper-deep/50 text-left">
              <Th>Lot</Th>
              <Th>Sale</Th>
              <Th>Date</Th>
              <Th align="right">Estimate</Th>
              <Th align="right">Realised</Th>
              <Th align="right">Realised (CNY)</Th>
              <Th>Result</Th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((lot) => (
              <tr key={lot.id} className="border-b hairline last:border-0 hover:bg-orange-soft/30">
                <td className="px-3 py-2.5 align-top">
                  <div className="flex items-start gap-2.5">
                    {lot.image ? (
                      <img
                        src={lot.image}
                        alt=""
                        loading="lazy"
                        className="size-10 shrink-0 rounded object-cover"
                        onError={(event) => {
                          event.currentTarget.style.visibility = "hidden";
                        }}
                      />
                    ) : null}
                    <div className="min-w-0">
                      {lot.url ? (
                        <a
                          href={lot.url}
                          target="_blank"
                          rel="noreferrer noopener"
                          className="line-clamp-2 max-w-[22rem] text-ink underline decoration-rule underline-offset-2 hover:decoration-orange"
                        >
                          {lot.title}
                        </a>
                      ) : (
                        <span className="line-clamp-2 max-w-[22rem] text-ink">{lot.title}</span>
                      )}
                      <div className="mt-1 flex flex-wrap gap-1">
                        {lot.stampYear ? <Pill tone="muted">{lot.stampYear}</Pill> : null}
                        {lot.grade ? <Pill tone="muted">Grade {lot.grade}</Pill> : null}
                        {lot.construction ? <Pill tone="muted">{lot.construction}</Pill> : null}
                        {lot.editions.map((edition) => (
                          <Pill key={edition} tone="orange">
                            {edition}
                          </Pill>
                        ))}
                      </div>
                    </div>
                  </div>
                </td>
                <td className="px-3 py-2.5 align-top">
                  <div className="max-w-[15rem] text-ink-soft">{lot.saleName}</div>
                  <div className="text-[11px] text-ink-faint">
                    {lot.houseName}
                    {lot.location ? ` · ${lot.location}` : ""}
                    {lot.lotNumber ? ` · Lot ${lot.lotNumber}` : ""}
                  </div>
                </td>
                <td className="tnum whitespace-nowrap px-3 py-2.5 align-top text-ink-soft">
                  {shortDate(lot.saleDate)}
                </td>
                <td className="tnum whitespace-nowrap px-3 py-2.5 text-right align-top text-ink-faint">
                  {lot.estLow == null
                    ? "—"
                    : `${nativeMoney(lot.estLow, lot.currency)} – ${nativeMoney(lot.estHigh, lot.currency)}`}
                </td>
                <td className="tnum whitespace-nowrap px-3 py-2.5 text-right align-top text-ink-soft">
                  {nativeMoney(lot.price, lot.currency)}
                </td>
                <td className="tnum whitespace-nowrap px-3 py-2.5 text-right align-top font-medium text-ink">
                  {cny(lot.priceCny)}
                  {lot.fxRate ? (
                    <div className="text-[10px] font-normal text-ink-faint">
                      @ {lot.fxRate.toFixed(4)} on {lot.fxDate}
                    </div>
                  ) : null}
                </td>
                <td className="px-3 py-2.5 align-top">
                  <Pill tone={OUTCOME_TONE[lot.outcome]}>{OUTCOME_LABEL[lot.outcome]}</Pill>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Th({ children, align = "left" }: { children: React.ReactNode; align?: "left" | "right" }) {
  return (
    <th
      scope="col"
      className={`px-3 py-2 text-[11px] font-medium uppercase tracking-[0.08em] text-ink-faint ${
        align === "right" ? "text-right" : "text-left"
      }`}
    >
      {children}
    </th>
  );
}
