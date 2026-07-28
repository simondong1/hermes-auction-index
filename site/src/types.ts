/** Shapes emitted by `hermes-auction build` into `data/processed/dataset.json`. */

export type Outcome = "sold" | "unsold" | "withdrawn" | "result_hidden" | "unknown";

export type FamilyKey = "birkin" | "kelly" | "mini_kelly" | "kelly_pochette";

export interface Lot {
  id: string;
  house: string;
  houseName: string;
  saleName: string;
  saleDate: string;
  location: string | null;
  lotNumber: string | null;
  title: string;
  url: string | null;
  image: string | null;

  family: FamilyKey;
  size: number | null;
  sizeBucket: string;
  leather: string | null;
  leatherCategory: string | null;
  colour: string | null;
  colourFamily: string | null;
  colourHex: string | null;
  hardware: string | null;
  hardwareAbbrev: string | null;
  construction: string | null;
  editions: string[];
  stampYear: number | null;
  grade: string | null;

  outcome: Outcome;
  currency: string;
  estLow: number | null;
  estHigh: number | null;
  price: number | null;
  priceCny: number | null;
  estLowCny: number | null;
  estHighCny: number | null;
  fxRate: number | null;
  fxDate: string | null;
  priceBasis: string;
}

export interface FamilyMeta {
  key: FamilyKey;
  name: string;
  headlineSizes: number[];
  nonexistentSizes: number[];
  sizeless: boolean;
}

export interface HouseMeta {
  key: string;
  name: string;
  note: string;
  lots: number;
}

export interface Dataset {
  generatedAt: string;
  families: FamilyMeta[];
  houses: HouseMeta[];
  fxSource: string;
  summary: {
    raw_lots: number;
    in_scope: number;
    sold: number;
    priced_in_cny: number;
    by_house: Record<string, number>;
    by_family: Record<string, number>;
    by_outcome: Record<string, number>;
    unresolved_fields: Record<string, number>;
    fx_failures: number;
    carried_forward_rates: number;
  };
  lots: Lot[];
}

/** Active filter state, kept in the URL so any view is shareable. */
export interface Filters {
  houses: string[];
  colourFamilies: string[];
  leathers: string[];
  hardware: string[];
  editions: string[];
  yearFrom: number | null;
  yearTo: number | null;
  soldOnly: boolean;
  query: string;
}

export const EMPTY_FILTERS: Filters = {
  houses: [],
  colourFamilies: [],
  leathers: [],
  hardware: [],
  editions: [],
  yearFrom: null,
  yearTo: null,
  soldOnly: true,
  query: "",
};
