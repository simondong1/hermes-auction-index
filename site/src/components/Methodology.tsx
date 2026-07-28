import { count } from "../lib/format";
import type { Dataset } from "../types";

/**
 * The honesty panel. Everything a reader needs to judge how far to trust the numbers,
 * including what is missing and why.
 */
export function Methodology({ dataset }: { dataset: Dataset }) {
  const { summary, houses, fxSource, generatedAt } = dataset;
  const withheld = summary.by_outcome.result_hidden ?? 0;

  return (
    <section id="methodology" className="space-y-6 border-t hairline pt-8">
      <div>
        <p className="text-[11px] uppercase tracking-[0.14em] text-orange">Methodology</p>
        <h2 className="font-display text-2xl text-ink">How these numbers were built</h2>
      </div>

      <div className="grid gap-8 lg:grid-cols-2">
        <div className="space-y-4 text-sm leading-relaxed text-ink-soft">
          <Block title="Sources">
            <p>
              Every record comes from the auction house's own published results. No
              aggregator, no resale platform, no estimate stands in for a price.
            </p>
            <ul className="mt-2 space-y-2">
              {houses.map((house) => (
                <li key={house.key}>
                  <span className="font-medium text-ink">{house.name}</span>
                  {" — "}
                  <span className="tnum">{count(house.lots)}</span> lots in scope.{" "}
                  {house.note}
                </li>
              ))}
            </ul>
          </Block>

          <Block title="What a price means">
            <p>
              Realised prices are the total the buyer paid,{" "}
              <span className="text-ink">inclusive of buyer's premium</span>, as published
              by the house. Hammer-only figures are never mixed in with premium-inclusive
              ones.
            </p>
          </Block>

          <Block title="Currency conversion">
            <p>
              Each price is converted to renminbi at the {fxSource} for the day of the
              sale — not at today's rate. Sales falling on a weekend or holiday use the
              most recent prior publication; that applied to{" "}
              <span className="tnum">{count(summary.carried_forward_rates)}</span> lots.
            </p>
          </Block>
        </div>

        <div className="space-y-4 text-sm leading-relaxed text-ink-soft">
          <Block title="Withheld results">
            <p>
              Sotheby's declines to publish the realised price on a share of its lots.
              Those <span className="tnum">{count(withheld)}</span> lots appear as{" "}
              <span className="text-ink">result withheld</span> — they are excluded from
              every median and from the sell-through denominator, and are never treated
              as unsold. There is no public route to those prices.
            </p>
          </Block>

          <Block title="Attribute parsing">
            <p>
              Leather, colour, hardware, size, construction and special editions are
              parsed from each catalogue title with a rules-based vocabulary. Terms the
              vocabulary cannot name are recorded rather than guessed.
            </p>
            {Object.keys(summary.unresolved_fields).length > 0 ? (
              <p className="mt-2">
                Currently unresolved:{" "}
                {Object.entries(summary.unresolved_fields)
                  .sort((a, b) => b[1] - a[1])
                  .map(([field, n]) => `${field} on ${count(n)} lots`)
                  .join(", ")}
                .
              </p>
            ) : null}
          </Block>

          <Block title="Scope">
            <p>
              Only the Birkin, Kelly, Mini Kelly and Kelly Pochette handbags are
              included. Kelly-named objects that are not the handbag — Kelly Cut, Kelly
              Danse, Kelly Doll, Kelly Lakis and the rest — are deliberately excluded so
              they cannot distort a size bucket.
            </p>
          </Block>

          <Block title="Reproducibility">
            <p>
              Raw snapshots of every source response are kept alongside the code, so any
              figure here can be traced back to the bytes the house served. Dataset
              generated <span className="tnum">{generatedAt.replace("T", " ")}</span>.
            </p>
          </Block>
        </div>
      </div>
    </section>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="font-display text-base text-ink">{title}</h3>
      <div className="mt-1">{children}</div>
    </div>
  );
}
