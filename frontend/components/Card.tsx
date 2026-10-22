export function Card({
  step,
  title,
  done,
  disabled,
  children,
}: {
  step: number;
  title: string;
  done?: boolean;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <section
      className={`rounded-lg border p-5 transition-opacity ${
        disabled
          ? "border-line opacity-50"
          : "border-line"
      }`}
    >
      <div className="mb-3 flex items-center gap-2">
        <span
          className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-sm font-medium ${
            done
              ? "bg-success text-app"
              : "bg-hover text-ink"
          }`}
        >
          {done ? "✓" : step}
        </span>
        <h2 className="text-base font-semibold">{title}</h2>
      </div>
      <div className={disabled ? "pointer-events-none" : ""}>{children}</div>
    </section>
  );
}

export function ErrorText({ message }: { message: string | null }) {
  if (!message) return null;
  return <p className="mt-2 text-sm text-danger">{message}</p>;
}

export function Button({
  children,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className="rounded-md px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 bg-accent text-accent-fg hover:opacity-90"
    >
      {children}
    </button>
  );
}
