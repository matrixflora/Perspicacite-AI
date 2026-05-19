// User-tunable preferences. Persisted to localStorage so they survive
// reloads. Loaded by ChatPanel + reader pages.

import { DEFAULT_DATABASES, type DatabaseId } from "./databases";
import type { RAGMode } from "./modes";

export type RelevanceMethod = "bm25" | "rerank" | "llm";
export type ScreenMethod = "bm25" | "rerank" | "llm";

export type Preferences = {
  // Retrieval — wired to /api/chat ChatRequest fields.
  defaultMode: RAGMode;
  maxPapers: number;             // 1–10
  maxPapersToDownload: number;   // 1–50 (agentic)
  defaultDatabases: DatabaseId[];
  defaultKbName: string | null;

  // Relevance — surfaced via search_literature MCP today; kept here
  // so future chat-side wiring can read them centrally.
  relevanceMethod: RelevanceMethod;
  minRelevance: number;          // 0.0 – 1.0
  screenMethod: ScreenMethod;
  screenThreshold: number;       // 0.0 – 1.0

  // Display.
  theme: "light" | "dark" | "system";
  showThinkingByDefault: boolean;
  estimateTokensWhileTyping: boolean;
};

export const DEFAULT_PREFS: Preferences = {
  defaultMode: "basic",
  maxPapers: 5,
  maxPapersToDownload: 10,
  defaultDatabases: DEFAULT_DATABASES,
  defaultKbName: null,

  relevanceMethod: "rerank",
  minRelevance: 0.3,
  screenMethod: "rerank",
  screenThreshold: 0.3,

  theme: "system",
  showThinkingByDefault: true,
  estimateTokensWhileTyping: true,
};

const STORAGE_KEY = "perspicacite-prefs-v1";

export function loadPreferences(): Preferences {
  if (typeof window === "undefined") return DEFAULT_PREFS;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_PREFS;
    const parsed = JSON.parse(raw) as Partial<Preferences>;
    return { ...DEFAULT_PREFS, ...parsed };
  } catch {
    return DEFAULT_PREFS;
  }
}

export function savePreferences(prefs: Partial<Preferences>): Preferences {
  if (typeof window === "undefined") return DEFAULT_PREFS;
  const current = loadPreferences();
  const next: Preferences = { ...current, ...prefs };
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // localStorage may be unavailable (Safari private mode, etc).
  }
  return next;
}

export function resetPreferences(): Preferences {
  if (typeof window !== "undefined") {
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      // noop
    }
  }
  return DEFAULT_PREFS;
}
