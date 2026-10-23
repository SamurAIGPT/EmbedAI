export type Takeover = "watching" | "taking" | "connecting" | "live" | "lost";
export type TakeoverEvent = "take" | "taken" | "frame" | "failed" | "release" | "disconnect";
export function takeover(state: Takeover, event: TakeoverEvent): Takeover {
  if (event === "release" || event === "failed") return "watching";
  if (event === "take" && (state === "watching" || state === "lost")) return "taking";
  if (event === "taken" && state === "taking") return "connecting";
  if (event === "frame" && state === "connecting") return "live";
  if (event === "disconnect" && (state === "live" || state === "connecting")) return "lost";
  return state;
}
