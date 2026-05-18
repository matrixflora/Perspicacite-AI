"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ModeSwitcher } from "./ModeSwitcher";
import { SourcePill } from "./SourcePill";
import { streamChat, cancelChat, type ChatSource } from "@/lib/chat";
import type { RAGMode } from "@/lib/modes";
import { MODES, accentClasses } from "@/lib/modes";

type Turn = {
  id: string;
  role: "user" | "assistant";
  mode?: RAGMode;
  text: string;
  sources?: ChatSource[];
  streaming?: boolean;
  error?: string;
};

function nid(): string {
  return Math.random().toString(36).slice(2, 10);
}

export function ChatPanel() {
  const [mode, setMode] = useState<RAGMode>("basic");
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [streaming, setStreaming] = useState(false);
  const conversationIdRef = useRef<string | undefined>(undefined);
  const abortRef = useRef<AbortController | null>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);

  const currentMode = useMemo(() => MODES.find((m) => m.id === mode)!, [mode]);
  const accent = accentClasses(currentMode.accent);

  // Auto-scroll on new content.
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [turns]);

  const submit = useCallback(async () => {
    const q = draft.trim();
    if (!q || streaming) return;

    const userTurn: Turn = { id: nid(), role: "user", text: q, mode };
    const asstTurn: Turn = {
      id: nid(),
      role: "assistant",
      mode,
      text: "",
      streaming: true,
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
                ? { ...t, sources: ev.sources ?? t.sources }
                : t,
            ),
          );
        } else if (ev.kind === "done") {
          if (ev.conversation_id) conversationIdRef.current = ev.conversation_id;
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
          t.id === asstTurn.id
            ? { ...t, streaming: false, error: msg }
            : t,
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
  }, [draft, mode, streaming]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    if (conversationIdRef.current) cancelChat(conversationIdRef.current);
  }, []);

  return (
    <section
      id="chat"
      className="relative mx-auto flex w-full max-w-5xl flex-1 flex-col gap-4 px-4 py-6 md:px-6"
    >
      <ModeSwitcher value={mode} onChange={setMode} disabled={streaming} />

      <div
        ref={scrollerRef}
        className="min-h-[40vh] flex-1 overflow-y-auto rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-soft)] p-4 shadow-[var(--shadow-card)] md:p-6"
      >
        {turns.length === 0 ? (
          <EmptyState mode={mode} />
        ) : (
          <ol className="flex flex-col gap-5">
            {turns.map((t) =>
              t.role === "user" ? (
                <UserBubble key={t.id} turn={t} />
              ) : (
                <AssistantBubble key={t.id} turn={t} />
              ),
            )}
          </ol>
        )}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        className="relative flex items-end gap-2 rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-2 shadow-[var(--shadow-card)] focus-within:border-[var(--cnrs-blue)]"
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
          placeholder={`Ask anything — ${currentMode.label.toLowerCase()} mode`}
          className="flex-1 resize-none bg-transparent px-3 py-2 text-[15px] leading-relaxed outline-none placeholder:text-[var(--text-muted)]"
          disabled={streaming}
        />
        {streaming ? (
          <button
            type="button"
            onClick={cancel}
            className="rounded-[var(--radius-md)] border border-[var(--cnrs-blue)] px-4 py-2.5 text-sm font-medium text-[var(--cnrs-blue)] transition hover:bg-[var(--cnrs-blue)] hover:text-white"
          >
            Cancel
          </button>
        ) : (
          <button
            type="submit"
            disabled={!draft.trim()}
            className={[
              "rounded-[var(--radius-md)] px-5 py-2.5 text-sm font-semibold transition",
              accent.bg,
              accent.text,
              "disabled:cursor-not-allowed disabled:opacity-50",
            ].join(" ")}
          >
            Ask →
          </button>
        )}
      </form>
    </section>
  );
}

function EmptyState({ mode }: { mode: RAGMode }) {
  const m = MODES.find((x) => x.id === mode)!;
  return (
    <div className="flex h-full min-h-[36vh] flex-col items-center justify-center gap-3 text-center">
      <span
        className="grid h-14 w-14 place-items-center rounded-full"
        style={{ background: "var(--cnrs-yellow)" }}
        aria-hidden
      />
      <h2 className="text-xl font-semibold text-[var(--cnrs-blue)]">
        Ready in <em className="not-italic">{m.label}</em> mode
      </h2>
      <p className="max-w-md text-sm text-[var(--text-muted)]">{m.blurb}</p>
    </div>
  );
}

function UserBubble({ turn }: { turn: Turn }) {
  return (
    <li className="flex justify-end">
      <div className="max-w-[80%] rounded-[var(--radius-lg)] rounded-tr-sm bg-[var(--cnrs-blue)] px-4 py-2.5 text-sm leading-relaxed text-white shadow-[var(--shadow-card)]">
        {turn.text}
      </div>
    </li>
  );
}

function AssistantBubble({ turn }: { turn: Turn }) {
  const m = turn.mode ? MODES.find((x) => x.id === turn.mode) : undefined;
  const accent = m ? accentClasses(m.accent) : undefined;
  return (
    <li className="flex flex-col gap-2">
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
        {turn.streaming && (
          <span className="flex items-center gap-1 text-xs text-[var(--text-muted)]">
            <span className="pulse-dot">●</span>
            <span className="pulse-dot" style={{ animationDelay: "0.15s" }}>
              ●
            </span>
            <span className="pulse-dot" style={{ animationDelay: "0.3s" }}>
              ●
            </span>
            <span className="ml-1">thinking</span>
          </span>
        )}
      </div>

      <div className="max-w-[88%] rounded-[var(--radius-lg)] rounded-tl-sm border border-[var(--border)] bg-[var(--surface)] px-4 py-3 text-[15px] leading-relaxed text-[var(--text-body)] shadow-[var(--shadow-card)]">
        {turn.error ? (
          <p className="text-sm text-red-700">⚠ {turn.error}</p>
        ) : turn.text ? (
          <p className="whitespace-pre-wrap">{turn.text}</p>
        ) : (
          <p className="text-sm text-[var(--text-muted)]">
            Retrieving and synthesizing…
          </p>
        )}
      </div>

      {turn.sources && turn.sources.length > 0 && (
        <div className="max-w-[88%]">
          <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
            Sources
          </p>
          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            {turn.sources.map((s, i) => (
              <SourcePill key={i} source={s} index={i} />
            ))}
          </div>
        </div>
      )}
    </li>
  );
}
