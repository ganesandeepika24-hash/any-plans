import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import {
  CATEGORIES,
  DEFAULT_AREA,
  KM_PER_MILE,
  dayAfterStart,
  dayStart,
  londonDate,
  londonDateMinus5h,
  type SearchFilters,
} from "@/lib/filters";

export function FilterPanel({
  value,
  onApply,
}: {
  value: SearchFilters;
  onApply: (f: SearchFilters) => void;
}) {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [cats, setCats] = useState<string[]>([]);
  const [pmin, setPmin] = useState("");
  const [pmax, setPmax] = useState("");
  const [hideUnpriced, setHideUnpriced] = useState(false);
  const [miles, setMiles] = useState(DEFAULT_AREA.radius_km / KM_PER_MILE);

  useEffect(() => {
    setFrom(value.date_from ? londonDate(value.date_from) : "");
    setTo(value.date_to ? londonDateMinus5h(value.date_to) : "");
    setCats(value.categories ?? []);
    setPmin(value.price_min?.toString() ?? "");
    setPmax(value.price_max?.toString() ?? "");
    setHideUnpriced(value.include_unpriced === false);
    setMiles((value.radius_km ?? DEFAULT_AREA.radius_km) / KM_PER_MILE);
  }, [value]);

  function apply() {
    const f: SearchFilters = {};
    const datesSame =
      (value.date_from ? londonDate(value.date_from) : "") === from &&
      (value.date_to ? londonDateMinus5h(value.date_to) : "") === to;
    if (datesSame) {
      if (value.date_from) f.date_from = value.date_from;
      if (value.date_to) f.date_to = value.date_to;
      if (value.windows) f.windows = value.windows; // windows belong to these dates only
    } else {
      if (from) f.date_from = dayStart(from);
      if (to || from) f.date_to = dayAfterStart(to || from);
    }
    if (cats.length) f.categories = cats;
    if (pmin !== "") f.price_min = Number(pmin);
    if (pmax !== "") f.price_max = Number(pmax);
    if (hideUnpriced) f.include_unpriced = false;
    const km = Math.round(miles * KM_PER_MILE * 1000) / 1000;
    const hasPoint = value.lat !== undefined && value.lon !== undefined;
    f.lat = hasPoint ? value.lat : DEFAULT_AREA.lat;
    f.lon = hasPoint ? value.lon : DEFAULT_AREA.lon;
    f.radius_km = Math.min(100, Math.max(0.1, km));
    onApply(f);
  }

  return (
    <aside className="space-y-5 rounded-xl border border-border bg-card p-5 shadow-soft">
      <h2 className="font-semibold text-card-foreground">Filters</h2>
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <Label htmlFor="from">From</Label>
          <Input id="from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="to">To</Label>
          <Input id="to" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
      </div>
      {value.windows?.length ? (
        <p className="text-xs text-muted-foreground">Only {value.windows.length} chosen days in this range.</p>
      ) : null}
      <div>
        <Label>Categories</Label>
        <div className="mt-2 flex flex-wrap gap-2">
          {CATEGORIES.map((c) => {
            const on = cats.includes(c);
            return (
              <Button
                key={c}
                type="button"
                size="sm"
                variant={on ? "default" : "outline"}
                className="rounded-full"
                aria-pressed={on}
                onClick={() => setCats(on ? cats.filter((x) => x !== c) : [...cats, c])}
              >
                {c}
              </Button>
            );
          })}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <Label htmlFor="pmin">Min £</Label>
          <Input id="pmin" type="number" min={0} value={pmin} onChange={(e) => setPmin(e.target.value)} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="pmax">Max £</Label>
          <Input id="pmax" type="number" min={0} value={pmax} onChange={(e) => setPmax(e.target.value)} />
        </div>
      </div>
      <div className="flex items-center justify-between">
        <Label htmlFor="unpriced">Hide events with no price</Label>
        <Switch id="unpriced" checked={hideUnpriced} onCheckedChange={setHideUnpriced} />
      </div>
      <div>
        <Label>Within {miles.toFixed(1)} miles</Label>
        <Slider className="mt-3" min={0.5} max={62} step={0.5} value={[miles]} onValueChange={([m]) => setMiles(m)} />
      </div>
      <Button className="w-full" onClick={apply}>
        Show events
      </Button>
    </aside>
  );
}
