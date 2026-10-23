"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, ApiError, type UsageSummary, type UsageTotals } from "@/lib/api";
import { currentMonth, formatCost, formatTokens, shiftMonth } from "@/lib/everyday";

function Row({ group, link }: { group: UsageTotals; link?: string }) {
  return (
    <tr className="border-t border-line">
      <td className="max-w-56 truncate py-1.5 pr-3">
        {link ? <Link href={link} className="hover:underline">{group.label}</Link> : group.label}
      </td>
      <td className="py-1.5 pr-3 text-right tabular-nums">{group.turns}</td>
      <td className="py-1.5 pr-3 text-right tabular-nums">{formatTokens(group.input_tokens)}</td>
      <td className="py-1.5 pr-3 text-right tabular-nums">{formatTokens(group.output_tokens)}</td>
      <td className="py-1.5 text-right tabular-nums" title={group.unreported_turns ? `${group.unreported_turns} turn(s) reported nothing` : undefined}>
        {formatCost(group.cost_usd)}
        {group.cost_usd !== null && group.unreported_turns > 0 ? "*" : ""}
      </td>
    </tr>
  );
}

function Table({ title, groups, linkFor }: { title: string; groups: UsageTotals[]; linkFor?: (g: UsageTotals) => string | undefined }) {
  if (groups.length === 0) return null;
  return (
    <section>
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">{title}</h3>
      <table className="w-full text-xs">
        <thead className="text-muted">
          <tr>
            <th className="py-1 text-left font-normal">Name</th>
            <th className="py-1 text-right font-normal">Turns</th>
            <th className="py-1 text-right font-normal">Input</th>
            <th className="py-1 text-right font-normal">Output</th>
            <th className="py-1 text-right font-normal">Cost</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => (
            <Row key={group.key} group={group} link={linkFor?.(group)} />
          ))}
        </tbody>
      </table>
    </section>
  );
}

// Tokens and cost for a month, exactly as engines reported them (docs/EVERYDAY.md).
export function UsageView() {
  const [month, setMonth] = useState(currentMonth());
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [csvError, setCsvError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .getUsage(month)
      .then((value) => {
        if (!alive) return;
        setSummary(value);
        setError(null);
      })
      .catch((err) => alive && setError(err instanceof ApiError ? err.message : "Could not reach the backend."));
    return () => {
      alive = false;
    };
  }, [month]);

  const exportCsv = async () => {
    setCsvError(null);
    try {
      const text = await api.usageCsv(month);
      const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `open-dots-usage-${month}.csv`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch (err) {
      setCsvError(err instanceof ApiError ? err.message : "Could not export.");
    }
  };

  const shown = summary?.month === month ? summary : null;
  const total = shown?.total;

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-2">
        <button onClick={() => setMonth(shiftMonth(month, -1))} aria-label="Previous month" className="cursor-pointer rounded px-3 py-1 text-sm hover:bg-hover">‹</button>
        <span className="text-sm tabular-nums">{month}</span>
        <button onClick={() => setMonth(shiftMonth(month, 1))} disabled={month >= currentMonth()} aria-label="Next month" className="cursor-pointer rounded px-3 py-1 text-sm hover:bg-hover disabled:opacity-40">›</button>
      </div>
      <div className="space-y-5">
          {error && <p role="alert" className="text-sm text-danger">{error}</p>}
          {!error && !shown && <p className="text-sm text-muted">Loading…</p>}
          {total && total.turns === 0 && <p className="text-sm text-muted">No finished turns in {month}.</p>}
          {total && total.turns > 0 && (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <div><p className="text-xs text-muted">Turns</p><p className="text-lg tabular-nums">{total.turns}</p></div>
                <div>
                  <p className="text-xs text-muted">Input tokens</p>
                  <p className="text-lg tabular-nums">{formatTokens(total.input_tokens)}</p>
                  {total.cache_read_input_tokens + total.cache_creation_input_tokens > 0 && (
                    <p className="text-[11px] text-muted">
                      + {formatTokens(total.cache_read_input_tokens + total.cache_creation_input_tokens)} cached
                    </p>
                  )}
                </div>
                <div><p className="text-xs text-muted">Output tokens</p><p className="text-lg tabular-nums">{formatTokens(total.output_tokens)}</p></div>
                <div><p className="text-xs text-muted">Cost</p><p className="text-lg tabular-nums">{formatCost(total.cost_usd)}</p></div>
              </div>
              <p className="text-xs text-muted">
                Numbers are what each engine reported; nothing is estimated. Days and months are in UTC.
                {total.unreported_turns > 0 &&
                  ` ${total.unreported_turns} of ${total.turns} turns reported no usage, so they are not in these totals (marked * where a cost is shown).`}
              </p>
              <Table title="By conversation" groups={shown.by_thread} linkFor={(g) => (g.thread_id ? `/chat?thread=${g.thread_id}` : undefined)} />
              <Table title="By engine and model" groups={shown.by_engine} />
              <Table title="By day" groups={shown.by_day} />
            </>
          )}
          {shown?.helper && shown.helper.calls > 0 && (
            <section aria-label="Helper model">
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Helper model</h3>
              <p className="mb-1 text-xs text-muted">
                {shown.helper.calls} call{shown.helper.calls === 1 ? "" : "s"} written for agents whose own engine cannot
                write them
                {shown.helper.failed > 0 && ` (${shown.helper.failed} failed)`}. Tokens only where the engine reported them.
              </p>
              <ul className="space-y-0.5 text-xs">
                {shown.helper.groups.map((g) => (
                  <li key={`${g.engine}/${g.model}/${g.job}/${g.agent}`} className="flex flex-wrap gap-x-3">
                    <span>{g.agent || "an agent"}</span>
                    <span className="text-muted">{g.job === "compaction" ? "summaries" : "memory upkeep"}</span>
                    <span className="text-muted">{g.engine}{g.model ? ` · ${g.model}` : ""}</span>
                    <span className="tabular-nums">{g.calls} call{g.calls === 1 ? "" : "s"}</span>
                    {g.input_tokens + g.output_tokens > 0 && (
                      <span className="tabular-nums text-muted">
                        {formatTokens(g.input_tokens)} in · {formatTokens(g.output_tokens)} out
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
          <div className="flex items-center gap-3">
            <button onClick={() => void exportCsv()} className="cursor-pointer rounded-md border border-line px-3 py-1.5 text-xs">
              Export {month} as CSV
            </button>
            {csvError && <span className="text-xs text-danger">{csvError}</span>}
          </div>
      </div>
    </div>
  );
}
