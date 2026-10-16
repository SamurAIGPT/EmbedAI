"use client";

import { useEffect, useState } from "react";
import { api, ApiError, type Agent, type AgentInput, type ApprovalMode } from "@/lib/api";
import { APPROVAL_MODES, MODEL_SUGGESTIONS, approvalModeLabel, modelFromInput, modelLabel } from "@/lib/agents";

type Draft = {
  id: number | null;
  name: string;
  instructions: string;
  model: string;
  workspace_path: string;
  approval_mode: ApprovalMode;
};

const EMPTY: Draft = {
  id: null,
  name: "",
  instructions: "",
  model: "default",
  workspace_path: "",
  approval_mode: "ask",
};

function toDraft(agent: Agent): Draft {
  return {
    id: agent.id,
    name: agent.name,
    instructions: agent.instructions ?? "",
    model: modelLabel(agent.model),
    workspace_path: agent.workspace_path ?? "",
    approval_mode: agent.approval_mode,
  };
}

function toInput(draft: Draft): AgentInput {
  return {
    name: draft.name.trim(),
    instructions: draft.instructions.trim() || null,
    model: modelFromInput(draft.model),
    workspace_path: draft.workspace_path.trim() || null,
    approval_mode: draft.approval_mode,
  };
}

const FIELD =
  "w-full rounded-md border border-zinc-300 bg-transparent px-3 py-1.5 text-sm placeholder:text-zinc-500 dark:border-zinc-700";
const BUTTON =
  "cursor-pointer rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-900";
const PRIMARY =
  "cursor-pointer rounded-md bg-zinc-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300";

