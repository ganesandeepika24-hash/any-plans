import { createFileRoute } from "@tanstack/react-router";
import { Search } from "lucide-react";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Any Plans? — Find something to do in London" },
      { name: "description", content: "Any Plans? helps you find something to do in London." },
      { property: "og:title", content: "Any Plans?" },
      { property: "og:description", content: "Find something to do in London." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: Index,
});

function Index() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-6">
      <div className="w-full max-w-xl text-center">
        <h1 className="font-display text-6xl tracking-tight text-foreground sm:text-7xl">
          Any Plans<span className="text-primary">?</span>
        </h1>
        <p className="mt-4 text-lg text-muted-foreground">Find something to do in London</p>
        <form className="mt-10" onSubmit={(e) => e.preventDefault()}>
          <label className="flex items-center gap-3 rounded-full border border-border bg-card px-5 py-4 shadow-soft focus-within:ring-2 focus-within:ring-ring">
            <Search className="h-5 w-5 text-muted-foreground" />
            <input
              type="search"
              placeholder="Gigs, markets, galleries, pubs…"
              className="w-full bg-transparent text-foreground outline-none placeholder:text-muted-foreground"
            />
          </label>
        </form>
      </div>
    </main>
  );
}
