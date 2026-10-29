import { useState } from "react";
import type { Question } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/** One step showing every question with its chips. `onAnswer` gets the text to append. */
export function ClarifyStep({
  questions,
  onAnswer,
  busy,
}: {
  questions: Question[];
  onAnswer: (text: string) => void;
  busy: boolean;
}) {
  return (
    <div className="space-y-5 rounded-xl border border-border bg-card p-5 shadow-soft">
      {questions.map((q) => (
        <QuestionBlock key={q.reason} q={q} onAnswer={onAnswer} busy={busy} />
      ))}
    </div>
  );
}

function QuestionBlock({ q, onAnswer, busy }: { q: Question; onAnswer: (t: string) => void; busy: boolean }) {
  const [text, setText] = useState("");
  const [areaOpen, setAreaOpen] = useState(false);
  const rows = q.groups?.length ? q.groups.map((g) => g.choices) : q.choices.length ? [q.choices] : [];

  function tap(choice: string) {
    if (choice === "Pick an area") return setAreaOpen(true);
    onAnswer(choice);
  }
  const unavailable = (c: string) => c === "Use my location" || c === "Drop a pin on the map";

  return (
    <div>
      <p className="font-medium text-card-foreground">{q.question}</p>
      {rows.map((row, i) => (
        <div key={i} className="mt-2 flex flex-wrap gap-2">
          {row.map((c) => (
            <Button
              key={c}
              size="sm"
              variant="outline"
              className="rounded-full"
              disabled={busy || unavailable(c)}
              title={unavailable(c) ? "Coming soon" : undefined}
              onClick={() => tap(c)}
            >
              {c}
            </Button>
          ))}
        </div>
      ))}
      {(areaOpen || q.choices.length === 0 || q.groups?.length) && (
        <form
          className="mt-3 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) onAnswer(text.trim());
          }}
        >
          <Input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={areaOpen ? "Area, e.g. Camden" : "Or type your answer"}
            disabled={busy}
          />
          <Button type="submit" disabled={busy || !text.trim()}>
            Send
          </Button>
        </form>
      )}
    </div>
  );
}
