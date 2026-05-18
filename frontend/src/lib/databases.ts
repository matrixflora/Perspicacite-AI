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
  blurb?: string;
};

export const DATABASES: DatabaseDescriptor[] = [
  { id: "semantic_scholar", label: "Semantic Scholar", blurb: "broad CS + biomed" },
  { id: "openalex", label: "OpenAlex", blurb: "open scholarly graph" },
  { id: "pubmed", label: "PubMed", blurb: "biomedical literature" },
  { id: "arxiv", label: "arXiv", blurb: "preprints, physics + CS" },
  { id: "europepmc", label: "Europe PMC", blurb: "life sciences" },
  { id: "core", label: "CORE", blurb: "open-access aggregator" },
  { id: "inspire", label: "INSPIRE-HEP", blurb: "high-energy physics" },
  { id: "pubchem", label: "PubChem", blurb: "chemistry" },
  { id: "dblp_sparql", label: "DBLP-SPARQL", blurb: "computer science bib" },
  { id: "google_scholar", label: "Google Scholar", blurb: "broad coverage (scraped)" },
  { id: "ieee", label: "IEEE", blurb: "engineering" },
  { id: "springer", label: "Springer", blurb: "Springer Nature" },
];

// Defaults match the original GUI selection.
export const DEFAULT_DATABASES: DatabaseId[] = ["pubmed", "google_scholar"];
