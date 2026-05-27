"use client";

import type { ExpandHistBucket } from "@/lib/api";

// 10 equal-width 0–1 score buckets as vertical bars. When `cutoff` is set,
// bars fully at/above it are highlighted (kept) and a vertical line marks it.
export function ScoreHistogram({
  buckets,
  cutoff,
}: {
  buckets: ExpandHistBucket[];
  cutoff?: number | null;
}) {
  const max = Math.max(1, ...buckets.map((b) => b.count));
  return (
    <div className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-4 shadow-[var(--shadow-card)]">
      <div className="relative flex h-32 items-stretch gap-1">
        {buckets.map((b, i) => {
          const pct = (b.count / max) * 100;
          const kept = cutoff != null && b.lo >= cutoff;
          return (
            <div
              key={i}
              className="flex flex-1 flex-col items-center"
              title={`${b.lo.toFixed(1)}–${b.hi.toFixed(1)}: ${b.count}`}
            >
              <span className="mb-0.5 font-mono text-[10px] text-[var(--text-muted)]">
                {b.count || ""}
              </span>
              {/* flex-1 wrapper gives the bar a definite height to take a % of */}
              <div className="flex w-full flex-1 items-end">
                <div
                  className="w-full rounded-t-sm transition-[height]"
                  style={{
                    height: `${pct}%`,
                    minHeight: b.count ? "2px" : "0",
                    background: kept
                      ? "var(--cnrs-blue)"
                      : "var(--cnrs-grey-light)",
                  }}
                />
              </div>
            </div>
          );
        })}
        {cutoff != null && (
          <div
            className="pointer-events-none absolute inset-y-0 w-px bg-[var(--cnrs-orange)]"
            style={{ left: `${Math.min(100, Math.max(0, cutoff * 100))}%` }}
            aria-hidden
          />
        )}
      </div>
      <div className="mt-1 flex justify-between font-mono text-[10px] text-[var(--text-muted)]">
        <span>0.0</span>
        <span>similarity score</span>
        <span>1.0</span>
      </div>
    </div>
  );
}
