"""Füllt ein Home-Assistant-App-Repository mit der aktuellen Version des Device Managers.

Das App-Repository ist ein eigenes Git-Repo auf GitHub, das in Home Assistant unter
Einstellungen → Apps → App-Store → ⋮ → Repositories eingetragen wird. Home Assistant baut die App
daraus selbst (Dockerfile + build.yaml) und zeigt neue Versionen samt CHANGELOG als Update an.

Aufbau des Ziel-Repos:
    repository.yaml
    README.md
    device_manager/            config.yaml, build.yaml, Dockerfile, DOCS.md, CHANGELOG.md, translations/, app/

Aufruf (Ziel = lokaler Klon des App-Repos):
    python scripts/publish_addon.py ../ha-apps --url https://github.com/macpo-homelab/ha-apps
    python scripts/publish_addon.py ../ha-apps --commit      # zusätzlich git add + commit (kein push)
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "addon" / "device_manager"
SLUG = "device_manager"
DEFAULT_URL = "https://github.com/macpo-homelab/ha-apps"
REPO_NAME = "macpo-homelab Home-Assistant-Apps"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_addon import build  # noqa: E402


def _version() -> str:
    import tomllib

    return str(tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])


def publish(target: Path, url: str = DEFAULT_URL, maintainer: str = "macpo") -> Path:
    """Schreibt repository.yaml, den App-Ordner und – falls noch keine da ist – README.md nach `target`."""
    target.mkdir(parents=True, exist_ok=True)
    app_dir = target / SLUG
    if app_dir.exists():
        shutil.rmtree(app_dir)  # alte Dateien entfernen, damit gelöschte Dateien nicht stehen bleiben
    shutil.copytree(ADDON, app_dir, ignore=shutil.ignore_patterns("app", "__pycache__"))
    build(app_dir / "app")

    (target / "repository.yaml").write_text(
        f"name: {REPO_NAME}\nurl: {url}\nmaintainer: {maintainer}\n", encoding="utf-8"
    )
    readme = target / "README.md"
    if not readme.exists():  # das Repo enthält später mehrere Apps – eine gepflegte README nicht überschreiben
        readme.write_text(
            f"# {REPO_NAME}\n\n"
            "Home-Assistant-Apps von macpo-homelab.\n\n"
            "In Home Assistant: Einstellungen → Apps → App-Store → ⋮ → Repositories → diese URL hinzufügen:\n\n"
            f"    {url}\n\n"
            "| App | Beschreibung |\n|---|---|\n"
            f"| [Device Manager]({SLUG}/DOCS.md) | Shelly-Geräte übernehmen, IP in OPNsense reservieren, "
            "Firmware und HTTPS-Zertifikate verwalten |\n",
            encoding="utf-8",
        )
    return target


def commit(target: Path) -> None:
    version = _version()
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)  # noqa: S603,S607
    message = f"Device Manager {version}"
    subprocess.run(["git", "-C", str(target), "commit", "-m", message], check=True)  # noqa: S603,S607


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", type=Path, help="lokaler Klon des App-Repositorys")
    parser.add_argument("--url", default=DEFAULT_URL, help="URL des App-Repositorys (für repository.yaml)")
    parser.add_argument("--maintainer", default="macpo")
    parser.add_argument("--commit", action="store_true", help="Änderungen im Ziel-Repo committen (kein push)")
    args = parser.parse_args(argv)

    path = publish(args.target, args.url, args.maintainer)
    print(f"App-Repository aktualisiert: {path} (Version {_version()})")
    if args.commit:
        commit(path)
        print("Commit erstellt. Jetzt pushen, danach in Home Assistant: App-Store → ⋮ → Nach Updates suchen.")


if __name__ == "__main__":
    main()
