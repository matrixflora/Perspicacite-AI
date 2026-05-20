/* Perspicacité — single React component
   Reimagined CNRS literature workspace, Claude Desktop × Perplexity DNA
   Uses Tailwind v4 utility classes + var(--cnrs-*) tokens defined in index.html
*/
const { useState, useEffect, useRef, useMemo, useCallback } = React;

// ---------- data ----------
const MODES = [
  { id: "basic",         label: "Basic",            sub: "Single-pass retrieval over your KB, fast answer." },
  { id: "advanced",      label: "Advanced",         sub: "Query rewriting + reranking; cited answer." },
  { id: "profound",      label: "Profound",         sub: "Iterative deepening; long-form synthesis." },
  { id: "agentic",       label: "Agentic",          sub: "Multi-step tools: search, fetch, cross-check." },
  { id: "survey",        label: "Literature survey",sub: "Structured review across many corpora." },
  { id: "contradiction", label: "Contradiction",    sub: "Find disagreements between sources." },
];

const DATABASES = [
  { id: "kb-tfm",     label: "audit-p72-tfm",  papers: 2,  chunks: 25, kind: "kb", on: true  },
  { id: "kb-rag",     label: "audit-p72-rag",  papers: 2,  chunks: 27, kind: "kb", on: true  },
  { id: "kb-p75",     label: "audit-p75",      papers: 0,  chunks: 0,  kind: "kb", on: false },
  { id: "kb-p74",     label: "audit-p74",      papers: 2,  chunks: 30, kind: "kb", on: false },
  { id: "kb-p73",     label: "audit-p73",      papers: 1,  chunks: 15, kind: "kb", on: false },
  { id: "kb-p68",     label: "audit-p68",      papers: 1,  chunks: 15, kind: "kb", on: false },
  { id: "kb-p64",     label: "audit-p64",      papers: 6,  chunks: 82, kind: "kb", on: false },
  { id: "kb-p62",     label: "audit-p62",      papers: 4,  chunks: 52, kind: "kb", on: false },
  { id: "kb-p57",     label: "audit-p57",      papers: 0,  chunks: 0,  kind: "kb", on: false },
  { id: "web-arxiv",  label: "arXiv (web)",    papers: "live", chunks: "—", kind: "web", on: false },
  { id: "web-cr",     label: "Crossref",       papers: "live", chunks: "—", kind: "web", on: false },
  { id: "web-pubmed", label: "PubMed",         papers: "live", chunks: "—", kind: "web", on: false },
];

const KNOWLEDGE_BASES = [
  { id: "audit-p72-tfm", title: "audit-p72-tfm", desc: "Papers on transformer architectures",  papers: 2, chunks: 25, created: "May 19, 2026" },
  { id: "audit-p72-rag", title: "audit-p72-rag", desc: "Papers on retrieval-augmented generation", papers: 2, chunks: 27, created: "May 19, 2026" },
  { id: "audit-p75",     title: "audit-p75",     desc: "Created via MCP",                       papers: 0, chunks: 0,  created: "May 19, 2026" },
  { id: "audit-p74",     title: "audit-p74",     desc: "Created via MCP",                       papers: 2, chunks: 30, created: "May 19, 2026" },
  { id: "audit-p73",     title: "audit-p73",     desc: "Created via MCP",                       papers: 1, chunks: 15, created: "May 19, 2026" },
  { id: "audit-p68",     title: "audit-p68",     desc: "Created via MCP",                       papers: 1, chunks: 15, created: "May 19, 2026" },
  { id: "audit-p64",     title: "audit-p64",     desc: "Created via MCP",                       papers: 6, chunks: 82, created: "May 19, 2026" },
  { id: "audit-p62",     title: "audit-p62",     desc: "Created via MCP",                       papers: 4, chunks: 52, created: "May 19, 2026" },
  { id: "audit-p57",     title: "audit-p57",     desc: "Created via MCP",                       papers: 0, chunks: 0,  created: "May 19, 2026" },
  { id: "audit-p51",     title: "audit-p51",     desc: "Imported from audit_p51.bib",           papers: 3, chunks: 41, created: "May 18, 2026" },
  { id: "audit-p49",     title: "audit-p49",     desc: "Audit KB for arXiv URL ingestion test", papers: 5, chunks: 64, created: "May 18, 2026" },
  { id: "audit-p44",     title: "audit-p44",     desc: "Screening and ingest chain audit",      papers: 8, chunks: 102,created: "May 17, 2026" },
];

const CONVOS = {
  yesterday: [
    { id: "c1", title: "Which mass-spectrometry foundation models exist in 2026?", model: "deepseek-v4-flash", mode: "agentic" },
    { id: "c2", title: "feature based molecular networking review",                 model: "claude-haiku-4-5",  mode: "advanced" },
    { id: "c3", title: "Which mass-spectrometry foundation models exist in 2026?", model: "deepseek-v4-flash", mode: "profound" },
    { id: "c4", title: "Summarise the state-of-the-art on multi-modal scientific search.", model: "gpt-5-mini", mode: "survey" },
  ],
  earlier: [
    { id: "c5", title: "Self-RAG vs Corrective RAG — empirical differences", model: "claude-haiku-4-5", mode: "advanced" },
    { id: "c6", title: "Critique tokens taxonomy in RAG systems",            model: "deepseek-v4-flash", mode: "basic" },
    { id: "c7", title: "BibTeX import edge cases (DOI without metadata)",    model: "gpt-5-mini",        mode: "basic" },
  ],
};

const SUGGESTED = [
  { q: "What are critique tokens in Self-RAG?", mode: "basic" },
  { q: "Compare retrieval-augmented generation with corrective RAG.", mode: "advanced" },
  { q: "Summarise the state-of-the-art on multi-modal scientific search.", mode: "survey" },
  { q: "Which mass-spectrometry foundation models exist in 2026?", mode: "agentic" },
];

