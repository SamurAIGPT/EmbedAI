"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, ApiError, type SearchHit } from "@/lib/api";
import { snippetSegments } from "@/lib/everyday";

const MIN_CHARS = 2;
const WAIT_MS = 250;
const KIND_LABEL: Record<SearchHit["kind"], string> = { title: "Title", user: "You", assistant: "Agent" };

function when(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString([], { month: "short", day: "numeric" });
}

// A search box over every conversation's title and messages. While a query is
// typed, its hits replace the conversation list (`onActive` says so).
export function ConversationSearch({
  agentNames,
  onActive,
  onNavigate,
}: {
  agentNames?: Map<number, string>;
  onActive: (active: boolean) => void;
  onNavigate?: () => void;
}) {
  const [query, setQuery] = useState("");
  const [archived, setArchived] = useState(false);
  const [agentId, setAgentId] = useState<number | null>(null);
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const q = query.trim();
  const active = q.length >= MIN_CHARS;

  useEffect(() => onActive(active), [active, onActive]);

  useEffect(() => {
    if (!active) return;
    let alive = true;
    const timer = setTimeout(() => {
      setError(null);
      api
        .searchConversations(q, { includeArchived: archived, agentId, limit: 40 })
        .then((response) => alive && setHits(response.hits))
        .catch((err) => {
          if (!alive) return;
          setHits([]);
          setError(err instanceof ApiError ? err.message : "Could not reach the backend to search.");
        });
    }, WAIT_MS);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [q, active, archived, agentId]);

  return (
    <div className="px-3 pb-2">
      <input
        type="search"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setHits(null);
        }}
        onKeyDown={(e) => e.key === "Escape" && setQuery("")}
        placeholder="Search conversations"
        aria-label="Search conversations"
        className="w-full rounded-field border border-line bg-raised px-3 py-2 text-sm placeholder:text-faint"
      />
      {active && (
        <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted">
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />
            Include archived
          </label>
          {agentNames && agentNames.size > 0 && (
            <select
              value={agentId ?? ""}
              onChange={(e) => setAgentId(e.target.value === "" ? null : Number(e.target.value))}
              aria-label="Only conversations with this agent"
              className="max-w-36 truncate rounded border border-line bg-raised px-1 py-0.5"
            >
              <option value="">Any agent</option>
              {[...agentNames.entries()].map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          )}
        </div>
      )}
      {active && (
        <ul aria-label="Search results" className="mt-2 space-y-1">
          {hits === null && <li className="px-1 py-2 text-xs text-muted">Searching…</li>}
          {error && <li className="px-1 py-2 text-xs text-danger">{error}</li>}
          {hits?.length === 0 && !error && (
            <li className="px-1 py-2 text-xs text-muted">
              No conversation mentions “{q}”{archived ? "" : ". Archived ones are not searched unless you include them"}.
            </li>
          )}
          {hits?.map((hit, index) => (
            <li key={`${hit.thread_id}-${hit.seq ?? "t"}-${index}`}>
              <Link
                href={`/chat?thread=${hit.thread_id}`}
                onClick={onNavigate}
                className="block rounded-field px-2 py-1.5 hover:bg-hover/60"
              >
                <span className="flex items-baseline gap-2 text-xs text-muted">
                  <span className="min-w-0 flex-1 truncate font-medium text-ink">
                    {hit.thread_title || `Conversation ${hit.thread_id}`}
                  </span>
                  <span className="shrink-0">{when(hit.at)}</span>
                </span>
                <span className="mt-0.5 line-clamp-3 break-words text-xs text-muted">
                  <span className="mr-1 text-faint">{KIND_LABEL[hit.kind]}:</span>
                  {snippetSegments(hit.snippet, hit.highlights).map((part, i) =>
                    part.mark ? (
                      <mark key={i} className="rounded-sm bg-warning/30 px-0.5 text-ink">
                        {part.text}
                      </mark>
                    ) : (
                      <span key={i}>{part.text}</span>
                    )
                  )}
                </span>
                {(hit.archived || (hit.agent_id != null && agentNames?.get(hit.agent_id))) && (
                  <span className="mt-0.5 block text-[11px] text-faint">
                    {[hit.agent_id != null ? agentNames?.get(hit.agent_id) : null, hit.archived ? "archived" : null]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
