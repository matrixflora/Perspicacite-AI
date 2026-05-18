// Streaming chat client for POST /api/chat (stream=true).
//
// The Perspicacité backend emits an SSE-style line stream:
//   event: token   data: {"token":"..."}
//   event: meta    data: {"papers_found": 3, "sources":[...]}
//   event: done    data: {"answer":"...","conversation_id":"..."}
// (The exact event names vary by mode; we treat any line with `data:` as
// the JSON payload and stitch tokens together.)

import type { RAGMode } from "./modes";
import type { DatabaseId } from "./databases";

export type ChatSource = {
  paper_id?: string;
  title?: string;
  authors?: string[];
  year?: number;
  doi?: string;
  url?: string;
  relevance_score?: number;
};

export type ChatStreamEvent =
  | { kind: "token"; text: string }
  | { kind: "meta"; papers_found?: number; sources?: ChatSource[] }
  | { kind: "done"; conversation_id?: string; answer?: string }
  | { kind: "error"; message: string };

// Empty string → same-origin requests, proxied by Next.js rewrites in
// next.config.ts. Override via NEXT_PUBLIC_PERSPICACITE_URL for direct
// cross-origin calls (requires backend CORS).
const BACKEND = process.env.NEXT_PUBLIC_PERSPICACITE_URL ?? "";

export async function* streamChat(opts: {
  query: string;
  mode: RAGMode;
  kbName?: string;
  conversationId?: string;
  maxPapers?: number;
  databases?: DatabaseId[];
  signal?: AbortSignal;
}): AsyncGenerator<ChatStreamEvent> {
  const body = {
    query: opts.query,
    mode: opts.mode,
    stream: true,
    max_papers: opts.maxPapers ?? 5,
    kb_name: opts.kbName,
    conversation_id: opts.conversationId,
    databases: opts.databases,
  };

  const res = await fetch(`${BACKEND}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal: opts.signal,
  });

  if (!res.ok || !res.body) {
    yield { kind: "error", message: `HTTP ${res.status}` };
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by blank lines.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      const ev = parseFrame(frame);
      if (ev) yield ev;
    }
  }

  // Any trailing partial frame after stream end.
  if (buffer.trim()) {
    const ev = parseFrame(buffer);
    if (ev) yield ev;
  }
}

function parseFrame(frame: string): ChatStreamEvent | null {
  // Tolerant parser: pull the `data:` payload regardless of `event:` name.
  const dataLine = frame
    .split("\n")
    .find((l) => l.trim().toLowerCase().startsWith("data:"));
  if (!dataLine) return null;
  const json = dataLine.replace(/^data:\s*/i, "").trim();
  if (!json || json === "[DONE]") return { kind: "done" };

  try {
    const obj = JSON.parse(json);
    if (typeof obj.token === "string") return { kind: "token", text: obj.token };
    if (typeof obj.chunk === "string") return { kind: "token", text: obj.chunk };
    if (typeof obj.delta === "string") return { kind: "token", text: obj.delta };
    if (typeof obj.answer === "string" && obj.sources)
      return {
        kind: "done",
        answer: obj.answer,
        conversation_id: obj.conversation_id,
      };
    if (obj.sources || typeof obj.papers_found === "number")
      return { kind: "meta", papers_found: obj.papers_found, sources: obj.sources };
    if (obj.event === "done" || obj.done === true)
      return { kind: "done", conversation_id: obj.conversation_id };
    return null;
  } catch {
    // Plain-text payloads (some modes stream raw text in `data:` lines).
    return { kind: "token", text: json };
  }
}

export async function cancelChat(conversationId: string): Promise<void> {
  try {
    await fetch(`${BACKEND}/api/chat/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: conversationId }),
    });
  } catch {
    // Best-effort.
  }
}
