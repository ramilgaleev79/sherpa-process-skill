#!/usr/bin/env python3
"""Прогон schemas/sherpa-rules.sch через SaxonC (dev-venv, PLAN.md D9).

Pipeline ISO Schematron (XSLT2 skeleton):
    .sch → iso_dsdl_include.xsl → iso_abstract_expand.xsl → iso_svrl_for_xslt2.xsl
    → компиляция в SVRL-XSLT → прогон на .process → SVRL → нормализованный JSON.

Коды/роли в текущем .sch зашиты в текст ассертов — до миграции на @id/@role
парсер извлекает код (SHP-…) и роль (FATAL/ERROR/WARN) из текста.

Запуск: .venv/Scripts/python.exe scripts/dev/run_schematron.py FILE.process [--phase strict]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKELETON = Path(__file__).resolve().parent / "schematron-skeleton"
SCH = REPO / "schemas" / "sherpa-rules.sch"
SVRL_NS = {"svrl": "http://purl.oclc.org/dsdl/svrl"}

CODE_RE = re.compile(r"\b(SHP-[A-Z]+(?:-[A-Z]+)*-\d+)\b")
ROLE_RE = re.compile(r"\b(FATAL|ERROR|WARN)\b")


def compile_schematron(proc, sch_path: Path) -> Path:
    sch_path = sch_path.resolve()
    skeleton = SKELETON.resolve()
    with tempfile.TemporaryDirectory() as tmp:
        x1, x2, svrl_xsl = (Path(tmp) / n for n in ("x1.xsl", "x2.xsl", "svrl_gen.xsl"))
        xslt = proc.new_xslt30_processor()
        exe = xslt.compile_stylesheet(stylesheet_file=str(skeleton / "iso_dsdl_include.xsl"))
        exe.transform_to_file(source_file=str(sch_path), output_file=str(x1))
        exe = xslt.compile_stylesheet(stylesheet_file=str(skeleton / "iso_abstract_expand.xsl"))
        exe.transform_to_file(source_file=str(x1), output_file=str(x2))
        exe = xslt.compile_stylesheet(stylesheet_file=str(skeleton / "iso_svrl_for_xslt2.xsl"))
        exe.transform_to_file(source_file=str(x2), output_file=str(svrl_xsl))
        out = Path(tempfile.gettempdir()) / "sherpa_svrl_gen.xsl"
        out.write_bytes(svrl_xsl.read_bytes())
        return out


def run(proc, svrl_gen: Path, xml_path: Path, phase: str) -> str:
    xslt = proc.new_xslt30_processor()
    exe = xslt.compile_stylesheet(stylesheet_file=str(svrl_gen))
    exe.set_parameter("phase", proc.make_string_value(phase))
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "report.svrl"
        exe.transform_to_file(source_file=str(xml_path.resolve()), output_file=str(out))
        return out.read_text(encoding="utf-8")


def svrl_to_json(svrl_text: str) -> dict:
    root = ET.fromstring(svrl_text)
    issues = []
    for item in root.findall(".//svrl:failed-assert", SVRL_NS):
        text = " ".join((item.findtext("svrl:text", "", SVRL_NS) or "").split())
        code_m = CODE_RE.search(text)
        role_attr = item.get("role") or ""
        role_m = ROLE_RE.search(role_attr) or ROLE_RE.search(text)
        issues.append(
            {
                "code": code_m.group(1) if code_m else None,
                "severity": role_m.group(1) if role_m else "ERROR",
                "location": item.get("location", ""),
                "test": item.get("test", ""),
                "message": text,
            }
        )
    return {"status": "failed" if issues else "passed", "issues": issues}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", type=Path)
    ap.add_argument("--phase", default="strict", choices=["structural", "semantic-lite", "strict"])
    args = ap.parse_args()

    from saxonche import PySaxonProcessor

    with PySaxonProcessor(license=False) as proc:
        gen = compile_schematron(proc, SCH)
        svrl = run(proc, gen, args.file, args.phase)
    report = svrl_to_json(svrl)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
