import { useState } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { sendMagicLink, signInGoogle } from "@/lib/sign-in";

export function AuthDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [email, setEmail] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await sendMagicLink(email, window.location.origin);
      setMsg("Check your email for your sign-in link.");
    } catch (error) {
      setMsg(error instanceof Error ? error.message : "Couldn't send your sign-in link. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function google() {
    setBusy(true);
    setMsg(null);
    try {
      await signInGoogle(window.location.origin);
    } catch (error) {
      setMsg(error instanceof Error ? error.message : "Google sign-in isn't available. Try email instead.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Sign in</DialogTitle>
          <DialogDescription>Sign in to use AI search. Filters work without an account.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          {msg && <p role="status" className="text-sm text-muted-foreground">{msg}</p>}
          <Button type="submit" className="w-full" disabled={busy}>
            Send magic link
          </Button>
          <Button type="button" variant="outline" className="w-full" disabled={busy} onClick={google}>Continue with Google</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
