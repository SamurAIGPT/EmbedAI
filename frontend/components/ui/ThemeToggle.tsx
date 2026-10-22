"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useSyncExternalStore } from "react";
import { cn } from "@/lib/cn";
import { t } from "@/lib/i18n";
import { applyTheme, parseChoice, THEME_CHOICES, writeChoice, type ThemeChoice } from "@/lib/theme";

const ICONS = { system: Monitor, light: Sun, dark: Moon } as const;
const LABELS = { system: "theme.system", light: "theme.light", dark: "theme.dark" } as const;

const DARK_QUERY = "(prefers-color-scheme: dark)";

// The page itself is the source of truth: the pre-paint script (lib/theme.ts) put the choice on
// <html data-theme-choice>. Reading it here means no storage read after hydration.
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => void listeners.delete(listener);
};
const snapshot = (): ThemeChoice => parseChoice(document.documentElement.dataset.themeChoice);
const serverSnapshot = (): ThemeChoice => "system";

// System / Light / Dark. The choice is remembered in this browser only; when it is "system"
// the page follows the operating system live.
export function ThemeToggle({ className }: { className?: string }) {
  const choice = useSyncExternalStore(subscribe, snapshot, serverSnapshot);

  useEffect(() => {
    if (choice !== "system") return;
    const query = window.matchMedia(DARK_QUERY);
    const follow = () => applyTheme(document, "system", query.matches);
    query.addEventListener("change", follow);
    return () => query.removeEventListener("change", follow);
  }, [choice]);

  const pick = (next: ThemeChoice) => {
    writeChoice(() => window.localStorage, next);
    applyTheme(document, next, window.matchMedia(DARK_QUERY).matches);
    listeners.forEach((listener) => listener());
  };

  return (
    <div role="radiogroup" aria-label={t("theme.label")} className={cn("inline-flex rounded-field border border-line bg-inset p-0.5", className)}>
      {THEME_CHOICES.map((value) => {
        const Icon = ICONS[value];
        const selected = value === choice;
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => pick(value)}
            className={cn(
              "inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-lg px-3 text-[13px] transition-colors duration-(--dur-fast) touch:h-11",
              selected ? "bg-raised text-ink shadow-sm" : "text-muted hover:text-ink",
            )}
          >
            <Icon size={14} aria-hidden="true" />
            {t(LABELS[value])}
          </button>
        );
      })}
    </div>
  );
}
