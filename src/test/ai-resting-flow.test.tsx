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
vi.mock("@/components/ClarifyStep", () => ({ ClarifyStep: () => null }));
import { AgentError } from "@/lib/api";
import { Route } from "@/routes/index";

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

async function search() {
  fireEvent.change(screen.getByRole("searchbox", { name: "Search" }), { target: { value: "Jazz in Soho tonight" } });
  fireEvent.click(screen.getByRole("button", { name: "AI search" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "AI search" })).toBeEnabled());
}

function renderPage() {
  const Page = Route.options.component;
  if (!Page) throw new Error("Missing home page");
  render(<Page />);
}

describe("Signed-in resting notice flow (simulated session)", () => {
  it("keeps a healthy search free of a resting state", async () => {
    mocks.submitAi.mockResolvedValue({ status: "ok", query_id: "latest-query", filters: {}, results: [], notes: [], location_source: null });
    renderPage();
    await search();
    expect(mocks.submitAi).toHaveBeenCalledWith("Jazz in Soho tonight", undefined, null);
    expect(document.getElementById("ai-resting")).toBeNull();
    expect(screen.getByRole("textbox", { name: "Follow-up" })).toBeInTheDocument();
  });

  it.each(["daily_cap", "providers", "unavailable"])("enters resting for a %s reply", async (reason) => {
    mocks.submitAi.mockResolvedValue({ status: "ai_resting", reason });
    renderPage();
    await search();
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("enters resting only for unreachable requests, then recovers on success", async () => {
    mocks.submitAi.mockRejectedValueOnce(new AgentError(null, "Unreachable"))
      .mockResolvedValueOnce({ status: "ok", query_id: "recovered-query", filters: {}, results: [], notes: [], location_source: null });
    renderPage();
    await search();
    expect(screen.getByRole("status")).toBeInTheDocument();
    await search();
    expect(document.getElementById("ai-resting")).toBeNull();
    expect(screen.getByRole("textbox", { name: "Follow-up" })).toBeInTheDocument();
  });

  it.each([401, 422, 503])("keeps HTTP %s separate from resting", async (status) => {
    mocks.submitAi.mockRejectedValue(new AgentError(status, "Request failed"));
    renderPage();
    await search();
    expect(document.getElementById("ai-resting")).toBeNull();
    expect(mocks.submitAi).toHaveBeenCalledTimes(1);
  });
});