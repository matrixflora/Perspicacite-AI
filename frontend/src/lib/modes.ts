// The six RAG modes supported by the Perspicacité chat router.
// Wording is GUI-only; values must match the backend `ChatRequest.mode` enum.

export type RAGMode =
  | "basic"
  | "advanced"
  | "profound"
  | "agentic"
  | "literature_survey"
  | "contradiction";

export type ModeDescriptor = {
  id: RAGMode;
  label: string;
  blurb: string;
  // CNRS palette accent — picked from the four official aplats.
  accent: "yellow" | "blue-pale" | "green" | "orange" | "violet" | "grey";
  // Approximate latency budget shown to the user before they pick.
  latency: string;
};

export const MODES: ModeDescriptor[] = [
  {
    id: "basic",
    label: "Basic",
    blurb: "Single-pass retrieval over your KB, fast answer.",
    accent: "blue-pale",
    latency: "~10s",
  },
  {
    id: "advanced",
    label: "Advanced",
    blurb: "Multi-step retrieval with web fallback when the KB is thin.",
    accent: "yellow",
    latency: "~30s",
  },
  {
    id: "profound",
    label: "Profound",
    blurb: "Deep reasoning over multiple passes — slower, more thorough.",
    accent: "violet",
    latency: "~90s",
  },
  {
    id: "agentic",
    label: "Agentic",
    blurb: "Tool-using agent — explores citation graph, searches the web.",
    accent: "orange",
    latency: "~60s",
  },
  {
    id: "literature_survey",
    label: "Literature survey",
    blurb: "Multi-paper survey with theme extraction and select-then-deepen.",
    accent: "green",
    latency: "~2 min",
  },
  {
    id: "contradiction",
    label: "Contradiction",
    blurb: "Surface disagreements across papers on a focused claim.",
    accent: "grey",
    latency: "~45s",
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
