from __future__ import annotations

from dataclasses import dataclass
from typing import Any

REQUISITES = {"require", "require_in", "watch", "watch_in", "onchanges", "onchanges_in", "onfail", "onfail_in", "prereq", "prereq_in", "use", "use_in", "listen", "listen_in"}


@dataclass
class Edge:
    source: str
    destination: str
    kind: str
    sls: str
    resolved: bool
    reverse: bool = False
    minion: str = "local"


def dependencies(low: Any) -> list[Edge]:
    """Read relationships from Salt's compiled low chunks; unresolved is a hint only."""
    if isinstance(low, dict):
        all_edges: list[Edge] = []
        for minion, chunks in low.items():
            for edge in dependencies(chunks):
                edge.minion = str(minion)
                all_edges.append(edge)
        return all_edges
    if not isinstance(low, list):
        return []
    known = {str(c.get("__id__")) for c in low if isinstance(c, dict)}
    edges: list[Edge] = []
    for chunk in low:
        if not isinstance(chunk, dict):
            continue
        state_id = str(chunk.get("__id__", ""))
        for kind in REQUISITES:
            for req in chunk.get(kind, []) if isinstance(chunk.get(kind, []), list) else []:
                if not isinstance(req, dict):
                    continue
                for _, target in req.items():
                    target = str(target)
                    reverse = kind.endswith("_in")
                    source, dest = (state_id, target) if reverse else (target, state_id)
                    edges.append(Edge(source, dest, kind, str(chunk.get("__sls__", "")), target in known, reverse))
    return edges


def tree_lines(low: Any) -> list[str]:
    edges = dependencies(low)
    if not edges:
        return ["No compiled requisites found."]
    return [f"[{e.minion}] {e.source} ──{e.kind}──▶ {e.destination}  [{e.sls or '?'}]" + ("  (unresolved in this low data)" if not e.resolved else "") for e in edges]