// A rich pre-baked "agentic" answer to populate the conversation view
const SAMPLE_ANSWER = {
  query: "Which mass-spectrometry foundation models exist in 2026?",
  mode: "agentic",
  model: "deepseek-v4-flash",
  thinking: [
    { t: "00:00.21", k: "plan",     text: "Decompose: (1) define MS foundation model, (2) survey 2024–2026 releases, (3) compare modalities/scales." },
    { t: "00:01.04", k: "search",   text: "Query KB audit-p72-tfm: \"foundation model mass spectrometry\" — 8 hits, 2 high-relevance.", meta: "8 hits" },
    { t: "00:02.12", k: "search",   text: "Query KB audit-p72-rag: \"MS2 transformer pretraining\" — 4 hits, 1 high-relevance.", meta: "4 hits" },
    { t: "00:03.30", k: "fetch",    text: "Fetch arXiv:2503.18421 — MS2Mol-XL technical report." },
    { t: "00:04.18", k: "fetch",    text: "Fetch arXiv:2601.09812 — SpecBERT-2 release notes." },
    { t: "00:05.62", k: "reflect",  text: "Two strong candidates with empirical results; one boundary case (MoleculeNet-MS) without pretraining objective — exclude.", meta: "rejected 1" },
    { t: "00:06.41", k: "compose",  text: "Compose grounded answer, attach citations [1]–[4]." },
  ],
  body: [
    {
      kind: "p",
      text: "As of May 2026, four publicly described systems meet a working definition of a mass-spectrometry foundation model — i.e. a single pre-trained encoder that transfers across spectral interpretation, retention-time prediction, and molecular property tasks without per-task feature engineering."
    },
    {
      kind: "list",
      items: [
        ["MS2Mol-XL (2025) [1]",   "1.4B parameters, contrastively pre-trained on 38M MS² spectra paired with SMILES. Tops MoNA and CASMI benchmarks on top-1 structure recovery."],
        ["SpecBERT-2 (2026) [2]",  "Encoder-only, 340M params; published in Nature Methods. Fine-tunes for retention-time and CCS regression with <1% labelled data."],
        ["MetaMS-Flow (2025) [3]", "Diffusion backbone for spectrum-to-structure inversion; weaker zero-shot but strong with retrieval augmentation."],
        ["CNRS-eDIAM/Halo-1 (2026) [4]", "In-house at ICN UMR 7272; 220M params, trained on natural-product corpora; reported on Côte d'Azur internal benchmarks."],
      ]
    },
    { kind: "p", text: "Two earlier systems often grouped under this label — MoleculeNet-MS and Spec2Vec — do not meet the pre-training criterion and are excluded from this comparison." },
    { kind: "p", text: "Contradictions surfaced: [1] and [3] disagree on top-1 recovery on CASMI-2022; [3]'s reported uplift is not reproduced by the [1] authors using the published weights." },
  ],
  sources: [
    { n: 1, title: "MS2Mol-XL: a 1.4B contrastive encoder for mass-spectral interpretation", venue: "arXiv 2503.18421", year: 2025, color: "#d96f4a", letter: "M" },
    { n: 2, title: "Scaling laws for mass-spectrometry encoders (SpecBERT-2)",                 venue: "Nature Methods",   year: 2026, color: "#4a8a6f", letter: "N" },
    { n: 3, title: "MetaMS-Flow: diffusion backbones for spectrum-to-structure inversion",   venue: "NeurIPS",          year: 2025, color: "#0d3a66", letter: "F" },
    { n: 4, title: "Halo-1: a domain-pretrained encoder for natural-product MS",             venue: "ICN UMR 7272 TR",  year: 2026, color: "#caa83a", letter: "H" },
  ],
  tokens: { up: 1842, down: 612, elapsed: 18.4 },
};

// ---------- tiny icon set (16px) — outline, IBM-friendly weights ----------
const I = {
  plus:    (p) => <svg width="16" height="16" viewBox="0 0 16 16" fill="none" {...p}><path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></svg>,
  search:  (p) => <svg width="16" height="16" viewBox="0 0 16 16" fill="none" {...p}><circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.4"/><path d="M10.5 10.5L14 14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg>,
  arrow:   (p) => <svg width="14" height="14" viewBox="0 0 16 16" fill="none" {...p}><path d="M3 8h10m-4-4 4 4-4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  globe:   (p) => <svg width="14" height="14" viewBox="0 0 16 16" fill="none" {...p}><circle cx="8" cy="8" r="6.2" stroke="currentColor" strokeWidth="1.4"/><path d="M2 8h12M8 2c2 2 2 10 0 12M8 2c-2 2-2 10 0 12" stroke="currentColor" strokeWidth="1.2"/></svg>,
  books:   (p) => <svg width="16" height="16" viewBox="0 0 16 16" fill="none" {...p}><rect x="2" y="3.5" width="4" height="9" rx="0.6" stroke="currentColor" strokeWidth="1.3"/><rect x="6.5" y="3.5" width="4" height="9" rx="0.6" stroke="currentColor" strokeWidth="1.3"/><path d="M11 5l3.4.9-2 8.3-3.4-.9" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>,
  chart:   (p) => <svg width="16" height="16" viewBox="0 0 16 16" fill="none" {...p}><path d="M2 13V3m0 10h12M5 11V8m3 3V5m3 6V7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg>,
  sun:     (p) => <svg width="16" height="16" viewBox="0 0 16 16" fill="none" {...p}><circle cx="8" cy="8" r="3" stroke="currentColor" strokeWidth="1.4"/><path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.3 3.3l1 1M11.7 11.7l1 1M3.3 12.7l1-1M11.7 4.3l1-1" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg>,
  moon:    (p) => <svg width="16" height="16" viewBox="0 0 16 16" fill="none" {...p}><path d="M13 9.5A5.5 5.5 0 1 1 6.5 3a4.5 4.5 0 0 0 6.5 6.5Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"/></svg>,
  settings:(p) => <svg width="16" height="16" viewBox="0 0 16 16" fill="none" {...p}><circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.4"/><path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.3 3.3l1.1 1.1M11.6 11.6l1.1 1.1M3.3 12.7l1.1-1.1M11.6 4.4l1.1-1.1" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>,
  send:    (p) => <svg width="16" height="16" viewBox="0 0 16 16" fill="none" {...p}><path d="M2 8l12-5-5 12-2-5-5-2Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" fill="none"/></svg>,
  stop:    (p) => <svg width="12" height="12" viewBox="0 0 12 12" {...p}><rect x="2.5" y="2.5" width="7" height="7" rx="1" fill="currentColor"/></svg>,
  chev:    (p) => <svg width="12" height="12" viewBox="0 0 12 12" fill="none" {...p}><path d="M3 4.5l3 3 3-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  check:   (p) => <svg width="12" height="12" viewBox="0 0 12 12" fill="none" {...p}><path d="M2.5 6.5l2.5 2.5 5-6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  cmd:     (p) => <svg width="11" height="11" viewBox="0 0 11 11" fill="none" {...p}><path d="M3 1.5a1.5 1.5 0 1 0 1.5 1.5V8a1.5 1.5 0 1 0 1.5-1.5H3" stroke="currentColor" strokeWidth="1.1"/><path d="M8 1.5a1.5 1.5 0 1 1-1.5 1.5V8a1.5 1.5 0 1 1-1.5-1.5H8" stroke="currentColor" strokeWidth="1.1"/></svg>,
  copy:    (p) => <svg width="14" height="14" viewBox="0 0 16 16" fill="none" {...p}><rect x="5" y="5" width="9" height="9" rx="1.2" stroke="currentColor" strokeWidth="1.3"/><path d="M3 11V3.5A.5.5 0 0 1 3.5 3H11" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>,
  share:   (p) => <svg width="14" height="14" viewBox="0 0 16 16" fill="none" {...p}><path d="M8 2v8m0-8L5.5 4.5M8 2l2.5 2.5M3 9v3.5A1.5 1.5 0 0 0 4.5 14h7a1.5 1.5 0 0 0 1.5-1.5V9" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  bookmark:(p) => <svg width="14" height="14" viewBox="0 0 16 16" fill="none" {...p}><path d="M4 2.5h8v11l-4-2.5-4 2.5v-11Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>,
  more:    (p) => <svg width="14" height="14" viewBox="0 0 16 16" fill="none" {...p}><circle cx="3" cy="8" r="1.2" fill="currentColor"/><circle cx="8" cy="8" r="1.2" fill="currentColor"/><circle cx="13" cy="8" r="1.2" fill="currentColor"/></svg>,
  spark:   (p) => <svg width="14" height="14" viewBox="0 0 16 16" fill="none" {...p}><path d="M8 2l1.4 4.1L13.5 7.5 9.4 8.9 8 13l-1.4-4.1L2.5 7.5l4.1-1.4L8 2Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round"/></svg>,
  paperclip:(p)=> <svg width="14" height="14" viewBox="0 0 16 16" fill="none" {...p}><path d="M11 7.5l-4.2 4.2a2.2 2.2 0 1 1-3.1-3.1L7.5 4.2a3.5 3.5 0 1 1 5 5L8 14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg>,
  filter:  (p) => <svg width="14" height="14" viewBox="0 0 16 16" fill="none" {...p}><path d="M2 3h12l-4.5 6v4l-3 1.5V9L2 3Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>,
};

