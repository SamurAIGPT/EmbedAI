import { MessageSquare } from "lucide-react";
import { cn } from "@/lib/cn";
import { identityVar, initialOf } from "@/lib/identity";

const SIZES = { sm: "size-6 text-[11px]", md: "size-8 text-sm", lg: "size-11 text-lg" } as const;
const ICONS = { sm: 12, md: 16, lg: 22 } as const;

// An agent's picture: its first letter on a colour derived from its id. With no agent
// (a plain chat) it shows a neutral chat glyph. Decorative: the name is always printed beside it.
export function Avatar({
  agentId,
  name,
  size = "md",
  className,
}: {
  agentId: number | null;
  name?: string | null;
  size?: keyof typeof SIZES;
  className?: string;
}) {
  const base = "inline-flex shrink-0 select-none items-center justify-center rounded-full font-semibold";
  if (agentId === null) {
    return (
      <span aria-hidden="true" className={cn(base, "bg-inset text-muted", SIZES[size], className)}>
        <MessageSquare size={ICONS[size]} />
      </span>
    );
  }
  return (
    <span
      aria-hidden="true"
      style={{ backgroundColor: identityVar(agentId) }}
      className={cn(base, "text-white", SIZES[size], className)}
    >
      {initialOf(name)}
    </span>
  );
}
