"""Общая настройка путей для тестов: scripts/ (пакет sherpa_xml и cli) в sys.path."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