// ---------- top-level component ----------
function Perspicacite() {
  // theme
  const [theme, setTheme] = useState("light");
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "dark") root.classList.add("dark");
    else root.classList.remove("dark");
  }, [theme]);

  // view
  const [view, setView] = useState("chat"); // 'home' | 'chat' | 'knowledge' | 'survey'
  const [activeConvo, setActiveConvo] = useState("c1");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [dbOpen, setDbOpen] = useState(false);
  const [thinkingOpen, setThinkingOpen] = useState(true);

  // composer
  const [mode, setMode] = useState("agentic");
  const [input, setInput] = useState("");
  const [dbs, setDbs] = useState(() => Object.fromEntries(DATABASES.map(d => [d.id, d.on])));
  const dbCount = Object.values(dbs).filter(Boolean).length;
  const dbTotal = DATABASES.length;

  // streaming sim
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [streamMsg, setStreamMsg] = useState(null); // {kind:'stream', stepIdx, bodyIdx, chars}
  const [conversation, setConversation] = useState([
    // existing baked conversation: 1 user, 1 assistant (the SAMPLE_ANSWER)
    { role: "user",      text: SAMPLE_ANSWER.query },
    { role: "assistant", mode: SAMPLE_ANSWER.mode, model: SAMPLE_ANSWER.model,
      thinking: SAMPLE_ANSWER.thinking, body: SAMPLE_ANSWER.body,
      sources: SAMPLE_ANSWER.sources, tokens: SAMPLE_ANSWER.tokens, done: true },
  ]);

  // global shortcuts
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen(v => !v);
      } else if (e.key === "Escape") {
        setPaletteOpen(false);
        setDbOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // elapsed timer while running
  useEffect(() => {
    if (!running) return;
    const t0 = performance.now();
    const id = setInterval(() => setElapsed((performance.now() - t0) / 1000), 100);
    return () => clearInterval(id);
  }, [running]);

  // send: simulate agentic streaming
  const send = (q, optMode) => {
    const text = (q ?? input).trim();
    if (!text) return;
    const m = optMode ?? mode;
    setInput("");
    setView("chat");
    setMode(m);
    setRunning(true);
    setElapsed(0);
    setThinkingOpen(true);

    const newUser = { role: "user", text };
    const newAssistant = {
      role: "assistant", mode: m, model: SAMPLE_ANSWER.model,
      thinking: [], body: [], sources: [], tokens: { up: 0, down: 0, elapsed: 0 },
      done: false
    };
    setConversation(c => [...c, newUser, newAssistant]);

    // schedule thinking steps then body
    const steps = SAMPLE_ANSWER.thinking;
    const body  = SAMPLE_ANSWER.body;
    const srcs  = SAMPLE_ANSWER.sources;
    let acc = 0;
    steps.forEach((s, i) => {
      acc += 600 + Math.random() * 500;
      setTimeout(() => {
        setConversation(c => {
          const cp = c.slice();
          const last = { ...cp[cp.length - 1] };
          last.thinking = steps.slice(0, i + 1);
          cp[cp.length - 1] = last;
          return cp;
        });
      }, acc);
    });
    // body
    body.forEach((b, i) => {
      acc += 800;
      setTimeout(() => {
        setConversation(c => {
          const cp = c.slice();
          const last = { ...cp[cp.length - 1] };
          last.body = body.slice(0, i + 1);
          cp[cp.length - 1] = last;
          return cp;
        });
      }, acc);
    });
    // sources + done
    acc += 700;
    setTimeout(() => {
      setConversation(c => {
        const cp = c.slice();
        const last = { ...cp[cp.length - 1] };
        last.sources = srcs;
        last.tokens = { up: 1700 + Math.floor(Math.random()*300), down: 500 + Math.floor(Math.random()*200), elapsed: elapsed };
        last.done = true;
        cp[cp.length - 1] = last;
        return cp;
      });
      setRunning(false);
    }, acc);
  };

  const stop = () => {
    setRunning(false);
    setConversation(c => {
      const cp = c.slice();
      const last = { ...cp[cp.length - 1] };
      if (last && last.role === "assistant" && !last.done) {
        last.done = true;
        if (last.body.length === 0) last.body = [{ kind: "p", text: "Stopped." }];
      }
      cp[cp.length - 1] = last;
      return cp;
    });
  };

  const newChat = () => {
    setConversation([]);
    setView("home");
    setActiveConvo(null);
  };

  return (
    <div className="h-full w-full flex bg-paper relative overflow-hidden">
      <Sidebar
        theme={theme} setTheme={setTheme}
        view={view} setView={setView}
        activeConvo={activeConvo} setActiveConvo={setActiveConvo}
        openPalette={() => setPaletteOpen(true)}
        newChat={newChat}
      />
      <main className="flex-1 min-w-0 flex flex-col relative">
        {view === "chat" && (
          <ChatView
            conversation={conversation}
            thinkingOpen={thinkingOpen} setThinkingOpen={setThinkingOpen}
            running={running}
            onAsk={(s, m) => send(s, m)}
          />
        )}
        {view === "home" && (
          <HomeView mode={mode} onPick={(q, m) => send(q, m)} />
        )}
        {view === "knowledge" && <KnowledgeView />}
        {view === "survey" && <SurveyView />}
        <Composer
          mode={mode} setMode={setMode}
          input={input} setInput={setInput}
          dbCount={dbCount} dbTotal={dbTotal}
          openDbs={() => setDbOpen(true)}
          send={() => send()}
          running={running} stop={stop}
          elapsed={elapsed}
        />
      </main>

      {dbOpen && <DbPicker dbs={dbs} setDbs={setDbs} onClose={() => setDbOpen(false)} />}
      {paletteOpen && <CommandPalette
        onClose={() => setPaletteOpen(false)}
        onNav={(v) => { setView(v); setPaletteOpen(false); }}
        onAsk={(q, m) => { send(q, m); setPaletteOpen(false); }}
        onNewChat={() => { newChat(); setPaletteOpen(false); }}
        setMode={(m) => { setMode(m); setPaletteOpen(false); }}
      />}
    </div>
  );
}

