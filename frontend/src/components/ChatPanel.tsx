"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { DatabasePicker } from "./DatabasePicker";
import { SourcePill } from "./SourcePill";
import { streamChat, cancelChat, type ChatSource } from "@/lib/chat";
import type { RAGMode } from "@/lib/modes";
import { MODES, accentClasses } from "@/lib/modes";
import { DEFAULT_DATABASES, type DatabaseId } from "@/lib/databases";
import { conversations as convApi, type ConvMessage } from "@/lib/api";

type Turn = {
  id: string;
  role: "user" | "assistant";
  mode?: RAGMode;
  text: string;
  sources?: ChatSource[];
  streaming?: boolean;
  startedAt?: number;
  elapsedMs?: number;
  papersFound?: number;
  error?: string;
};

function nid(): string {
  return Math.random().toString(36).slice(2, 10);
}

const EXAMPLE_PROMPTS = [
  "What are critique tokens in Self-RAG?",
  "Compare retrieval-augmented generation with corrective RAG.",
  "Summarise the state-of-the-art on multi-modal scientific search.",
  "Which mass-spectrometry foundation models exist in 2026?",
];

export function ChatPanel({
  initialConversationId,
}: {
  initialConversationId?: string;
}) {
  const router = useRouter();
  const [mode, setMode] = useState<RAGMode>("basic");
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [databases, setDatabases] = useState<DatabaseId[]>(DEFAULT_DATABASES);
  const [showDbPicker, setShowDbPicker] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const conversationIdRef = useRef<string | undefined>(initialConversationId);
  const abortRef = useRef<AbortController | null>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);

  const currentMode = useMemo(() => MODES.find((m) => m.id === mode)!, [mode]);
  const accent = accentClasses(currentMode.accent);

  // Resume an existing conversation when /chat/[id] is opened.
  useEffect(() => {
    conversationIdRef.current = initialConversationId;
    if (!initialConversationId) {
      setTurns([]);
      return;
    }
    let cancelled = false;
    setLoadingHistory(true);
    convApi
      .get(initialConversationId)
      .then((conv) => {
        if (cancelled) return;
        const restored: Turn[] = (conv.messages ?? []).map((m: ConvMessage) => ({
          id: nid(),
          role: m.role === "user" ? "user" : "assistant",
          text: m.content,
        }));
        setTurns(restored);
      })
      .catch(() => {
        if (!cancelled) setTurns([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingHistory(false);
      });
    return () => {
      cancelled = true;
    };
  }, [initialConversationId]);

  // Auto-scroll on new content.
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [turns]);

  // Tick the elapsed counter on the streaming turn.
  useEffect(() => {
    if (!streaming) return;
    const interval = setInterval(() => {
      setTurns((ts) =>
        ts.map((t) =>
          t.streaming && t.startedAt
            ? { ...t, elapsedMs: Date.now() - t.startedAt }
            : t,
        ),
      );
    }, 250);
    return () => clearInterval(interval);
  }, [streaming]);

  const submit = useCallback(
    async (queryArg?: string) => {
      const q = (queryArg ?? draft).trim();
      if (!q || streaming) return;

      const userTurn: Turn = { id: nid(), role: "user", text: q, mode };
      const asstTurn: Turn = {
        id: nid(),
        role: "assistant",
        mode,
        text: "",
        streaming: true,
        startedAt: Date.now(),
        elapsedMs: 0,
      };
      setTurns((t) => [...t, userTurn, asstTurn]);
      setDraft("");
      setStreaming(true);

      const ctrl = new AbortController();
      abortRef.current = ctrl;

      try {
        for await (const ev of streamChat({
          query: q,
          mode,
          conversationId: conversationIdRef.current,
          databases,
          signal: ctrl.signal,
        })) {
          if (ev.kind === "token") {
            setTurns((ts) =>
              ts.map((t) =>
                t.id === asstTurn.id ? { ...t, text: t.text + ev.text } : t,
              ),
            );
          } else if (ev.kind === "meta") {
            setTurns((ts) =>
              ts.map((t) =>
                t.id === asstTurn.id
                  ? {
                      ...t,
                      sources: ev.sources ?? t.sources,
                      papersFound: ev.papers_found ?? t.papersFound,
                    }
                  : t,
              ),
            );
          } else if (ev.kind === "done") {
            if (ev.conversation_id) {
              const newId = ev.conversation_id;
              conversationIdRef.current = newId;
              // Reflect the new conversation ID in the URL so refresh works.
              if (!initialConversationId) {
                router.replace(`/chat/${encodeURIComponent(newId)}`);
              }
            }
            setTurns((ts) =>
              ts.map((t) =>
                t.id === asstTurn.id
                  ? {
                      ...t,
                      streaming: false,
                      text: ev.answer && !t.text ? ev.answer : t.text,
                    }
                  : t,
              ),
            );
          } else if (ev.kind === "error") {
            setTurns((ts) =>
              ts.map((t) =>
                t.id === asstTurn.id
                  ? { ...t, streaming: false, error: ev.message }
                  : t,
              ),
            );
          }
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : "stream interrupted";
        setTurns((ts) =>
          ts.map((t) =>
            t.id === asstTurn.id ? { ...t, streaming: false, error: msg } : t,
          ),
        );
      } finally {
        setStreaming(false);
        abortRef.current = null;
        setTurns((ts) =>
          ts.map((t) =>
            t.id === asstTurn.id ? { ...t, streaming: false } : t,
          ),
        );
      }
    },
    [draft, mode, streaming, databases, initialConversationId, router],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    if (conversationIdRef.current) cancelChat(conversationIdRef.current);
  }, []);

  const hasMessages = turns.length > 0;
  const lastAssistant = [...turns].reverse().find((t) => t.role === "assistant");

  return (
    <section className="relative mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 md:px-6">
      {/* Transcript or hero empty-state */}
      <div ref={scrollerRef} className="flex-1 overflow-y-auto py-6">
        {loadingHistory ? (
          <p className="py-12 text-center text-sm text-[var(--text-muted)]">
            Loading conversation…
          </p>
        ) : !hasMessages ? (
          <HeroEmptyState
            mode={mode}
            onPick={(prompt) => {
              setDraft(prompt);
            }}
          />
        ) : (
          <ol className="flex flex-col gap-6">
            {turns.map((t) =>
              t.role === "user" ? (
                <UserMessage key={t.id} turn={t} />
              ) : (
                <AssistantMessage key={t.id} turn={t} streaming={streaming} />
              ),
            )}
          </ol>
        )}
      </div>

      {/* Streaming status bar */}
      {streaming && lastAssistant && (
        <StatusBar turn={lastAssistant} />
      )}

      {/* Compose */}
      <div className="sticky bottom-0 z-10 -mx-4 border-t border-[var(--border)] bg-[var(--bg)]/90 px-4 pb-4 pt-3 backdrop-blur md:-mx-6 md:px-6">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
          className="relative rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-card)] focus-within:border-[var(--cnrs-blue)]"
        >
          <textarea
            rows={2}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder="Ask the literature…  (Enter to send · Shift+Enter for newline)"
            className="w-full resize-none bg-transparent px-4 pt-3 pb-2 text-[15px] leading-relaxed outline-none placeholder:text-[var(--text-muted)]"
            disabled={streaming}
            autoFocus
          />
          <div className="flex flex-wrap items-center gap-1.5 border-t border-[var(--border)] px-3 py-2">
            <CompactModePicker
              value={mode}
              onChange={setMode}
              disabled={streaming}
            />
            <button
              type="button"
              onClick={() => setShowDbPicker((s) => !s)}
              className="ml-1 inline-flex items-center gap-1 rounded-full border border-[var(--border)] px-2.5 py-1 text-[11px] font-medium text-[var(--text-muted)] hover:border-[var(--cnrs-blue)] hover:text-[var(--cnrs-blue)]"
              aria-expanded={showDbPicker}
              disabled={streaming}
            >
              <span aria-hidden>{showDbPicker ? "▾" : "🌐"}</span>
              <span>{databases.length}/12 DBs</span>
            </button>
            <div className="ml-auto">
              {streaming ? (
                <button
                  type="button"
                  onClick={cancel}
                  className="rounded-[var(--radius-md)] border border-[var(--cnrs-blue)] px-3 py-1.5 text-xs font-medium text-[var(--cnrs-blue)] transition hover:bg-[var(--cnrs-blue)] hover:text-white"
                >
                  Cancel
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!draft.trim()}
                  className={[
                    "inline-flex items-center gap-1 rounded-[var(--radius-md)] px-4 py-1.5 text-xs font-semibold transition",
                    accent.bg,
                    accent.text,
                    "disabled:cursor-not-allowed disabled:opacity-50",
                  ].join(" ")}
                >
                  Ask <span aria-hidden>→</span>
                </button>
              )}
            </div>
          </div>
          {showDbPicker && (
            <div className="border-t border-[var(--border)] p-3">
              <DatabasePicker
                value={databases}
                onChange={setDatabases}
                disabled={streaming}
                compact
              />
            </div>
          )}
        </form>
        <p className="mt-2 text-center text-[10px] text-[var(--text-muted)]">
          Perspicacité may make mistakes. Verify against the linked sources.
        </p>
      </div>
    </section>
  );
}

