"""Per-KB manifest: paper content hashes + indicium schema version.

The manifest lives at `data/claim_graphs/<kb_name>/manifest.json` and is the
authoritative record of which papers have been ingested into the claim graph
and at what indicium schema version. The builder uses it to compute deltas;
the reasoning mode reads it to surface a rebuild banner when the schema
version drifts.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import asdict, dataclass, field

_DATA_DIR = pathlib.Path("data/claim_graphs")


@dataclass
class Manifest:
    kb_name: str
    paper_hashes: dict[str, str] = field(default_factory=dict)
    indicium_schema_version: str = "0.0.0+unknown"
    builder_version: str = "1"
    last_build_iso: str | None = None


def manifest_path(kb_name: str) -> pathlib.Path:
    return _DATA_DIR / kb_name / "manifest.json"


def read_manifest(kb_name: str) -> Manifest:
    p = manifest_path(kb_name)
    if not p.exists():
        return Manifest(kb_name=kb_name)
    raw = json.loads(p.read_text())
    return Manifest(
        kb_name=raw.get("kb_name", kb_name),
        paper_hashes=raw.get("paper_hashes", {}),
        indicium_schema_version=raw.get("indicium_schema_version", "0.0.0+unknown"),
        builder_version=raw.get("builder_version", "1"),
        last_build_iso=raw.get("last_build_iso"),
    )


def write_manifest(manifest: Manifest) -> None:
    p = manifest_path(manifest.kb_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(manifest), indent=2))
