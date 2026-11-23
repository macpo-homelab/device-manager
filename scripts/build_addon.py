"""Kopiert den Quellcode in den Build-Kontext der HA-App (addon/device_manager/app).

Aufruf:  python scripts/build_addon.py
Danach kann der Ordner addon/device_manager z. B. nach /addons/device_manager auf den HA-Host kopiert
oder lokal gebaut werden:  docker build -t device-manager addon/device_manager
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "addon" / "device_manager" / "app"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info")


def build(target: Path = TARGET) -> Path:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    shutil.copytree(ROOT / "src", target / "src", ignore=IGNORE)
    for name in ("pyproject.toml", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, target / name)
    return target


if __name__ == "__main__":
    path = build(Path(sys.argv[1]) if len(sys.argv) > 1 else TARGET)
    print(f"Build-Kontext erstellt: {path}")
