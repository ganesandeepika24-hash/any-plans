import { createClient, type SupabaseClient } from "@supabase/supabase-js";

// Read from environment variables only (never hardcoded): VITE_SUPABASE_URL and
// VITE_SUPABASE_PUBLISHABLE_KEY.
const url = import.meta.env["VITE_SUPABASE_URL"] as string | undefined;
const key = import.meta.env["VITE_SUPABASE_PUBLISHABLE_KEY"] as string | undefined;

export const supabaseConfigured = Boolean(url && key);

export const supabase: SupabaseClient | null = supabaseConfigured
  ? createClient(url!, key!, {
      auth: {
        persistSession: typeof window !== "undefined",
        autoRefreshToken: typeof window !== "undefined",
      },
    })
  : null;
