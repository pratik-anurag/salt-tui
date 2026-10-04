from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from salt_tui.sls.dependencies import REQUISITES


@dataclass(frozen=True)
class StateNode:
    key: str
    state_id: str
    module: str
    function: str
    sls: str
    name: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class GraphEdge:
    source: str
    destination: str
    kind: str
    reverse: bool
    resolved: bool
    sls: str


@dataclass(frozen=True)
class GraphDiagnostic:
    kind: str
    detail: str
    nodes: tuple[str, ...]


class StateGraph:
    """Navigable graph from Salt's compiled low chunks; never validates Salt states."""
    def __init__(self, nodes: dict[str, StateNode], edges: list[GraphEdge]):
        self.nodes = nodes
        self.edges = edges
        self._out: dict[str, list[GraphEdge]] = defaultdict(list)
        self._in: dict[str, list[GraphEdge]] = defaultdict(list)
        for edge in edges:
            self._out[edge.source].append(edge)
            self._in[edge.destination].append(edge)

    @classmethod
    def from_low(cls, low: Any, minion: str | None = None) -> StateGraph:
        if isinstance(low, dict):
            if minion and minion in low:
                low = low[minion]
            else:
                low = next((v for v in low.values() if isinstance(v, list)), [])
        if not isinstance(low, list):
            return cls({}, [])
        nodes: dict[str, StateNode] = {}
        ids: dict[str, list[str]] = defaultdict(list)
        chunks = [c for c in low if isinstance(c, dict)]
        for index, chunk in enumerate(chunks):
            state_id = str(chunk.get("__id__", ""))
            key = f"{state_id}|{chunk.get('state', '')}.{chunk.get('fun', '')}|{index}"
            nodes[key] = StateNode(key, state_id, str(chunk.get("state", "")), str(chunk.get("fun", "")),
                                   str(chunk.get("__sls__", "")), str(chunk.get("name", "")), chunk)
            ids[state_id].append(key)
        edges: list[GraphEdge] = []
        for node in nodes.values():
            for kind in REQUISITES:
                requests = node.raw.get(kind, [])
                if not isinstance(requests, list):
                    continue
                for request in requests:
                    if not isinstance(request, dict):
                        continue
                    for module, target in request.items():
                        if module == "sls":
                            candidates = [key for key, other in nodes.items() if other.sls == str(target)]
                        elif module == "id":
                            candidates = ids.get(str(target), [])
                        else:
                            candidates = [key for key in ids.get(str(target), []) if nodes[key].module == str(module)]
                        target_key = candidates[0] if candidates else f"unresolved:{module}:{target}"
                        reverse = kind.endswith("_in")
                        source, dest = (node.key, target_key) if reverse else (target_key, node.key)
                        edges.append(GraphEdge(source, dest, kind, reverse, bool(candidates), node.sls))
        return cls(nodes, edges)

    def upstream(self, key: str, kinds: set[str] | None = None) -> list[GraphEdge]:
        return [e for e in self._in.get(key, []) if kinds is None or e.kind in kinds]

    def downstream(self, key: str, kinds: set[str] | None = None) -> list[GraphEdge]:
        return [e for e in self._out.get(key, []) if kinds is None or e.kind in kinds]

    def shortest_path(self, source: str, destination: str, kinds: set[str] | None = None) -> list[str]:
        if source == destination and source in self.nodes:
            return [source]
        visited = {source}
        queue = deque([(source, [source])])
        while queue:
            current, path = queue.popleft()
            for edge in self.downstream(current, kinds):
                if edge.destination == destination:
                    return path + [destination]
                if edge.destination in self.nodes and edge.destination not in visited:
                    visited.add(edge.destination)
                    queue.append((edge.destination, path + [edge.destination]))
        return []

    def filtered(self, *, search: str = "", module: str = "", sls: str = "", kinds: set[str] | None = None) -> StateGraph:
        keep = {key for key, node in self.nodes.items() if search.lower() in (node.state_id + node.name).lower()
                and module.lower() in node.module.lower() and sls.lower() in node.sls.lower()}
        edges = [e for e in self.edges if e.source in keep and e.destination in keep and (kinds is None or e.kind in kinds)]
        return StateGraph({key: self.nodes[key] for key in keep}, edges)

    def focused(self, key: str, depth: int = 1) -> StateGraph:
        keep = {key}
        frontier = {key}
        for _ in range(depth):
            neighbors = {e.source for node in frontier for e in self.upstream(node)} | \
                        {e.destination for node in frontier for e in self.downstream(node)}
            frontier = neighbors - keep
            keep |= neighbors
        keep &= self.nodes.keys()
        return StateGraph({k: self.nodes[k] for k in keep},
                          [e for e in self.edges if e.source in keep and e.destination in keep])

    def diagnostics(self) -> list[GraphDiagnostic]:
        issues: list[GraphDiagnostic] = []
        for edge in self.edges:
            if not edge.resolved:
                issues.append(GraphDiagnostic("unresolved", f"{edge.kind}: {edge.source} → {edge.destination}", (edge.source, edge.destination)))
            elif edge.source in self.nodes and edge.destination in self.nodes and self.nodes[edge.source].sls != self.nodes[edge.destination].sls:
                issues.append(GraphDiagnostic("cross-SLS", f"{self.nodes[edge.source].sls} → {self.nodes[edge.destination].sls}", (edge.source, edge.destination)))
            if edge.reverse:
                issues.append(GraphDiagnostic("reverse", f"{edge.kind}: {edge.source} → {edge.destination}", (edge.source, edge.destination)))
        by_id: dict[str, list[str]] = defaultdict(list)
        for node in self.nodes.values():
            by_id[node.state_id].append(node.key)
            if not self.upstream(node.key) and not self.downstream(node.key):
                issues.append(GraphDiagnostic("isolated", f"{node.state_id} has no compiled requisites", (node.key,)))
            degree = len(self.upstream(node.key)) + len(self.downstream(node.key))
            if degree >= 10:
                issues.append(GraphDiagnostic("hub", f"{node.state_id} has {degree} connections", (node.key,)))
        for state_id, keys in by_id.items():
            if len(keys) > 1:
                issues.append(GraphDiagnostic("shared ID", f"{state_id} appears in {len(keys)} low chunks; this can be valid Salt output", tuple(keys)))
        # Kahn traversal avoids recursion limits on large highstates.
        indegree = {key: 0 for key in self.nodes}
        for edge in self.edges:
            if edge.source in self.nodes and edge.destination in self.nodes:
                indegree[edge.destination] += 1
        queue = deque(key for key, degree in indegree.items() if degree == 0)
        depth = {key: 0 for key in self.nodes}
        visited = 0
        while queue:
            key = queue.popleft()
            visited += 1
            for edge in self.downstream(key):
                dest = edge.destination
                if dest not in indegree:
                    continue
                depth[dest] = max(depth[dest], depth[key] + 1)
                indegree[dest] -= 1
                if indegree[dest] == 0:
                    queue.append(dest)
        if visited < len(self.nodes):
            residual = tuple(key for key, degree in indegree.items() if degree > 0)
            issues.append(GraphDiagnostic("cycle-looking", f"{len(residual)} low chunks participate in or follow a cycle-looking structure", residual[:20]))
        if depth and max(depth.values()) >= 20:
            deepest = max(depth, key=depth.get)
            issues.append(GraphDiagnostic("deep chain", f"At least {depth[deepest]} requisite edges lead to {self.nodes[deepest].state_id}", (deepest,)))
        return issues
