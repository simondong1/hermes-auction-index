const CNY = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "CNY",
  maximumFractionDigits: 0,
});

const CNY_COMPACT = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "CNY",
  notation: "compact",
  maximumFractionDigits: 1,
});

const PERCENT = new Intl.NumberFormat("en-US", {
  style: "percent",
  maximumFractionDigits: 0,
});

const DATE = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

export function cny(value: number | null | undefined): string {
  return value == null ? "—" : CNY.format(value);
}

export function cnyCompact(value: number | null | undefined): string {
  return value == null ? "—" : CNY_COMPACT.format(value);
}

export function nativeMoney(value: number | null | undefined, currency: string): string {
  if (value == null) return "—";
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    }).format(value);
  } catch {
    // A currency code Intl does not know still deserves a readable number.
    return `${currency} ${Math.round(value).toLocaleString("en-US")}`;
  }
}

export function percent(value: number | null | undefined): string {
  return value == null ? "—" : PERCENT.format(value);
}

export function shortDate(iso: string): string {
  return DATE.format(new Date(`${iso}T00:00:00Z`));
}

export function count(value: number): string {
  return value.toLocaleString("en-US");
}

/** "3 lots" / "1 lot" — used a lot in bucket subtitles. */
export function plural(n: number, singular: string, pluralForm = `${singular}s`): string {
  return `${count(n)} ${n === 1 ? singular : pluralForm}`;
}
