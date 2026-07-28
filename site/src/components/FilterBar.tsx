import { useState } from "react";
import { facetValues } from "../lib/data";
import { count } from "../lib/format";
import type { Filters, HouseMeta, Lot } from "../types";
import { EMPTY_FILTERS } from "../types";

interface Props {
  lots: Lot[];
  houses: HouseMeta[];
  filters: Filters;
  onChange: (filters: Filters) => void;
  resultCount: number;
}

export function FilterBar({ lots, houses, filters, onChange, resultCount }: Props) {
  const [expanded, setExpanded] = useState(false);

  const colourFamilies = facetValues(lots, (lot) => lot.colourFamily);
  const leathers = facetValues(lots, (lot) => lot.leather).slice(0, 24);
  const hardware = facetValues(lots, (lot) => lot.hardware);
  const editions = facetValues(lots, (lot) => lot.editions);
  const years = facetValues(lots, (lot) => lot.saleDate.slice(0, 4))
    .map(Number)
    .sort((a, b) => a - b);

  const toggle = (key: keyof Pick<Filters, "houses" | "colourFamilies" | "leathers" | "hardware" | "editions">, value: string) => {
    const current = filters[key];
    onChange({
      ...filters,
      [key]: current.includes(value) ? current.filter((v) => v !== value) : [...current, value],
    });
  };

  const activeCount =
    filters.houses.length +
    filters.colourFamilies.length +
    filters.leathers.length +
    filters.hardware.length +
    filters.editions.length +
    (filters.yearFrom != null ? 1 : 0) +
    (filters.yearTo != null ? 1 : 0) +
    (filters.query ? 1 : 0) +
    // Counted only when switched on, since off is the default.
    (filters.soldOnly ? 1 : 0);

  return (
    <div className="sticky top-0 z-20 -mx-4 border-b hairline bg-paper/90 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-2">
        <input
          type="search"
          value={filters.query}
          onChange={(event) => onChange({ ...filters, query: event.target.value })}
          placeholder="Search colour, leather, sale…"
          className="min-w-0 flex-1 rounded-md border hairline bg-white px-3 py-1.5 text-sm text-ink placeholder:text-ink-faint focus:outline-none focus-visible:ring-2 focus-visible:ring-orange/40"
        />

        <label className="flex cursor-pointer items-center gap-1.5 rounded-md border hairline bg-white px-2.5 py-1.5 text-sm text-ink-soft">
          <input
            type="checkbox"
            checked={filters.soldOnly}
            onChange={(event) => onChange({ ...filters, soldOnly: event.target.checked })}
            className="accent-[var(--color-orange)]"
          />
          Sold only
        </label>

        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          className="rounded-md border hairline bg-white px-2.5 py-1.5 text-sm text-ink-soft hover:border-orange/40"
        >
          Filters{activeCount > 0 ? ` (${activeCount})` : ""}
        </button>

        {activeCount > 0 ? (
          <button
            type="button"
            onClick={() => onChange({ ...EMPTY_FILTERS })}
            className="rounded-md px-2 py-1.5 text-sm text-orange hover:underline"
          >
            Reset
          </button>
        ) : null}

        <span className="tnum ml-auto text-sm text-ink-faint">{count(resultCount)} lots</span>
      </div>

      {expanded ? (
        <div className="mx-auto mt-3 max-w-6xl space-y-3 border-t hairline pt-3">
          <FacetRow
            label="House"
            options={houses.filter((h) => h.lots > 0).map((h) => h.key)}
            selected={filters.houses}
            labelFor={(key) => houses.find((h) => h.key === key)?.name ?? key}
            onToggle={(value) => toggle("houses", value)}
          />
          <FacetRow
            label="Colour"
            options={colourFamilies}
            selected={filters.colourFamilies}
            onToggle={(value) => toggle("colourFamilies", value)}
          />
          <FacetRow
            label="Leather"
            options={leathers}
            selected={filters.leathers}
            onToggle={(value) => toggle("leathers", value)}
          />
          <FacetRow
            label="Hardware"
            options={hardware}
            selected={filters.hardware}
            onToggle={(value) => toggle("hardware", value)}
          />
          {editions.length > 0 ? (
            <FacetRow
              label="Edition"
              options={editions}
              selected={filters.editions}
              onToggle={(value) => toggle("editions", value)}
            />
          ) : null}

          <div className="flex flex-wrap items-center gap-2">
            <span className="w-20 shrink-0 text-[11px] uppercase tracking-[0.08em] text-ink-faint">
              Sale year
            </span>
            <YearSelect
              value={filters.yearFrom}
              years={years}
              placeholder="From"
              onChange={(value) => onChange({ ...filters, yearFrom: value })}
            />
            <span className="text-ink-faint">–</span>
            <YearSelect
              value={filters.yearTo}
              years={years}
              placeholder="To"
              onChange={(value) => onChange({ ...filters, yearTo: value })}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}

function FacetRow({
  label,
  options,
  selected,
  onToggle,
  labelFor = (value: string) => value,
}: {
  label: string;
  options: string[];
  selected: string[];
  onToggle: (value: string) => void;
  labelFor?: (value: string) => string;
}) {
  if (options.length === 0) return null;
  return (
    <div className="flex flex-wrap items-start gap-2">
      <span className="mt-1 w-20 shrink-0 text-[11px] uppercase tracking-[0.08em] text-ink-faint">
        {label}
      </span>
      <div className="flex flex-wrap gap-1.5">
        {options.map((option) => {
          const active = selected.includes(option);
          return (
            <button
              key={option}
              type="button"
              aria-pressed={active}
              onClick={() => onToggle(option)}
              className={`rounded-full px-2.5 py-1 text-xs ring-1 transition ${
                active
                  ? "bg-orange text-white ring-orange"
                  : "bg-white text-ink-soft ring-rule hover:ring-orange/40"
              }`}
            >
              {labelFor(option)}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function YearSelect({
  value,
  years,
  placeholder,
  onChange,
}: {
  value: number | null;
  years: number[];
  placeholder: string;
  onChange: (value: number | null) => void;
}) {
  return (
    <select
      value={value ?? ""}
      onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)}
      className="rounded-md border hairline bg-white px-2 py-1 text-sm text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-orange/40"
    >
      <option value="">{placeholder}</option>
      {years.map((year) => (
        <option key={year} value={year}>
          {year}
        </option>
      ))}
    </select>
  );
}
