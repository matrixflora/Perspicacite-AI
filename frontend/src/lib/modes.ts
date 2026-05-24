// The six RAG modes supported by the Perspicacité chat router.
// Wording is GUI-only; values must match the backend `ChatRequest.mode` enum.

export type RAGMode =
  | "basic"
  | "advanced"
  | "deep_research"
  | "agentic"
  | "literature_survey"
  | "contradiction";

// A single expected phase in a mode's pipeline. Surfaced in the
// Agentic trail as the *planned sequence* — Claude-Code-style — so
// the user knows what's about to happen, then watches each phase
// tick from "planned" → "running" → "done" as backend events arrive.
// Glyph keys map 1:1 to PhaseGlyph component. Reusing the same key
// across modes keeps "retrieve" visually consistent everywhere.
export type ModePhaseGlyph =
  | "retrieve"
  | "rewrite"
  | "screen"
  | "synthesize"
  | "reason"
  | "critique"
  | "revise"
  | "plan"
  | "tools"
  | "themes"
  | "select"
  | "deepen"
  | "group"
  | "contrast";

export type ModePhase = {
  id: string;
  label: string;
  // Lightweight match: when a backend frame's label / kind contains
  // any of these substrings (lowercased), we consider this phase active.
  // Order in `phases` matters — first match wins.
  match: string[];
  // Approximate time budget shown next to the phase while it runs.
  hint?: string;
  // Symbol shown next to the phase status dot. See PhaseGlyph. Optional
  // — when omitted, the row falls back to the bare status dot.
  glyph?: ModePhaseGlyph;
};

export type ModeDescriptor = {
  id: RAGMode;
  label: string;
  blurb: string;
  // One-line user-facing explanation shown beneath the mode pill while
  // a query runs — meant to set expectations on latency and behaviour.
  helper: string;
  // CNRS palette accent — picked from the four official aplats.
  accent: "yellow" | "blue-pale" | "green" | "orange" | "violet" | "grey";
  // Approximate latency budget shown to the user before they pick.
  latency: string;
  // The planned sequence shown in the agentic trail.
  phases: ModePhase[];
};

