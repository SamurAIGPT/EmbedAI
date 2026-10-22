"use client";

import * as RadixTabs from "@radix-ui/react-tabs";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export const Tabs = RadixTabs.Root;

// The tab strip scrolls sideways instead of wrapping or overflowing a narrow screen.
// `w-0 min-w-full` makes it contribute no intrinsic width, so a long strip cannot widen a
// shrink-wrapped parent (for example a centered `mx-auto` column) past the screen.
export function TabList({ children, label }: { children: ReactNode; label: string }) {
  return (
    <RadixTabs.List
      aria-label={label}
      className="flex w-0 min-w-full gap-1 overflow-x-auto border-b border-line [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
    >
      {children}
    </RadixTabs.List>
  );
}

export function Tab({ value, children }: { value: string; children: ReactNode }) {
  return (
    <RadixTabs.Trigger
      value={value}
      className={cn(
        "-mb-px shrink-0 cursor-pointer border-b-2 border-transparent px-3 py-2 text-sm text-muted transition-colors duration-(--dur-fast) hover:text-ink touch:py-3",
        "data-[state=active]:border-accent data-[state=active]:text-ink",
      )}
    >
      {children}
    </RadixTabs.Trigger>
  );
}

export const TabPanel = RadixTabs.Content;
