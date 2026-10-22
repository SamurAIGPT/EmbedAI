"use client";

import { Dialog } from "@/components/ui/Dialog";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, type Agent, type AgentInput, type AgentMemory, type ApprovalMode, type Engine } from "@/lib/api";
import {
  APPROVAL_MODES,
  agentFileHasNotes,
  agentFileText,
  approvalModeLabel,
  memoryGauge,
  modelLabel,
  parseAgentFile,
  redactionNotice,
} from "@/lib/agents";
import { computerStateLabel } from "@/lib/computers";
import { ComputerView } from "./ComputerPanel";
import { EngineModelFields } from "./EngineModelFields";
import { MemoryPanel } from "./MemoryPanel";

type Draft = {
  id: number | null;
  name: string;
  title: string;
  description: string;
  instructions: string;
  provider: string;
  model: string | null;
  workspace_path: string;
  approval_mode: ApprovalMode;
  memory_enabled: boolean;
  fallback_provider: string | null;
  fallback_model: string | null;
  // Create only: the first contents of the agent's notes.
  starter_notes: string;
};

const EMPTY: Draft = {
  id: null,
  name: "",
  title: "",
  description: "",
  instructions: "",
  provider: "claude",
  model: null,
  workspace_path: "",
  approval_mode: "ask",
  memory_enabled: true,
  fallback_provider: null,
  fallback_model: null,
  starter_notes: "",
};

function toDraft(agent: Agent): Draft {
  return {
    id: agent.id,
    name: agent.name,
    title: agent.title ?? "",
    description: agent.description ?? "",
    instructions: agent.instructions ?? "",
    provider: agent.provider,
    model: agent.model,
    workspace_path: agent.workspace_path ?? "",
    approval_mode: agent.approval_mode,
    memory_enabled: agent.memory_enabled ?? true,
    fallback_provider: agent.fallback_provider ?? null,
    fallback_model: agent.fallback_model ?? null,
    starter_notes: "",
  };
}

function toInput(draft: Draft): AgentInput {
  const input: AgentInput = {
    name: draft.name.trim(),
    title: draft.title.trim() || null,
    description: draft.description.trim() || null,
    instructions: draft.instructions.trim(),
    provider: draft.provider,
    model: draft.model?.trim() || null,
    workspace_path: draft.workspace_path.trim() || null,
    approval_mode: draft.approval_mode,
    memory_enabled: draft.memory_enabled,
    // The agent's own engine cannot be its fallback.
    fallback_provider: draft.fallback_provider && draft.fallback_provider !== draft.provider ? draft.fallback_provider : null,
    fallback_model: draft.fallback_provider && draft.fallback_provider !== draft.provider ? draft.fallback_model?.trim() || null : null,
  };
  if (draft.id === null && draft.starter_notes.trim()) input.starter_notes = draft.starter_notes;
  return input;
}

// The notes editor: what was loaded, what is typed, and a save refused because
// the agent changed the file meanwhile.
type NotesState = {
  agent: Agent;
  memory: AgentMemory | null;
  text: string;
  conflict: boolean;
  saved: string | null;
};

type ExportState = { agent: Agent; includeNotes: boolean; notice: string | null };
type ImportState = { document: Record<string, unknown>; name: string; hasNotes: boolean; includeNotes: boolean };

const FIELD =
  "w-full rounded-md border border-line bg-transparent px-3 py-1.5 text-sm placeholder:text-muted";
const BUTTON =
  "cursor-pointer rounded-md border border-line px-3 py-1.5 text-sm font-medium hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50";
const SMALL =
  "cursor-pointer rounded-md border border-line px-2 py-1 text-xs font-medium hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50";
const PRIMARY =
  "cursor-pointer rounded-md px-4 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50 bg-accent text-accent-fg hover:opacity-90";
const GAUGE_COLOR = { ok: "bg-success", near: "bg-warning", over: "bg-danger" } as const;

