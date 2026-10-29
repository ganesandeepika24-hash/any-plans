import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/supabase", () => ({ supabase: null }));
import { AgentError, isAiResting } from "@/lib/api";

describe("AI resting conditions", () => {
  it("shows resting when a request cannot reach the agent", () => {
    expect(isAiResting(new AgentError(null, "Unreachable"))).toBe(false);
  });

  it("shows resting when the daily limit reply arrives", () => {
    expect(isAiResting({ status: "ai_resting", reason: "daily_cap" })).toBe(true);
  });

  it.each(["providers", "unavailable"])("shows resting for the agent's %s resting reply", (reason) => {
    expect(isAiResting({ status: "ai_resting", reason })).toBe(true);
  });

  it("also accepts an explicit resting status", () => {
    expect(isAiResting({ status: "resting" })).toBe(true);
  });

  it("hides resting for a successful signed-in search reply", () => {
    expect(isAiResting({ status: "ok", query_id: "signed-in-query", results: [] })).toBe(false);
  });

  it("hides resting when a successful reply falls back to filters", () => {
    expect(isAiResting({ status: "ok", results: null, message: "Search unavailable" })).toBe(false);
  });

  it("hides resting while the agent asks for clarification", () => {
    expect(isAiResting({ status: "needs_clarification" })).toBe(false);
  });

  it.each([401, 403, 404, 409, 422, 429, 500, 503])("does not mistake HTTP %s for a resting reply", (status) => {
    expect(isAiResting(new AgentError(status, "Request failed"))).toBe(false);
  });

  it("does not mistake an unexpected client error for an unreachable agent", () => {
    expect(isAiResting(new Error("Invalid response"))).toBe(false);
  });

  it("clears resting when a later reply succeeds", () => {
    const replies = [{ status: "ai_resting", reason: "daily_cap" }, { status: "ok" }];
    expect(replies.map(isAiResting)).toEqual([true, false]);
  });
});