export const MODES: ModeDescriptor[] = [
  {
    id: "basic",
    label: "Basic",
    blurb: "Single-pass retrieval over your KB, fast answer.",
    helper:
      "One retrieval pass + one synthesis. Fastest mode — best for quick lookups; expect ~10–30 s.",
    accent: "blue-pale",
    latency: "~10s",
    phases: [
      { id: "retrieve", label: "Retrieve documents", glyph: "retrieve", match: ["retriev", "querying database", "no kb results", "web fallback", "search returned"] },
      { id: "screen", label: "Screen for relevance", glyph: "screen", match: ["screen", "relevance", "rerank"] },
      { id: "synthesize", label: "Generate answer", glyph: "synthesize", match: ["generat", "synthes"] },
    ],
  },
  {
    id: "advanced",
    label: "Advanced",
    blurb: "Multi-step retrieval with web fallback when the KB is thin.",
    helper:
      "Rewrites the query, hits your KB, falls back to web search if results are thin. Takes ~30–60 s.",
    accent: "yellow",
    latency: "~30s",
    phases: [
      { id: "rewrite", label: "Rewrite query", glyph: "rewrite", match: ["rewrite", "rephras", "refined"] },
      { id: "retrieve_kb", label: "Search KB", glyph: "retrieve", match: ["kb", "knowledge base"] },
      { id: "retrieve_web", label: "Search web databases", glyph: "retrieve", match: ["querying database", "web fallback", "search returned", "provider"] },
      { id: "screen", label: "Screen for relevance", glyph: "screen", match: ["screen", "relevance", "rerank"] },
      { id: "synthesize", label: "Generate answer", glyph: "synthesize", match: ["generat", "synthes"] },
    ],
  },
  {
    id: "deep_research",
    label: "Deep Research",
    blurb: "Deep reasoning over multiple passes — slower, more thorough.",
    helper:
      "Multi-pass deep reasoning with critique + revision loops. For hard questions — expect ~1–3 min.",
    accent: "violet",
    latency: "~90s",
    phases: [
      { id: "rewrite", label: "Rewrite query", glyph: "rewrite", match: ["rewrite", "rephras", "refined"] },
      { id: "retrieve", label: "Retrieve broadly", glyph: "retrieve", match: ["retriev", "querying database", "search"] },
      { id: "reason", label: "Reason over evidence", glyph: "reason", match: ["reason", "analyz", "evaluat"] },
      { id: "synthesize", label: "Draft answer", glyph: "synthesize", match: ["draft", "generat", "synthes"] },
      { id: "critique", label: "Critique draft", glyph: "critique", match: ["critique", "self-critique", "verify"] },
      { id: "revise", label: "Revise final", glyph: "revise", match: ["revis", "final"] },
    ],
  },
  {
    id: "agentic",
    label: "Agentic",
    blurb: "Tool-using agent — explores citation graph, searches the web.",
    helper:
      "Tool-using agent: plans, then calls search / fetch / cite tools dynamically. Plan adapts as it learns — can take 1–2 min.",
    accent: "orange",
    latency: "~60s",
    phases: [
      { id: "plan", label: "Plan approach", glyph: "plan", match: ["plan", "intent", "phase"] },
      { id: "tools", label: "Call tools (search · fetch · cite)", glyph: "tools", match: ["tool", "search", "fetch", "querying"] },
      { id: "synthesize", label: "Synthesize answer", glyph: "synthesize", match: ["generat", "synthes", "final"] },
    ],
  },
  {
    id: "literature_survey",
    label: "Literature survey",
    blurb: "Multi-paper survey with theme extraction and select-then-deepen.",
    helper:
      "Broad survey: collects many papers, extracts themes, lets you pick, then deepens on the selection. Takes 2–3 min before the picker appears.",
    accent: "green",
    latency: "~2 min",
    phases: [
      { id: "collect", label: "Collect papers", glyph: "retrieve", match: ["collect", "retriev", "search"] },
      { id: "themes", label: "Extract themes", glyph: "themes", match: ["theme", "cluster", "topic"] },
      { id: "select", label: "Await selection", glyph: "select", match: ["select", "await"] },
      { id: "deepen", label: "Deepen on selection", glyph: "deepen", match: ["deepen", "synthes", "generat"] },
    ],
  },
  {
    id: "contradiction",
    label: "Contradiction",
    blurb: "Surface disagreements across papers on a focused claim.",
    helper:
      "Searches for papers on a claim, groups them by stance, then contrasts them. Best with a sharply phrased claim. ~45 s.",
    accent: "grey",
    latency: "~45s",
    phases: [
      { id: "search", label: "Search for claim coverage", glyph: "retrieve", match: ["search", "retriev", "querying"] },
      { id: "group", label: "Group by stance", glyph: "group", match: ["group", "stance", "classify"] },
      { id: "contrast", label: "Contrast positions", glyph: "contrast", match: ["contrast", "compar"] },
      { id: "synthesize", label: "Synthesize summary", glyph: "synthesize", match: ["synthes", "generat", "final"] },
    ],
  },
];

export function accentClasses(accent: ModeDescriptor["accent"]): {
  bg: string;
  border: string;
  ring: string;
  text: string;
} {
  // Tailwind classes — referencing the @theme tokens we set up in globals.css.
  switch (accent) {
    case "yellow":
      return {
        bg: "bg-[var(--cnrs-yellow)]",
        border: "border-[var(--cnrs-yellow)]",
        ring: "ring-[var(--cnrs-yellow)]",
        text: "text-[var(--cnrs-blue)]",
      };
    case "blue-pale":
      return {
        bg: "bg-[var(--cnrs-blue-pale)]",
        border: "border-[var(--cnrs-blue-pale)]",
        ring: "ring-[var(--cnrs-blue-pale)]",
        text: "text-[var(--cnrs-blue)]",
      };
    case "green":
      return {
        bg: "bg-[var(--cnrs-green)]",
        border: "border-[var(--cnrs-green)]",
        ring: "ring-[var(--cnrs-green)]",
        text: "text-[var(--cnrs-blue)]",
      };
    case "orange":
      return {
        bg: "bg-[var(--cnrs-orange)]",
        border: "border-[var(--cnrs-orange)]",
        ring: "ring-[var(--cnrs-orange)]",
        text: "text-[var(--cnrs-blue)]",
      };
    case "violet":
      return {
        bg: "bg-[var(--cnrs-violet)]",
        border: "border-[var(--cnrs-violet)]",
        ring: "ring-[var(--cnrs-violet)]",
        text: "text-white",
      };
    case "grey":
      return {
        bg: "bg-[var(--cnrs-grey-light)]",
        border: "border-[var(--cnrs-grey)]",
        ring: "ring-[var(--cnrs-grey)]",
        text: "text-[var(--cnrs-blue)]",
      };
  }
}
