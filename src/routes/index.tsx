import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Loader2, Search, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AuthDialog } from "@/components/AuthDialog";
import { ClarifyStep } from "@/components/ClarifyStep";
import { FilterPanel } from "@/components/FilterPanel";
import { ResultCardView } from "@/components/ResultCardView";
import { useAuth } from "@/hooks/useAuth";
import { AgentError, submitAi, filterSearch, type ResultCard, type SearchResponse } from "@/lib/api";
import { supabase, supabaseConfigured } from "@/lib/supabase";
import {
  filtersFromUrl,
  filtersToUrl,
  hasAnyFilter,
  withDefaultArea,
  type SearchFilters,
  type UrlParams,
} from "@/lib/filters";

export const Route = createFileRoute("/")({
  validateSearch: (s: Record<string, unknown>) => s as UrlParams,
  head: () => ({
    meta: [
      { title: "Any Plans? — Find something to do in London" },
      { name: "description", content: "Search gigs, comedy, theatre and more happening in London, by words or by filters." },
      { property: "og:title", content: "Any Plans? — Find something to do in London" },
      { property: "og:description", content: "One search, every plan in the city." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: Index,
});

function Index() {
  const params = Route.useSearch();
  const navigate = useNavigate({ from: "/" });
  const { session, ready } = useAuth();
  const [authOpen, setAuthOpen] = useState(false);

  const [text, setText] = useState(typeof params.q === "string" ? params.q : "");
  const [originalQuery, setOriginalQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState<SearchResponse | null>(null);
  const [aiResults, setAiResults] = useState<ResultCard[] | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [aiResting, setAiResting] = useState(false);
  const [followUp, setFollowUp] = useState("");
  const [successfulQueryId, setSuccessfulQueryId] = useState<string | null>(null);

  const urlFilters = useMemo(() => filtersFromUrl(params), [params]);
  const searched = hasAnyFilter(params);

  const filterQ = useQuery({
    queryKey: ["filter-search", urlFilters],
    queryFn: async () => {
      const { filters, defaultArea } = withDefaultArea(urlFilters);
      return { rows: await filterSearch(filters), defaultArea };
    },
    enabled: supabaseConfigured && searched && aiResults === null,
  });

  function showFilters(f: SearchFilters, q?: string, personalPoint = false) {
    navigate({ search: { ...(q ? { q } : {}), ...filtersToUrl(f, { personalPoint }) } });
  }

  async function runAi(query: string, refineId?: string | null) {
    if (!session) return setAuthOpen(true);
    setBusy(true);
    setNotice(null);
    setAiResting(false);
    try {
      const r = await submitAi(query, refineId);
      setReply(r);
      if (r.status === "ok" && r.filters) {
        setSuccessfulQueryId(r.query_id);
        setFollowUp("");
        showFilters(r.filters, refineId ? originalQuery : query, r.location_source !== null);
        setAiResults(r.results); // null means the search step failed: filter search runs instead
        if (!r.results && r.message) setNotice(r.message);
      } else if (r.status === "ai_resting") {
        setAiResting(true);
        setSuccessfulQueryId(null);
        setAiResults(null);
        if (!searched) showFilters({});
      }
    } catch (e) {
      setReply(null);
      setAiResults(null);
      if (e instanceof AgentError && e.status === 401) {
        setNotice("Please sign in again to use AI search.");
        setAuthOpen(true);
      } else {
        setAiResting(true);
        setSuccessfulQueryId(null);
        if (!searched) showFilters({});
      }
    } finally {
      setBusy(false);
    }
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const q = text.trim();
    if (!q) return;
    setOriginalQuery(q);
    setSuccessfulQueryId(null);
    setAiResults(null);
    runAi(q);
  }

  function answer(t: string) {
    const base = originalQuery.replace(/[.\s]+$/, "");
    const q = `${base}. ${t}`;
    setOriginalQuery(q);
    runAi(q);
  }

  const results = aiResults ?? filterQ.data?.rows ?? null;
  const notes =
    aiResults && reply ? reply.notes.map((n) => n.message) : filterQ.data?.defaultArea ? ["Showing central London."] : [];
  const clarifying = reply?.status === "needs_clarification";

  return (
    <main className="min-h-screen bg-background px-4 pb-16">
      <header className="mx-auto flex max-w-6xl items-center justify-between py-4">
        <button className="font-display text-2xl text-foreground" onClick={() => { setReply(null); setAiResults(null); setSuccessfulQueryId(null); setAiResting(false); navigate({ search: {} }); }}>
          Any Plans?
        </button>
        {ready && supabaseConfigured && (
          session ? (
            <Button variant="ghost" size="sm" onClick={() => supabase?.auth.signOut()}>Sign out</Button>
          ) : (
            <Button variant="outline" size="sm" onClick={() => setAuthOpen(true)}>Sign in</Button>
          )
        )}
      </header>

      <section className={`mx-auto max-w-2xl text-center ${searched || reply ? "pt-4" : "pt-24"}`}>
        <h1 className="font-display text-5xl leading-tight tracking-tight text-foreground sm:text-6xl">
          Find something to do in London
        </h1>
        <p className="mt-4 text-lg text-muted-foreground">One search, every plan in the city</p>
        <form className="mt-8" onSubmit={submit}>
          <label className="flex items-center gap-3 rounded-full border border-border bg-card px-5 py-3 shadow-soft focus-within:ring-2 focus-within:ring-ring">
            <Search className="h-5 w-5 shrink-0 text-muted-foreground" />
            <input
              type="search"
              value={text}
              maxLength={500}
              onChange={(e) => setText(e.target.value)}
              placeholder="Jazz in Soho tonight, comedy this weekend…"
              className="w-full bg-transparent text-foreground outline-none placeholder:text-muted-foreground"
              aria-label="Search"
            />
            <div className="flex shrink-0 items-center gap-2">
              <Button type="submit" size="sm" className="rounded-full" disabled={busy || !text.trim()} aria-describedby={aiResting ? "ai-resting" : undefined}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Sparkles className="h-4 w-4" aria-hidden="true" />} AI search
              </Button>
            </div>
          </label>
          {aiResting && <p id="ai-resting" role="status" className="mt-2 text-right text-sm text-muted-foreground">AI search is resting. Filters still work.</p>}
        </form>
        {!supabaseConfigured && (
          <p className="mt-4 text-sm text-destructive">Search isn't connected yet: the database settings are missing.</p>
        )}
        {ready && supabaseConfigured && !session && (
          <p className="mt-3 text-sm text-muted-foreground">
            <button className="underline" onClick={() => setAuthOpen(true)}>Sign in</button> for AI search, or use the filters below.
          </p>
        )}
      </section>

      <div className="mx-auto mt-10 grid max-w-6xl gap-6 lg:grid-cols-[300px_1fr]">
        <FilterPanel
          value={urlFilters}
          onApply={(f) => {
            setReply(null);
            setAiResults(null);
            setNotice(null);
            setSuccessfulQueryId(null);
            showFilters(f);
          }}
        />
        <div className="space-y-4">
          {notice && <p className="rounded-lg bg-muted px-4 py-3 text-sm text-foreground">{notice}</p>}
          {clarifying && reply && <ClarifyStep questions={reply.questions} onAnswer={answer} busy={busy} />}
          {!clarifying && notes.map((n) => <p key={n} className="text-sm text-muted-foreground">{n}</p>)}
          {!clarifying && filterQ.isFetching && aiResults === null && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Finding events…</p>
          )}
          {!clarifying && filterQ.isError && aiResults === null && (
            <div className="text-sm text-muted-foreground">
              Search is unavailable right now. <button className="underline" onClick={() => filterQ.refetch()}>Try again</button>
            </div>
          )}
          {!clarifying && results && results.length === 0 && (
            <p className="text-sm text-muted-foreground">Nothing matched. Try widening the dates or the distance.</p>
          )}
          {!clarifying && results?.map((r) => <ResultCardView key={r.id} r={r} />)}
          {!clarifying && successfulQueryId && (
            <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (followUp.trim()) runAi(followUp.trim(), successfulQueryId); }}>
              <input className="min-w-0 flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm" aria-label="Follow-up" placeholder="Make it cheaper…" value={followUp} maxLength={500} onChange={(e) => setFollowUp(e.target.value)} />
              <Button type="submit" disabled={busy || !followUp.trim()}><Sparkles className="h-4 w-4" aria-hidden="true" /> Refine</Button>
            </form>
          )}
          {!searched && !reply && !notice && (
            <p className="text-sm text-muted-foreground">Type what you fancy above, or pick some filters.</p>
          )}
        </div>
      </div>

      <AuthDialog open={authOpen} onOpenChange={setAuthOpen} />
    </main>
  );
}
