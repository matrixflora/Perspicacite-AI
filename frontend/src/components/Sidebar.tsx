"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import Image from "next/image";
import { ThemeToggle } from "./ThemeToggle";

const NAV = [
  { href: "/", label: "Chat", icon: "💬" },
  { href: "/kb", label: "Knowledge bases", icon: "📚" },
  { href: "/conversations", label: "Conversations", icon: "🗂" },
  { href: "/survey", label: "Literature survey", icon: "📊" },
] as const;

export function Sidebar() {
  const pathname = usePathname();

  const isActive = (href: string) => {
    if (href === "/") return pathname === "/";
    return pathname === href || pathname.startsWith(`${href}/`);
  };

  return (
    <aside className="sticky top-0 z-20 hidden h-screen w-60 shrink-0 flex-col border-r border-[var(--border)] bg-[var(--surface)] md:flex">
      <div className="flex items-center gap-3 border-b border-[var(--border)] px-4 py-4">
        <span
          className="grid h-9 w-9 place-items-center rounded-full"
          style={{ background: "var(--cnrs-yellow)" }}
          aria-hidden
        />
        <div className="leading-tight">
          <p className="text-sm font-semibold text-[var(--cnrs-blue)]">
            Perspicacité
          </p>
          <p className="text-[11px] text-[var(--text-muted)]">v2 · POC</p>
        </div>
      </div>

      <nav className="flex flex-1 flex-col gap-1 px-2 py-3">
        {NAV.map((item) => {
          const active = isActive(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={[
                "flex items-center gap-3 rounded-[var(--radius-md)] px-3 py-2 text-sm font-medium transition",
                active
                  ? "bg-[var(--cnrs-yellow)] text-[var(--cnrs-blue)]"
                  : "text-[var(--text-body)] hover:bg-[var(--cnrs-grey-light)]",
              ].join(" ")}
              aria-current={active ? "page" : undefined}
            >
              <span aria-hidden>{item.icon}</span>
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-[var(--border)] px-4 py-4">
        <div className="mb-3">
          <ThemeToggle />
        </div>
        <div className="flex items-center gap-2">
          <Image
            src="/brand/logos/LOGO_CNRS_BLEU.png"
            alt="CNRS"
            width={32}
            height={32}
            className="h-7 w-auto"
          />
          <Image
            src="/brand/logos/unica_logo.png"
            alt="Université Côte d'Azur"
            width={100}
            height={26}
            className="h-6 w-auto opacity-80"
          />
        </div>
        <p className="mt-2 text-[10px] leading-tight text-[var(--text-muted)]">
          ICN UMR 7272 · 3iA Côte d&apos;Azur
        </p>
      </div>
    </aside>
  );
}
