import { BadgeCheck, ExternalLink, Info } from "lucide-react";
import type { ResultCard } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

const when = (iso: string) =>
  new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/London",
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));

function price(r: ResultCard) {
  if (r.price_min == null && r.price_max == null) return "Price not listed";
  if (r.price_max === 0) return "Free";
  const sym = r.currency === "GBP" || !r.currency ? "£" : `${r.currency} `;
  const lo = r.price_min ?? r.price_max!;
  const hi = r.price_max ?? r.price_min!;
  return lo === hi ? `${sym}${lo}` : `${sym}${lo}–${sym}${hi}`;
}

function checked(iso: string | null) {
  if (!iso) return "Not yet checked";
  const h = (Date.now() - new Date(iso).getTime()) / 3600_000;
  if (h < 1) return "Checked under an hour ago";
  if (h < 48) return `Checked ${Math.round(h)} h ago`;
  return `Checked ${Math.round(h / 24)} days ago`;
}

export function ResultCardView({ r }: { r: ResultCard }) {
  return (
    <article className="flex gap-4 rounded-xl border border-border bg-card p-4 shadow-soft">
      {r.image_url && (
        <img src={r.image_url} alt="" loading="lazy" className="h-24 w-24 shrink-0 rounded-lg object-cover" />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {r.category && <Badge variant="secondary">{r.category}</Badge>}
          {r.genre && <span>{r.genre}</span>}
        </div>
        <h3 className="mt-1 font-semibold text-card-foreground">
          {r.url ? (
            <a href={r.url} target="_blank" rel="noopener noreferrer" className="hover:text-primary hover:underline">
              {r.title}
            </a>
          ) : (
            r.title
          )}
        </h3>
        <p className="text-sm text-muted-foreground">
          {when(r.start_at)}
          {r.venue_name && ` · ${r.venue_name}`}
          {r.distance_m != null && ` · ${(r.distance_m / 1609.344).toFixed(1)} mi`}
        </p>
        <p className="mt-1 text-sm font-medium text-foreground">{price(r)}</p>
        <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
          <span
            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 ${
              r.confidence === "checked_today" ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground"
            }`}
          >
            {r.confidence === "checked_today" ? <BadgeCheck className="h-3 w-3" /> : <Info className="h-3 w-3" />}
            {r.confidence_label}
          </span>
          <span className="text-muted-foreground">{checked(r.last_checked_at)}</span>
        </div>
        {r.url && (
          <Button asChild size="sm" className="mt-3 rounded-full">
            <a href={r.url} target="_blank" rel="noopener noreferrer">
              Book now <ExternalLink className="h-3 w-3" aria-hidden="true" />
            </a>
          </Button>
        )}
      </div>
    </article>
  );
}
