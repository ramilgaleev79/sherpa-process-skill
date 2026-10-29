"""Инструменты работы с корпусом: сравнение графов и майнинг пар «описание + .process»."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from dev.corpus_mine import collect  # noqa: E402
from dev.graph_compare import compare, load_graph  # noqa: E402

GOLDEN = REPO / "golden" / "real"
PLAN = REPO / "tests" / "fixtures" / "plan-screenshots5.json"


def _ir(types: list[str], edges: list[tuple[int, int]], displays: dict[int, str] | None = None) -> dict:
    nodes = []
    for i, t in enumerate(types):
        node = {"key": f"n{i}", "type": t}
        if displays and i in displays:
            node["display_name"] = displays[i]
        nodes.append(node)
    return {"process": {"name": "T"}, "variables": [], "nodes": nodes,
            "edges": [{"from": f"n{a}", "from_port": "Next", "to": f"n{b}", "to_port": "Input"}
                      for a, b in edges]}


def _write(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_identical_graphs_score_one(tmp_path):
    path = _write(tmp_path / "a.ir.json", _ir(["Start", "Log", "Stop"], [(0, 1), (1, 2)]))
    report = compare(load_graph(path), load_graph(path))
    assert report["score"] == 1.0
    assert not report["missing_blocks"] and not report["extra_blocks"]


def test_extra_block_and_edge_detected(tmp_path):
    ref = _write(tmp_path / "ref.ir.json", _ir(["Start", "Log", "Stop"], [(0, 1), (1, 2)]))
    got = _write(tmp_path / "got.ir.json",
                 _ir(["Start", "Log", "CopyItem", "Stop"], [(0, 1), (1, 2), (2, 3)]))
    report = compare(load_graph(ref), load_graph(got))
    assert report["score"] < 1.0
    assert "CopyItem" in report["extra_blocks"]
    assert not report["missing_blocks"]


def test_process_file_parsing_and_self_compare():
    graph = load_graph(GOLDEN / "diag01-start-stop.process")
    assert graph["types"].get("Start") == 1
    assert graph["types"].get("ReturnOrStop", 0) >= 1
    assert compare(graph, graph)["score"] == 1.0


def test_plan_input_is_compiled(tmp_path):
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    path = _write(tmp_path / "plan.json", plan)
    graph = load_graph(path)
    assert graph["types"].get("For") == 1
    assert graph["types"].get("MakeScreenshot") == 1
    assert compare(graph, graph)["score"] == 1.0


def test_ignored_blocks_do_not_affect_score(tmp_path):
    ref = _write(tmp_path / "r.ir.json", _ir(["Start", "Log", "Stop"], [(0, 1), (1, 2)]))
    got = _write(tmp_path / "g.ir.json",
                 _ir(["Start", "Log", "Comment", "Stop"], [(0, 1), (1, 3)]))
    strict = compare(load_graph(ref), load_graph(got), ignore=frozenset())
    relaxed = compare(load_graph(ref), load_graph(got), ignore=frozenset({"Comment"}))
    assert strict["score"] < relaxed["score"] == 1.0


def test_corpus_mine_links_verb_to_block_via_display_name(tmp_path):
    data = _ir(["Start", "SendMailMessage", "Stop"], [(0, 1), (1, 2)],
               displays={1: "Отправить письмо клиенту"})
    _write(tmp_path / "task.ir.json", data)
    (tmp_path / "task.md").write_text(
        "Отправить письмо клиенту с вложением.", encoding="utf-8")
    report, descs = collect([tmp_path])
    assert "task" in descs
    assert report["verbs"]["отправить"]["blocks"].get("SendMailMessage") == 1
    assert report["verbs"]["отправить"]["confidence"] == 1.0


def test_corpus_mine_without_descriptions(tmp_path):
    _write(tmp_path / "a.ir.json", _ir(["Start", "Log", "Stop"], [(0, 1), (1, 2)]))
    report, descs = collect([tmp_path])
    assert report["meta"]["processes"] == 1
    assert report["meta"]["described_inline"] == 0
    assert report["meta"]["described_sidecar"] == 0
    assert not descs
    assert report["ports"]
