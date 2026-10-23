// Words for agent computers. Pure: no React, no network.
import type { Computer, ComputerNetwork, ComputerSummary } from "./api";

export const COMPUTER_TOOL_PREFIX = "mcp__computer__";

export const NETWORK_MODES: { value: ComputerNetwork; label: string; description: string }[] = [
  { value: "none", label: "No network", description: "The computer cannot reach the internet or anything else." },
  { value: "default", label: "Internet", description: "Docker's default network: it can reach the internet; nothing can reach it." },
];

export function computerCreationGate(supported: boolean | undefined, engineLabel: string): { enabled: boolean; reason: string | null } {
  if (supported === true) return { enabled: true, reason: null };
  if (supported === false) {
    return {
      enabled: false,
      reason: `${engineLabel} can't run its tools in an agent computer. Choose an engine that supports computers to create one.`,
    };
  }
  return {
    enabled: false,
    reason: `Computer support for ${engineLabel} could not be confirmed. Refresh engine details before creating one.`,
  };
}

const STATE_LABEL: Record<string, string> = {
  missing: "Not made yet",
  creating: "Starting…",
  running: "Running",
  stopped: "Stopped",
  error: "Problem",
  deleting: "Removing…",
  foreign: "Name taken",
  unsafe: "Unsafe",
  unavailable: "Unreachable",
};

export function computerStateLabel(state: string | null | undefined): string {
  if (!state) return "Unknown";
  return STATE_LABEL[state] ?? state;
}

// What to show: what Docker says now when known, else what the app last saw.
export function effectiveState(computer: Pick<Computer, "state" | "live_state">): string {
  return computer.live_state ?? computer.state;
}

export function computerChip(summary: ComputerSummary | null | undefined, liveState?: string | null): string | null {
  if (!summary) return null;
  return `${summary.kind === "desktop" ? "Desktop" : "Computer"} · ${computerStateLabel(liveState ?? summary.state).toLowerCase()}`;
}

// Short image name for a narrow space: "python:3.12-slim-bookworm" from a pinned reference.
export function imageLabel(image: string): string {
  const withoutDigest = image.split("@")[0];
  const parts = withoutDigest.split("/");
  return parts[parts.length - 1] || image;
}

export function limitsText(computer: Pick<Computer, "cpus" | "memory_mb" | "pids_limit">): string {
  const memory = computer.memory_mb >= 1024 && computer.memory_mb % 1024 === 0 ? `${computer.memory_mb / 1024} GB` : `${computer.memory_mb} MB`;
  return `${computer.cpus} CPU · ${memory} · ${computer.pids_limit} processes`;
}

export function isComputerTool(tool: string | null | undefined): boolean {
  return typeof tool === "string" && tool.startsWith(COMPUTER_TOOL_PREFIX);
}

const TOOL_LABEL: Record<string, string> = {
  exec: "run a command",
  read_file: "read a file",
  write_file: "write a file",
  list_files: "list a folder",
};

// "run a command in its computer" for mcp__computer__exec; the name itself otherwise.
export function toolLabel(tool: string): string {
  if (!isComputerTool(tool)) return tool;
  const name = tool.slice(COMPUTER_TOOL_PREFIX.length);
  return `${TOOL_LABEL[name] ?? name} in its computer`;
}

// What the person can do next, in check order.
export type ComputerAction = "prepare-image" | "start" | "stop" | "recreate";

export function nextActions(computer: Computer): ComputerAction[] {
  const state = effectiveState(computer);
  if (computer.health && !computer.health.available) return [];
  const actions: ComputerAction[] = [];
  if (state === "missing" || state === "error") actions.push("prepare-image", "start");
  else if (state === "stopped") actions.push("start");
  else if (state === "running") actions.push("stop");
  // A machine that exists (or is unusable) can be made again from the settings.
  if (["running", "stopped", "unsafe", "error"].includes(state)) actions.push("recreate");
  return actions;
}

export const ACTION_LABEL: Record<ComputerAction, string> = {
  "prepare-image": "Download image",
  start: "Start",
  stop: "Stop",
  recreate: "Recreate",
};

export function lastCommandText(computer: Pick<Computer, "last_command">): string | null {
  const last = computer.last_command;
  if (!last || !last.subject) return null;
  const outcome = last.timed_out ? "timed out" : last.exit_code === null ? "no exit code" : `exit ${last.exit_code}`;
  const verb = last.tool === "exec" || last.tool === null ? "" : `${last.tool} `;
  return `${verb}${last.subject} (${outcome})`;
}

// An older backend has no computer routes: it answers 404 for all of them.
export function computersUnsupported(status: number, code: string | null): boolean {
  return status === 404 && code !== "no_computer";
}