function saveFile(fileName: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

// List, create, edit, duplicate, pin, archive, export and import named agents,
// and edit each agent's notes. A new model or approval mode applies to an agent's
// next message; new instructions and notes reach conversations started after them.
export function AgentsPanel({
  agents,
  onClose,
  onChanged,
  startCreating = false,
}: {
  agents: Agent[];
  onClose: () => void;
  // Open straight on the new-agent form (the sidebar's New agent button).
  startCreating?: boolean;
  // Called after any change, so the page reads the agent list again.
  onChanged: () => void;
}) {
  const [draft, setDraft] = useState<Draft | null>(startCreating ? EMPTY : null);
  const [notes, setNotes] = useState<NotesState | null>(null);
  const [exporting, setExporting] = useState<ExportState | null>(null);
  const [importing, setImporting] = useState<ImportState | null>(null);
  const [computerOf, setComputerOf] = useState<Agent | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [engines, setEngines] = useState<Engine[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    api.getEngines().then((items) => { if (!cancelled) setEngines(items); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const escape = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [onClose]);

  const errorText = (err: unknown) =>
    err instanceof ApiError ? err.message || "That did not work." : err instanceof Error ? err.message : "Could not reach the backend.";

  const run = async (action: () => Promise<unknown>, done?: () => void) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      done?.();
      onChanged();
    } catch (err) {
      setError(errorText(err));
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

  const openNotes = async (agent: Agent) => {
    setError(null);
    setNotes({ agent, memory: null, text: "", conflict: false, saved: null });
    try {
      const memory = await api.getAgentMemory(agent.id);
      setNotes({ agent, memory, text: memory.text, conflict: false, saved: null });
    } catch (err) {
      setError(errorText(err));
    }
  };

  // `overwrite` saves without the hash check, after a conflict was shown.
  const saveNotes = async (overwrite = false) => {
    if (!notes?.memory) return;
    setBusy(true);
    setError(null);
    try {
      const memory = await api.saveAgentMemory(notes.agent.id, notes.text, overwrite ? null : notes.memory.hash);
      const saved = memory.redacted > 0 ? `Saved. ${memory.redacted} thing(s) that looked like secrets were replaced.` : "Saved.";
      setNotes({ ...notes, memory, text: memory.text, conflict: false, saved });
    } catch (err) {
      if (err instanceof ApiError && err.code === "memory_changed") setNotes({ ...notes, conflict: true, saved: null });
      else setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const loadTheirs = async () => {
    if (!notes) return;
    try {
      const memory = await api.getAgentMemory(notes.agent.id);
      setNotes({ ...notes, memory, text: memory.text, conflict: false, saved: null });
    } catch (err) {
      setError(errorText(err));
    }
  };

  const toggleRecallOn = (agent: Agent, on: boolean) =>
    run(
      () => api.updateAgent(agent.id, { recall_enabled: on }),
      () => setNotes((open) => (open ? { ...open, agent: { ...open.agent, recall_enabled: on } } : open))
    );

  const toggleNotesOn = (agent: Agent, on: boolean) =>
    run(
      () => api.updateAgent(agent.id, { memory_enabled: on }),
      () => setNotes((open) => (open ? { ...open, agent: { ...open.agent, memory_enabled: on } } : open))
    );

  const doExport = async () => {
    if (!exporting) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.exportAgent(exporting.agent.id, exporting.includeNotes);
      saveFile(result.file_name, agentFileText(result.document));
      setExporting({ ...exporting, notice: redactionNotice(result.redacted) ?? "Saved. The file has no model, folder, approval mode or conversations." });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const pickImport = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    try {
      const parsed = parseAgentFile(await file.text());
      const agent = parsed.agent as { name?: unknown } | undefined;
      const name = typeof agent?.name === "string" ? agent.name : file.name;
      const hasNotes = agentFileHasNotes(parsed);
      setImporting({ document: parsed, name, hasNotes, includeNotes: hasNotes });
    } catch (err) {
      setError(errorText(err));
    } finally {
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const visible = agents.filter((agent) => showArchived || agent.archived_at === null);
  const modeInfo = APPROVAL_MODES.find((m) => m.value === draft?.approval_mode);
  const heading = draft
    ? draft.id === null
      ? "New agent"
      : "Edit agent"
    : notes
      ? `Notes · ${notes.agent.name}`
      : exporting
        ? `Export · ${exporting.agent.name}`
        : importing
          ? "Import agent"
          : computerOf
            ? `Computer · ${computerOf.name}`
            : "Agents";
  const back = () => {
    setDraft(null);
    setNotes(null);
    setExporting(null);
    setImporting(null);
    setComputerOf(null);
    setError(null);
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }} title={heading} size="wide">
          {error && (
            <p role="alert" className="mb-3 text-sm text-danger">
              {error}
            </p>
          )}

          {draft ? (
            <form
              className="space-y-3"
              onSubmit={(event) => {
                event.preventDefault();
                save();
              }}
            >
              <label className="block text-xs text-muted">
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
              <label className="block text-xs text-muted">
                Title
                <input
                  value={draft.title}
                  maxLength={200}
                  onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                  placeholder="Reviews pull requests before merge"
                  className={`${FIELD} mt-1`}
                />
              </label>
              <label className="block text-xs text-muted">
                Description
                <textarea
                  value={draft.description}
                  rows={2}
                  maxLength={4000}
                  onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                  placeholder="For you: what this agent is for. Not sent to Claude."
                  className={`${FIELD} mt-1 resize-y`}
                />
              </label>
              <label className="block text-xs text-muted">
                Instructions
                <textarea
                  value={draft.instructions}
                  rows={5}
                  onChange={(e) => setDraft({ ...draft, instructions: e.target.value })}
                  placeholder="How this agent should behave. Added to every conversation it starts."
                  className={`${FIELD} mt-1 resize-y`}
                />
              </label>
              {draft.id === null && (
                <label className="block text-xs text-muted">
                  Starter notes
                  <textarea
                    value={draft.starter_notes}
                    rows={3}
                    onChange={(e) => setDraft({ ...draft, starter_notes: e.target.value })}
                    placeholder={"- The project uses pnpm\n- Releases go out on Fridays"}
                    className={`${FIELD} mt-1 resize-y font-mono`}
                  />
                  <span className="mt-1 block">The agent&apos;s first notes (MEMORY.md). It keeps them up to date itself.</span>
                </label>
              )}
              <EngineModelFields engines={engines} provider={draft.provider} model={draft.model} disabled={busy} hasComputer={agents.some((agent) => agent.id === draft.id && agent.computer != null)} onChange={(provider, model) => setDraft({ ...draft, provider, model })} />
              <p className="text-xs text-muted">Engine changes apply to new conversations. Existing conversations keep their engine.</p>
              <label className="block text-xs text-muted">
                Fallback engine when limited
                <select
                  aria-label="Fallback engine"
                  value={draft.fallback_provider && draft.fallback_provider !== draft.provider ? draft.fallback_provider : ""}
                  disabled={busy}
                  onChange={(e) => setDraft({ ...draft, fallback_provider: e.target.value || null, fallback_model: null })}
                  className={`${FIELD} mt-1`}
                >
                  <option value="">None</option>
                  {engines.filter((engine) => engine.id !== draft.provider).map((engine) => (
                    <option key={engine.id} value={engine.id}>{engine.label}</option>
                  ))}
                </select>
              </label>
              {draft.fallback_provider && draft.fallback_provider !== draft.provider && (
                <label className="block text-xs text-muted">
                  Fallback model
                  <input
                    aria-label="Fallback model"
                    value={draft.fallback_model ?? ""}
                    onChange={(e) => setDraft({ ...draft, fallback_model: e.target.value || null })}
                    placeholder="Engine default"
                    maxLength={200}
                    className={`${FIELD} mt-1`}
                  />
                </label>
              )}
              <p className="text-xs text-muted">When the engine refuses a message for a usage or rate limit before starting on it, the message goes to the fallback, if it can do everything the turn needs. The conversation shows the switch.</p>
              <label className="block text-xs text-muted">
                Workspace folder
                <input
                  value={draft.workspace_path}
                  onChange={(e) => setDraft({ ...draft, workspace_path: e.target.value })}
                  placeholder="Leave empty for the agent's own folder"
                  className={`${FIELD} mt-1 font-mono`}
                />
              </label>
              <fieldset className="text-xs text-muted">
                <legend>Approvals</legend>
                <div className="mt-1 flex flex-wrap gap-2">
                  {APPROVAL_MODES.map((mode) => (
                    <label
                      key={mode.value}
                      className={`cursor-pointer rounded-md border px-3 py-1.5 text-sm ${
                        draft.approval_mode === mode.value
                          ? "border-accent bg-inset"
                          : "border-line"
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
              <label className="flex cursor-pointer items-center gap-2 text-xs text-muted">
                <input
                  type="checkbox"
                  checked={draft.memory_enabled}
                  onChange={() => setDraft({ ...draft, memory_enabled: !draft.memory_enabled })}
                />
                Use notes: load this agent&apos;s MEMORY.md into each conversation
              </label>
              <div className="flex gap-2 pt-1">
                <button type="submit" disabled={busy || draft.name.trim() === ""} className={PRIMARY}>
                  {busy ? "Saving…" : "Save"}
                </button>
                <button type="button" onClick={back} disabled={busy} className={BUTTON}>
                  Cancel
                </button>
              </div>
            </form>
          ) : computerOf ? (
            <div className="space-y-3">
              <ComputerView
                agent={computerOf}
                onChanged={onChanged}
                engineSupportsComputers={engines.find((engine) => engine.id === computerOf.provider)?.capabilities.computers}
                engineLabel={engines.find((engine) => engine.id === computerOf.provider)?.label ?? computerOf.provider}
              />
              <button onClick={back} className={BUTTON}>
                Back
              </button>
            </div>
          ) : notes ? (
            <NotesEditor
              notes={notes}
              busy={busy}
              onText={(text) => setNotes({ ...notes, text, saved: null })}
              onSave={() => saveNotes(false)}
              onOverwrite={() => saveNotes(true)}
              onLoadTheirs={loadTheirs}
              onToggle={(on) => toggleNotesOn(notes.agent, on)}
              onToggleRecall={(on) => toggleRecallOn(notes.agent, on)}
              onReload={loadTheirs}
              onBack={back}
            />
          ) : exporting ? (
            <div className="space-y-3 text-sm">
              <p className="text-muted">
                Saves this agent as one file you can keep or give to someone. It holds the name, title, description and
                instructions. Never its model, folder, approval mode or conversations; an imported copy always starts on
                &quot;Ask first&quot;.
              </p>
              <label className="flex cursor-pointer items-center gap-2 text-xs text-muted">
                <input
                  type="checkbox"
                  checked={exporting.includeNotes}
                  onChange={() => setExporting({ ...exporting, includeNotes: !exporting.includeNotes, notice: null })}
                />
                Include its notes (MEMORY.md) as starter notes. Leave off if they hold private details.
              </label>
              {exporting.notice && <p className="text-xs text-muted">{exporting.notice}</p>}
              <div className="flex gap-2">
                <button onClick={doExport} disabled={busy} className={PRIMARY}>
                  {busy ? "Saving…" : "Save file"}
                </button>
                <button onClick={back} disabled={busy} className={BUTTON}>
                  Done
                </button>
              </div>
            </div>
          ) : importing ? (
            <div className="space-y-3 text-sm">
              <p>
                Add <span className="font-medium">{importing.name}</span> as a new agent. It starts on &quot;Ask first&quot;
                with the default model and its own folder; a name already in use gets a number.
              </p>
              {importing.hasNotes && (
                <label className="flex cursor-pointer items-center gap-2 text-xs text-muted">
                  <input
                    type="checkbox"
                    checked={importing.includeNotes}
                    onChange={() => setImporting({ ...importing, includeNotes: !importing.includeNotes })}
                  />
                  Keep the file&apos;s starter notes
                </label>
              )}
              <div className="flex gap-2">
                <button
                  onClick={() => run(() => api.importAgent(importing.document, importing.includeNotes), back)}
                  disabled={busy}
                  className={PRIMARY}
                >
                  {busy ? "Adding…" : "Add agent"}
                </button>
                <button onClick={back} disabled={busy} className={BUTTON}>
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <>
              {visible.length === 0 ? (
                <p className="text-sm text-muted">
                  No agents yet. An agent keeps its own instructions, model, workspace, approval mode and notes.
                </p>
              ) : (
                <ul className="space-y-2">
                  {visible.map((agent) => {
                    const archived = agent.archived_at !== null;
                    return (
                      <li key={agent.id} className="rounded-md border border-line p-3">
                        <p className="truncate text-sm font-medium">
                          {agent.name}
                          {agent.pinned && <span className="ml-2 rounded bg-hover px-1.5 text-xs font-normal">pinned</span>}
                          {agent.title && <span className="ml-2 text-xs font-normal text-muted">{agent.title}</span>}
                          {archived && <span className="ml-2 text-xs font-normal text-muted">archived</span>}
                        </p>
                        <p className="text-xs text-muted">
                          {engines.find((engine) => engine.id === agent.provider)?.label ?? agent.provider} · {modelLabel(agent.model)} · {approvalModeLabel(agent.approval_mode)}
                          {agent.memory_enabled === false && " · notes off"}
                          {agent.memory_enabled !== false && agent.recall_enabled === false && " · recall off"}
                          {agent.computer && ` · computer: ${computerStateLabel(agent.computer.state).toLowerCase()}`}
                        </p>
                        {agent.instructions && (
                          <p className="mt-1 line-clamp-2 text-xs text-muted">{agent.instructions}</p>
                        )}
                        <div className="mt-2 flex flex-wrap gap-1">
                          <button onClick={() => setDraft(toDraft(agent))} disabled={busy} className={SMALL}>
                            Edit
                          </button>
                          <button onClick={() => openNotes(agent)} disabled={busy} className={SMALL}>
                            Notes
                          </button>
                          {!archived && (
                            <button onClick={() => setComputerOf(agent)} disabled={busy} className={SMALL}>
                              Computer
                            </button>
                          )}
                          {!archived && (
                            <button onClick={() => run(() => api.duplicateAgent(agent.id))} disabled={busy} className={SMALL}>
                              Duplicate
                            </button>
                          )}
                          {!archived && (
                            <button
                              onClick={() => run(() => api.updateAgent(agent.id, { pinned: !agent.pinned }))}
                              disabled={busy}
                              className={SMALL}
                            >
                              {agent.pinned ? "Unpin" : "Pin"}
                            </button>
                          )}
                          <button
                            onClick={() => setExporting({ agent, includeNotes: false, notice: null })}
                            disabled={busy}
                            className={SMALL}
                          >
                            Export
                          </button>
                          <button
                            onClick={() => run(() => api.updateAgent(agent.id, { archived: !archived }))}
                            disabled={busy}
                            className={SMALL}
                          >
                            {archived ? "Restore" : "Archive"}
                          </button>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
              <div className="mt-4 flex flex-wrap items-center gap-3">
                <button onClick={() => setDraft(EMPTY)} className={PRIMARY}>
                  New agent
                </button>
                <button onClick={() => fileInput.current?.click()} className={BUTTON}>
                  Import…
                </button>
                <input
                  ref={fileInput}
                  type="file"
                  accept=".json,application/json"
                  className="hidden"
                  aria-label="Agent file to import"
                  onChange={(e) => pickImport(e.target.files?.[0])}
                />
                <label className="flex cursor-pointer items-center gap-2 text-xs text-muted">
                  <input type="checkbox" checked={showArchived} onChange={() => setShowArchived((on) => !on)} />
                  Show archived
                </label>
              </div>
            </>
          )}
    </Dialog>
  );
}

function NotesEditor({
  notes,
  busy,
  onText,
  onSave,
  onOverwrite,
  onLoadTheirs,
  onToggle,
  onToggleRecall,
  onReload,
  onBack,
}: {
  notes: NotesState;
  busy: boolean;
  onText: (text: string) => void;
  onSave: () => void;
  onOverwrite: () => void;
  onLoadTheirs: () => void;
  onToggle: (on: boolean) => void;
  onToggleRecall: (on: boolean) => void;
  onReload: () => void;
  onBack: () => void;
}) {
  const { memory } = notes;
  if (!memory) return <p className="text-sm text-muted">Loading notes…</p>;
  const gauge = memoryGauge(memory);
  const dirty = notes.text !== memory.text;
  const enabled = notes.agent.memory_enabled ?? true;
  const recall = notes.agent.recall_enabled ?? true;
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted">
        Notes this agent keeps between conversations, in <span className="font-mono break-all">{memory.path}</span>. Only
        the first {memory.max_lines} lines load into each conversation. Anything that looks like a password or key is
        replaced when saved.
      </p>
      <div>
        <div className="h-1.5 w-full overflow-hidden rounded bg-hover" aria-hidden="true">
          <div className={`h-full ${GAUGE_COLOR[gauge.level]}`} style={{ width: `${gauge.percent}%` }} />
        </div>
        <p className={`mt-1 text-xs ${gauge.level === "over" ? "text-danger" : "text-muted"}`}>{gauge.text}</p>
      </div>
      <textarea
        value={notes.text}
        rows={12}
        onChange={(e) => onText(e.target.value)}
        aria-label="Notes"
        placeholder={"- Facts this agent should remember\n- One per line"}
        className={`${FIELD} resize-y font-mono`}
      />
      {notes.conflict && (
        <div role="alert" className="rounded-md border border-warning/60 p-3 text-xs">
          <p>The agent changed these notes while you were editing. Load its version, or keep yours and overwrite it.</p>
          <div className="mt-2 flex gap-2">
            <button onClick={onLoadTheirs} disabled={busy} className={SMALL}>
              Load its version
            </button>
            <button onClick={onOverwrite} disabled={busy} className={SMALL}>
              Overwrite with mine
            </button>
          </div>
        </div>
      )}
      {notes.saved && <p className="text-xs text-muted">{notes.saved}</p>}
      <label className="flex cursor-pointer items-center gap-2 text-xs text-muted">
        <input type="checkbox" checked={enabled} disabled={busy} onChange={() => onToggle(!enabled)} />
        Use notes in this agent&apos;s conversations
      </label>
      <label className="flex cursor-pointer items-center gap-2 text-xs text-muted">
        <input type="checkbox" checked={recall} disabled={busy || !enabled} onChange={() => onToggleRecall(!recall)} />
        Recall before each message: add matching topic notes and passages from earlier conversations to the message
        (shown as a &ldquo;Recalled&rdquo; chip)
      </label>
      <div className="flex gap-2">
        <button onClick={onSave} disabled={busy || !dirty} className={PRIMARY}>
          {busy ? "Saving…" : "Save notes"}
        </button>
        <button onClick={onBack} disabled={busy} className={BUTTON}>
          {dirty ? "Discard" : "Done"}
        </button>
      </div>
      <MemoryPanel agentId={notes.agent.id} onNotesChanged={() => !dirty && onReload()} />
    </div>
  );
}
