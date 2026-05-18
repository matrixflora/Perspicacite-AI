"use client";

import { useState } from "react";
import type { ThinkingStep } from "@/lib/chat";
import { describeProvider, providerToneClasses } from "@/lib/databases";

export function ThinkingSteps({
  steps,
  defaultOpen = true,
}: {
  steps: ThinkingStep[];
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  if (steps.length === 0) return null;
  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-soft)]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-3 py-1.5 text-left text-xs text-[var(--text-muted)] hover:text-[var(--cnrs-blue)]"
        aria-expanded={open}
      >
        <span className="flex items-center gap-2">
          <span aria-hidden>{open ? "▾" : "▸"}</span>
          <span>Reasoning trail · {steps.length} step{steps.length === 1 ? "" : "s"}</span>
        </span>
      </button>
      {open && (
        <ol className="flex flex-col gap-1.5 border-t border-[var(--border)] px-3 py-2.5">
          {steps.map((s) => (
            <li key={s.id}>
              <StepRow step={s} />
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function StepRow({ step }: { step: ThinkingStep }) {
  if (step.kind === "query_rephrased" && step.detail) {
    return (
      <div className="flex items-start gap-2 text-xs">
        <Icon glyph="↻" />
        <div className="min-w-0 flex-1">
          <p className="font-medium text-[var(--text-body)]">Query refined</p>
          {step.detail.original && (
            <p className="text-[var(--text-muted)] line-through">
              {step.detail.original}
            </p>
          )}
          {step.detail.rephrased && (
            <p className="text-[var(--cnrs-blue)]">{step.detail.rephrased}</p>
          )}
        </div>
      </div>
    );
  }

  if (step.kind === "provider_progress" && step.detail) {
    const phase = step.detail.phase ?? "start";
    const providers = step.detail.providers ?? [];
    const byProvider = step.detail.byProvider ?? {};
    return (
      <div className="flex items-start gap-2 text-xs">
        <Icon glyph={phase === "start" ? "⌕" : "✓"} />
        <div className="min-w-0 flex-1">
          <p className="font-medium text-[var(--text-body)]">
            {phase === "start" ? "Querying databases" : "Database results"}
          </p>
          {phase === "start" && providers.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {providers.map((p) => (
                <ProviderPill key={p} name={p} />
              ))}
            </div>
          )}
          {phase === "done" && Object.keys(byProvider).length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {Object.entries(byProvider)
                .sort((a, b) => b[1] - a[1])
                .map(([p, count]) => (
                  <ProviderPill key={p} name={p} count={count} />
                ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  if (step.kind === "batch_progress" && step.detail) {
    const { current = 0, total = 0 } = step.detail;
    const pct = total > 0 ? Math.min(100, Math.round((current / total) * 100)) : 0;
    const done = total > 0 && current >= total;
    return (
      <div className="flex items-start gap-2 text-xs">
        <Icon glyph={done ? "✓" : "⋯"} />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <p className="font-medium text-[var(--text-body)]">{step.label}</p>
            <span className="font-mono text-[10px] tabular-nums text-[var(--text-muted)]">
              {current}/{total}
            </span>
          </div>
          {total > 0 && (
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-[var(--cnrs-grey-light)]">
              <div
                className="h-full transition-all"
                style={{
                  width: `${pct}%`,
                  background: done ? "var(--cnrs-green)" : "var(--cnrs-yellow)",
                }}
              />
            </div>
          )}
        </div>
      </div>
    );
  }

  // status (plain message)
  return (
    <div className="flex items-start gap-2 text-xs">
      <Icon glyph="·" />
      <p className="text-[var(--text-body)]">{step.label}</p>
    </div>
  );
}

function Icon({ glyph }: { glyph: string }) {
  return (
    <span
      aria-hidden
      className="grid h-4 w-4 shrink-0 place-items-center rounded-full bg-[var(--surface)] text-[10px] text-[var(--cnrs-blue)] ring-1 ring-[var(--border)]"
    >
      {glyph}
    </span>
  );
}

export function ProviderPill({
  name,
  count,
  href,
}: {
  name: string;
  count?: number;
  href?: string;
}) {
  const d = describeProvider(name);
  const label = d?.label ?? name;
  const short = d?.short ?? name.slice(0, 2).toUpperCase();
  const tone = d ? providerToneClasses(d.tone) : null;
  const linkUrl = href ?? d?.homepage;

  const content = (
    <>
      <span
        aria-hidden
        className={[
          "grid h-4 w-4 place-items-center rounded-sm text-[9px] font-bold",
          tone?.bg ?? "bg-[var(--cnrs-grey-light)]",
          tone?.text ?? "text-[var(--cnrs-blue)]",
        ].join(" ")}
      >
        {short}
      </span>
      <span className="text-[10px] font-medium text-[var(--text-body)]">
        {label}
      </span>
      {typeof count === "number" && (
        <span className="font-mono text-[10px] text-[var(--text-muted)]">{count}</span>
      )}
    </>
  );

  const className =
    "inline-flex items-center gap-1.5 rounded-full border border-[var(--border)] bg-[var(--surface)] px-1.5 py-0.5 transition hover:border-[var(--cnrs-blue)]";

  if (linkUrl) {
    return (
      <a
        href={linkUrl}
        target="_blank"
        rel="noreferrer noopener"
        className={className}
        title={`${label} · open homepage`}
      >
        {content}
      </a>
    );
  }
  return (
    <span className={className} title={label}>
      {content}
    </span>
  );
}
