"""Общая настройка путей для тестов: scripts/ (sherpa_xml, cli) и tests/ (golden)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
