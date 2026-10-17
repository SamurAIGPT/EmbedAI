"use client";

import { useEffect, useState } from "react";
import { api, ApiError, type ApprovalRule } from "@/lib/api";
import { ruleMatchLabel, ruleScopeLabel } from "@/lib/approvals";
import { BUTTON, FIELD, FIELD_BOX, PRIMARY } from "./styles";

// The saved "always allow" rules for shell commands: list, add and remove.
// With a `workspace`, it starts on the rules that apply in that folder.
export function ApprovalRules({
  workspace = null,
  threadId = null,
}: {
  workspace?: string | null;
  threadId?: number | null;
}) {
  const [rules, setRules] = useState<ApprovalRule[] | null>(null);
  const [onlyHere, setOnlyHere] = useState(workspace !== null);
  const [tick, setTick] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [match, setMatch] = useState<"exact" | "prefix">("exact");
  const [command, setCommand] = useState("");
  const [folder, setFolder] = useState(workspace ?? "");

  useEffect(() => {
    let cancelled = false;
    api
      .listApprovalRules(onlyHere ? workspace : null)
      .then((list) => {
        if (!cancelled) {
          setRules(list);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Could not load the saved rules.");
      });
    return () => {
      cancelled = true;
    };
  }, [onlyHere, workspace, tick]);

  const run = async (action: () => Promise<unknown>, then?: () => void) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      then?.();
      setTick((n) => n + 1);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  };

  const add = (event: React.FormEvent) => {
    event.preventDefault();
    run(
      () => api.addApprovalRule({ match, command: command.trim(), workspace: folder.trim() || null }),
      () => setCommand("")
    );
  };

  return (
    <div className="space-y-4 text-sm">
      <p className="text-xs text-zinc-500">
        A saved rule lets a shell command run without asking. Anything else still asks, and a command on the
        safety floor (deleting your home folder, formatting a disk, a force push…) always asks, whatever a rule
        says. A rule that starts with some words never matches a command that chains another one.
      </p>
      {workspace !== null && (
        <label className="flex items-center gap-2 text-xs">
          <input type="checkbox" checked={onlyHere} onChange={(e) => setOnlyHere(e.target.checked)} />
          Only rules that apply in this conversation&apos;s folder
        </label>
      )}
      {error && <p role="alert" className="text-xs text-red-600 dark:text-red-400">{error}</p>}
      {rules === null && !error && <p className="text-xs text-zinc-500">Loading…</p>}
      {rules !== null && rules.length === 0 && (
        <p className="rounded-md bg-zinc-100 px-3 py-3 text-xs text-zinc-500 dark:bg-zinc-900">
          No saved rules{onlyHere ? " for this folder" : ""}. Choose &ldquo;Always allow this command&rdquo; on a
          request, or add one below.
        </p>
      )}
      {rules !== null && rules.length > 0 && (
        <ul className="divide-y divide-zinc-200 rounded-md border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
          {rules.map((rule) => (
            <li key={rule.id} className="flex items-start gap-3 px-3 py-2">
              <div className="min-w-0 flex-1">
                <p className="break-all font-mono text-xs">{rule.command}</p>
                <p className="mt-0.5 break-all text-xs text-zinc-500">
                  {ruleMatchLabel(rule)} · {ruleScopeLabel(rule)}
                </p>
              </div>
              <button
                className={BUTTON}
                disabled={busy}
                aria-label={`Remove the rule for ${rule.command}`}
                onClick={() => run(() => api.deleteApprovalRule(rule.id, threadId))}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={add} className="space-y-2 rounded-md border border-zinc-200 p-3 dark:border-zinc-800">
        <p className="text-xs font-medium">Add a rule</p>
        <div className="flex gap-2">
          <select
            aria-label="How the command must match"
            value={match}
            onChange={(e) => setMatch(e.target.value === "prefix" ? "prefix" : "exact")}
            className={`${FIELD_BOX} w-36 shrink-0`}
          >
            <option value="exact">Exactly</option>
            <option value="prefix">Starts with</option>
          </select>
          <input
            aria-label="Command"
            className={`${FIELD} font-mono`}
            placeholder={match === "prefix" ? "git status" : "npm test"}
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            maxLength={1000}
            spellCheck={false}
            autoComplete="off"
          />
        </div>
        <input
          aria-label="Folder"
          className={`${FIELD} font-mono`}
          placeholder="Folder (empty: every folder)"
          value={folder}
          onChange={(e) => setFolder(e.target.value)}
          spellCheck={false}
          autoComplete="off"
        />
        <button type="submit" className={PRIMARY} disabled={busy || !command.trim()}>
          {busy ? "Saving…" : "Add rule"}
        </button>
      </form>
    </div>
  );
}