// ---------- Sidebar ----------
function Sidebar({ theme, setTheme, view, setView, activeConvo, setActiveConvo, openPalette, newChat }) {
  return (
    <aside className="w-[280px] shrink-0 flex flex-col border-r border-line bg-paper relative">
      {/* Brand */}
      <div className="px-4 pt-4 pb-3 flex items-center gap-3">
        <div className="relative">
          <div className="w-8 h-8" style={{
            background: "var(--cnrs-yellow)",
            WebkitMask: "url('data:image/svg+xml;utf8,<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 100 100\"><path d=\"M50 2 C 78 2 98 22 98 50 C 98 78 78 98 50 98 C 22 98 2 78 2 50 C 2 22 22 2 50 2 Z\" fill=\"black\"/></svg>') center/contain no-repeat",
            mask: "url('data:image/svg+xml;utf8,<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 100 100\"><path d=\"M50 2 C 78 2 98 22 98 50 C 98 78 78 98 50 98 C 22 98 2 78 2 50 C 2 22 22 2 50 2 Z\" fill=\"black\"/></svg>') center/contain no-repeat",
          }}/>
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-semibold tracking-tight ink leading-none">Perspicacité</div>
          <div className="text-[11px] mt-1 mute font-mono">v2 · POC · eDIAM</div>
        </div>
      </div>

      {/* New chat */}
      <div className="px-3 pb-2">
        <button onClick={newChat}
          className="btn-primary w-full rounded-xl px-3 py-2.5 text-sm font-medium flex items-center justify-center gap-2 hover:opacity-95 active:translate-y-px transition">
          <I.plus/> New chat
        </button>
      </div>

      {/* Quick switcher (cmd+K) */}
      <div className="px-3 pt-1">
        <button onClick={openPalette}
          className="w-full flex items-center gap-2 rounded-lg px-3 py-2 text-[13px] mute bg-paper-2 hover:bg-paper-2/70 border border-line transition">
          <I.search className="opacity-60"/>
          <span className="flex-1 text-left">Quick switcher…</span>
          <kbd className="flex items-center gap-0.5"><I.cmd/>K</kbd>
        </button>
      </div>

      {/* Search chats */}
      <div className="px-3 pt-2 pb-3">
        <label className="focus-ring flex items-center gap-2 rounded-lg px-3 py-2 text-[13px] border border-line bg-card">
          <I.search className="opacity-50"/>
          <input placeholder="Search chats…" className="flex-1 bg-transparent outline-none text-[13px] placeholder:opacity-50 ink"/>
        </label>
      </div>

      {/* Convo lists */}
      <nav className="flex-1 min-h-0 overflow-y-auto px-2 pb-2">
        <SectionLabel>Yesterday</SectionLabel>
        {CONVOS.yesterday.map(c => (
          <ConvoItem key={c.id} c={c} active={view==="chat" && activeConvo===c.id}
            onClick={() => { setView("chat"); setActiveConvo(c.id); }}/>
        ))}
        <SectionLabel className="mt-3">Earlier this week</SectionLabel>
        {CONVOS.earlier.map(c => (
          <ConvoItem key={c.id} c={c} active={view==="chat" && activeConvo===c.id}
            onClick={() => { setView("chat"); setActiveConvo(c.id); }}/>
        ))}
        <button className="mt-2 ml-2 text-[12.5px] font-medium ink/80 hover:underline flex items-center gap-1">
          See all 125 conversations <I.arrow className="opacity-60"/>
        </button>
      </nav>

      {/* Bottom nav */}
      <div className="px-2 pt-2 border-t border-line">
        <NavItem icon={<I.books/>} label="Knowledge bases" badge="12"
          active={view==="knowledge"} onClick={() => setView("knowledge")}/>
        <NavItem icon={<I.chart/>} label="Literature survey" badge="3"
          active={view==="survey"} onClick={() => setView("survey")}/>
      </div>

      {/* Theme + settings */}
      <div className="p-3 flex items-center gap-2">
        <button onClick={() => setTheme(theme==="light" ? "dark" : "light")}
          className="flex-1 flex items-center gap-2 px-3 py-2 rounded-xl border border-line bg-card text-[13px] hover:bg-paper-2 transition">
          {theme==="light" ? <I.sun/> : <I.moon/>}
          <span className="font-medium">{theme==="light" ? "Light" : "Dark"}</span>
        </button>
        <button className="w-9 h-9 rounded-xl border border-line bg-card flex items-center justify-center hover:bg-paper-2 transition" title="Settings">
          <I.settings/>
        </button>
      </div>

      {/* Institutional footer */}
      <div className="px-3 pb-3 pt-1 flex items-center gap-2">
        <div className="w-7 h-7 rounded-full bg-cnrs-blue-ink flex items-center justify-center text-cnrs-yellow text-[11px] font-bold font-mono shrink-0" title="ICN UMR 7272">N</div>
        <div className="min-w-0 leading-tight">
          <div className="text-[11px] mute font-mono truncate">ICN UMR 7272 · 3iA Côte d'Azur</div>
          <div className="text-[10px] faint font-mono truncate">CNRS · Université Côte d'Azur</div>
        </div>
      </div>
    </aside>
  );
}

function SectionLabel({ children, className = "" }) {
  return <div className={`px-3 pt-2 pb-1 text-[10.5px] uppercase tracking-[0.14em] faint font-mono ${className}`}>{children}</div>;
}

function ConvoItem({ c, active, onClick }) {
  return (
    <button onClick={onClick}
      className={"group w-full text-left px-3 py-1.5 rounded-md text-[13px] flex items-center gap-2 transition " +
        (active
          ? "bg-cnrs-yellow/40 ink"
          : "hover:bg-paper-2 ink/90")}>
      <span className="flex-1 truncate">{c.title}</span>
      <span className="opacity-0 group-hover:opacity-100 transition text-[10px] font-mono faint">{c.mode}</span>
    </button>
  );
}

function NavItem({ icon, label, badge, active, onClick }) {
  return (
    <button onClick={onClick}
      className={"w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] transition mb-0.5 " +
        (active ? "bg-cnrs-yellow text-cnrs-blue-ink font-semibold"
                : "hover:bg-paper-2 ink/90")}>
      <span className={active ? "" : "opacity-70"}>{icon}</span>
      <span className="flex-1 text-left">{label}</span>
      {badge && <span className={"text-[10.5px] font-mono px-1.5 py-0.5 rounded " + (active ? "bg-cnrs-blue-ink/10" : "bg-paper-2 mute")}>{badge}</span>}
    </button>
  );
}

// ---------- Halo (signature device) ----------
function Halo({ size = 720, x = "55%", y = "-22%", breathing = false, opacity }) {
  return (
    <div
      className={"halo " + (breathing ? "halo-breathing" : "")}
      style={{ width: size, height: size, left: x, top: y, ['--cnrs-halo-opacity']: opacity }}>
      <div className="halo-shape" />
    </div>
  );
}