function CompactModePicker({
  value,
  onChange,
  disabled,
}: {
  value: RAGMode;
  onChange: (m: RAGMode) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Mode">
      {MODES.map((m) => {
        const selected = value === m.id;
        const accent = accentClasses(m.accent);
        return (
          <button
            key={m.id}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={disabled}
            onClick={() => onChange(m.id)}
            title={`${m.label} · ${m.blurb} · ~${m.latency}`}
            className={[
              "rounded-full px-2.5 py-1 text-[11px] font-medium transition",
              selected
                ? `${accent.bg} ${accent.text} shadow-sm`
                : "border border-[var(--border)] bg-transparent text-[var(--text-muted)] hover:border-[var(--cnrs-blue)] hover:text-[var(--cnrs-blue)]",
              disabled && "cursor-not-allowed opacity-60",
            ].join(" ")}
          >
            {m.label}
          </button>
        );
      })}
    </div>
  );
}

function HeroEmptyState({
  mode,
  onPick,
}: {
  mode: RAGMode;
  onPick: (prompt: string) => void;
}) {
  const m = MODES.find((x) => x.id === mode)!;
  return (
    <div className="flex flex-col items-center justify-center gap-6 pt-12 pb-6 text-center">
      <span
        className="grid h-14 w-14 place-items-center rounded-full"
        style={{ background: "var(--cnrs-yellow)" }}
        aria-hidden
      />
      <div>
        <h1 className="text-3xl font-semibold tracking-tight text-[var(--cnrs-blue)] md:text-4xl">
          Ask the literature.
        </h1>
        <p className="mt-2 text-sm text-[var(--text-muted)]">
          {m.label} mode · {m.blurb}
        </p>
      </div>
      <div className="mt-2 grid w-full max-w-2xl grid-cols-1 gap-2 sm:grid-cols-2">
        {EXAMPLE_PROMPTS.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => onPick(p)}
            className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] px-3 py-2.5 text-left text-sm text-[var(--text-body)] transition hover:border-[var(--cnrs-blue)] hover:shadow-[var(--shadow-card)]"
          >
            {p}
          </button>
        ))}
      </div>
    </div>
  );
}

