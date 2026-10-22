// Strings for the new interface only; older components keep their literal text. One language
// today: add another by adding a table here and picking it by the document's language.
// Keys are surface.name; {name} in a message is a placeholder. No imports, so node tests can load it.
export const en = {
  "theme.label": "Theme",
  "theme.system": "System",
  "theme.light": "Light",
  "theme.dark": "Dark",
  "common.close": "Close",
  "common.loading": "Loading…",
  "common.cancel": "Cancel",
} as const;

export type MessageKey = keyof typeof en;

export function t(key: MessageKey, params?: Record<string, string | number>): string {
  const template: string = en[key] ?? key;
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name: string) => (name in params ? String(params[name]) : whole));
}
