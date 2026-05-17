"""Bundle manifest parser for Perspicacite skill-bundle format.

A skill bundle is a git repository (or subdirectory) with an optional
``bundle.yml`` manifest.  When ``bundle.yml`` is absent the directory
is still usable — the manifest falls back to README-only mode.

Bundle manifest YAML structure (all keys optional except ``name``):

    name: scrna-qc
    description: "Single-cell RNA-seq QC pipeline"
    version: "1.0.0"
    domain: genomics

    papers:
      - doi: 10.1234/foo
      - arxiv: "2204.12345"
      - pmc: "PMC9123456"

    content:
      include:
        - "**/*.py"
        - "**/*.md"
        - "**/*.ipynb"
      exclude:
        - ".git/**"
        - "__pycache__/**"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path  # noqa: TC003

DEFAULT_INCLUDE_GLOBS: list[str] = ["**/*.py", "**/*.md", "**/*.ipynb", "**/*.rst"]
DEFAULT_EXCLUDE_GLOBS: list[str] = [
    ".git/**", "__pycache__/**", "*.pyc", "node_modules/**",
    ".venv/**", "venv/**", "*.egg-info/**",
]


@dataclass
class ContentConfig:
    include: list[str] = field(default_factory=lambda: list(DEFAULT_INCLUDE_GLOBS))
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_GLOBS))


@dataclass
class LinkBag:
    """Typed references extracted from free text."""
    dois: list[str] = field(default_factory=list)
    arxiv_ids: list[str] = field(default_factory=list)
    pmc_ids: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)

    def paper_refs(self) -> list[tuple[str, str]]:
        """Return [(type, id), ...] for all paper references."""
        return (
            [("doi", d) for d in self.dois]
            + [("arxiv", a) for a in self.arxiv_ids]
            + [("pmc", p) for p in self.pmc_ids]
        )


@dataclass
class BundleManifest:
    name: str
    description: str | None = None
    version: str | None = None
    domain: str | None = None
    papers: list[dict] = field(default_factory=list)
    content: ContentConfig = field(default_factory=ContentConfig)
    readme_only: bool = False
    readme_text: str | None = None

    def collect_paper_refs(self) -> list[tuple[str, str]]:
        """Extract (type, id) tuples from the papers list."""
        refs: list[tuple[str, str]] = []
        for entry in self.papers:
            if isinstance(entry, dict):
                for key in ("doi", "arxiv", "pmc"):
                    if key in entry:
                        refs.append((key, str(entry[key])))
        return refs

    @classmethod
    def parse(cls, path: Path) -> BundleManifest:
        """Parse a bundle.yml file."""
        import yaml  # pyyaml - already a dep
        data = yaml.safe_load(path.read_text()) or {}
        name = str(data.get("name", path.parent.name))
        papers = data.get("papers") or []

        raw_content = data.get("content") or {}
        content = ContentConfig(
            include=raw_content.get("include", list(DEFAULT_INCLUDE_GLOBS)),
            exclude=raw_content.get("exclude", list(DEFAULT_EXCLUDE_GLOBS)),
        )

        return cls(
            name=name,
            description=data.get("description"),
            version=data.get("version"),
            domain=data.get("domain"),
            papers=papers if isinstance(papers, list) else [],
            content=content,
        )

    @classmethod
    def from_directory(cls, directory: Path) -> BundleManifest:
        """Load manifest from a directory. Falls back to README-only mode."""
        yaml_path = directory / "bundle.yml"
        if yaml_path.exists():
            return cls.parse(yaml_path)

        readme_text: str | None = None
        for readme_name in ("README.md", "README.rst", "README.txt", "README"):
            readme_path = directory / readme_name
            if readme_path.exists():
                readme_text = readme_path.read_text(errors="replace")
                break

        return cls(
            name=directory.name,
            readme_only=True,
            readme_text=readme_text,
        )


# ── Link extraction from free text ───────────────────────────────────────────

_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\]\)>\"']+")
_ARXIV_RE = re.compile(r"\barxiv[:\s]+(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)
_PMC_RE = re.compile(r"\bPMC(\d{6,8})\b")
_URL_RE = re.compile(r"https?://[^\s\]\)>\"']+")


def extract_links_from_text(text: str) -> LinkBag:
    """Extract typed references from free text (README, docs)."""
    bag = LinkBag()
    if not text:
        return bag
    bag.dois = list(dict.fromkeys(_DOI_RE.findall(text)))
    bag.arxiv_ids = [m.group(1) for m in _ARXIV_RE.finditer(text)]
    bag.pmc_ids = [f"PMC{m.group(1)}" for m in _PMC_RE.finditer(text)]
    # URLs that are not DOI/arXiv/PMC links
    all_urls = _URL_RE.findall(text)
    doi_urls = {f"https://doi.org/{d}" for d in bag.dois}
    bag.urls = [u for u in all_urls if u not in doi_urls]
    return bag