// ---------- Home view (empty-state) ----------
function HomeView({ mode, onPick }) {
  const m = MODES.find(x => x.id === mode) ?? MODES[0];
  return (
    <section className="flex-1 min-h-0 overflow-y-auto relative grain">
      <Halo size={840} x="42%" y="-32%" opacity={0.85} breathing/>
      <div className="relative max-w-[860px] mx-auto px-10 pt-28 pb-40">
        <div className="text-[10.5px] uppercase tracking-[0.16em] font-mono faint mb-3">Perspicacité · eDIAM</div>
        <h1 className="text-[56px] leading-[1.02] font-semibold tracking-tight ink">
          Ask the literature.
        </h1>
        <p className="mt-4 text-[15px] mute max-w-[58ch]">
          <span className="font-medium ink/90">{m.label} mode</span> · {m.sub}
        </p>

        <div className="mt-12 grid grid-cols-2 gap-3">
          {SUGGESTED.map((s, i) => (
            <button key={i} onClick={() => onPick(s.q, s.mode)}
              className="group text-left rounded-xl border border-line bg-card hover:border-line-strong hover:bg-paper-2 transition px-4 py-3.5 relative">
              <div className="text-[14px] ink leading-snug pr-6">{s.q}</div>
              <div className="mt-2 flex items-center gap-2">
                <span className="text-[10.5px] font-mono uppercase tracking-wider mute">{MODES.find(x=>x.id===s.mode)?.label}</span>
              </div>
              <I.arrow className="absolute top-3.5 right-3.5 opacity-0 group-hover:opacity-60 transition"/>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

// ---------- Chat view ----------
function ChatView({ conversation, thinkingOpen, setThinkingOpen, running, onAsk }) {
  const scrollRef = useRef(null);
  useEffect(() => {
    if (!scrollRef.current) return;
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [conversation]);

  return (
    <section ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto relative grain">
      {/* halo */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        <Halo size={820} x="50%" y="-30%" opacity={running ? 0.85 : 0.5} breathing={running}/>
      </div>

      <div className="relative max-w-[820px] mx-auto px-10 pt-10 pb-20">
        {conversation.map((m, i) =>
          m.role === "user"
            ? <UserMessage key={i} text={m.text}/>
            : <AssistantMessage key={i}
                msg={m}
                thinkingOpen={thinkingOpen}
                setThinkingOpen={setThinkingOpen}
                running={running && i === conversation.length - 1}/>
        )}
      </div>
    </section>
  );
}

function UserMessage({ text }) {
  return (
    <div className="flex justify-end mb-6">
      <div className="max-w-[78%] btn-primary rounded-2xl rounded-tr-md px-4 py-2.5 text-[14.5px] leading-snug shadow-sm">
        {text}
      </div>
    </div>
  );
}

function AssistantMessage({ msg, thinkingOpen, setThinkingOpen, running }) {
  return (
    <div className="mb-12">
      {/* mode tag */}
      <div className="flex items-center gap-2 mb-3">
        <ModeBadge mode={msg.mode}/>
        {running && <span className="text-[12px] mute">Working…</span>}
        {!running && msg.done && <span className="text-[12px] mute font-mono">{(msg.tokens?.elapsed ?? 18.4).toFixed(1)}s · ↑{msg.tokens?.up ?? 0} ↓{msg.tokens?.down ?? 0}</span>}
      </div>

      {/* Thinking trail */}
      {msg.thinking?.length > 0 && (
        <div className="mb-5 rounded-xl border border-line bg-card overflow-hidden">
          <button onClick={() => setThinkingOpen(o => !o)}
            className="w-full flex items-center gap-3 px-4 py-2.5 text-[12.5px] mute hover:bg-paper-2/60 transition">
            <span className="inline-flex items-center gap-1.5 px-1.5 py-0.5 rounded text-[10px] font-mono uppercase tracking-wider bg-paper-2 mute border border-line">
              <I.spark/> Thinking trail
            </span>
            <span className="font-mono text-[11px] faint">{msg.thinking.length} step{msg.thinking.length===1?"":"s"}{running ? " · live" : ""}</span>
            <span className="flex-1"/>
            <I.chev className="transition" style={{ transform: thinkingOpen ? "rotate(0deg)" : "rotate(-90deg)" }}/>
          </button>
          {thinkingOpen && (
            <ol className="px-4 pb-3 pt-1 space-y-1.5">
              {msg.thinking.map((s, i) => (
                <li key={i} className="step-in flex gap-3 text-[13px]">
                  <span className="font-mono text-[11px] mute pt-[3px] w-[68px] shrink-0">{s.t}</span>
                  <span className={"text-[10.5px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded h-fit mt-[2px] " + stepStyle(s.k)}>
                    {s.k}
                  </span>
                  <span className="flex-1 ink/85">{s.text}</span>
                  {s.meta && <span className="font-mono text-[11px] faint pt-[3px]">{s.meta}</span>}
                </li>
              ))}
              {running && (
                <li className="flex gap-3 text-[13px] mute">
                  <span className="font-mono text-[11px] pt-[3px] w-[68px] shrink-0">∙∙∙</span>
                  <span className="flex-1">…</span>
                </li>
              )}
            </ol>
          )}
        </div>
      )}

      {/* Body */}
      <div className="prose-tight">
        {msg.body.map((b, i) => {
          if (b.kind === "p") return <p key={i} className="text-[15px] ink leading-[1.65] mb-4">{renderInline(b.text, msg.sources)}</p>;
          if (b.kind === "list") return (
            <ul key={i} className="space-y-2.5 mb-5">
              {b.items.map((it, j) => (
                <li key={j} className="flex gap-3">
                  <span className="mt-[9px] w-1.5 h-1.5 rounded-full bg-cnrs-blue shrink-0"/>
                  <div className="text-[14.5px] ink leading-snug">
                    <span className="font-semibold">{renderInline(it[0], msg.sources)}</span>{" — "}
                    <span className="ink/85">{renderInline(it[1], msg.sources)}</span>
                  </div>
                </li>
              ))}
            </ul>
          );
          return null;
        })}
        {running && msg.body.length > 0 && <span className="caret"></span>}
      </div>

      {/* Sources */}
      {msg.sources?.length > 0 && (
        <>
          <div className="mt-7 mb-2 flex items-center gap-2">
            <div className="text-[10.5px] uppercase tracking-[0.16em] font-mono faint">Sources</div>
            <div className="flex-1 h-px bg-line"/>
            <button className="text-[12px] mute hover:ink flex items-center gap-1"><I.filter/> Filter</button>
          </div>
          <div className="grid grid-cols-2 gap-2.5">
            {msg.sources.map(s => <SourceCard key={s.n} s={s}/>)}
          </div>
        </>
      )}

      {/* Action row */}
      {msg.done && (
        <div className="mt-5 flex items-center gap-1.5">
          <ActionBtn icon={<I.copy/>}    label="Copy"/>
          <ActionBtn icon={<I.share/>}   label="Export"/>
          <ActionBtn icon={<I.bookmark/>}label="Pin"/>
          <span className="flex-1"/>
          <button className="text-[12px] mute hover:ink flex items-center gap-1.5"><I.spark/> Follow-up suggestions</button>
        </div>
      )}
    </div>
  );
}

function ActionBtn({ icon, label }) {
  return (
    <button className="flex items-center gap-1.5 px-2 py-1 rounded-md text-[12px] mute hover:bg-paper-2 hover:ink transition">
      {icon}<span>{label}</span>
    </button>
  );
}

function stepStyle(k) {
  // outlined colored chips
  const map = {
    plan:    { color: "var(--cnrs-blue)",  bg: "color-mix(in srgb, var(--cnrs-blue) 12%, transparent)" },
    search:  { color: "#5d6a78",           bg: "color-mix(in srgb, #5d6a78 12%, transparent)" },
    fetch:   { color: "var(--cnrs-coral)", bg: "color-mix(in srgb, var(--cnrs-coral) 14%, transparent)" },
    reflect: { color: "var(--cnrs-sage)",  bg: "color-mix(in srgb, var(--cnrs-sage) 14%, transparent)" },
    compose: { color: "#a07a14",           bg: "color-mix(in srgb, #ffeb6e 50%, transparent)" },
  };
  const s = map[k] ?? map.plan;
  return "";
}

// Render inline text and turn [N] markers into superscript citation pills
function renderInline(text, sources) {
  if (!sources) return text;
  const parts = [];
  let last = 0;
  const re = /\[(\d+)\]/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const n = parseInt(m[1], 10);
    const src = sources.find(s => s.n === n);
    parts.push(
      <a key={m.index} href="#" onClick={e=>e.preventDefault()}
        className="cite-pill inline-flex items-center justify-center -translate-y-[2px] mx-[1px]"
        style={{
          background: src?.color ?? "var(--cnrs-blue)",
          color: "white", fontFamily: "var(--font-mono)",
          fontSize: 10, fontWeight: 600, width: 16, height: 16,
          borderRadius: 5, lineHeight: 1, letterSpacing: 0,
        }}
        title={src?.title}>
        {n}
      </a>
    );
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function SourceCard({ s }) {
  return (
    <a href="#" onClick={e=>e.preventDefault()}
      className="group rounded-xl border border-line bg-card hover:bg-paper-2 hover:border-line-strong transition px-3.5 py-3 flex items-start gap-3">
      <span className="favicon-dot" style={{ background: s.color }}>{s.letter}</span>
      <div className="min-w-0 flex-1">
        <div className="text-[13px] ink leading-snug line-clamp-2 group-hover:underline">{s.title}</div>
        <div className="mt-1 text-[11px] font-mono mute flex items-center gap-2">
          <span>{s.venue}</span>
          <span className="opacity-50">·</span>
          <span>{s.year}</span>
        </div>
      </div>
      <span className="text-[11px] font-mono mute shrink-0">[{s.n}]</span>
    </a>
  );
}

function ModeBadge({ mode }) {
  const m = MODES.find(x => x.id === mode) ?? MODES[0];
  return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10.5px] font-mono uppercase tracking-[0.14em] font-medium"
      style={{ background: "var(--cnrs-yellow)", color: "var(--cnrs-blue-ink)" }}>
      <span className="w-1.5 h-1.5 rounded-full bg-cnrs-blue-ink"/>{m.label}
    </span>
  );
}

// ---------- Knowledge bases view ----------
function KnowledgeView() {
  return (
    <section className="flex-1 min-h-0 overflow-y-auto relative grain">
      <Halo size={620} x="62%" y="-28%" opacity={0.45}/>
      <div className="relative max-w-[1180px] mx-auto px-10 py-10">
        <div className="flex items-end justify-between mb-7">
          <div>
            <div className="text-[10.5px] uppercase tracking-[0.16em] font-mono faint mb-2">Knowledge bases</div>
            <h1 className="text-[40px] leading-tight font-semibold tracking-tight ink">Your literature corpora.</h1>
            <p className="mt-2 mute text-[14px] max-w-[70ch]">Curate DOIs and BibTeX into searchable, embedded knowledge bases.</p>
          </div>
          <button className="btn-primary rounded-xl px-4 py-2.5 text-[13.5px] font-medium flex items-center gap-2">
            <I.plus/> New KB
          </button>
        </div>

        <div className="flex items-center gap-2 mb-5">
          <label className="focus-ring flex items-center gap-2 rounded-lg px-3 py-2 text-[13px] border border-line bg-card w-[280px]">
            <I.search className="opacity-50"/>
            <input placeholder="Filter knowledge bases…" className="flex-1 bg-transparent outline-none text-[13px] placeholder:opacity-50 ink"/>
          </label>
          <span className="text-[12px] mute font-mono">{KNOWLEDGE_BASES.length} total</span>
          <div className="flex-1"/>
          <span className="text-[11px] font-mono mute uppercase tracking-wider">Sort</span>
          <button className="text-[12.5px] px-2.5 py-1.5 rounded-md border border-line bg-card hover:bg-paper-2 flex items-center gap-1">Recent <I.chev/></button>
        </div>

        <div className="grid grid-cols-3 gap-4">
          {KNOWLEDGE_BASES.map(k => (
            <article key={k.id} className="rounded-xl border border-line bg-card p-5 hover:border-line-strong transition relative">
              <span className="absolute top-4 right-4 w-2.5 h-2.5 rounded-full bg-cnrs-yellow"></span>
              <h3 className="text-[15px] font-semibold ink">{k.title}</h3>
              <p className="mt-1 text-[13px] mute leading-snug min-h-[36px]">{k.desc}</p>
              <hr className="my-3 border-line"/>
              <div className="flex items-center gap-6">
                <Stat label="Papers" value={k.papers}/>
                <Stat label="Chunks" value={k.chunks}/>
              </div>
              <div className="mt-4 text-[10.5px] uppercase tracking-[0.14em] font-mono faint">Created {k.created}</div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

function Stat({ label, value }) {
  return (
    <div>
      <div className="text-[10.5px] uppercase tracking-[0.14em] font-mono faint">{label}</div>
      <div className="mt-0.5 text-[20px] font-semibold ink tabular-nums">{value}</div>
    </div>
  );
}

// ---------- Literature survey view ----------
function SurveyView() {
  const cols = [
    { id: "claim",     label: "Claim",      w: "26%" },
    { id: "ms2mol",    label: "MS2Mol-XL",  w: "18%" },
    { id: "specbert",  label: "SpecBERT-2", w: "18%" },
    { id: "metamsflow",label: "MetaMS-Flow",w: "18%" },
    { id: "halo1",     label: "Halo-1",     w: "20%" },
  ];
  const rows = [
    ["Top-1 structure recovery on CASMI-22", "0.58 [1]", "0.51 [2]", "0.61 [3] ⚠︎", "—"],
    ["Pretraining objective", "Contrastive (MS²↔SMILES) [1]", "MLM on spectra [2]", "Diffusion [3]", "Domain-pretrained [4]"],
    ["Parameter count", "1.4B [1]", "340M [2]", "≈ 700M [3]", "220M [4]"],
    ["License", "Apache-2.0 [1]", "Non-commercial [2]", "Apache-2.0 [3]", "Internal [4]"],
    ["Fine-tunable in <1% labelled regime", "Reported [1]", "Yes [2]", "Unclear", "Yes [4]"],
  ];

  return (
    <section className="flex-1 min-h-0 overflow-y-auto relative grain">
      <Halo size={620} x="60%" y="-26%" opacity={0.4}/>
      <div className="relative max-w-[1180px] mx-auto px-10 py-10">
        <div className="flex items-end justify-between mb-7">
          <div>
            <div className="text-[10.5px] uppercase tracking-[0.16em] font-mono faint mb-2">Literature survey</div>
            <h1 className="text-[40px] leading-tight font-semibold tracking-tight ink">MS foundation models — comparison.</h1>
            <p className="mt-2 mute text-[14px] max-w-[70ch]">Side-by-side claims across the four 2025–2026 candidate systems. Cells link back to the supporting passage.</p>
          </div>
          <div className="flex items-center gap-2">
            <button className="text-[12.5px] px-3 py-2 rounded-lg border border-line bg-card hover:bg-paper-2 flex items-center gap-1.5"><I.share/> Export</button>
            <button className="btn-primary rounded-xl px-4 py-2.5 text-[13.5px] font-medium flex items-center gap-2"><I.plus/> Add claim</button>
          </div>
        </div>

        <div className="rounded-xl border border-line bg-card overflow-hidden">
          <table className="w-full text-[13px]">
            <thead className="bg-paper-2">
              <tr>
                {cols.map(c => (
                  <th key={c.id} style={{width: c.w}} className="text-left px-4 py-3 text-[10.5px] uppercase tracking-[0.14em] font-mono faint border-b border-line">{c.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-b border-line last:border-0 hover:bg-paper-2/40">
                  {r.map((cell, j) => (
                    <td key={j} className={"px-4 py-3 align-top " + (j===0 ? "ink font-medium" : "ink/85")}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="mt-5 text-[12px] mute font-mono">⚠︎ Contradiction detected on row 1. <a href="#" onClick={e=>e.preventDefault()} className="underline">Open contradiction explorer →</a></div>
      </div>
    </section>
  );
}

// ---------- Composer ----------
function Composer({ mode, setMode, input, setInput, dbCount, dbTotal, openDbs, send, running, stop, elapsed }) {
  const taRef = useRef(null);
  // auto-grow
  useEffect(() => {
    if (!taRef.current) return;
    taRef.current.style.height = "auto";
    taRef.current.style.height = Math.min(220, taRef.current.scrollHeight) + "px";
  }, [input]);

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  };

  return (
    <div className="shrink-0 px-8 pt-3 pb-6 relative">
      {/* Status bar (Perplexity-style) */}
      {(running) && (
        <div className="mx-auto max-w-[820px] mb-2 flex items-center justify-center">
          <div className="flex items-center gap-3 px-3 py-1.5 rounded-full bg-card border border-line text-[12px] mute font-mono">
            <span className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-cnrs-blue animate-pulse"/>
              Sending query…
            </span>
            <span className="opacity-50">·</span>
            <span className="tabular-nums">{elapsed.toFixed(1)}s</span>
            <span className="opacity-50">·</span>
            <span>14 ↑ 0 ↓</span>
            <span className="opacity-50">·</span>
            <span style={{color: "var(--cnrs-blue)"}}>{mode}</span>
            <span className="opacity-50">·</span>
            <span>deepseek-v4-flash</span>
            <button onClick={stop} className="ml-1 px-2 py-0.5 rounded-md border border-line bg-paper hover:bg-paper-2 flex items-center gap-1 ink">
              <I.stop/> <span className="text-[11px]">Stop</span>
            </button>
          </div>
        </div>
      )}

      <div className="mx-auto max-w-[820px] rounded-2xl border border-line-strong bg-card shadow-[0_2px_18px_-10px_rgba(0,0,0,0.15)] focus-ring">
        {/* textarea */}
        <textarea ref={taRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={onKey}
          placeholder="Ask the literature…  (Enter to send · Shift+Enter for newline)"
          className="w-full bg-transparent outline-none resize-none px-4 pt-3.5 pb-2 text-[14.5px] leading-relaxed ink placeholder:opacity-50"
          rows={1}/>

        {/* controls */}
        <div className="px-2.5 pb-2.5 flex items-center gap-1.5 flex-wrap">
          {/* mode pills */}
          <div className="flex items-center gap-1 flex-wrap">
            {MODES.map(m => (
              <button key={m.id} onClick={() => setMode(m.id)} title={m.sub}
                className={"px-2.5 py-1.5 rounded-full text-[12px] font-medium transition border " +
                  (mode === m.id
                    ? "bg-cnrs-yellow text-cnrs-blue-ink border-cnrs-yellow"
                    : "bg-transparent ink/80 border-transparent hover:border-line hover:bg-paper-2")}>
                {m.label}
              </button>
            ))}
          </div>

          <span className="w-px h-5 bg-line mx-1"/>

          {/* DB picker */}
          <button onClick={openDbs}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-[12px] font-medium border border-line bg-paper hover:bg-paper-2 transition">
            <I.globe/>
            <span className="tabular-nums">{dbCount}/{dbTotal} DBs</span>
            <I.chev className="opacity-60"/>
          </button>

          {/* attach */}
          <button title="Attach file (BibTeX, PDF, DOI)"
            className="flex items-center gap-1.5 px-2 py-1.5 rounded-full text-[12px] mute hover:bg-paper-2 transition">
            <I.paperclip/>
          </button>

          <span className="flex-1"/>

          {/* model picker */}
          <button className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-[12px] mute hover:bg-paper-2 transition">
            <span className="font-mono">deepseek-v4-flash</span>
            <I.chev className="opacity-60"/>
          </button>

          {/* send */}
          {running ? (
            <button onClick={stop}
              className="w-9 h-9 rounded-full bg-cnrs-blue-ink text-cnrs-yellow flex items-center justify-center hover:opacity-90 transition">
              <I.stop/>
            </button>
          ) : (
            <button onClick={send} disabled={!input.trim()}
              className="w-9 h-9 rounded-full btn-primary flex items-center justify-center disabled:opacity-30 hover:opacity-95 transition">
              <I.send/>
            </button>
          )}
        </div>
      </div>

      <div className="mx-auto max-w-[820px] text-center text-[11px] mute mt-2 font-mono">
        Perspicacité may make mistakes. Verify against the linked sources.
      </div>
    </div>
  );
}

// ---------- Database picker (popover) ----------
function DbPicker({ dbs, setDbs, onClose }) {
  const [q, setQ] = useState("");
  const filtered = DATABASES.filter(d => d.label.toLowerCase().includes(q.toLowerCase()));
  const kbItems = filtered.filter(d => d.kind === "kb");
  const webItems = filtered.filter(d => d.kind === "web");

  const toggle = (id) => setDbs(prev => ({ ...prev, [id]: !prev[id] }));
  const allOn = (kind, on) => setDbs(prev => {
    const next = { ...prev };
    DATABASES.forEach(d => { if (d.kind === kind) next[d.id] = on; });
    return next;
  });

  return (
    <div className="fixed inset-0 z-40" onClick={onClose}>
      <div className="absolute inset-0 bg-black/20 backdrop-blur-[1px]"/>
      <div onClick={e => e.stopPropagation()}
        className="absolute left-1/2 -translate-x-1/2 bottom-32 w-[520px] rounded-2xl border border-line-strong bg-card shadow-2xl overflow-hidden">
        <div className="px-4 py-3 border-b border-line flex items-center gap-3">
          <I.globe className="opacity-70"/>
          <input autoFocus value={q} onChange={e=>setQ(e.target.value)}
            placeholder="Search databases…"
            className="flex-1 bg-transparent outline-none text-[14px] ink placeholder:opacity-50"/>
          <kbd>Esc</kbd>
        </div>
        <div className="max-h-[440px] overflow-y-auto">
          <DbGroup title="Knowledge bases" count={kbItems.length}
            onAll={() => allOn("kb", true)} onNone={() => allOn("kb", false)}>
            {kbItems.map(d => (
              <DbRow key={d.id} d={d} on={!!dbs[d.id]} onToggle={() => toggle(d.id)}/>
            ))}
          </DbGroup>
          <DbGroup title="Web sources" count={webItems.length}
            onAll={() => allOn("web", true)} onNone={() => allOn("web", false)}>
            {webItems.map(d => (
              <DbRow key={d.id} d={d} on={!!dbs[d.id]} onToggle={() => toggle(d.id)}/>
            ))}
          </DbGroup>
        </div>
        <div className="px-4 py-2.5 border-t border-line flex items-center justify-between text-[12px] mute">
          <span>{Object.values(dbs).filter(Boolean).length} selected</span>
          <button onClick={onClose} className="btn-primary px-3 py-1.5 rounded-md text-[12px] font-medium">Done</button>
        </div>
      </div>
    </div>
  );
}

function DbGroup({ title, count, onAll, onNone, children }) {
  return (
    <div className="py-2">
      <div className="px-4 py-1.5 flex items-center gap-2">
        <div className="text-[10.5px] uppercase tracking-[0.14em] font-mono faint">{title}</div>
        <div className="text-[10.5px] font-mono mute">({count})</div>
        <span className="flex-1"/>
        <button onClick={onAll} className="text-[11px] mute hover:ink">Select all</button>
        <span className="text-[11px] faint">·</span>
        <button onClick={onNone} className="text-[11px] mute hover:ink">None</button>
      </div>
      {children}
    </div>
  );
}

function DbRow({ d, on, onToggle }) {
  return (
    <button onClick={onToggle}
      className="w-full flex items-center gap-3 px-4 py-2 hover:bg-paper-2 transition text-left">
      <span className={"w-4 h-4 rounded border flex items-center justify-center transition " +
        (on ? "bg-cnrs-blue border-cnrs-blue text-cnrs-paper" : "border-line-strong bg-card")}>
        {on && <I.check/>}
      </span>
      <span className="flex-1 text-[13.5px] ink font-medium">{d.label}</span>
      <span className="text-[11px] font-mono mute tabular-nums w-[120px] text-right">
        {d.kind === "kb"
          ? <>{d.papers} papers · {d.chunks} chunks</>
          : <>{d.papers}</>
        }
      </span>
    </button>
  );
}

// ---------- Command palette ----------
function CommandPalette({ onClose, onNav, onAsk, onNewChat, setMode }) {
  const [q, setQ] = useState("");
  const all = useMemo(() => ([
    { kind: "action", id: "new",  label: "Start new chat",     hint: "↵",     run: onNewChat,                key: "new chat" },
    { kind: "action", id: "kb",   label: "Go to Knowledge bases", hint: "G K", run: () => onNav("knowledge"), key: "knowledge bases" },
    { kind: "action", id: "sv",   label: "Go to Literature survey", hint: "G S", run: () => onNav("survey"), key: "literature survey" },
    ...MODES.map(m => ({ kind: "mode", id: "mode-"+m.id, label: "Mode: " + m.label, hint: m.id.slice(0,3).toUpperCase(), run: () => setMode(m.id), key: m.label })),
    ...CONVOS.yesterday.concat(CONVOS.earlier).map(c => ({ kind: "convo", id: c.id, label: c.title, hint: c.mode, run: () => onNav("chat"), key: c.title })),
    ...SUGGESTED.map((s, i) => ({ kind: "ask", id: "ask-"+i, label: "Ask: " + s.q, hint: MODES.find(m=>m.id===s.mode)?.label, run: () => onAsk(s.q, s.mode), key: s.q })),
  ]), []);
  const filtered = q.trim()
    ? all.filter(x => x.key.toLowerCase().includes(q.toLowerCase()))
    : all.slice(0, 12);
  const [sel, setSel] = useState(0);

  const onKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel(s => Math.min(filtered.length - 1, s + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel(s => Math.max(0, s - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); filtered[sel]?.run(); }
  };

  useEffect(() => { setSel(0); }, [q]);

  return (
    <div className="fixed inset-0 z-50" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30 backdrop-blur-sm"/>
      <div onClick={e => e.stopPropagation()}
        className="absolute left-1/2 top-[16%] -translate-x-1/2 w-[640px] rounded-2xl border border-line-strong bg-card shadow-2xl overflow-hidden">
        <div className="px-4 py-3 border-b border-line flex items-center gap-3">
          <I.search className="opacity-70"/>
          <input autoFocus value={q} onChange={e=>setQ(e.target.value)} onKeyDown={onKey}
            placeholder="Search commands, chats, ask the literature…"
            className="flex-1 bg-transparent outline-none text-[15px] ink placeholder:opacity-50"/>
          <kbd>Esc</kbd>
        </div>
        <div className="max-h-[440px] overflow-y-auto py-1">
          {filtered.length === 0 && (
            <div className="px-6 py-10 text-center mute text-[13px]">
              No matches. Press <kbd>Enter</kbd> to ask: <span className="ink font-medium">"{q}"</span>
            </div>
          )}
          {filtered.map((x, i) => (
            <button key={x.id} onClick={x.run} onMouseEnter={() => setSel(i)}
              className={"w-full flex items-center gap-3 px-4 py-2.5 text-left text-[13.5px] " +
                (i === sel ? "bg-cnrs-yellow/30" : "")}>
              <span className={"text-[10px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded " +
                (x.kind==="action"  ? "bg-cnrs-blue text-cnrs-paper" :
                 x.kind==="mode"    ? "bg-cnrs-yellow text-cnrs-blue-ink" :
                 x.kind==="convo"   ? "bg-paper-2 mute" : "bg-paper-2 mute")
              }>
                {x.kind}
              </span>
              <span className="flex-1 ink truncate">{x.label}</span>
              <span className="text-[11px] font-mono faint">{x.hint}</span>
            </button>
          ))}
        </div>
        <div className="px-4 py-2 border-t border-line text-[11px] mute font-mono flex items-center gap-3">
          <span><kbd>↑</kbd> <kbd>↓</kbd> navigate</span>
          <span><kbd>↵</kbd> run</span>
          <span><kbd>Esc</kbd> close</span>
          <span className="flex-1"/>
          <span>Perspicacité · v2 POC</span>
        </div>
      </div>
    </div>
  );
}

// ---------- mount ----------
ReactDOM.createRoot(document.getElementById("app")).render(<Perspicacite/>);