function UserMessage({ turn }: { turn: Turn }) {
  return (
    <li className="flex justify-end">
      <div className="max-w-[85%] rounded-[var(--radius-lg)] bg-[var(--cnrs-blue)] px-4 py-2.5 text-[15px] leading-relaxed text-white">
        {turn.text}
      </div>
    </li>
  );
}

function AssistantMessage({
  turn,
  streaming,
}: {
  turn: Turn;
  streaming: boolean;
}) {
  const m = turn.mode ? MODES.find((x) => x.id === turn.mode) : undefined;
  const accent = m ? accentClasses(m.accent) : undefined;

  return (
    <li className="flex flex-col gap-3">
      {/* Mode tag */}
      <div className="flex items-center gap-2">
        <span
          className={[
            "rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider",
            accent?.bg ?? "bg-[var(--cnrs-grey-light)]",
            accent?.text ?? "text-[var(--cnrs-blue)]",
          ].join(" ")}
        >
          {m?.label ?? "assistant"}
        </span>
      </div>

      {/* Sources first (Perplexity pattern) */}
      {turn.sources && turn.sources.length > 0 && (
        <div>
          <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
            Sources · {turn.sources.length}
          </p>
          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            {turn.sources.map((s, i) => (
              <SourcePill key={i} source={s} index={i} />
            ))}
          </div>
        </div>
      )}

      {/* Answer */}
      <div className="text-[15px] leading-relaxed text-[var(--text-body)]">
        {turn.error ? (
          <p className="rounded-[var(--radius-md)] border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            ⚠ {turn.error}
          </p>
        ) : turn.text ? (
          <p className="whitespace-pre-wrap">{turn.text}</p>
        ) : turn.streaming && streaming ? (
          <span className="inline-flex items-center gap-1 text-sm text-[var(--text-muted)]">
            <span className="pulse-dot">●</span>
            <span className="pulse-dot" style={{ animationDelay: "0.15s" }}>
              ●
            </span>
            <span className="pulse-dot" style={{ animationDelay: "0.3s" }}>
              ●
            </span>
            <span className="ml-1">Retrieving…</span>
          </span>
        ) : (
          <p className="text-sm text-[var(--text-muted)]">(no answer)</p>
        )}
      </div>
    </li>
  );
}

function StatusBar({ turn }: { turn: Turn }) {
  const elapsed = ((turn.elapsedMs ?? 0) / 1000).toFixed(1);
  return (
    <div
      role="status"
      aria-live="polite"
      className="sticky bottom-[120px] z-10 mx-auto mb-2 flex items-center gap-3 self-center rounded-full border border-[var(--border)] bg-[var(--surface)] px-3 py-1.5 text-xs text-[var(--text-muted)] shadow-[var(--shadow-card)]"
    >
      <span className="flex items-center gap-1.5">
        <span className="pulse-dot text-[var(--cnrs-blue)]">●</span>
        <span>Working…</span>
      </span>
      <span aria-hidden>·</span>
      <span className="font-mono tabular-nums">{elapsed}s</span>
      {typeof turn.papersFound === "number" && turn.papersFound > 0 && (
        <>
          <span aria-hidden>·</span>
          <span>{turn.papersFound} papers</span>
        </>
      )}
      {turn.mode && (
        <>
          <span aria-hidden>·</span>
          <span className="font-medium text-[var(--cnrs-blue)]">{turn.mode}</span>
        </>
      )}
    </div>
  );
}
