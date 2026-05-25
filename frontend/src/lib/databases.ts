// The 12 academic databases the Perspicacité aggregator can query.
// Values must match the backend's provider names exactly.

export type DatabaseId =
  | "semantic_scholar"
  | "openalex"
  | "pubmed"
  | "arxiv"
  | "europepmc"
  | "core"
  | "inspire"
  | "pubchem"
  | "dblp_sparql"
  | "google_scholar"
  | "ieee"
  | "springer";

export type DatabaseDescriptor = {
  id: DatabaseId;
  label: string;
  short: string;       // 2–3 char monogram for tight pills
  blurb?: string;
  homepage?: string;
  // Tailwind class tokens for the brand chip.
  tone: "blue" | "yellow" | "violet" | "green" | "orange" | "grey" | "blue-pale";
  // The six "priority" providers are shown in the always-visible row of
  // the database picker; the rest live behind a "More databases" expander.
  // Order in this array is the on-screen order.
  priority?: boolean;
};

// Order here drives both the picker layout and the "More databases" expander.
// Priority providers (the six the user asked for) come first.
export const DATABASES: DatabaseDescriptor[] = [
  {
    id: "semantic_scholar",
    label: "Semantic Scholar",
    short: "S²",
    blurb: "broad CS + biomed",
    homepage: "https://www.semanticscholar.org",
    tone: "blue",
    priority: true,
  },
  {
    id: "openalex",
    label: "OpenAlex",
    short: "OA",
    blurb: "open scholarly graph",
    homepage: "https://openalex.org",
    tone: "violet",
    priority: true,
  },
  {
    id: "pubmed",
    label: "PubMed",
    short: "PM",
    blurb: "biomedical literature",
    homepage: "https://pubmed.ncbi.nlm.nih.gov",
    tone: "green",
    priority: true,
  },
  {
    id: "arxiv",
    label: "arXiv",
    short: "aX",
    blurb: "preprints, physics + CS",
    homepage: "https://arxiv.org",
    tone: "orange",
    priority: true,
  },
  {
    id: "europepmc",
    label: "Europe PMC",
    short: "EM",
    blurb: "life sciences",
    homepage: "https://europepmc.org",
    tone: "green",
    priority: true,
  },
  {
    id: "google_scholar",
    label: "Google Scholar",
    short: "GS",
    blurb: "broad coverage (scraped)",
    homepage: "https://scholar.google.com",
    tone: "yellow",
    priority: true,
  },
  {
    id: "core",
    label: "CORE",
    short: "Co",
    blurb: "open-access aggregator",
    homepage: "https://core.ac.uk",
    tone: "blue-pale",
  },
  {
    id: "inspire",
    label: "INSPIRE-HEP",
    short: "iH",
    blurb: "high-energy physics",
    homepage: "https://inspirehep.net",
    tone: "violet",
  },
  {
    id: "pubchem",
    label: "PubChem",
    short: "PC",
    blurb: "chemistry",
    homepage: "https://pubchem.ncbi.nlm.nih.gov",
    tone: "blue-pale",
  },
  {
    id: "dblp_sparql",
    label: "DBLP-SPARQL",
    short: "dB",
    blurb: "computer science bib",
    homepage: "https://dblp.org",
    tone: "grey",
  },
  {
    id: "ieee",
    label: "IEEE",
    short: "iE",
    blurb: "engineering",
    homepage: "https://ieeexplore.ieee.org",
    tone: "blue",
  },
  {
    id: "springer",
    label: "Springer",
    short: "Sp",
    blurb: "Springer Nature",
    homepage: "https://link.springer.com",
    tone: "orange",
  },
];

export const PRIORITY_DATABASES: DatabaseDescriptor[] = DATABASES.filter(
  (d) => d.priority,
);
export const OTHER_DATABASES: DatabaseDescriptor[] = DATABASES.filter(
  (d) => !d.priority,
);

const BY_ID = new Map<string, DatabaseDescriptor>(
  DATABASES.map((d) => [d.id, d]),
);

// Tolerant lookup — accepts the canonical id, the label, or common aliases.
export function describeProvider(name?: string): DatabaseDescriptor | undefined {
  if (!name) return undefined;
  const key = name.toLowerCase().replace(/[\s-]/g, "_");
  return BY_ID.get(key) ?? BY_ID.get(name as DatabaseId);
}

export function providerToneClasses(
  tone: DatabaseDescriptor["tone"],
): { bg: string; text: string; border: string } {
  switch (tone) {
    case "blue":
      return { bg: "bg-[var(--cnrs-blue)]", text: "text-white", border: "border-[var(--cnrs-blue)]" };
    case "yellow":
      return { bg: "bg-[var(--cnrs-yellow)]", text: "text-[var(--cnrs-blue)]", border: "border-[var(--cnrs-yellow)]" };
    case "violet":
      return { bg: "bg-[var(--cnrs-violet)]", text: "text-white", border: "border-[var(--cnrs-violet)]" };
    case "green":
      return { bg: "bg-[var(--cnrs-green)]", text: "text-[var(--cnrs-blue)]", border: "border-[var(--cnrs-green)]" };
    case "orange":
      return { bg: "bg-[var(--cnrs-orange)]", text: "text-[var(--cnrs-blue)]", border: "border-[var(--cnrs-orange)]" };
    case "blue-pale":
      return { bg: "bg-[var(--cnrs-blue-pale)]", text: "text-[var(--cnrs-blue)]", border: "border-[var(--cnrs-blue-pale)]" };
    case "grey":
      return { bg: "bg-[var(--cnrs-grey-light)]", text: "text-[var(--cnrs-blue)]", border: "border-[var(--cnrs-grey)]" };
  }
}

// Default selection: PubMed only. Picked as the single sensible default
// because (1) it has no API key requirement, (2) it always builds in
// the aggregator when pdf_download.unpaywall_email is set, and (3) the
// alternative — all six priority DBs — sent slow scrapers (Google
// Scholar, etc.) on every query. The user can add more from the picker.
export const DEFAULT_DATABASES: DatabaseId[] = ["pubmed"];
