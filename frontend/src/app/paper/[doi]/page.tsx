"use client";

import { use, useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { papers, type PaperDetail } from "@/lib/api";

type Params = { doi: string };

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; paper: PaperDetail }
  | { kind: "not-found" }
  | { kind: "error"; message: string };

type Figure = NonNullable<NonNullable<PaperDetail["capsule"]>["figures"]>[number];

export default function PaperDetailPage({
  params,
}: {
  params: Promise<Params>;
}) {
  const { doi: rawDoi } = use(params);
  const doi = decodeURIComponent(rawDoi);

  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [lightbox, setLightbox] = useState<Figure | null>(null);

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    papers
      .byDoi(doi)
      .then((paper) => {
        if (cancelled) return;
        setState({ kind: "ready", paper });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : "Unknown error";
        if (msg.includes("404")) {
          setState({ kind: "not-found" });
        } else {
          setState({ kind: "error", message: msg });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [doi]);

  // Close lightbox on Escape.
  useEffect(() => {
    if (!lightbox) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setLightbox(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lightbox]);

  if (state.kind === "loading") {
    return (
      <main className="min-h-screen">
        <PageHeader eyebrow="Paper" title="Loading…" subtitle={doi} />
        <div className="mx-auto max-w-6xl px-6 py-10">
          <div className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-6 shadow-[var(--shadow-card)]">
            <p className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
              <span className="pulse-dot">●</span>
              <span className="pulse-dot" style={{ animationDelay: "0.15s" }}>
                ●
              </span>
              <span className="pulse-dot" style={{ animationDelay: "0.3s" }}>
                ●
              </span>
              <span className="ml-1">Fetching paper metadata…</span>
            </p>
          </div>
        </div>
      </main>
    );
  }

  if (state.kind === "not-found") {
    return (
      <main className="min-h-screen">
        <PageHeader eyebrow="Paper" title="Not found" subtitle={doi} />
        <div className="mx-auto max-w-3xl px-6 py-12">
          <div className="relative overflow-hidden rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-8 shadow-[var(--shadow-card)]">
            <span
              className="cnrs-halo"
              style={{
                width: 240,
                height: 240,
                top: -100,
                right: -80,
                opacity: 0.8,
              }}
              aria-hidden
            />
            <div className="relative">
              <p className="font-mono text-[11px] uppercase tracking-widest text-[var(--text-muted)]">
                404
              </p>
              <h2 className="mt-1 text-xl font-semibold text-[var(--cnrs-blue)]">
                Paper not in the knowledge base
              </h2>
              <p className="mt-2 max-w-xl text-sm text-[var(--text-muted)]">
                No paper was found for DOI{" "}
                <span className="font-mono text-[var(--cnrs-blue)]">{doi}</span>
                . It may not yet be indexed.
              </p>
              <a
                href={`https://doi.org/${doi}`}
                target="_blank"
                rel="noreferrer noopener"
                className="mt-4 inline-block rounded-[var(--radius-md)] border border-[var(--cnrs-blue)] px-4 py-2 text-sm font-medium text-[var(--cnrs-blue)] transition hover:bg-[var(--cnrs-blue)] hover:text-white"
              >
                Open at doi.org →
              </a>
            </div>
          </div>
        </div>
      </main>
    );
  }

  if (state.kind === "error") {
    return (
      <main className="min-h-screen">
        <PageHeader eyebrow="Paper" title="Error" subtitle={doi} />
        <div className="mx-auto max-w-3xl px-6 py-12">
          <div className="rounded-[var(--radius-lg)] border border-red-300 bg-red-50 p-6 text-sm text-red-800 shadow-[var(--shadow-card)]">
            <p className="font-medium">⚠ Failed to load paper</p>
            <p className="mt-1 font-mono text-xs">{state.message}</p>
          </div>
        </div>
      </main>
    );
  }

  const paper = state.paper;
  const title = paper.title ?? "(no title)";
  const authorsLine = paper.authors?.length ? paper.authors.join(", ") : null;

  const figures = paper.capsule?.figures ?? [];
  const supplementary = paper.capsule?.supplementary ?? [];
  const references = paper.references ?? [];

  return (
    <main className="min-h-screen">
      <PageHeader
        eyebrow="Paper"
        title={title}
        subtitle={undefined}
      />
      <div className="border-b border-[var(--border)] bg-[var(--surface)]/40">
        <div className="mx-auto max-w-6xl px-6 pb-4 -mt-2">
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-[var(--text-muted)]">
            {authorsLine && <span>{authorsLine}</span>}
            {authorsLine && paper.year && <span aria-hidden>·</span>}
            {paper.year && <span>{paper.year}</span>}
            {(authorsLine || paper.year) && <span aria-hidden>·</span>}
            <a
              href={`https://doi.org/${doi}`}
              target="_blank"
              rel="noreferrer noopener"
              className="font-mono text-[var(--cnrs-blue)] underline-offset-2 hover:underline"
            >
              {doi}
            </a>
          </p>
        </div>
      </div>

      <div className="mx-auto max-w-6xl px-6 py-8">
        <div className="grid gap-6 lg:grid-cols-3">
          {/* Main column */}
          <div className="flex flex-col gap-6 lg:col-span-2">
            <Card title="Abstract">
              {paper.abstract ? (
                <div className="flex flex-col gap-3 text-[15px] leading-relaxed text-[var(--text-body)]">
                  {paper.abstract
                    .split(/\n{2,}/)
                    .map((para, i) => (
                      <p key={i} className="whitespace-pre-wrap">
                        {para}
                      </p>
                    ))}
                </div>
              ) : (
                <p className="text-sm text-[var(--text-muted)]">
                  No abstract available.
                </p>
              )}
            </Card>

            {paper.full_text && (
              <Card>
                <details className="group">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-sm font-medium text-[var(--cnrs-blue)]">
                    <span className="font-mono text-[11px] uppercase tracking-widest text-[var(--text-muted)] group-open:text-[var(--cnrs-blue)]">
                      Full text
                    </span>
                    <span className="text-xs text-[var(--text-muted)] group-open:hidden">
                      Show full text →
                    </span>
                    <span className="hidden text-xs text-[var(--text-muted)] group-open:inline">
                      Hide
                    </span>
                  </summary>
                  <div className="mt-4 max-h-[600px] overflow-y-auto whitespace-pre-wrap rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-soft)] p-4 font-mono text-[13px] leading-relaxed text-[var(--text-body)]">
                    {paper.full_text}
                  </div>
                </details>
              </Card>
            )}

            {references.length > 0 && (
              <Card title={`References (${references.length})`}>
                <ol className="flex flex-col divide-y divide-[var(--border)]">
                  {references.map((ref, i) => {
                    const refDoi = ref.doi;
                    const refTitle = ref.title ?? refDoi ?? "(untitled)";
                    return (
                      <li
                        key={i}
                        className="flex items-start gap-3 py-2.5 text-sm"
                      >
                        <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-[var(--cnrs-grey-light)] font-mono text-[10px] font-semibold text-[var(--cnrs-blue)]">
                          {i + 1}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-[var(--text-body)]">
                            {refTitle}
                          </span>
                          <span className="mt-0.5 flex flex-wrap gap-x-2 text-xs text-[var(--text-muted)]">
                            {ref.year && <span>{ref.year}</span>}
                            {refDoi && (
                              <a
                                href={`https://doi.org/${refDoi}`}
                                target="_blank"
                                rel="noreferrer noopener"
                                className="font-mono text-[var(--cnrs-blue)] underline-offset-2 hover:underline"
                              >
                                {refDoi}
                              </a>
                            )}
                          </span>
                        </span>
                      </li>
                    );
                  })}
                </ol>
              </Card>
            )}
          </div>

          {/* Side column */}
          <aside className="flex flex-col gap-6 lg:col-span-1">
            {figures.length > 0 && paper.paper_id && (
              <Card title="Capsule figures">
                <ul className="flex flex-col gap-4">
                  {figures.map((fig) => {
                    const paperId = paper.paper_id as string;
                    return (
                      <li key={fig.id} className="flex flex-col gap-2">
                        <button
                          type="button"
                          onClick={() => setLightbox(fig)}
                          className="group relative block overflow-hidden rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-soft)] transition hover:border-[var(--cnrs-blue)]/40 hover:shadow-[var(--shadow-card)]"
                          aria-label={`Open figure ${fig.id}`}
                        >
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img
                            src={papers.capsuleFigureUrl(paperId, fig.id)}
                            alt={fig.caption ?? `Figure ${fig.id}`}
                            className="block w-full bg-white object-contain transition group-hover:opacity-95"
                          />
                        </button>
                        {fig.caption && (
                          <p className="text-xs leading-relaxed text-[var(--text-muted)]">
                            {fig.caption}
                          </p>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </Card>
            )}

            {supplementary.length > 0 && (
              <Card title="Supplementary">
                <ul className="flex flex-col gap-1.5">
                  {supplementary.map((sup, i) => (
                    <li key={i}>
                      <a
                        href={sup.url}
                        target={sup.url ? "_blank" : undefined}
                        rel="noreferrer noopener"
                        className="flex items-start gap-2 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-xs transition hover:border-[var(--cnrs-blue)]/40 hover:shadow-[var(--shadow-card)]"
                      >
                        <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-[var(--cnrs-yellow)] font-mono text-[10px] font-semibold text-[var(--cnrs-blue)]">
                          {i + 1}
                        </span>
                        <span className="min-w-0">
                          <span className="block truncate font-medium text-[var(--cnrs-blue)]">
                            {sup.name}
                          </span>
                        </span>
                      </a>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </aside>
        </div>
      </div>

      {lightbox && paper.paper_id && (
        <Lightbox
          figure={lightbox}
          paperId={paper.paper_id}
          onClose={() => setLightbox(null)}
        />
      )}
    </main>
  );
}

function Card({
  title,
  children,
}: {
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-5 shadow-[var(--shadow-card)] md:p-6">
      {title && (
        <p className="mb-3 font-mono text-[11px] uppercase tracking-widest text-[var(--text-muted)]">
          {title}
        </p>
      )}
      {children}
    </section>
  );
}

function Lightbox({
  figure,
  paperId,
  onClose,
}: {
  figure: Figure;
  paperId: string;
  onClose: () => void;
}) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={figure.caption ?? `Figure ${figure.id}`}
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--cnrs-blue)]/80 p-4 backdrop-blur-sm"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="relative flex max-h-[92vh] max-w-5xl flex-col overflow-hidden rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-elev)]"
      >
        <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] px-4 py-2.5">
          <p className="font-mono text-[11px] uppercase tracking-widest text-[var(--text-muted)]">
            Figure · {figure.id}
          </p>
          <button
            type="button"
            onClick={onClose}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] px-2.5 py-1 text-xs text-[var(--cnrs-blue)] transition hover:bg-[var(--cnrs-grey-light)]"
            aria-label="Close"
          >
            Close ✕
          </button>
        </div>
        <div className="flex-1 overflow-auto bg-white p-3">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={papers.capsuleFigureUrl(paperId, figure.id)}
            alt={figure.caption ?? `Figure ${figure.id}`}
            className="mx-auto block max-h-[70vh] object-contain"
          />
        </div>
        {figure.caption && (
          <div className="border-t border-[var(--border)] bg-[var(--bg-soft)] px-4 py-3">
            <p className="text-sm leading-relaxed text-[var(--text-body)]">
              {figure.caption}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
