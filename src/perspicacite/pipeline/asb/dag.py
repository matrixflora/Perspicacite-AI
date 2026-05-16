"""Workflow DAG reader (workflow_dag.json).

Supports two on-disk edge formats:

  2026-05-15: edges as [[src, dst], ...]
  2026-05-16+: edges as [{"from": src, "port": label, "to": dst}, ...]

The internal Edge record carries an optional ``port`` label preserving
the data-flow name between tasks. v1 does not index edges as chunks —
the DAG is bundle-level metadata, attached to the KB description and
surfaced in auto-KB-routing responses.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Edge:
    """One DAG edge. ``port`` is None for pre-2026-05-16 edges."""
    src: str
    dst: str
    port: str | None = None


@dataclass
class WorkflowDag:
    nodes: list[str] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def upstream(self, task_id: str) -> list[str]:
        return [e.src for e in self.edges if e.dst == task_id]

    def downstream(self, task_id: str) -> list[str]:
        return [e.dst for e in self.edges if e.src == task_id]

    def edge_port(self, src: str, dst: str) -> str | None:
        """Return the port label for an edge, or None if the edge
        doesn't exist or the source was a pre-2026-05-16 pair."""
        for e in self.edges:
            if e.src == src and e.dst == dst:
                return e.port
        return None

    def to_dict(self) -> dict:
        """Serialise back to JSON-compatible dict. Edges always come
        out in the new dict-with-port form regardless of source format."""
        return {
            "nodes": list(self.nodes),
            "edges": [
                {"from": e.src, "to": e.dst, "port": e.port}
                for e in self.edges
            ],
        }


def load_workflow_dag(run_dir: Path | str) -> WorkflowDag:
    """Return the workflow DAG. Missing or invalid file -> empty DAG."""
    p = Path(run_dir) / "workflow_dag.json"
    if not p.exists():
        return WorkflowDag()
    try:
        raw = json.loads(p.read_text())
    except json.JSONDecodeError:
        return WorkflowDag()
    nodes = list(raw.get("nodes", []))
    edges_raw = raw.get("edges", [])
    edges: list[Edge] = []
    for e in edges_raw:
        if isinstance(e, dict):
            # 2026-05-16+ form
            src = e.get("from")
            dst = e.get("to")
            port = e.get("port")
            if src and dst:
                edges.append(Edge(src=src, dst=dst, port=port))
        elif isinstance(e, (list, tuple)) and len(e) == 2:
            # 2026-05-15 form
            edges.append(Edge(src=e[0], dst=e[1], port=None))
    return WorkflowDag(nodes=nodes, edges=edges)
