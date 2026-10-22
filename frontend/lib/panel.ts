// The right-hand inspector page lives in the address (/chat?thread=3&panel=usage), so a reload
// or a shared link reopens it. No runtime imports, so node tests can load it.

export type Panel = "usage" | "computer";

export function parsePanel(raw: string | null): Panel | null {
  return raw === "usage" || raw === "computer" ? raw : null;
}

// The chat address with the panel set or (null) removed; every other parameter is kept.
export function chatHref(search: string, panel: Panel | null): string {
  const query = new URLSearchParams(search);
  if (panel) query.set("panel", panel);
  else query.delete("panel");
  const text = query.toString();
  return text ? `/chat?${text}` : "/chat";
}
