import Image from "next/image";

export function Header() {
  return (
    <header className="relative z-10 border-b border-[var(--border)] bg-[var(--surface)]/80 backdrop-blur supports-[backdrop-filter]:bg-[var(--surface)]/60">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-3">
        <div className="flex items-center gap-3">
          <span
            className="grid h-9 w-9 place-items-center rounded-full"
            style={{ background: "var(--cnrs-yellow)" }}
            aria-hidden
          />
          <div className="leading-tight">
            <h1 className="font-semibold tracking-tight text-[var(--cnrs-blue)]">
              Perspicacité
            </h1>
            <p className="text-xs text-[var(--text-muted)]">
              AI for scientific literature
            </p>
          </div>
        </div>
        <nav className="hidden items-center gap-6 text-sm text-[var(--text-muted)] md:flex">
          <a className="hover:text-[var(--cnrs-blue)]" href="#chat">
            Chat
          </a>
          <a className="hover:text-[var(--cnrs-blue)]" href="#kb">
            Knowledge bases
          </a>
          <a className="hover:text-[var(--cnrs-blue)]" href="#about">
            About
          </a>
        </nav>
        <div className="flex items-center gap-3">
          <Image
            src="/brand/logos/LOGO_CNRS_BLEU.png"
            alt="CNRS"
            width={42}
            height={42}
            className="h-8 w-auto"
          />
          <Image
            src="/brand/logos/unica_logo.png"
            alt="Université Côte d'Azur"
            width={120}
            height={32}
            className="h-7 w-auto opacity-80"
          />
        </div>
      </div>
    </header>
  );
}
