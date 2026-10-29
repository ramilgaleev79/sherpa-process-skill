#!/usr/bin/env python3
"""Установка скила sherpa-process в ~/.agents/skills/ по white-list.

Установленная копия — только рантайм: CLI, инспектор, пакет sherpa_xml,
шаблоны блоков, справочники, инструменты обслуживания каталога и манифест golden.
Репозиторий разработки (golden .process, scripts/dev/, schemas/, main.md,
PLAN.md, README.md, tests/) в копию не попадает — он нужен только при работе в репо.

Запуск (cwd любой, путь репозитория вычисляется от __file__):
    python scripts/install_skill.py [--target PATH] [--dry-run] [--force]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = Path.home() / ".agents" / "skills" / "sherpa-process"

# Что ставится: (источник относительно корня репо, назначение относительно корня скилла).
WHITELIST: list[tuple[str, str]] = [
    ("SKILL.md", "SKILL.md"),
    ("scripts/cli.py", "scripts/cli.py"),
    ("scripts/golden_inspector.py", "scripts/golden_inspector.py"),
    ("scripts/structure_audit.py", "scripts/structure_audit.py"),
    ("scripts/change_impact.py", "scripts/change_impact.py"),
    ("scripts/apply_spec.py", "scripts/apply_spec.py"),
    ("scripts/edit_spec.py", "scripts/edit_spec.py"),
    ("scripts/process_diff.py", "scripts/process_diff.py"),
    ("scripts/lint_process.py", "scripts/lint_process.py"),
    ("scripts/block_suggest.py", "scripts/block_suggest.py"),
    ("scripts/sherpa_xml", "scripts/sherpa_xml"),
    ("scripts/dev/plan_to_ir.py", "scripts/dev/plan_to_ir.py"),
    ("scripts/dev/graph_compare.py", "scripts/dev/graph_compare.py"),
    ("scripts/dev/plan_from_text.py", "scripts/dev/plan_from_text.py"),
    ("scripts/dev/corpus_eval.py", "scripts/dev/corpus_eval.py"),
    ("scripts/dev/corpus_mine.py", "scripts/dev/corpus_mine.py"),
    ("scripts/dev/corpus_stats.py", "scripts/dev/corpus_stats.py"),
    ("scripts/dev/harvest_catalog.py", "scripts/dev/harvest_catalog.py"),
    ("scripts/dev/replay_corpus.py", "scripts/dev/replay_corpus.py"),
    ("scripts/dev/build_verb_index.py", "scripts/dev/build_verb_index.py"),
    ("scripts/dev/extract_skeleton.py", "scripts/dev/extract_skeleton.py"),
    ("assets/block_templates.json", "assets/block_templates.json"),
    ("assets/block_index.json", "assets/block_index.json"),
    ("assets/verb_block_index.json", "assets/verb_block_index.json"),
    ("references", "references"),
    ("golden/manifest.yaml", "golden/manifest.yaml"),
]

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".gitkeep")


def _iter_files(root: Path):
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if "__pycache__" in p.parts or p.suffix in (".pyc", ".pyo") or p.name == ".gitkeep":
            continue
        yield p


def _copy_entry(src: Path, dst: Path, dry: bool) -> tuple[int, int]:
    if src.is_file():
        if not dry:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return 1, src.stat().st_size
    if not dry:
        shutil.copytree(src, dst, ignore=IGNORE)
    files = list(_iter_files(src))
    return len(files), sum(f.stat().st_size for f in files)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET,
                    help=f"куда ставить (по умолчанию {DEFAULT_TARGET})")
    ap.add_argument("--dry-run", action="store_true", help="показать план, ничего не писать")
    ap.add_argument("--force", action="store_true",
                    help="перезаписать цель, даже если в ней нет маркера sherpa-process")
    args = ap.parse_args()
    target: Path = args.target

    missing = [s for s, _ in WHITELIST if not (REPO / s).exists()]
    if missing:
        print(f"FAIL: нет источников в репозитории: {', '.join(missing)}")
        return 1

    # Страховка: цель не должна совпадать с репозитортием или быть его предком.
    resolved = target.resolve()
    if resolved == REPO.resolve() or REPO.resolve().is_relative_to(resolved):
        print(f"FAIL: цель {resolved} совпадает с репозиторием или содержит его")
        return 1

    if target.exists():
        marker = target / "SKILL.md"
        marker_text = marker.read_text(encoding="utf-8", errors="ignore") if marker.exists() else ""
        is_skill = "name: sherpa-process" in marker_text
        if not is_skill and not args.force:
            print(f"FAIL: {target} существует и не похож на копию sherpa-process "
                  f"(нет маркера в SKILL.md); используйте --force после проверки")
            return 1
    if not args.dry_run and target.exists():
        shutil.rmtree(target)

    total_n = total_b = 0
    for src_rel, dst_rel in WHITELIST:
        n, b = _copy_entry(REPO / src_rel, target / dst_rel, args.dry_run)
        total_n += n
        total_b += b
        print(f"  {src_rel:<34} -> {dst_rel}  ({n} файл., {b // 1024} KB)")

    action = "план установки" if args.dry_run else "установлено"
    print(f"{action}: {total_n} файлов, {total_b / 1024:.0f} KB -> {target}")
    if not args.dry_run:
        print(f"smoke: python \"{target / 'scripts' / 'cli.py'}\" init-ir <tmp.ir.json>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
