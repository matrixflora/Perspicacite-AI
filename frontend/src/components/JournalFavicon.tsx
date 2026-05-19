"use client";

import { useState } from "react";

// Best-effort journal / publisher logo. Reuses Google's favicon
// service against a domain we infer from the journal name.
//
// We can't go from journal name → publisher domain perfectly without
// a CrossRef lookup, so we keep a short alias map for the names we
// see most often and fall through to an italic "J" badge otherwise.
// The badge size matches DatabaseGlyph so they line up.

const ALIASES: Array<{ test: RegExp; domain: string }> = [
  { test: /\bnature\b/i, domain: "nature.com" },
  { test: /\bcell\b/i, domain: "cell.com" },
  { test: /\bsciencedirect\b|\belsevier\b/i, domain: "sciencedirect.com" },
  { test: /\bspringer\b|biomed central|^bmc/i, domain: "springer.com" },
  { test: /\bwiley\b/i, domain: "onlinelibrary.wiley.com" },
  { test: /\btaylor.*francis\b|tandfonline/i, domain: "tandfonline.com" },
  { test: /\bplos\b|public library of science/i, domain: "plos.org" },
  { test: /\bfrontiers\b/i, domain: "frontiersin.org" },
  { test: /\bbmj\b|british medical journal/i, domain: "bmj.com" },
  { test: /\bnejm\b|new england journal/i, domain: "nejm.org" },
  { test: /\boxford\b/i, domain: "academic.oup.com" },
  { test: /\bcambridge\b/i, domain: "cambridge.org" },
  { test: /\bsage\b/i, domain: "journals.sagepub.com" },
  { test: /\bacs\b|american chemical society/i, domain: "pubs.acs.org" },
  { test: /\brsc\b|royal society of chemistry/i, domain: "pubs.rsc.org" },
  { test: /\biop\b|institute of physics/i, domain: "iopscience.iop.org" },
  { test: /\bieee\b/i, domain: "ieeexplore.ieee.org" },
  { test: /\bacm\b/i, domain: "dl.acm.org" },
  { test: /\bbiorxiv\b/i, domain: "biorxiv.org" },
  { test: /\bmedrxiv\b/i, domain: "medrxiv.org" },
  { test: /\barxiv\b/i, domain: "arxiv.org" },
  { test: /\bpnas\b|proceedings of the national academy/i, domain: "pnas.org" },
  { test: /\baaas\b|\bscience\b/i, domain: "science.org" },
  { test: /\blancet\b/i, domain: "thelancet.com" },
  { test: /\bjama\b/i, domain: "jamanetwork.com" },
];

export function JournalFavicon({
  name,
  size = 12,
}: {
  name: string;
  size?: number;
}) {
  const [failed, setFailed] = useState(false);
  const box = size + 4;
  const alias = ALIASES.find((a) => a.test.test(name));

  if (!alias || failed) {
    return (
      <span
        className="inline-grid place-items-center rounded-sm bg-[var(--cnrs-grey-light)] text-[var(--cnrs-blue)]"
        style={{ width: box, height: box, fontSize: size - 2 }}
        title={name}
      >
        <span className="italic font-serif">J</span>
      </span>
    );
  }

  const src = `https://www.google.com/s2/favicons?domain=${alias.domain}&sz=${Math.max(32, box * 2)}`;
  return (
    <span
      className="inline-grid place-items-center overflow-hidden rounded-sm bg-white ring-1 ring-[var(--border)]"
      style={{ width: box, height: box }}
      title={name}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={src}
        alt=""
        width={size}
        height={size}
        loading="lazy"
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
        style={{ width: size, height: size, objectFit: "contain" }}
      />
    </span>
  );
}
