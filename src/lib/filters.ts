// SearchFilters <-> URL. One query model for AI search and the filter panel.

export const CATEGORIES = [
  "Music",
  "Comedy",
  "Theatre",
  "Sport",
  "Nightlife",
  "Arts",
  "Family",
  "Food & Drink",
] as const;

export const DEFAULT_AREA = { lat: 51.507333, lon: -0.127654, radius_km: 10 };
export const KM_PER_MILE = 1.609344;

export type SearchFilters = {
  date_from?: string;
  date_to?: string;
  categories?: string[];
  price_min?: number;
  price_max?: number;
  include_unpriced?: boolean;
  lat?: number;
  lon?: number;
  radius_km?: number;
  windows?: [string, string][];
};

/** Raw URL params: every value is a string or a list of strings. */
export type UrlParams = Record<string, string | string[] | undefined>;

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);
const many = (v: string | string[] | undefined) => (v === undefined ? [] : Array.isArray(v) ? v : [v]);
const num = (v: string | string[] | undefined) => {
  const s = one(v);
  if (s === undefined || s === "") return undefined;
  const n = Number(s);
  return Number.isFinite(n) ? n : undefined;
};

export function filtersFromUrl(p: UrlParams): SearchFilters {
  const f: SearchFilters = {};
  const df = one(p.date_from);
  const dt = one(p.date_to);
  if (df) f.date_from = df;
  if (dt) f.date_to = dt;
  const cats = many(p.cat).filter((c) => (CATEGORIES as readonly string[]).includes(c));
  if (cats.length) f.categories = cats;
  const pmin = num(p.price_min);
  const pmax = num(p.price_max);
  if (pmin !== undefined) f.price_min = pmin;
  if (pmax !== undefined) f.price_max = pmax;
  if (one(p.unpriced) === "0") f.include_unpriced = false;
  const lat = num(p.lat);
  const lon = num(p.lon);
  const r = num(p.radius_km);
  if (lat !== undefined && lon !== undefined && r !== undefined) {
    f.lat = lat;
    f.lon = lon;
    f.radius_km = r;
  }
  const ws = many(p.w)
    .map((w) => w.split("/"))
    .filter((pair): pair is [string, string] => pair.length === 2 && !!pair[0] && !!pair[1]);
  if (ws.length && f.date_from && f.date_to) f.windows = ws;
  return f;
}

/** Filters to URL params. The point is left out when it's the user's own (`personalPoint`). */
export function filtersToUrl(f: SearchFilters, opts: { personalPoint?: boolean } = {}): UrlParams {
  const p: UrlParams = {};
  if (f.date_from) p.date_from = f.date_from;
  if (f.date_to) p.date_to = f.date_to;
  if (f.categories?.length) p.cat = [...f.categories];
  if (f.price_min !== undefined) p.price_min = String(f.price_min);
  if (f.price_max !== undefined) p.price_max = String(f.price_max);
  if (f.include_unpriced === false) p.unpriced = "0";
  if (f.radius_km !== undefined && f.lat !== undefined && f.lon !== undefined) {
    if (opts.personalPoint) {
      p.near = "me";
    } else {
      p.lat = String(f.lat);
      p.lon = String(f.lon);
    }
    p.radius_km = String(f.radius_km);
  }
  if (f.windows?.length) p.w = f.windows.map(([a, b]) => `${a}/${b}`);
  return p;
}

/** Filters ready for search_items_public: default area when no place is set. */
export function withDefaultArea(f: SearchFilters): { filters: SearchFilters; defaultArea: boolean } {
  if (f.lat !== undefined && f.lon !== undefined && f.radius_km !== undefined) {
    return { filters: f, defaultArea: false };
  }
  return { filters: { ...f, ...DEFAULT_AREA }, defaultArea: true };
}

export function hasAnyFilter(p: UrlParams): boolean {
  return ["date_from", "date_to", "cat", "price_min", "price_max", "unpriced", "lat", "radius_km", "w", "near"].some(
    (k) => p[k] !== undefined,
  );
}

// URL query string with repeated keys for lists (w=…&w=…), "+" encoded as %2B.
export function stringifyParams(p: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(p)) {
    if (v === undefined || v === null || v === "") continue;
    const vals = Array.isArray(v) ? v : [v];
    for (const x of vals) {
      parts.push(
        `${encodeURIComponent(k)}=${encodeURIComponent(String(x)).replace(/%2F/g, "/").replace(/%3A/g, ":")}`,
      );
    }
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

export function parseParams(search: string): UrlParams {
  const out: UrlParams = {};
  const s = search.startsWith("?") ? search.slice(1) : search;
  if (!s) return out;
  for (const part of s.split("&")) {
    if (!part) continue;
    const i = part.indexOf("=");
    const k = decodeURIComponent(i < 0 ? part : part.slice(0, i));
    const v = i < 0 ? "" : decodeURIComponent(part.slice(i + 1).replace(/\+/g, " "));
    const prev = out[k];
    out[k] = prev === undefined ? v : Array.isArray(prev) ? [...prev, v] : [prev, v];
  }
  return out;
}

// Dates: London day boundaries at 05:00, as the agent uses.
function londonOffset(date: string): string {
  const d = new Date(`${date}T12:00:00Z`);
  const name =
    new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/London", timeZoneName: "shortOffset" })
      .formatToParts(d)
      .find((x) => x.type === "timeZoneName")?.value ?? "GMT";
  return name === "GMT" ? "+00:00" : "+01:00";
}
export function dayStart(date: string): string {
  return `${date}T05:00:00${londonOffset(date)}`;
}
export function dayAfterStart(date: string): string {
  const d = new Date(`${date}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 1);
  const next = d.toISOString().slice(0, 10);
  return dayStart(next);
}
/** ISO time back to the London date (yyyy-mm-dd), for date inputs. */
export function londonDate(iso: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/London" }).format(new Date(iso));
}
export function londonDateMinus5h(iso: string): string {
  return londonDate(new Date(new Date(iso).getTime() - 5 * 3600_000 - 1).toISOString());
}
