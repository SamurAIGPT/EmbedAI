// The colour theme: System, Light or Dark. No runtime imports, so node tests can load it.
//
// The page always carries a resolved `data-theme` ("light" or "dark") on <html>, set by
// THEME_INIT_SCRIPT before the first paint. The person's choice lives only in this
// browser's localStorage (a convenience, never server state), so every read and write is
// guarded: blocked storage means "system".

export type ThemeChoice = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEME_KEY = "od-theme";
export const THEME_CHOICES: readonly ThemeChoice[] = ["system", "light", "dark"];

export function parseChoice(raw: unknown): ThemeChoice {
  return raw === "light" || raw === "dark" ? raw : "system";
}

export function resolveTheme(choice: ThemeChoice, systemDark: boolean): ResolvedTheme {
  if (choice === "system") return systemDark ? "dark" : "light";
  return choice;
}

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export function readChoice(storage: () => StorageLike): ThemeChoice {
  try {
    return parseChoice(storage().getItem(THEME_KEY));
  } catch {
    return "system";
  }
}

// Returns false when the choice could not be remembered (the theme still applies now).
export function writeChoice(storage: () => StorageLike, choice: ThemeChoice): boolean {
  try {
    if (choice === "system") storage().removeItem(THEME_KEY);
    else storage().setItem(THEME_KEY, choice);
    return true;
  } catch {
    return false;
  }
}

type DocumentLike = {
  documentElement: { dataset: Record<string, string | undefined>; style: { colorScheme: string } };
};

export function applyTheme(doc: DocumentLike, choice: ThemeChoice, systemDark: boolean): ResolvedTheme {
  const resolved = resolveTheme(choice, systemDark);
  doc.documentElement.dataset.theme = resolved;
  doc.documentElement.dataset.themeChoice = choice;
  doc.documentElement.style.colorScheme = resolved;
  return resolved;
}

// Runs inline in <head>, before first paint, so there is no flash of the wrong theme.
// Plain ES5 on purpose: it must keep working if nothing else loads. Must match the
// functions above (theme.test.mjs runs both against the same inputs).
export const THEME_INIT_SCRIPT = `(function(){var d=document.documentElement,c="system",m=false;try{m=matchMedia("(prefers-color-scheme: dark)").matches}catch(e){}try{var v=localStorage.getItem("${THEME_KEY}");if(v==="light"||v==="dark")c=v}catch(e){}var r=c==="system"?(m?"dark":"light"):c;d.dataset.theme=r;d.dataset.themeChoice=c;d.style.colorScheme=r})();`;
