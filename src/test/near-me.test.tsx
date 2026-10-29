import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ submitAi: vi.fn(), navigate: vi.fn() }));
vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: Record<string, unknown>) => ({ options, useSearch: () => ({}) }),
  useNavigate: () => mocks.navigate,
}));
vi.mock("@tanstack/react-query", () => ({ useQuery: () => ({ data: undefined }) }));
vi.mock("@/hooks/useAuth", () => ({ useAuth: () => ({ session: { user: { id: "test-user" } }, ready: true }) }));
vi.mock("@/lib/supabase", () => ({ supabase: null, supabaseConfigured: true }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(), submitAi: mocks.submitAi,
}));
vi.mock("@/components/FilterPanel", () => ({ FilterPanel: () => null }));
vi.mock("@/components/AuthDialog", () => ({ AuthDialog: () => null }));
import { hasNearMe, NEAR_ME_PHRASES } from "@/lib/api";
import { resetGeoForTests } from "@/lib/geo";
import { Route } from "@/routes/index";

const POS = { coords: { latitude: 51.5031, longitude: -0.1132 } };
function stubGeo(state: PermissionState, ok = true) {
  const getCurrentPosition = vi.fn((yes: PositionCallback, no: PositionErrorCallback) =>
    ok ? yes(POS as GeolocationPosition) : no({ code: 1 } as GeolocationPositionError));
  Object.defineProperty(navigator, "geolocation", { value: { getCurrentPosition }, configurable: true });
  Object.defineProperty(navigator, "permissions", { value: { query: vi.fn().mockResolvedValue({ state }) }, configurable: true });
  return getCurrentPosition;
}
const okReply = (source: "device" | null, extra = {}) => ({ status: "ok", query_id: "q1", filters: { lat: 51.5031, lon: -0.1132, radius_km: 1.6 }, results: [], notes: [], missing: [], questions: [], location_source: source, ...extra });
const DEVICE = { lat: 51.5031, lon: -0.1132, source: "device" };

beforeEach(() => { vi.clearAllMocks(); resetGeoForTests(); });
afterEach(cleanup);

function renderPage() {
  const Page = Route.options.component!;
  render(<Page />);
}
async function search(text: string) {
  fireEvent.change(screen.getByRole("searchbox", { name: "Search" }), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "AI search" }));
  await waitFor(() => expect(mocks.submitAi).toHaveBeenCalled());
}

describe("near-me phrases", () => {
  it("matches docs/api.md section 8 exactly", () => {
    expect([...NEAR_ME_PHRASES]).toEqual(["near me", "nearby", "nearby me", "around me", "around here"]);
  });
  it("spots whole words only, ignoring case", () => {
    expect(hasNearMe("Something NEAR ME tonight")).toBe(true);
    expect(hasNearMe("comedy around here")).toBe(true);
    expect(hasNearMe("jazz in Soho")).toBe(false);
    expect(hasNearMe("nearmeal")).toBe(false);
  });
});

describe("near me in one search", () => {
  it("(a) allowed: one /search with the device location", async () => {
    stubGeo("granted");
    mocks.submitAi.mockResolvedValue(okReply("device"));
    renderPage();
    await search("something near me tonight");
    expect(mocks.submitAi).toHaveBeenCalledTimes(1);
    expect(mocks.submitAi).toHaveBeenCalledWith("something near me tonight", undefined, DEVICE);
    await screen.findByText("Near you");
    const url = mocks.navigate.mock.calls.at(-1)![0].search;
    expect(url.near).toBe("me");
    expect(url.lat).toBeUndefined();
  });
  it("(b) blocked: one /search without location, Use my location hidden", async () => {
    const get = stubGeo("denied");
    mocks.submitAi.mockResolvedValue({ ...okReply(null), status: "needs_clarification", filters: null, missing: ["location"],
      questions: [{ reason: "location", question: "Something: which part of London?", choices: ["Use my location", "Pick an area", "Drop a pin on the map"], groups: null }] });
    renderPage();
    await search("something near me tonight");
    expect(get).not.toHaveBeenCalled();
    expect(mocks.submitAi).toHaveBeenCalledWith("something near me tonight", undefined, null);
    await screen.findByRole("button", { name: "Pick an area" });
    expect(screen.getByRole("button", { name: "Drop a pin on the map" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use my location" })).toBeNull();
  });
  it("(c) no phrase: no position read and no location sent", async () => {
    const get = stubGeo("granted");
    mocks.submitAi.mockResolvedValue(okReply(null));
    renderPage();
    await search("jazz in Soho");
    expect(get).not.toHaveBeenCalled();
    expect(mocks.submitAi).toHaveBeenCalledWith("jazz in Soho", undefined, null);
  });
  it("(d) make it cheaper resends the device location to /refine", async () => {
    stubGeo("granted");
    mocks.submitAi.mockResolvedValue(okReply("device"));
    renderPage();
    await search("something near me tonight");
    const follow = await screen.findByRole("textbox", { name: "Follow-up" });
    fireEvent.change(follow, { target: { value: "make it cheaper" } });
    fireEvent.submit(follow.closest("form")!);
    await waitFor(() => expect(mocks.submitAi).toHaveBeenCalledTimes(2));
    expect(mocks.submitAi).toHaveBeenLastCalledWith("make it cheaper", "q1", DEVICE);
  });
});
