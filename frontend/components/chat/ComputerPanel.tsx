"use client";

import { useCallback, useEffect, useId, useState } from "react";
import { api, ApiError, type Agent, type Computer, type ComputerHealth, type ComputerNetwork } from "@/lib/api";
import {
  ACTION_LABEL,
  NETWORK_MODES,
  computerStateLabel,
  computerCreationGate,
  computersUnsupported,
  effectiveState,
  imageLabel,
  lastCommandText,
  limitsText,
  nextActions,
  type ComputerAction,
} from "@/lib/computers";

const FIELD =
  "w-full rounded-md border border-zinc-300 bg-transparent px-2 py-1 text-sm placeholder:text-zinc-500 dark:border-zinc-700";
const SMALL =
  "cursor-pointer rounded-md border border-zinc-300 px-2 py-1 text-xs font-medium hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-900";
const PRIMARY =
  "cursor-pointer rounded-md bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300";

const REFRESH_MS = 10_000;

type Phase =
  | { kind: "loading" }
  | { kind: "unsupported" }
  | { kind: "none"; health: ComputerHealth | null }
  | { kind: "ready"; computer: Computer };

type Limits = { cpus: string; memory_mb: string; pids_limit: string; idle_minutes: string; image: string };

function limitsOf(computer: Computer): Limits {
  return {
    cpus: String(computer.cpus),
    memory_mb: String(computer.memory_mb),
    pids_limit: String(computer.pids_limit),
    idle_minutes: String(computer.idle_minutes),
    image: computer.image,
  };
}

function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.message || "That did not work.";
  return err instanceof Error ? err.message : "Could not reach the backend.";
}

