import { describe, expect, it } from "vitest";
import { filtersFromUrl, filtersToUrl, parseParams, stringifyParams } from "@/lib/filters";

const windows: [string, string][] = [
  ["2026-10-31T05:00:00+00:00", "2026-11-01T05:00:00+00:00"],
  ["2026-11-07T05:00:00+00:00", "2026-11-08T05:00:00+00:00"],
];

describe("filters in the URL", () => {
  it("writes one w per window with + as %2B and reads them back unchanged", () => {
    const f = { date_from: "2026-10-31T05:00:00+00:00", date_to: "2026-11-22T05:00:00+00:00", windows };
    const qs = stringifyParams(filtersToUrl(f));
    expect(qs).toContain("w=2026-10-31T05:00:00%2B00:00/2026-11-01T05:00:00%2B00:00&w=2026-11-07T05");
    expect(filtersFromUrl(parseParams(qs)).windows).toEqual(windows);
  });

  it("writes near=me instead of the user's own point", () => {
    const p = filtersToUrl({ lat: 51.5031, lon: -0.1132, radius_km: 1.6 }, { personalPoint: true });
    expect(p.near).toBe("me");
    expect(p.lat).toBeUndefined();
    expect(p.lon).toBeUndefined();
  });
});
