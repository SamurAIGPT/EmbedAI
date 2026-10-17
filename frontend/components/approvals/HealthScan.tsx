"use client";

import Link from "next/link";
import { useState } from "react";
import { api, ApiError, type SentinelReport } from "@/lib/api";
import { issueKindLabel } from "@/lib/approvals";
import { BUTTON, FIELD_BOX } from "./styles";

const SEVERITY = {
  high: "text-red-700 dark:text-red-400",
  medium: "text-amber-700 dark:text-amber-400",
  low: "text-zinc-500",
};

// A read-only scan of recent conversations: retry loops, requests asked again
// after a denial, tool errors and failed answers. It changes nothing.
export function HealthScan() {
  const [hours, setHours] = useState(24);
  const [report, setReport] = useState<SentinelReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const scan = async () => {
    setBusy(true);
    setError(null);
    try {
      setReport(await api.scanHealth(hours));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not run the scan.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3 text-sm">
      <div className="flex items-center gap-2">
        <select
          aria-label="How far back"
          value={hours}
          onChange={(e) => setHours(Number(e.target.value))}
          className={`${FIELD_BOX} w-auto`}
        >
          <option value={1}>Last hour</option>
          <option value={24}>Last day</option>
          <option value={168}>Last week</option>
        </select>
        <button className={BUTTON} onClick={scan} disabled={busy}>
          {busy ? "Scanning…" : "Scan"}
        </button>
      </div>
      <p className="text-xs text-zinc-500">
        Issues show the error with paths, numbers and quoted text taken out, never the tool&apos;s output.
      </p>
      {error && <p role="alert" className="text-xs text-red-600 dark:text-red-400">{error}</p>}
      {report && report.issues.length === 0 && (
        <p className="rounded-md bg-zinc-100 px-3 py-3 text-xs text-zinc-500 dark:bg-zinc-900">
          No issues. Read {report.scanned_events} events and {report.scanned_decisions} denials.
        </p>
      )}
      {report && report.issues.length > 0 && (
        <ul className="divide-y divide-zinc-200 rounded-md border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
          {report.issues.map((issue) => (
            <li key={`${issue.sig}-${issue.thread_id}-${issue.turn_id}`} className="px-3 py-2">
              <p className="text-xs">
                <span className={`font-medium ${SEVERITY[issue.severity]}`}>{issueKindLabel(issue.kind)}</span>
                {issue.tool && <span className="font-mono"> · {issue.tool}</span>} · {issue.count}×
              </p>
              <p className="mt-0.5 break-words font-mono text-xs text-zinc-600 dark:text-zinc-400">{issue.title}</p>
              <p className="mt-0.5 text-xs text-zinc-500">
                Last {new Date(issue.last_at).toLocaleString()}
                {issue.thread_id != null && (
                  <>
                    {" · "}
                    <Link href={`/chat/?thread=${issue.thread_id}`} className="underline">
                      conversation {issue.thread_id}
                    </Link>
                  </>
                )}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