// An agent's computer: whether it has one, its state, image and limits, what
// it ran last, and the buttons to make, start, stop, recreate or remove it.
export function ComputerView({
  agent,
  onChanged,
  engineSupportsComputers,
  engineLabel,
}: {
  agent: Agent;
  onChanged?: () => void;
  engineSupportsComputers: boolean | undefined;
  engineLabel: string;
}) {
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [limits, setLimits] = useState<Limits | null>(null);
  const [network, setNetwork] = useState<ComputerNetwork>("none");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const capabilityHelpId = useId();
  const createGate = computerCreationGate(engineSupportsComputers, engineLabel);

  const load = useCallback(async () => {
    try {
      const computer = await api.getAgentComputer(agent.id);
      setPhase({ kind: "ready", computer });
      setLimits((current) => current ?? limitsOf(computer));
    } catch (err) {
      if (err instanceof ApiError && computersUnsupported(err.status, err.code)) {
        setPhase({ kind: "unsupported" });
      } else if (err instanceof ApiError && err.code === "no_computer") {
        const health = await api.computersHealth().catch(() => null);
        setPhase({ kind: "none", health });
      } else {
        setError(errorText(err));
      }
    }
  }, [agent.id]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the first read starts the panel
    void load();
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      const result = await action();
      if (result && typeof result === "object" && "id" in result && "agent_id" in result) {
        setPhase({ kind: "ready", computer: result as Computer });
        setLimits(limitsOf(result as Computer));
      } else {
        setLimits(null);
        await load();
      }
      onChanged?.();
    } catch (err) {
      setError(errorText(err));
      await load();
    } finally {
      setBusy(false);
    }
  };

  if (phase.kind === "loading") return <p className="text-sm text-zinc-500">Loading the computer…</p>;
  if (phase.kind === "unsupported")
    return <p className="text-sm text-zinc-500">This backend does not offer computers yet. Update it to give agents their own computer.</p>;

  const alert = error && (
    <p role="alert" className="text-sm text-red-600 dark:text-red-400">
      {error}
    </p>
  );

  if (phase.kind === "none") {
    const docker = phase.health;
    return (
      <div className="space-y-3 text-sm">
        {alert}
        <p className="text-zinc-600 dark:text-zinc-400">
          {agent.name} works on this machine. Give it its own computer, an isolated Docker container, and its commands and
          file edits run there instead: only its workspace folder is shared, it runs as a normal user with CPU, memory and
          process limits. Reading files and listing folders inside the computer are automatically allowed. File writes and commands
          follow the agent&apos;s approval mode and any applicable saved rule.
        </p>
        {docker && !docker.available && (
          <p className="text-amber-700 dark:text-amber-400">Docker is not available: {docker.error}</p>
        )}
        <fieldset className="space-y-1">
          <legend className="text-xs text-zinc-500">Network</legend>
          {NETWORK_MODES.map((mode) => (
            <label key={mode.value} className="flex cursor-pointer items-start gap-2 text-xs">
              <input type="radio" name="network" checked={network === mode.value} onChange={() => setNetwork(mode.value)} />
              <span>
                <span className="font-medium">{mode.label}</span> <span className="text-zinc-500">{mode.description}</span>
              </span>
            </label>
          ))}
        </fieldset>
        {!createGate.enabled && (
          <p id={capabilityHelpId} className="text-xs text-amber-700 dark:text-amber-400">
            {createGate.reason}
          </p>
        )}
        <button
          disabled={busy || !createGate.enabled}
          aria-describedby={!createGate.enabled ? capabilityHelpId : undefined}
          className={PRIMARY}
          onClick={() => act(() => api.createAgentComputer(agent.id, { network }))}
        >
          {busy ? "Working…" : "Give it a computer"}
        </button>
      </div>
    );
  }

  const computer = phase.computer;
  const state = effectiveState(computer);
  const last = lastCommandText(computer);
  const actions = nextActions(computer);
  const draft = limits ?? limitsOf(computer);
  const run = (action: ComputerAction) => act(() => api.computerAction(computer.id, action));
  const saveLimits = () =>
    act(() =>
      api.updateComputer(computer.id, {
        cpus: Number(draft.cpus),
        memory_mb: Math.round(Number(draft.memory_mb)),
        pids_limit: Math.round(Number(draft.pids_limit)),
        idle_minutes: Math.round(Number(draft.idle_minutes)),
        ...(draft.image.trim() && draft.image.trim() !== computer.image ? { image: draft.image.trim() } : {}),
      })
    );

  return (
    <div className="space-y-3 text-sm">
      {alert}
      <div className="rounded-md border border-zinc-200 p-3 dark:border-zinc-800">
        <p className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{computerStateLabel(state)}</span>
          {computer.busy && <span className="rounded bg-zinc-200 px-1.5 text-xs dark:bg-zinc-800">in use by a turn</span>}
          <span className="font-mono text-xs text-zinc-500" title={computer.image}>
            {imageLabel(computer.image)}
            {computer.image_pinned ? " · pinned" : " · not pinned"}
          </span>
        </p>
        <p className="mt-1 text-xs text-zinc-500">
          {computer.provider} · {NETWORK_MODES.find((m) => m.value === computer.network)?.label ?? computer.network} ·{" "}
          {limitsText(computer)}
          {computer.idle_minutes > 0 ? ` · stops after ${computer.idle_minutes} idle min` : " · never stops on its own"}
        </p>
        {computer.workspace_path && (
          <p className="mt-1 truncate text-xs text-zinc-500" title={computer.workspace_path}>
            {computer.workspace_path} → {computer.guest_workspace}
          </p>
        )}
        {computer.health && !computer.health.available && (
          <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">Docker is not available: {computer.health.error}</p>
        )}
        {computer.problem && <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">{computer.problem}</p>}
        {!computer.problem && computer.last_error && state !== "running" && (
          <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">{computer.last_error}</p>
        )}
        {computer.needs_recreate && (
          <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">
            Settings changed since it was made. Recreate it for them to apply (files in the workspace folder stay).
          </p>
        )}
        <div className="mt-2 flex flex-wrap gap-1">
          {actions.map((action) => (
            <button key={action} disabled={busy || (computer.busy && action !== "start")} className={SMALL} onClick={() => run(action)}>
              {ACTION_LABEL[action]}
            </button>
          ))}
          <button disabled={busy} className={SMALL} onClick={() => void load()}>
            Refresh
          </button>
        </div>
      </div>

      <div>
        <p className="mb-1 text-xs text-zinc-500">Last command</p>
        {last ? (
          <>
            <p className="truncate font-mono text-xs" title={last}>
              {last}
            </p>
            {computer.last_command?.output && (
              <pre className="mt-1 max-h-40 overflow-auto rounded bg-zinc-100 p-2 text-xs whitespace-pre-wrap dark:bg-zinc-900">
                {computer.last_command.output}
              </pre>
            )}
          </>
        ) : (
          <p className="text-xs text-zinc-500">Nothing has run yet.</p>
        )}
      </div>

      <form
        className="grid grid-cols-2 gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          saveLimits();
        }}
      >
        <label className="text-xs text-zinc-500">
          CPUs
          <input type="number" step="0.1" min="0.1" max="64" value={draft.cpus} onChange={(e) => setLimits({ ...draft, cpus: e.target.value })} className={`${FIELD} mt-1`} />
        </label>
        <label className="text-xs text-zinc-500">
          Memory (MB)
          <input type="number" min="128" max="262144" value={draft.memory_mb} onChange={(e) => setLimits({ ...draft, memory_mb: e.target.value })} className={`${FIELD} mt-1`} />
        </label>
        <label className="text-xs text-zinc-500">
          Processes
          <input type="number" min="32" max="65536" value={draft.pids_limit} onChange={(e) => setLimits({ ...draft, pids_limit: e.target.value })} className={`${FIELD} mt-1`} />
        </label>
        <label className="text-xs text-zinc-500">
          Stop after idle (min, 0 never)
          <input type="number" min="0" max="10080" value={draft.idle_minutes} onChange={(e) => setLimits({ ...draft, idle_minutes: e.target.value })} className={`${FIELD} mt-1`} />
        </label>
        <label className="col-span-2 text-xs text-zinc-500">
          Image (applies on recreate; name@sha256:… pins it)
          <input value={draft.image} onChange={(e) => setLimits({ ...draft, image: e.target.value })} className={`${FIELD} mt-1 font-mono`} />
        </label>
        <div className="col-span-2 flex flex-wrap items-center gap-2">
          <button type="submit" disabled={busy} className={SMALL}>
            Save limits
          </button>
          <select
            aria-label="Network"
            value={computer.network}
            disabled={busy}
            onChange={(e) => act(() => api.updateComputer(computer.id, { network: e.target.value as ComputerNetwork }))}
            className="cursor-pointer rounded-md border border-zinc-300 bg-transparent px-2 py-1 text-xs dark:border-zinc-700"
          >
            {NETWORK_MODES.map((mode) => (
              <option key={mode.value} value={mode.value}>
                {mode.label}
              </option>
            ))}
          </select>
          <label className="flex cursor-pointer items-center gap-1 text-xs text-zinc-500">
            <input
              type="checkbox"
              checked={computer.auto_start}
              disabled={busy}
              onChange={() => act(() => api.updateComputer(computer.id, { auto_start: !computer.auto_start }))}
            />
            Start when a turn needs it
          </label>
        </div>
      </form>

      <div className="border-t border-zinc-200 pt-3 dark:border-zinc-800">
        {confirmRemove ? (
          <p className="flex flex-wrap items-center gap-2 text-xs">
            Remove the computer? Everything inside it is lost; the workspace folder stays. {agent.name} then works on this machine again.
            <button disabled={busy} className={SMALL} onClick={() => act(() => api.deleteComputer(computer.id)).then(() => setConfirmRemove(false))}>
              Remove
            </button>
            <button disabled={busy} className={SMALL} onClick={() => setConfirmRemove(false)}>
              Keep
            </button>
          </p>
        ) : (
          <button disabled={busy || computer.busy} className={SMALL} onClick={() => setConfirmRemove(true)}>
            Remove computer…
          </button>
        )}
      </div>
    </div>
  );
}
