"use client";

import { useState } from "react";
import {
  DATABASES,
  describeProvider,
  providerToneClasses,
  type DatabaseDescriptor,
  type DatabaseId,
} from "@/lib/databases";

// Official favicons fetched via Google's s2 service. Two reasons we
// prefer this over hosting our own images:
//   - it's the same trick browsers use for history/bookmark UIs;
//   - it stays correct when a provider rebrands.
//
// Each request is cached by Google, so we just embed a regular <img>.
// On load failure (network, blocked CDN) we fall back to a tonal
// SVG glyph so the row never collapses to a broken-image icon.

function faviconUrl(homepage: string | undefined, sz = 32): string | null {
  if (!homepage) return null;
  try {
    const u = new URL(homepage);
    return `https://www.google.com/s2/favicons?domain=${u.hostname}&sz=${sz}`;
  } catch {
    return null;
  }
}

// Hand-drawn fallback glyphs in the brand tone — only shown when the
// favicon fails to load. Kept geometric so they look intentional.
const FallbackGlyph: Record<
  DatabaseId,
  (props: { size: number }) => React.JSX.Element
> = {
  semantic_scholar: ({ size }) => (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
      <circle cx="12" cy="12" r="2" fill="currentColor" />
      <ellipse cx="12" cy="12" rx="10" ry="4" />
      <ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(60 12 12)" />
      <ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(120 12 12)" />
    </svg>
  ),
  openalex: ({ size }) => (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
      <circle cx="9" cy="12" r="5" />
      <circle cx="15" cy="12" r="5" />
    </svg>
  ),
  pubmed: ({ size }) => (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden>
      <path d="M5 4c0 5 14 5 14 10s-14 5-14 10" />
      <path d="M19 4c0 5-14 5-14 10s14 5 14 10" />
    </svg>
  ),
  arxiv: ({ size }) => (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden>
      <path d="M5 5l14 14M19 5L5 19" />
    </svg>
  ),
  europepmc: ({ size }) => (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden>
      <path d="M9 3h6M10 3v6L4.5 18a2 2 0 0 0 1.8 3h11.4a2 2 0 0 0 1.8-3L14 9V3" />
    </svg>
  ),
  google_scholar: ({ size }) => (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden>
      <path d="M2 9l10-4 10 4-10 4z" />
      <path d="M6 11v4c0 1.5 2.7 3 6 3s6-1.5 6-3v-4" />
    </svg>
  ),
  core: ({ size }) => (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden>
      <path d="M12 2l9 5v10l-9 5-9-5V7z" />
    </svg>
  ),
  inspire: ({ size }) => (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" aria-hidden>
      <path d="M12 2l2.6 6.6 7 .6-5.3 4.6 1.6 6.9L12 17.3l-5.9 3.4 1.6-6.9L2.4 9.2l7-.6z" />
    </svg>
  ),
  pubchem: ({ size }) => (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
      <path d="M12 3l7.5 4.5v9L12 21l-7.5-4.5v-9z" />
      <circle cx="12" cy="12" r="4" />
    </svg>
  ),
  dblp_sparql: ({ size }) => (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
      <ellipse cx="12" cy="6" rx="8" ry="3" />
      <path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6" />
      <path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
    </svg>
  ),
  ieee: ({ size }) => (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="currentColor" aria-hidden>
      <rect x="3" y="3" width="18" height="18" rx="3" />
    </svg>
  ),
  springer: ({ size }) => (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M4 20c0-9 7-16 16-16 0 9-7 16-16 16z" />
      <path d="M4 20L14 10" />
    </svg>
  ),
};

export function DatabaseGlyph({
  id,
  size = 14,
  className = "",
}: {
  id: DatabaseId | string;
  size?: number;
  className?: string;
}) {
  const desc = describeProvider(id) ?? (DATABASES.find((d) => d.id === id) as DatabaseDescriptor | undefined);
  const [imgFailed, setImgFailed] = useState(false);

  if (!desc) {
    return (
      <span
        className={`inline-grid place-items-center rounded-md bg-[var(--cnrs-grey-light)] text-[9px] font-bold text-[var(--cnrs-blue)] ${className}`}
        style={{ width: size + 6, height: size + 6 }}
      >
        ?
      </span>
    );
  }

  const tone = providerToneClasses(desc.tone);
  const box = size + 6;
  // Request a slightly larger source than the box so the favicon
  // stays crisp on Retina.
  const src = faviconUrl(desc.homepage, Math.max(32, box * 2));

  if (src && !imgFailed) {
    return (
      <span
        className={[
          "inline-grid place-items-center overflow-hidden rounded-md bg-white ring-1 ring-[var(--border)]",
          className,
        ].join(" ")}
        style={{ width: box, height: box }}
        title={desc.label}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={src}
          alt=""
          width={size}
          height={size}
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setImgFailed(true)}
          style={{ width: size, height: size, objectFit: "contain" }}
        />
      </span>
    );
  }

  const Fallback = FallbackGlyph[desc.id] ?? FallbackGlyph.ieee;
  return (
    <span
      className={[
        "inline-grid place-items-center rounded-md",
        tone.bg,
        tone.text,
        className,
      ].join(" ")}
      style={{ width: box, height: box }}
      title={desc.label}
    >
      <Fallback size={size} />
    </span>
  );
}
