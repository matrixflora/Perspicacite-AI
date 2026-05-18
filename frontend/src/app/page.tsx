import Image from "next/image";
import { Header } from "@/components/Header";
import { ChatPanel } from "@/components/ChatPanel";

export default function Home() {
  return (
    <main className="relative min-h-screen overflow-hidden">
      <div className="cnrs-halo cnrs-halo--hero" aria-hidden />

      <Header />

      <section className="relative z-10 mx-auto w-full max-w-5xl px-4 pt-10 pb-4 md:px-6">
        <div className="flex items-end justify-between gap-4">
          <div>
            <p className="font-mono text-xs uppercase tracking-widest text-[var(--text-muted)]">
              POC · CNRS chart
            </p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight text-[var(--cnrs-blue)] md:text-4xl">
              Ask the literature.
            </h1>
            <p className="mt-2 max-w-xl text-[15px] leading-relaxed text-[var(--text-muted)]">
              Six retrieval modes, real-time streaming, traceable sources.
              Pick a mode and start.
            </p>
          </div>
        </div>
      </section>

      <ChatPanel />

      <footer className="relative z-10 mt-8 border-t border-[var(--border)] bg-[var(--surface)]/60 py-6 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-6 text-xs text-[var(--text-muted)]">
          <Image
            src="/brand/blocmarque/CNRS-RF-Footer.svg"
            alt="République française – CNRS"
            width={220}
            height={60}
            className="h-10 w-auto opacity-90"
          />
          <p>
            Perspicacité v2 · POC · ICN UMR 7272 · 3iA Côte d&apos;Azur
          </p>
        </div>
      </footer>
    </main>
  );
}
