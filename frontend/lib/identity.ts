// A stable look for an agent, derived from its id: no uploads, no stored field, the same
// on every device. No runtime imports, so node tests can load it.

// Ten colours that carry white text at 4.5:1 or better (checked by scripts/check-contrast.mjs,
// which reads the same values from globals.css as --identity-1 .. --identity-10).
export const IDENTITY_COUNT = 10;

export function identityIndex(id: number | string): number {
  const text = String(id);
  let hash = 0;
  for (let i = 0; i < text.length; i++) hash = (hash * 31 + text.charCodeAt(i)) >>> 0;
  return hash % IDENTITY_COUNT;
}

export function identityVar(id: number | string): string {
  return `var(--identity-${identityIndex(id) + 1})`;
}

// The first letter or digit of a name, upper-cased; "?" when there is none.
export function initialOf(name: string | null | undefined): string {
  for (const char of (name ?? "").trim()) {
    if (/[\p{L}\p{N}]/u.test(char)) return char.toUpperCase();
  }
  return "?";
}
