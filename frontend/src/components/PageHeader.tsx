export function PageHeader({
  eyebrow,
  title,
  subtitle,
  actions,
}: {
  eyebrow?: string;
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="border-b border-[var(--border)] bg-[var(--surface)]/60 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-end justify-between gap-4 px-6 py-5">
        <div>
          {eyebrow && (
            <p className="font-mono text-[11px] uppercase tracking-widest text-[var(--text-muted)]">
              {eyebrow}
            </p>
          )}
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-[var(--cnrs-blue)]">
            {title}
          </h1>
          {subtitle && (
            <p className="mt-1 max-w-2xl text-sm text-[var(--text-muted)]">
              {subtitle}
            </p>
          )}
        </div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}
