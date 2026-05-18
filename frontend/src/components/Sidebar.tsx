"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import Image from "next/image";
import { ThemeToggle } from "./ThemeToggle";
import { AboutButton } from "./AboutModal";
import { conversations as convApi, type Conversation } from "@/lib/api";
import { groupByRecency } from "@/lib/groupByRecency";

const SECTION_LINKS = [
  { href: "/kb", label: "Knowledge bases", icon: "📚" },
  { href: "/survey", label: "Literature survey", icon: "📊" },
  { href: "/settings", label: "Settings", icon: "⚙" },
] as const;

const HISTORY_VISIBLE_LIMIT = 12;

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();

  const [items, setItems] = useState<Conversation[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [filter, setFilter] = useState("");

  const refresh = useCallback(async () => {
    try {
      const { conversations } = await convApi.list();
      setItems(conversations);
    } catch {
      // Sidebar is non-critical; fail silently.
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, pathname]);

  const isActive = (href: string) => {
    if (href === "/") return pathname === "/" || pathname.startsWith("/chat");
    return pathname === href || pathname.startsWith(`${href}/`);
  };

  const filteredItems = useMemo(() => {
    if (!filter.trim()) return items.slice(0, HISTORY_VISIBLE_LIMIT);
    const q = filter.trim().toLowerCase();
    return items
      .filter((c) => (c.title ?? "").toLowerCase().includes(q))
      .slice(0, HISTORY_VISIBLE_LIMIT);
  }, [items, filter]);

  const groups = useMemo(
    () => groupByRecency(filteredItems, (c) => c.updated_at ?? c.created_at),
    [filteredItems],
  );

  const activeConvId = pathname.startsWith("/chat/")
    ? decodeURIComponent(pathname.split("/chat/")[1] ?? "")
    : null;

  const newChat = () => {
    router.push("/");
  };

  return (
    <aside className="sticky top-0 z-20 hidden h-screen w-[272px] shrink-0 flex-col border-r border-[var(--border)] bg-[var(--surface)] md:flex">
      {/* Brand + new chat */}
      <div className="px-3 pt-4 pb-3">
        <div className="mb-3 flex items-center gap-2.5 px-2">
          <span
            className="grid h-7 w-7 place-items-center rounded-full"
            style={{ background: "var(--cnrs-yellow)" }}
            aria-hidden
          />
          <div className="leading-tight">
            <p className="text-[13px] font-semibold text-[var(--cnrs-blue)]">
              Perspicacité
            </p>
            <p className="text-[10px] text-[var(--text-muted)]">v2 · POC</p>
          </div>
        </div>

        <button
          type="button"
          onClick={newChat}
          className="flex w-full items-center justify-center gap-2 rounded-[var(--radius-md)] bg-[var(--cnrs-blue)] px-3 py-2.5 text-sm font-semibold text-white transition hover:bg-[#003a6a]"
        >
          <span aria-hidden>+</span>
          <span>New chat</span>
        </button>

        {/* Keyboard hint for the command palette. Pressing ⌘K opens the
            global quick-switcher mounted in src/app/layout.tsx. */}
        <button
          type="button"
          onClick={() =>
            window.dispatchEvent(
              new KeyboardEvent("keydown", { key: "k", metaKey: true }),
            )
          }
          className="mt-2 flex w-full items-center justify-between rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-soft)] px-3 py-1.5 text-[11px] text-[var(--text-muted)] hover:border-[var(--cnrs-blue)] hover:text-[var(--cnrs-blue)]"
          title="Open command palette"
        >
          <span>Quick switcher…</span>
          <kbd className="rounded border border-[var(--border)] px-1 font-mono text-[10px]">
            ⌘K
          </kbd>
        </button>
      </div>

      {/* Search */}
      <div className="px-3">
        <input
          type="search"
          placeholder="Search chats…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-full rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-soft)] px-3 py-1.5 text-xs text-[var(--text-body)] outline-none placeholder:text-[var(--text-muted)] focus:border-[var(--cnrs-blue)]"
        />
      </div>

      {/* Chat history */}
      <nav className="mt-2 flex-1 overflow-y-auto px-2 py-1">
        {!loaded ? (
          <p className="px-2 py-3 text-xs text-[var(--text-muted)]">Loading…</p>
        ) : groups.length === 0 ? (
          <p className="px-2 py-3 text-xs text-[var(--text-muted)]">
            {filter ? "No matching chats." : "Conversations will appear here once you start chatting."}
          </p>
        ) : (
          groups.map((group) => (
            <div key={group.label} className="mb-2">
              <p className="px-2 py-1 text-[10px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                {group.label}
              </p>
              <ul className="flex flex-col gap-px">
                {group.items.map((c) => {
                  const active = activeConvId === c.id;
                  return (
                    <li key={c.id}>
                      <Link
                        href={`/chat/${encodeURIComponent(c.id)}`}
                        title={c.title ?? "Untitled"}
                        className={[
                          "flex items-center gap-2 truncate rounded-[var(--radius-sm)] px-2 py-1.5 text-[13px] transition",
                          active
                            ? "bg-[var(--cnrs-yellow)] text-[var(--cnrs-blue)] font-medium"
                            : "text-[var(--text-body)] hover:bg-[var(--cnrs-grey-light)]",
                        ].join(" ")}
                      >
                        <span className="truncate">
                          {c.title?.trim() || "Untitled chat"}
                        </span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))
        )}

        {items.length > HISTORY_VISIBLE_LIMIT && !filter && (
          <Link
            href="/conversations"
            className="mt-2 block px-2 py-1.5 text-[11px] font-medium text-[var(--cnrs-blue)] hover:underline"
          >
            See all {items.length} conversations →
          </Link>
        )}
      </nav>

      {/* Section nav */}
      <div className="border-t border-[var(--border)] px-2 py-2">
        {SECTION_LINKS.map((item) => {
          const active = isActive(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={[
                "flex items-center gap-3 rounded-[var(--radius-sm)] px-2 py-1.5 text-[13px] transition",
                active
                  ? "bg-[var(--cnrs-yellow)] text-[var(--cnrs-blue)] font-medium"
                  : "text-[var(--text-body)] hover:bg-[var(--cnrs-grey-light)]",
              ].join(" ")}
              aria-current={active ? "page" : undefined}
            >
              <span aria-hidden className="text-base leading-none">
                {item.icon}
              </span>
              <span>{item.label}</span>
            </Link>
          );
        })}
      </div>

      {/* Bottom toolbar */}
      <div className="border-t border-[var(--border)] px-3 py-3">
        <div className="flex items-center gap-2">
          <div className="flex-1">
            <ThemeToggle />
          </div>
          <AboutButton />
        </div>
        <div className="mt-3 flex items-center gap-2 opacity-80">
          <Image
            src="/brand/logos/LOGO_CNRS_BLEU.png"
            alt="CNRS"
            width={28}
            height={28}
            className="h-6 w-auto"
          />
          <Image
            src="/brand/logos/unica_logo.png"
            alt="Université Côte d'Azur"
            width={90}
            height={22}
            className="h-5 w-auto"
          />
        </div>
        <p className="mt-1.5 text-[9px] leading-tight text-[var(--text-muted)]">
          ICN UMR 7272 · 3iA Côte d&apos;Azur
        </p>
      </div>
    </aside>
  );
}
