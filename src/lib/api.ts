// Every agent call goes through this module (docs/api.md section 1).
import { supabase } from "./supabase";
import type { SearchFilters } from "./filters";

export const AGENT_URL = (
  (import.meta.env["VITE_AGENT_URL"] as string | undefined) ?? "https://any-plans.onrender.com"
).replace(/\/$/, "");

export type Location = { lat: number; lon: number; source: "device" | "pin" };

export type Question = {
  reason: string;
  question: string;
  choices: string[];
  groups: { field: string | null; choices: string[] }[] | null;
};

export type ResultCard = {
  rank?: number;
  id: string;
  source: string;
  title: string;
  category: string | null;
  genre: string | null;
  start_at: string;
  end_at: string | null;
  venue_name: string | null;
  price_min: number | null;
  price_max: number | null;
  currency: string | null;
  url: string | null;
  image_url: string | null;
  last_checked_at: string | null;
  confidence: "checked_today" | "check_site" | "web";
  confidence_label: string;
  distance_m: number | null;
  lat: number | null;
  lon: number | null;
};

export type SearchResponse = {
  status: "ok" | "needs_clarification" | "ai_resting" | "resting";
  reason: "providers" | "daily_cap" | "unavailable" | null;
  message: string | null;
  query_id: string;
  filters: SearchFilters | null;
  vibe: string | null;
  who: string | null;
  location_source: "device" | "pin" | null;
  notes: { code: string; message: string }[];
  missing: string[];
  questions: Question[];
  results: ResultCard[] | null;
};

export class AgentError extends Error {
  constructor(
    public status: number | null,
    message: string,
  ) {
    super(message);
  }
}

// A failed request is not necessarily a resting agent: auth, validation and
// earlier-query errors must remain distinct. Daily caps arrive as ai_resting.
export function isAiResting(value: unknown): boolean {
  if (value instanceof AgentError) return value.status === null;
  if (typeof value !== "object" || value === null || !("status" in value)) return false;
  return value.status === "ai_resting" || value.status === "resting";
}

async function token(refresh = false): Promise<string | null> {
  if (!supabase) return null;
  if (refresh) {
    const { data } = await supabase.auth.refreshSession();
    return data.session?.access_token ?? null;
  }
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

async function postAgent<T>(path: string, body: unknown): Promise<T> {
  const send = async (t: string | null) => {
    try {
      return await fetch(`${AGENT_URL}${path}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(t ? { Authorization: `Bearer ${t}` } : {}),
        },
        body: JSON.stringify(body),
      });
    } catch {
      throw new AgentError(null, "The search service can't be reached");
    }
  };
  let res = await send(await token());
  if (res.status === 401) res = await send(await token(true)); // refresh once
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (typeof j.detail === "string") detail = j.detail;
    } catch {
      /* not JSON */
    }
    throw new AgentError(res.status, detail);
  }
  return (await res.json()) as T;
}

export function aiSearch(query: string, location?: Location | null) {
  const body: { query: string; location?: Location } = { query };
  if (location) body.location = location;
  return postAgent<SearchResponse>("/search", body);
}

export function aiRefine(query_id: string, message: string, location?: Location | null) {
  const body: { query_id: string; message: string; location?: Location } = { query_id, message };
  if (location) body.location = location;
  return postAgent<SearchResponse>("/refine", body);
}

// Only the explicit follow-up field supplies a successful query ID.
export function submitAi(query: string, successfulQueryId?: string | null) {
  return successfulQueryId ? aiRefine(successfulQueryId, query) : aiSearch(query);
}

// Filter-only search (docs/api.md section 13). Not an agent call.
export async function filterSearch(filters: SearchFilters): Promise<ResultCard[]> {
  if (!supabase) throw new Error("Search isn't set up yet");
  const { data, error } = await supabase.rpc("search_items_public", { filters });
  if (error) throw new Error("Search is unavailable right now");
  return (data ?? []) as ResultCard[];
}
