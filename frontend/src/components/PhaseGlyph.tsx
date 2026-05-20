"use client";

// One small, distinct SVG per pipeline phase (and for the sidebar
// section entries that mirror them). Reusing the same glyph across
// modes means a phase like "retrieve" looks the same in Basic,
// Advanced, Profound, and Agentic — visual continuity across the
// product.
//
// All glyphs are 24×24 with currentColor stroke / fill so they take
// the parent's text colour. Each is intentionally geometric and
// distinct so a quick glance can tell them apart at 12-16 px.

export type PhaseGlyphKey =
  // Sidebar
  | "kb"
  | "survey"
  | "settings"
  // Trail phases
  | "retrieve"      // magnifier
  | "rewrite"       // refresh / circular arrow
  | "screen"        // filter funnel
  | "synthesize"    // pen / quill
  | "reason"        // sparkles / brain
  | "critique"      // eye
  | "revise"        // pen-edit
  | "plan"          // checklist
  | "tools"         // wrench / build
  | "themes"        // network / nodes
  | "select"        // cursor
  | "deepen"        // zoom-in
  | "group"         // people
  | "contrast";     // scale / balance

type Props = {
  glyph: PhaseGlyphKey;
  size?: number;
  className?: string;
};

export function PhaseGlyph({ glyph, size = 14, className = "" }: Props) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={className}
    >
      {GLYPHS[glyph]}
    </svg>
  );
}

const GLYPHS: Record<PhaseGlyphKey, React.ReactNode> = {
  // Stack of books — knowledge base.
  kb: (
    <>
      <path d="M4 5v14M8 5v14M12 5v14" />
      <rect x="14" y="5" width="6" height="14" rx="1" />
    </>
  ),
  // Tilted bars — survey / chart.
  survey: (
    <>
      <path d="M4 20V10M10 20V4M16 20V14M22 20V8" />
    </>
  ),
  // Sliders — settings (cleaner than a gear at 14px).
  settings: (
    <>
      <path d="M4 6h10M18 6h2" />
      <circle cx="16" cy="6" r="2" />
      <path d="M4 12h2M10 12h10" />
      <circle cx="8" cy="12" r="2" />
      <path d="M4 18h14M22 18h0" />
      <circle cx="20" cy="18" r="2" />
    </>
  ),
  // Magnifier — retrieval / search.
  retrieve: (
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="M16 16l4 4" />
    </>
  ),
  // Circular arrow — rewrite / refresh.
  rewrite: (
    <>
      <path d="M20 11a8 8 0 1 0-3 6" />
      <path d="M20 5v6h-6" />
    </>
  ),
  // Filter funnel — screen / relevance filter.
  screen: (
    <>
      <path d="M4 4h16l-6 8v6l-4 2v-8L4 4Z" />
    </>
  ),
  // Pen / quill — synthesize.
  synthesize: (
    <>
      <path d="M14 4l6 6L8 22H2v-6L14 4Z" />
      <path d="M12 6l6 6" />
    </>
  ),
  // Sparkles — reason / deep thinking.
  reason: (
    <>
      <path d="M12 3l1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6L12 3Z" />
      <path d="M19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16Z" />
    </>
  ),
  // Eye — critique / verify.
  critique: (
    <>
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  // Pen on a doc — revise.
  revise: (
    <>
      <path d="M4 4h10l6 6v10H4V4Z" />
      <path d="M14 4v6h6" />
      <path d="M9 15l4-4 2 2-4 4H9v-2Z" />
    </>
  ),
  // Checklist — plan.
  plan: (
    <>
      <path d="M4 5l2 2 3-3" />
      <path d="M11 6h9" />
      <path d="M4 12l2 2 3-3" />
      <path d="M11 13h9" />
      <path d="M4 19l2 2 3-3" />
      <path d="M11 20h9" />
    </>
  ),
  // Wrench — tools.
  tools: (
    <>
      <path d="M15 4a5 5 0 0 1-6 6l-5 5 3 3 5-5a5 5 0 0 1 6-6l-3 3-3-3 3-3Z" />
    </>
  ),
  // Connected nodes — themes / network.
  themes: (
    <>
      <circle cx="5" cy="12" r="2" />
      <circle cx="19" cy="6" r="2" />
      <circle cx="19" cy="18" r="2" />
      <circle cx="12" cy="12" r="2" />
      <path d="M7 12h3M14 12h3M13 11l5-4M13 13l5 4" />
    </>
  ),
  // Cursor arrow — select.
  select: (
    <>
      <path d="M5 3l4 16 3-7 7-3L5 3Z" />
    </>
  ),
  // Magnifier with plus — deepen.
  deepen: (
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="M16 16l4 4" />
      <path d="M11 8v6M8 11h6" />
    </>
  ),
  // Three people — group by stance.
  group: (
    <>
      <circle cx="9" cy="9" r="3" />
      <path d="M3 20a6 6 0 0 1 12 0" />
      <circle cx="17" cy="9" r="2.5" />
      <path d="M14 20a5 5 0 0 1 8-1" />
    </>
  ),
  // Balance scale — contrast.
  contrast: (
    <>
      <path d="M12 4v16M6 20h12" />
      <path d="M6 10l-3 6h6l-3-6Z" />
      <path d="M18 8l-3 6h6l-3-6Z" />
      <path d="M12 4l-6 6M12 4l6 4" />
    </>
  ),
};
