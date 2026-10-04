import json
from pathlib import Path

from salt_tui.config import Settings
from salt_tui.sls.graph import StateGraph
from salt_tui.sls.source_links import extend_hints, include_hints, locate_source

FIXTURES = Path(__file__).parent / "fixtures"


def graph():
    return StateGraph.from_low(json.loads((FIXTURES / "show_low_sls.json").read_text()))


def test_graph_traversal_and_filtering():
    g = graph()
    keys = {node.state_id: key for key, node in g.nodes.items()}
    assert len(g.nodes) == 3
    assert [e.kind for e in g.upstream(keys["nginx-config"])] == ["require"]
    assert [g.nodes[key].state_id for key in g.shortest_path(keys["nginx-package"], keys["nginx-service"])] == [
        "nginx-package", "nginx-config", "nginx-service"]
    assert len(g.focused(keys["nginx-config"]).nodes) == 3
    assert len(g.filtered(sls="nginx.config").nodes) == 1
    assert not any(d.kind == "unresolved" for d in g.diagnostics())


def test_graph_diagnostics_are_hints():
    g = StateGraph.from_low(json.loads((FIXTURES / "missing_requisite.json").read_text()))
    assert any(d.kind == "unresolved" for d in g.diagnostics())
    cycle = StateGraph.from_low([
        {"__id__": "a", "state": "pkg", "fun": "installed", "require": [{"pkg": "b"}]},
        {"__id__": "b", "state": "pkg", "fun": "installed", "require": [{"pkg": "a"}]},
    ])
    assert any(d.kind == "cycle-looking" for d in cycle.diagnostics())


def test_source_location_and_literal_includes(tmp_path):
    root = tmp_path / "salt"
    path = root / "nginx/init.sls"
    path.parent.mkdir(parents=True)
    path.write_text("include:\n  - nginx.config\n\nnginx-package:\n  pkg.installed:\n    - name: nginx\n\nextend:\n  nginx-service:\n    service.running:\n      - watch:\n        - file: nginx-config\n")
    settings = Settings(file_roots={"base": [root]})
    assert locate_source(settings, "base", "nginx", "nginx-package") == (path, 4)
    assert include_hints(path) == ["nginx.config"]
    assert extend_hints(path) == ["nginx-service"]
    assert locate_source(settings, "base", "nginx", "generated-id") is None
