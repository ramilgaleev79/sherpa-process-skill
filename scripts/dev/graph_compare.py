"""Чтение графа процесса и его сравнение: структура, версии блоков, разрывы связей.

Читатель `.process` учитывает реальные особенности корпусов Sherpa:

- **старые версии блоков**: у блока в файле своя `Version`; она сверяется с версией
  в установленной библиотеке (`assets/block_index.json`) и помечается как `legacy`;
- **блоки, которых больше нет** (например `LaunchChrome` → `LaunchBrowser`) попадают
  в `unknown` и не ломают разбор;
- **разрывы связей**: связи на несуществующие ID считаются `dangling` и не роняют
  сравнение; блоки без связей — `isolated` (комментарии намеренно не связаны и
  в разрывы не засчитываются);
- **описания внутри файла**: `Comment`-блоки (текст в `DisplayName`) и корневой
  `Description` — это и есть описание задачи, отдельный `.md` не нужен.

Сравнение графов — по двум слоям: типы блоков и связи `тип:порт → тип:порт`.
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from functools import lru_cache
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dev.plan_to_ir import PlanCompiler  # noqa: E402
from sherpa_xml.console import utf8_stdio  # noqa: E402
from sherpa_xml.ir import resolve_physical  # noqa: E402

TECH_DEFAULT = {"Start", "Stop", "ReturnOrStop", "Comment", "PointConnector"}
PROFILE_FORMAT = "sherpa-graph-profile/1"

# Блоки, удалённые в 1.4.4; в корпусах встречаются в старых процессах.
LEGACY_REPLACEMENTS = {
    "LaunchChrome": "LaunchBrowser",
    "LaunchIE": "LaunchBrowser",
    "RecognizeCaptcha": "RecognizeCaptchaOCR",
    "InternalBrowserGet": "InternalBrowserAsyncGet",
    "NewFile": "OrchStorageCreateFile",
}
MIN_COMMENT_LEN = 40


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@lru_cache(maxsize=1)
def catalog() -> dict:
    path = SKILL_ROOT / "assets" / "block_index.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))["blocks"]
    except (OSError, KeyError, json.JSONDecodeError):
        return {}


def _version_is_legacy(block_type: str, version: str | None) -> bool:
    entry = catalog().get(block_type)
    if entry is None:
        return False
    return str(entry.get("version")) != str(version)


def read_process(path: Path) -> dict:
    """Разбирает `.process`: узлы, связи, описания, версии, разрывы."""
    root = ET.parse(path).getroot()
    nodes: list[dict] = []
    for item in root.iter():
        if _local(item.tag) != "DesignerItem":
            continue
        content = item.findtext("Content") or ""
        if not content:
            continue
        try:
            inner = ET.fromstring(content)
        except ET.ParseError:
            continue
        nodes.append({
            "id": item.findtext("ID") or "",
            "type": inner.get("InternalName") or "?",
            "display": (inner.get("DisplayName") or "").strip(),
            "version": inner.get("Version"),
        })

    by_id = {n["id"]: n for n in nodes}
    edges: set[tuple[str, str, str, str]] = set()
    linked: set[str] = set()
    dangling = 0
    connections = 0
    for conn in root.iter():
        if _local(conn.tag) != "Connection":
            continue
        connections += 1
        sid = conn.findtext("SourceID") or ""
        tid = conn.findtext("SinkID") or ""
        linked.update((sid, tid))
        if sid not in by_id or tid not in by_id:
            dangling += 1
            continue
        edges.add((by_id[sid]["type"], conn.findtext("SourceConnectorName") or "?",
                   by_id[tid]["type"], conn.findtext("SinkConnectorName") or "?"))

    comments = [n["display"] for n in nodes
                if n["type"] == "Comment" and len(n["display"]) >= MIN_COMMENT_LEN]
    root_desc = (root.findtext("Description") or "").strip()

    isolated = [n["type"] for n in nodes
                if n["id"] not in linked and n["type"] not in TECH_DEFAULT]
    unknown = sorted({n["type"] for n in nodes if n["type"] not in catalog()})
    legacy = sorted({n["type"] for n in nodes if _version_is_legacy(n["type"], n["version"])})
    versions: dict[str, set[str]] = {}
    for n in nodes:
        if n["version"]:
            versions.setdefault(n["type"], set()).add(n["version"])

    return {
        "types": Counter(n["type"] for n in nodes),
        "edges": edges,
        "nodes": [(n["type"], n["display"]) for n in nodes],
        "comments": comments,
        "description": root_desc,
        "versions": versions,
        "legacy": legacy,
        "unknown": unknown,
        "dangling": dangling,
        "isolated": isolated,
        "connections": connections,
        "replacements": {b: LEGACY_REPLACEMENTS[b] for b in unknown if b in LEGACY_REPLACEMENTS},
        "source": str(path),
    }


def graph_from_ir(data: dict, source: str = '<ir>') -> dict:
    def physical(node_type: str) -> str:
        return resolve_physical(node_type) or node_type

    nodes = {n["key"]: physical(n["type"]) for n in data.get("nodes", [])}
    pairs = [(physical(n["type"]), str(n.get("display_name") or "").strip())
             for n in data.get("nodes", [])]
    edges = {(nodes.get(e["from"], "?"), e.get("from_port", "?"),
              nodes.get(e["to"], "?"), e.get("to_port", "?")) for e in data.get("edges", [])}
    comments = [d for t, d in pairs if t == "Comment" and len(d) >= MIN_COMMENT_LEN]
    return {
        "types": Counter(nodes.values()),
        "edges": edges,
        "nodes": pairs,
        "comments": comments,
        "description": "",
        "versions": {},
        "legacy": [],
        "unknown": [],
        "dangling": 0,
        "isolated": [],
        "connections": len(data.get("edges", [])),
        "replacements": {},
        "source": source,
    }


def graph_profile(graph: dict, name: str | None = None) -> dict:
    """Сжимает граф до переносимого эталона: типы блоков + связи `тип:порт → тип:порт`.

    Нужно, чтобы эталоном мог быть большой реальный процесс (5 МБ, 200+ блоков),
    не копируя его в репозиторий: профиль — это несколько килобайт JSON.
    """
    return {
        "format": PROFILE_FORMAT,
        "source": name or graph.get("source") or "",
        "types": dict(sorted(graph["types"].items())),
        "edges": [list(e) for e in sorted(graph["edges"])],
        "stats": {
            "nodes": sum(graph["types"].values()),
            "edges": len(graph["edges"]),
            "comments": len(graph.get("comments") or []),
        },
    }


def profile_to_graph(data: dict) -> dict:
    """Профиль → граф того же вида, что `read_process`/`graph_from_ir`."""
    if data.get("format") != PROFILE_FORMAT:
        raise ValueError(f"не профиль графа: ожидался format={PROFILE_FORMAT}")
    edges = {(e[0], e[1], e[2], e[3]) for e in data.get("edges") or []}
    return {
        "types": Counter(data.get("types") or {}),
        "edges": edges,
        "nodes": [],
        "comments": [],
        "description": "",
        "versions": {},
        "legacy": [],
        "unknown": [],
        "dangling": 0,
        "isolated": [],
        "connections": len(edges),
        "replacements": {},
        "source": data.get("source") or "<profile>",
    }


def load_graph(path: Path) -> dict:
    if path.suffix.lower() == ".process":
        return read_process(path)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict) and data.get("format") == PROFILE_FORMAT:
        return profile_to_graph(data)
    if isinstance(data.get("steps"), list) and "process" in data:
        data = PlanCompiler(data).compile()
    return graph_from_ir(data, str(path))


def _jaccard(a: set, b: set) -> float:
    union = a | b
    return 1.0 if not union else len(a & b) / len(union)


def compare(ref: dict, got: dict, ignore: set[str] = frozenset()) -> dict:
    ref_types = Counter({k: v for k, v in ref["types"].items() if k not in ignore})
    got_types = Counter({k: v for k, v in got["types"].items() if k not in ignore})
    ref_edges = {e for e in ref["edges"] if e[0] not in ignore and e[2] not in ignore}
    got_edges = {e for e in got["edges"] if e[0] not in ignore and e[2] not in ignore}

    type_score = _jaccard(set(ref_types), set(got_types))
    edge_score = _jaccard(ref_edges, got_edges)
    score = round(0.5 * type_score + 0.5 * edge_score, 4)

    loops = {"For", "While", "ForeachRows", "ForeachList", "ForeachHashtable"}
    return {
        "score": score,
        "type_score": round(type_score, 4),
        "edge_score": round(edge_score, 4),
        "reference": {"blocks": dict(ref_types), "edges": len(ref_edges),
                      "loops": sum(ref_types.get(b, 0) for b in loops),
                      "conditions": ref_types.get("IfCondition", 0)},
        "actual": {"blocks": dict(got_types), "edges": len(got_edges),
                   "loops": sum(got_types.get(b, 0) for b in loops),
                   "conditions": got_types.get("IfCondition", 0)},
        "missing_blocks": sorted(set(ref_types) - set(got_types)),
        "extra_blocks": sorted(set(got_types) - set(ref_types)),
        "missing_edges": sorted(ref_edges - got_edges),
        "extra_edges": sorted(got_edges - ref_edges),
        "reference_health": {"legacy": len(ref.get("legacy", [])),
                             "unknown": len(ref.get("unknown", [])),
                             "dangling": ref.get("dangling", 0),
                             "isolated": len(ref.get("isolated", []))},
    }


def render(report: dict, ref_path: str, got_path: str) -> str:
    r, a = report["reference"], report["actual"]
    lines = [
        f"эталон:  {ref_path}",
        f"проверка:{got_path}",
        "",
        f"  score={report['score']:.3f}  (типы {report['type_score']:.3f}, связи {report['edge_score']:.3f})",
        f"  блоков: эталон {sum(r['blocks'].values())} · проверка {sum(a['blocks'].values())}",
        f"  связей: эталон {r['edges']} · проверка {a['edges']}",
        f"  циклов: {r['loops']} → {a['loops']}   условий: {r['conditions']} → {a['conditions']}",
    ]
    health = report.get("reference_health", {})
    if health.get("legacy") or health.get("unknown") or health.get("dangling"):
        lines.append(f"  состояние эталона: старых версий {health['legacy']}, "
                     f"неизвестных блоков {health['unknown']}, разрывов связей {health['dangling']}")
    if report["missing_blocks"]:
        lines.append(f"  нет блоков из эталона: {', '.join(report['missing_blocks'])}")
    if report["extra_blocks"]:
        lines.append(f"  лишние блоки:          {', '.join(report['extra_blocks'])}")
    if report["missing_edges"]:
        lines.append("  связи только в эталоне:")
        for e in report["missing_edges"][:8]:
            lines.append(f"     {e[0]}:{e[1]} → {e[2]}:{e[3]}")
    if report["extra_edges"]:
        lines.append("  связи только в проверке:")
        for e in report["extra_edges"][:8]:
            lines.append(f"     {e[0]}:{e[1]} → {e[2]}:{e[3]}")
    if not (report["missing_blocks"] or report["extra_blocks"]
            or report["missing_edges"] or report["extra_edges"]):
        lines.append("  структурных расхождений нет")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description="Структурное сравнение графов процесса")
    ap.add_argument("reference", type=Path, help="эталон (.process/.ir.json/plan.json)")
    ap.add_argument("actual", type=Path, help="проверяемый граф")
    ap.add_argument("--json", action="store_true", help="машинный отчёт")
    ap.add_argument("--ignore", default=",".join(sorted(TECH_DEFAULT)),
                    help="типы блоков, которые не учитываются")
    ap.add_argument("--threshold", type=float, default=0.8, help="порог приёмки (0..1)")
    ap.add_argument("--profile-out", type=Path, default=None,
                    help="сохранить профиль эталона (лёгкий эталон для eval-пар)")
    args = ap.parse_args(argv)

    ignore = {x.strip() for x in args.ignore.split(",") if x.strip()}
    try:
        ref = load_graph(args.reference)
        got = load_graph(args.actual)
    except Exception as exc:  # noqa: BLE001 — битый вход должен быть внятным
        print(f"FAIL: {exc}")
        return 2
    if args.profile_out is not None:
        args.profile_out.parent.mkdir(parents=True, exist_ok=True)
        args.profile_out.write_text(
            json.dumps(graph_profile(ref, str(args.reference)), ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
        if not args.json:
            print(f"профиль эталона: {args.profile_out}")
    report = compare(ref, got, ignore)
    report["threshold"] = args.threshold
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report, str(args.reference), str(args.actual)))
    return 0 if report["score"] >= args.threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
