// Centralized typed client for the Perspicacité HTTP API. Same-origin via
// the Next.js rewrite in next.config.ts; override with NEXT_PUBLIC_PERSPICACITE_URL.

const BASE = process.env.NEXT_PUBLIC_PERSPICACITE_URL ?? "";

async function get<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    cache: "no-store",
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`GET ${path} → HTTP ${res.status}`);
  return (await res.json()) as T;
}

async function post<T>(path: string, body: unknown, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    method: "POST",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST ${path} → HTTP ${res.status}`);
  return (await res.json()) as T;
}

async function del(path: string): Promise<void> {
  const res = await fetch(`${BASE}${path}`, { method: "DELETE", cache: "no-store" });
  if (!res.ok) throw new Error(`DELETE ${path} → HTTP ${res.status}`);
}

// ──────────── Knowledge bases ────────────────────────────────────────────

export type KBSummary = {
  name: string;
  description?: string | null;
  paper_count?: number;
  chunk_count?: number;
  created_at?: string | null;
  embedding_model?: string | null;
};

export type KBStats = {
  paper_count: number;
  chunk_count: number;
  embedding_model?: string;
  chunking_method?: string;
  total_size_bytes?: number;
  recent_papers?: { paper_id: string; title?: string; year?: number; added_at?: string }[];
};

export type KBPaper = {
  paper_id: string;
  title?: string;
  authors?: string[];
  year?: number;
  doi?: string;
  added_at?: string;
};

export type KBChunk = {
  paper_id: string;
  chunk_id?: string;
  chunk_index?: number;
  section?: string;
  text: string;
};

export const kb = {
  list: async (): Promise<KBSummary[]> => {
    const raw = await get<KBSummary[] | { kbs: KBSummary[] }>("/api/kb");
    return Array.isArray(raw) ? raw : raw.kbs;
  },
  create: (body: { name: string; description?: string; embedding_model?: string }) =>
    post<KBSummary>("/api/kb", body),
  get: (name: string) => get<KBSummary>(`/api/kb/${encodeURIComponent(name)}`),
  remove: (name: string) => del(`/api/kb/${encodeURIComponent(name)}`),
  stats: (name: string) => get<KBStats>(`/api/kb/${encodeURIComponent(name)}/stats`),
  chunks: (name: string, limit = 50) =>
    get<{ chunks: KBChunk[]; total?: number }>(
      `/api/kb/${encodeURIComponent(name)}/chunks?limit=${limit}`,
    ),
  papers: (name: string) =>
    get<{ papers: KBPaper[]; total?: number }>(
      `/api/kb/${encodeURIComponent(name)}/papers`,
    ),
  addDois: (name: string, dois: string[]) =>
    post<{ added_papers: number; skipped?: number }>(
      `/api/kb/${encodeURIComponent(name)}/dois`,
      { dois },
    ),
  addDoisAsync: (name: string, dois: string[]) =>
    post<{ job_id: string }>(
      `/api/kb/${encodeURIComponent(name)}/dois/async`,
      { dois },
    ),
  addBibtex: (name: string, bibtex: string) =>
    post<{ added_papers: number }>(
      `/api/kb/${encodeURIComponent(name)}/bibtex`,
      { bibtex },
    ),
  addBibtexAsync: (name: string, bibtex: string) =>
    post<{ job_id: string }>(
      `/api/kb/${encodeURIComponent(name)}/bibtex/async`,
      { bibtex },
    ),
  buildCapsules: (name: string) =>
    post<{ job_id: string }>(
      `/api/kb/${encodeURIComponent(name)}/build-capsules`,
      {},
    ),
  uploadLocalFiles: async (
    name: string,
    files: File[],
  ): Promise<{ added_papers?: number; job_id?: string; errors?: string[] }> => {
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    const res = await fetch(
      `${BASE}/api/kb/${encodeURIComponent(name)}/local-files`,
      { method: "POST", body: fd, cache: "no-store" },
    );
    if (!res.ok) throw new Error(`POST local-files → HTTP ${res.status}`);
    return res.json();
  },
  expandSimilarScore: (
    name: string,
    body: { direction: ExpandDirection; max_per_seed: number; method: ExpandMethod },
  ) =>
    post<{ job_id: string; sse_url: string }>(
      `/api/kb/${encodeURIComponent(name)}/expand-similar/score`,
      body,
    ),
  expandSimilarCutoff: (
    name: string,
    labels: { score: number; relevant: boolean }[],
  ) =>
    post<{ cutoff: number }>(
      `/api/kb/${encodeURIComponent(name)}/expand-similar/cutoff`,
      { labels },
    ),
  expandSimilarCommit: (
    name: string,
    scored: { doi: string | null; score: number }[],
    cutoff: number,
  ) =>
    post<{ job_id: string; sse_url: string }>(
      `/api/kb/${encodeURIComponent(name)}/expand-similar/commit`,
      { scored, cutoff },
    ),
  exportUrl: (name: string) => `${BASE}/api/kb/${encodeURIComponent(name)}/export`,
};

// ──────────── Conversations ───────────────────────────────────────────────

export type Conversation = {
  id: string;
  title?: string;
  kb_name?: string;
  session_id?: string;
  created_at?: string;
  updated_at?: string;
  message_count?: number;
};

export type ConvMessage = {
  id?: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at?: string;
};

export const conversations = {
  list: async (sessionId?: string): Promise<{ conversations: Conversation[] }> => {
    // Backend may return either a bare array or { conversations: [...] }.
    const raw = await get<Conversation[] | { conversations: Conversation[] }>(
      sessionId
        ? `/api/conversations?session_id=${encodeURIComponent(sessionId)}`
        : "/api/conversations",
    );
    return Array.isArray(raw) ? { conversations: raw } : raw;
  },
  get: (id: string) => get<Conversation & { messages: ConvMessage[] }>(`/api/conversations/${encodeURIComponent(id)}`),
  create: (body: { session_id?: string; kb_name?: string; title?: string }) =>
    post<Conversation>("/api/conversations", body),
  remove: (id: string) => del(`/api/conversations/${encodeURIComponent(id)}`),
  removeAll: () => del("/api/conversations"),
  addMessage: (id: string, msg: ConvMessage) =>
    post<{ status: string }>(`/api/conversations/${encodeURIComponent(id)}/messages`, msg),
  search: (q: string) =>
    get<{ results: Array<Conversation & { snippet?: string }> }>(
      `/api/conversations/search?q=${encodeURIComponent(q)}`,
    ),
  exportUrl: (id: string) => `${BASE}/api/conversations/${encodeURIComponent(id)}/export`,
  provenance: (id: string) => get(`/api/conversations/${encodeURIComponent(id)}/provenance`),
};

// ──────────── Literature survey ────────────────────────────────────────────

export type SurveyPaper = {
  id: string;
  title?: string;
  authors?: string[];
  year?: number;
  abstract?: string;
  doi?: string;
  citation_count?: number;
  relevance_score?: number;
  themes?: string[];
  recommended?: boolean;
  reason?: string;
};

export type SurveyTheme = {
  name: string;
  description?: string;
  paper_count?: number;
};

export type SurveySession = {
  session_id: string;
  query?: string;
  papers_count?: number;
  themes_count?: number;
  selected_count?: number;
  themes: SurveyTheme[];
  papers: SurveyPaper[];
  error?: string;
};

export const survey = {
  get: (id: string) => get<SurveySession>(`/api/survey/${encodeURIComponent(id)}`),
  select: (id: string, selected_paper_ids: string[]) =>
    post<{ success: boolean; selected_count: number }>(
      `/api/survey/${encodeURIComponent(id)}/select`,
      { session_id: id, selected_paper_ids },
    ),
  generate: (id: string) =>
    post<{ report?: string; error?: string }>(
      `/api/survey/${encodeURIComponent(id)}/generate`,
      {},
    ),
};

// ──────────── Papers ───────────────────────────────────────────────────────

export type PaperDetail = {
  doi?: string;
  paper_id?: string;
  title?: string;
  authors?: string[];
  year?: number;
  journal?: string | null;
  abstract?: string;
  full_text?: string;
  pdf_url?: string | null;
  oa_url?: string | null;
  citation_count?: number;
  references?: Array<{ doi?: string; title?: string; year?: number; authors?: string[] }>;
  capsule?: {
    figures?: Array<{ id: string; caption?: string; url?: string }>;
    supplementary?: Array<{ name: string; url?: string }>;
  };
  // When the paper was looked up from a KB, the server may attach
  // the KB-side chunks for the reader to display.
  chunks?: Array<{ section?: string; text: string; chunk_index?: number }>;
};

export const papers = {
  byDoi: (doi: string) =>
    get<PaperDetail>(`/api/paper?doi=${encodeURIComponent(doi)}`),
  capsuleFiguresUrl: (paperId: string) =>
    `${BASE}/api/capsule/${encodeURIComponent(paperId)}/figures`,
  capsuleFigureUrl: (paperId: string, figId: string) =>
    `${BASE}/api/capsule/${encodeURIComponent(paperId)}/figure/${encodeURIComponent(figId)}`,
};

// ──────────── Similarity expansion (grow a KB by similarity) ──────────────

export type ExpandDirection = "forward" | "backward" | "both";
export type ExpandMethod = "hybrid" | "embedding" | "bm25";

export type ExpandCandidate = {
  doi: string | null;
  title?: string | null;
  score: number;
  reason?: string | null;
};

export type ExpandHistBucket = { lo: number; hi: number; count: number };

export type ExpandSample = {
  doi: string | null;
  title?: string | null;
  abstract?: string | null;
  score: number;
};

export type ExpandScoreReport = {
  candidates: ExpandCandidate[];
  histogram: ExpandHistBucket[];
  samples: ExpandSample[];
  seed_count: number;
  method: string;
};

export type ExpandCommitResult = {
  added_papers?: number;
  added_chunks?: number;
  failed?: Array<{ doi?: string; reason?: string }>;
  kept: number;
};

// ──────────── Health ──────────────────────────────────────────────────────

export type Health = {
  status: string;
  initialized?: boolean;
  llm?: { default_provider?: string; default_model?: string };
};

export const health = () => get<Health>("/api/health");

// ──────────── Jobs (async ingest / scoring / commit) ─────────────────────

// Mirrors the `jobs` SQLite row returned by GET /api/jobs/{id}. `result` is
// JSON-parsed server-side; `status` is running | done | error (not the
// pending/succeeded/failed names the previous type wrongly guessed).
export type Job = {
  id: string;
  kind?: string;
  status: "queued" | "running" | "done" | "error";
  total?: number;
  done_count?: number;
  result?: unknown;
  error?: string | null;
  created_at?: string;
  updated_at?: string;
};

export const jobs = {
  get: (id: string) => get<Job>(`/api/jobs/${encodeURIComponent(id)}`),
  // Poll a job to its terminal state and resolve with the parsed `result`
  // (throws on failure). The score/commit jobs emit no incremental progress,
  // so polling reaches the same result as tailing the SSE stream — and avoids
  // the Next.js dev-rewrite SSE buffering that the chat route works around.
  waitFor: async (
    id: string,
    opts: { intervalMs?: number; signal?: AbortSignal } = {},
  ): Promise<unknown> => {
    const { intervalMs = 1200, signal } = opts;
    for (;;) {
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      const j = await jobs.get(id);
      if (j.status === "done") return j.result;
      if (j.status === "error") throw new Error(j.error || "Job failed");
      await new Promise((r) => setTimeout(r, intervalMs));
    }
  },
};
