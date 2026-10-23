import Link from "next/link";
import { rowAction, summary } from "@/lib/needs-you";
import type { TranscriptItem } from "@/lib/transcript";

// The day's "Needs you" card, written by the server in the lead's chat. A newer
// revision replaces the older one (the transcript keeps only the latest).
export function NeedsYouCard({ item }: { item: Extract<TranscriptItem, { kind: "needs_you" }> }) {
  return (
    <section aria-label="Needs you" className="space-y-2 rounded-field border border-line p-3 text-sm">
      <header className="flex items-baseline justify-between gap-2">
        <h2 className="font-semibold">{item.title}</h2>
        <span className="text-xs text-muted">{item.subtitle}</span>
      </header>
      {item.rows.length === 0 ? (
        <p className="text-xs text-muted">{item.emptyText}</p>
      ) : (
        <>
          <p className="sr-only">{summary(item.rows.length, item.more)}</p>
          <ul className="space-y-2">
            {item.rows.map((row) => {
              const action = rowAction(row);
              return (
                <li key={row.key} className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="break-words">{row.title}</p>
                    {row.detail && <p className="break-words text-xs text-muted">{row.detail}</p>}
                  </div>
                  {action.href && (
                    <Link href={action.href} className="shrink-0 text-xs underline">
                      {action.label}
                    </Link>
                  )}
                </li>
              );
            })}
          </ul>
          {item.more > 0 && <p className="text-xs text-muted">and {item.more} more</p>}
        </>
      )}
    </section>
  );
}