// List, create, edit and archive named agents. Changing an agent applies to its
// next message; conversations already running keep what they started with.
export function AgentsPanel({
  agents,
  onClose,
  onChanged,
}: {
  agents: Agent[];
  onClose: () => void;
  // Called after any change, so the page reads the agent list again.
  onChanged: () => void;
}) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const escape = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [onClose]);

  const run = async (action: () => Promise<unknown>, done?: () => void) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      done?.();
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message || "That did not work." : "Could not reach the backend.");
    } finally {
      setBusy(false);
    }
  };

  const save = () => {
    if (!draft || draft.name.trim() === "") return;
    const input = toInput(draft);
    run(
      () => (draft.id === null ? api.createAgent(input) : api.updateAgent(draft.id, input)),
      () => setDraft(null)
    );
  };

  const visible = agents.filter((agent) => showArchived || agent.archived_at === null);
  const modeInfo = APPROVAL_MODES.find((m) => m.value === draft?.approval_mode);

  return (
    <div
      className="fixed inset-0 z-30 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Agents"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <div className="flex max-h-full w-full max-w-xl flex-col rounded-lg border border-zinc-200 bg-background shadow-xl dark:border-zinc-800">
        <header className="flex items-center gap-3 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
          <h2 className="flex-1 text-sm font-semibold">{draft ? (draft.id === null ? "New agent" : "Edit agent") : "Agents"}</h2>
          <button onClick={onClose} aria-label="Close" className="cursor-pointer rounded px-2 py-1 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-900">
            ✕
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {error && <p className="mb-3 text-sm text-red-600 dark:text-red-400">{error}</p>}

          {draft ? (
            <form
              className="space-y-3"
              onSubmit={(event) => {
                event.preventDefault();
                save();
              }}
            >
              <label className="block text-xs text-zinc-500">
                Name
                <input
                  autoFocus
                  value={draft.name}
                  maxLength={80}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  placeholder="Code reviewer"
                  className={`${FIELD} mt-1`}
                />
              </label>
              <label className="block text-xs text-zinc-500">
                Instructions
                <textarea
                  value={draft.instructions}
                  rows={5}
                  onChange={(e) => setDraft({ ...draft, instructions: e.target.value })}
                  placeholder="How this agent should behave. Added to every conversation it starts."
                  className={`${FIELD} mt-1 resize-y`}
                />
              </label>
              <label className="block text-xs text-zinc-500">
                Model
                <input
                  list="agent-model-suggestions"
                  value={draft.model}
                  onChange={(e) => setDraft({ ...draft, model: e.target.value })}
                  className={`${FIELD} mt-1`}
                />
                <datalist id="agent-model-suggestions">
                  {MODEL_SUGGESTIONS.map((model) => (
                    <option key={model} value={model} />
                  ))}
                </datalist>
                <span className="mt-1 block">&quot;default&quot; uses whatever Claude Code picks. Any model name it accepts works.</span>
              </label>
              <label className="block text-xs text-zinc-500">
                Workspace folder
                <input
                  value={draft.workspace_path}
                  onChange={(e) => setDraft({ ...draft, workspace_path: e.target.value })}
                  placeholder="Leave empty for the default workspace"
                  className={`${FIELD} mt-1 font-mono`}
                />
              </label>
              <fieldset className="text-xs text-zinc-500">
                <legend>Approvals</legend>
                <div className="mt-1 flex flex-wrap gap-2">
                  {APPROVAL_MODES.map((mode) => (
                    <label
                      key={mode.value}
                      className={`cursor-pointer rounded-md border px-3 py-1.5 text-sm ${
                        draft.approval_mode === mode.value
                          ? "border-zinc-900 bg-zinc-100 dark:border-zinc-100 dark:bg-zinc-900"
                          : "border-zinc-300 dark:border-zinc-700"
                      }`}
                    >
                      <input
                        type="radio"
                        name="approval-mode"
                        className="sr-only"
                        checked={draft.approval_mode === mode.value}
                        onChange={() => setDraft({ ...draft, approval_mode: mode.value })}
                      />
                      {mode.label}
                    </label>
                  ))}
                </div>
                {modeInfo && <p className="mt-2">{modeInfo.description}</p>}
              </fieldset>
              <div className="flex gap-2 pt-1">
                <button type="submit" disabled={busy || draft.name.trim() === ""} className={PRIMARY}>
                  {busy ? "Saving…" : "Save"}
                </button>
                <button type="button" onClick={() => setDraft(null)} disabled={busy} className={BUTTON}>
                  Cancel
                </button>
              </div>
            </form>
          ) : (
            <>
              {visible.length === 0 ? (
                <p className="text-sm text-zinc-500">No agents yet. An agent keeps its own instructions, model, workspace and approval mode.</p>
              ) : (
                <ul className="space-y-2">
                  {visible.map((agent) => {
                    const archived = agent.archived_at !== null;
                    return (
                      <li key={agent.id} className="flex items-start gap-3 rounded-md border border-zinc-200 p-3 dark:border-zinc-800">
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium">
                            {agent.name}
                            {archived && <span className="ml-2 text-xs font-normal text-zinc-500">archived</span>}
                          </p>
                          <p className="text-xs text-zinc-500">
                            {modelLabel(agent.model)} · {approvalModeLabel(agent.approval_mode)}
                          </p>
                          {agent.instructions && (
                            <p className="mt-1 line-clamp-2 text-xs text-zinc-600 dark:text-zinc-400">{agent.instructions}</p>
                          )}
                        </div>
                        <div className="flex shrink-0 gap-1">
                          <button onClick={() => setDraft(toDraft(agent))} disabled={busy} className={BUTTON}>
                            Edit
                          </button>
                          <button
                            onClick={() => run(() => api.updateAgent(agent.id, { archived: !archived }))}
                            disabled={busy}
                            className={BUTTON}
                          >
                            {archived ? "Restore" : "Archive"}
                          </button>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
              <div className="mt-4 flex items-center gap-3">
                <button onClick={() => setDraft(EMPTY)} className={PRIMARY}>
                  New agent
                </button>
                <label className="flex cursor-pointer items-center gap-2 text-xs text-zinc-500">
                  <input type="checkbox" checked={showArchived} onChange={() => setShowArchived((on) => !on)} />
                  Show archived
                </label>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
