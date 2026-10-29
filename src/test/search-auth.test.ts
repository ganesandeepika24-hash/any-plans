import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({
  signInWithOtp: vi.fn(),
  signInWithOAuth: vi.fn(),
  getSession: vi.fn(),
  refreshSession: vi.fn(),
}));
vi.mock("@/lib/supabase", () => ({ supabase: { auth } }));
import { sendMagicLink, signInGoogle } from "@/lib/sign-in";
import { submitAi } from "@/lib/api";

beforeEach(() => {
  vi.clearAllMocks();
  auth.getSession.mockResolvedValue({ data: { session: { access_token: "test-session-token" } } });
  auth.signInWithOtp.mockResolvedValue({ error: null });
  auth.signInWithOAuth.mockResolvedValue({ error: null });
});
afterEach(() => vi.unstubAllGlobals());

describe("sign-in methods", () => {
  it("sends an email magic link, not password credentials", async () => {
    await sendMagicLink(" person@example.com ", "https://london-daydream-finder.lovable.app");
    expect(auth.signInWithOtp).toHaveBeenCalledWith({ email: "person@example.com", options: { emailRedirectTo: "https://london-daydream-finder.lovable.app" } });
  });
  it("uses Google OAuth and returns to the published home page", async () => {
    await signInGoogle("https://london-daydream-finder.lovable.app");
    expect(auth.signInWithOAuth).toHaveBeenCalledWith({ provider: "google", options: { redirectTo: "https://london-daydream-finder.lovable.app" } });
  });
});

describe("AI request routing", () => {
  it("sends new searches to Render /search with the signed-in Bearer token", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ok" })));
    vi.stubGlobal("fetch", fetcher);
    await submitAi("Jazz in Soho");
    expect(fetcher).toHaveBeenCalledWith("https://any-plans.onrender.com/search", expect.objectContaining({ method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer test-session-token" }, body: JSON.stringify({ query: "Jazz in Soho" }) }));
  });
  it("sends make it cheaper to /refine with the latest successful query ID", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ok" })));
    vi.stubGlobal("fetch", fetcher);
    await submitAi("make it cheaper", "8dfcc1ca-d29b-4df7-890d-59348c706725");
    expect(fetcher).toHaveBeenCalledWith("https://any-plans.onrender.com/refine", expect.objectContaining({ body: JSON.stringify({ query_id: "8dfcc1ca-d29b-4df7-890d-59348c706725", message: "make it cheaper" }), headers: { "Content-Type": "application/json", Authorization: "Bearer test-session-token" } }));
  });
  it("refreshes a rejected token only once", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response("", { status: 401 })).mockResolvedValueOnce(new Response(JSON.stringify({ status: "ok" })));
    auth.refreshSession.mockResolvedValue({ data: { session: { access_token: "refreshed-test-token" } } });
    vi.stubGlobal("fetch", fetcher);
    await submitAi("Jazz");
    expect(auth.refreshSession).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[1]?.[1].headers.Authorization).toBe("Bearer refreshed-test-token");
  });
});