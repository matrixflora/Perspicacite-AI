"use client";

import { useState } from "react";
import Link from "next/link";
import type { ChatSource } from "@/lib/chat";
import { ProviderPill } from "./ThinkingSteps";

// Relevance breakdown matches the original GUI's tooltip — useful context
// for users wondering why a paper ranked where it did.
const RELEVANCE_BREAKDOWN =
  "Blended relevance: 60% MiniLM (semantic, query vs title+abstract) + 25% log citation count + 15% BM25 (lexical)";

export function SourcePill({
  source,
  index,
}: {
  source: ChatSource;
  index: number;
}) {
  const [showAbstract, setShowAbstract] = useState(false);

  // Prefer the in-app reader when we have a DOI we can resolve; fall
  // through to whichever external URL the backend gave us.
  const readerHref = source.doi
    ? `/reader/${encodeURIComponent(source.doi)}`
    : null;
  const externalHref =
    source.url ??
    source.oa_url ??
    source.pdf_url ??
    (source.doi ? `https://doi.org/${source.doi}` : undefined);

  const label =
    source.title ?? source.doi ?? source.paper_id ?? `Source ${index + 1}`;

  const meta: string[] = [];
  if (source.authors?.length) {
    meta.push(
      source.authors.length > 1
        ? `${source.authors[0]} et al.`
        : source.authors[0],
    );
  }
  if (typeof source.year === "number") meta.push(String(source.year));
  if (typeof source.citation_count === "number")
    meta.push(`${source.citation_count.toLocaleString()} cites`);

  const relevancePct =
    typeof source.relevance_score === "number"
      ? Math.round(source.relevance_score * (source.relevance_score <= 1 ? 100 : 1))
      : null;

  // Tolerant: the backend sometimes ships providers as a list (discovery_sources)
  // or as a single `provider`. We render either, dedup.
  const providers = Array.from(
    new Set(
      [
        source.provider,
        ...(source.providers ?? []),
        ...(source.discovery_sources ?? []),
      ].filter((s): s is string => !!s),
    ),
  );

  return (
    <div className="group rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] transition hover:border-[var(--cnrs-blue)]/40 hover:shadow-[var(--shadow-card)]">
      {/* Top row: number + title + open link */}
      <div className="flex items-start gap-2 px-3 py-2">
        <span
          aria-hidden
          className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-[var(--cnrs-yellow)] font-mono text-[10px] font-semibold text-[var(--cnrs-blue)]"
        >
          {index + 1}
        </span>
        <div className="min-w-0 flex-1">
          {readerHref ? (
            <Link
              href={readerHref}
              className="block truncate text-xs font-medium text-[var(--cnrs-blue)] hover:underline"
              title={`Open in reader · ${label}`}
            >
              {label}
            </Link>
          ) : externalHref ? (
            <a
              href={externalHref}
              target="_blank"
              rel="noreferrer noopener"
              className="block truncate text-xs font-medium text-[var(--cnrs-blue)] hover:underline"
              title={label}
            >
              {label}
            </a>
          ) : (
            <span
              className="block truncate text-xs font-medium text-[var(--text-body)]"
              title={label}
            >
              {label}
            </span>
          )}
          {meta.length > 0 && (
            <p className="truncate text-[11px] text-[var(--text-muted)]">
              {meta.join(" · ")}
            </p>
          )}

          {/* Provider tags + relevance score */}
          {(providers.length > 0 || relevancePct != null) && (
            <div className="mt-1 flex flex-wrap items-center gap-1">
              {providers.map((p) => (
                <ProviderPill key={p} name={p} />
              ))}
              {relevancePct != null && (
                <span
                  className="inline-flex items-center gap-1 rounded-full border border-[var(--cnrs-yellow)] bg-[var(--cnrs-yellow)]/40 px-1.5 py-0.5 text-[10px] font-mono font-medium text-[var(--cnrs-blue)]"
                  title={RELEVANCE_BREAKDOWN}
                >
                  <span aria-hidden>★</span>
                  {relevancePct}%
                </span>
              )}
            </div>
          )}
        </div>

        {source.abstract && (
          <button
            type="button"
            onClick={() => setShowAbstract((v) => !v)}
            className="shrink-0 rounded-[var(--radius-sm)] border border-[var(--border)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--text-muted)] hover:border-[var(--cnrs-blue)] hover:text-[var(--cnrs-blue)]"
            aria-expanded={showAbstract}
            aria-label="Toggle abstract"
            title="Show abstract"
          >
            {showAbstract ? "Hide" : "Abstract"}
          </button>
        )}
      </div>

      {showAbstract && source.abstract && (
        <div className="border-t border-[var(--border)] px-3 py-2 text-[11px] leading-relaxed text-[var(--text-body)]">
          {source.abstract}
        </div>
      )}
    </div>
  );
}
