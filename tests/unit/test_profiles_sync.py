"""Синхронность profiles.py (runtime, stdlib) ↔ references/validation-profiles.yaml (справочник)."""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from sherpa_xml import profiles

YAML = Path(__file__).resolve().parents[2] / "references" / "validation-profiles.yaml"


def test_forbidden_codes_match_yaml():
    data = yaml.safe_load(YAML.read_text(encoding="utf-8"))["profiles"]
    yaml_release = set(data["release"]["forbidden"])
    yaml_strict = set(data["strict"]["forbidden"])
    assert yaml_release <= profiles.FORBIDDEN["release"], (
        f"в yaml есть коды, отсутствующие в profiles.py release: {yaml_release - profiles.FORBIDDEN['release']}"
    )
    assert yaml_strict <= profiles.FORBIDDEN["strict"], (
        f"в yaml есть коды, отсутствующие в profiles.py strict: {yaml_strict - profiles.FORBIDDEN['strict']}"
    )


def test_runtime_codes_documented_in_yaml():
    data = yaml.safe_load(YAML.read_text(encoding="utf-8"))["profiles"]
    yaml_all = set(data["release"]["forbidden"])
    if data["strict"].get("inherits") == "release":
        yaml_all |= set(data["strict"]["forbidden"])  # strict наследует release
    runtime = profiles.FORBIDDEN["release"] | profiles.FORBIDDEN["strict"]
    undocumented = runtime - yaml_all
    assert not undocumented, f"коды profiles.py не описаны в yaml: {undocumented}"


def test_strict_is_superset_of_release():
    # выпускной профиль не может пропускать ошибки более мягкого (структурная гарантия)
    assert profiles.FORBIDDEN["release"] <= profiles.FORBIDDEN["strict"]
