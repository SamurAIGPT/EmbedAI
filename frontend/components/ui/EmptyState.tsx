import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

// Centered "nothing here yet" or "can't do that here" block: icon, one line of what, one of why/next.
export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-2 px-6 py-10 text-center", className)}>
      {icon ? <div aria-hidden="true" className="mb-1 text-faint">{icon}</div> : null}
      <h2 className="text-base font-semibold text-ink">{title}</h2>
      {description ? <p className="max-w-sm text-sm text-muted">{description}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
