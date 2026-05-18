import type { ChatSource } from "@/lib/chat";

export function SourcePill({ source, index }: { source: ChatSource; index: number }) {
  const href =
    source.url ??
    (source.doi ? `https://doi.org/${source.doi}` : undefined);

  const label =
    source.title ?? source.doi ?? source.paper_id ?? `Source ${index + 1}`;

  const meta = [source.authors?.[0], source.year]
    .filter((x): x is string | number => Boolean(x))
    .join(" · ");

  return (
    <a
      href={href}
      target={href ? "_blank" : undefined}
      rel="noreferrer noopener"
      className="group flex items-start gap-2 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-xs transition hover:border-[var(--cnrs-blue)]/40 hover:shadow-[var(--shadow-card)]"
    >
      <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-[var(--cnrs-yellow)] font-mono text-[10px] font-semibold text-[var(--cnrs-blue)]">
        {index + 1}
      </span>
      <span className="min-w-0">
        <span className="block truncate font-medium text-[var(--cnrs-blue)] group-hover:underline">
          {label}
        </span>
        {meta && (
          <span className="block truncate text-[var(--text-muted)]">{meta}</span>
        )}
      </span>
    </a>
  );
}
