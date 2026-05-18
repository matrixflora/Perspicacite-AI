"use client";

import { useEffect, useState } from "react";
import { health } from "@/lib/api";

export type ChatPhase =
  | "idle"
  | "sending"
  | "retrieving"
  | "synthesizing"
  | "done"
  | "error"
  | "cancelled";

const PHASE_LABEL: Record<ChatPhase, string> = {
  idle: "Idle",
  sending: "Sending query…",
  retrieving: "Retrieving sources…",
  synthesizing: "Generating answer…",
  done: "Done",
  error: "Error",
  cancelled: "Cancelled",
};

export function StatusBar({
  phase,
  label,
  elapsedMs,
  tokensIn,
  tokensOut,
  papersFound,
  mode,
  onCancel,
}: {
  phase: ChatPhase;
  label?: string;
  elapsedMs?: number;
  tokensIn?: number;
  tokensOut?: number;
  papersFound?: number;
  mode?: string;
  onCancel?: () => void;
}) {
  const elapsed = ((elapsedMs ?? 0) / 1000).toFixed(1);
  const running =
    phase === "sending" || phase === "retrieving" || phase === "synthesizing";
  const display = label ?? PHASE_LABEL[phase];
  const llm = useLlmModelLabel();

  // Don't render anything in idle to keep the chat surface clean.
  if (phase === "idle" && !label) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-full border border-[var(--border)] bg-[var(--surface)] px-3.5 py-1.5 text-xs text-[var(--text-muted)] shadow-[var(--shadow-card)]"
    >
      {/* Heartbeat — yellow sun pulse when working, static when idle/done */}
      <span className="flex items-center gap-1.5">
        {running ? (
          <span className="cnrs-sun" aria-hidden />
        ) : phase === "error" ? (
          <span
            aria-hidden
            className="h-2 w-2 rounded-full"
            style={{ background: "#dc2626" }}
          />
        ) : (
          <span
            aria-hidden
            className="h-2 w-2 rounded-full"
            style={{ background: "var(--cnrs-grey)" }}
          />
        )}
        <span
          className={
            phase === "error"
              ? "font-medium text-red-700"
              : "font-medium text-[var(--text-body)]"
          }
        >
          {display}
        </span>
      </span>

      {(elapsedMs != null && elapsedMs > 0) && (
        <>
          <Sep />
          <span className="font-mono tabular-nums">{elapsed}s</span>
        </>
      )}

      {typeof papersFound === "number" && papersFound > 0 && (
        <>
          <Sep />
          <span>{papersFound} papers</span>
        </>
      )}

      {(tokensIn || tokensOut) && (
        <>
          <Sep />
          <span
            className="font-mono tabular-nums"
            title={`Input tokens (≈chars/4): ${tokensIn ?? 0}\nOutput tokens: ${tokensOut ?? 0}`}
          >
            {(tokensIn ?? 0).toLocaleString()} <span aria-hidden>↑</span>{" "}
            {(tokensOut ?? 0).toLocaleString()} <span aria-hidden>↓</span>
          </span>
        </>
      )}

      {mode && (
        <>
          <Sep />
          <span className="font-medium text-[var(--cnrs-blue)]">{mode}</span>
        </>
      )}

      {llm && (
        <>
          <Sep />
          <span
            className="font-mono text-[10px]"
            title={`LLM: ${llm}`}
          >
            {llm.split("/").slice(-1)[0]}
          </span>
        </>
      )}

      {running && onCancel && (
        <>
          <Sep />
          <button
            type="button"
            onClick={onCancel}
            className="rounded-full border border-[var(--cnrs-blue)] px-2.5 py-0.5 text-[10px] font-semibold text-[var(--cnrs-blue)] transition hover:bg-[var(--cnrs-blue)] hover:text-white"
            aria-label="Stop generation"
            title="Stop generation (Esc)"
          >
            ◼ Stop
          </button>
        </>
      )}
    </div>
  );
}

function Sep() {
  return (
    <span aria-hidden className="text-[var(--cnrs-grey)]">
      ·
    </span>
  );
}

function useLlmModelLabel(): string | null {
  const [llm, setLlm] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    health()
      .then((h) => {
        if (cancelled) return;
        const provider = h.llm?.default_provider;
        const model = h.llm?.default_model;
        if (model) setLlm(provider ? `${provider}/${model}` : model);
      })
      .catch(() => {
        /* non-critical */
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return llm;
}
