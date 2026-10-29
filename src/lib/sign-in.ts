import { supabase } from "./supabase";

export async function sendMagicLink(email: string, redirectTo: string) {
  if (!supabase) throw new Error("Sign-in isn't connected yet.");
  const { error } = await supabase.auth.signInWithOtp({
    email: email.trim(),
    options: { emailRedirectTo: redirectTo },
  });
  if (error) throw error;
}

export async function signInGoogle(redirectTo: string) {
  if (!supabase) throw new Error("Sign-in isn't connected yet.");
  const { error } = await supabase.auth.signInWithOAuth({
    provider: "google",
    options: { redirectTo },
  });
  if (error) throw error;
}