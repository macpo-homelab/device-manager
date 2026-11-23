"""F9 – Betrieb als Home-Assistant-App."""

import json
import re
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from device_manager.config import Settings, load_settings
from device_manager.web.app import _supervisor_mqtt, create_app

ADDON = Path(__file__).parents[1] / "addon" / "device_manager"


@pytest.fixture
def addon_config():
    return yaml.safe_load((ADDON / "config.yaml").read_text(encoding="utf-8"))


def test_options_json_has_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("DM_DNS_DOMAIN", "env.lan")
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"dns_domain": "ha.lan", "opnsense_url": "https://fw", "wifi_ssid": ""}))
    s = load_settings(options_file=options)
    assert s.dns_domain == "ha.lan"
    assert s.data_dir == tmp_path
    assert s.bind_host == "0.0.0.0"
    assert s.allowed_clients == ["172.30.32.2"]
    assert s.allowed_hosts == []


def test_addon_options_match_settings(addon_config):
    fields = set(Settings.model_fields)
    assert set(addon_config["options"]) <= fields
    assert set(addon_config["schema"]) <= fields
    assert set(addon_config["options"]) <= set(addon_config["schema"])
    # Geheimnisse sind in der HA-Oberfläche als Passwortfelder markiert
    for secret in (
        "shelly_password",
        "wifi_password",
        "opnsense_api_key",
        "opnsense_api_secret",
        "mqtt_password",
    ):
        assert str(addon_config["schema"][secret]).startswith("password")


def test_addon_options_load_into_settings(addon_config, tmp_path):
    options = dict(addon_config["options"]) | {
        "opnsense_url": "https://fw.lan",
        "ip_range_start": "10.0.0.1",
        "ip_range_end": "10.0.0.9",
        "cert_ca_name": "CA",
    }
    path = tmp_path / "options.json"
    path.write_text(json.dumps(options))
    s = load_settings(options_file=path)
    assert s.cert_key_location == "firewall" and s.default_firmware_stage == "stable"


def test_addon_ingress_and_mqtt(addon_config):
    assert addon_config["ingress"] is True
    assert addon_config["ingress_port"] == Settings().port
    assert "mqtt:want" in addon_config["services"]
    translations = yaml.safe_load((ADDON / "translations" / "de.yaml").read_text(encoding="utf-8"))
    assert set(addon_config["schema"]) <= set(translations["configuration"])


def test_dockerfile_uses_options_file():
    text = (ADDON / "Dockerfile").read_text(encoding="utf-8")
    assert "DM_OPTIONS_FILE=/data/options.json" in text
    assert re.search(r"python3\", \"-m\", \"device_manager", text)


def test_build_script_creates_context(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_addon", Path(__file__).parents[1] / "scripts" / "build_addon.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = module.build(tmp_path / "app")
    assert (target / "pyproject.toml").exists()
    assert (target / "src" / "device_manager" / "web" / "static" / "htmx.min.js").exists()


def test_ingress_base_path(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path, allowed_hosts=[]))) as client:
        html = client.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/abc123"}).text
    assert '<base href="/api/hassio_ingress/abc123/">' in html
    assert 'href="/' not in html.replace('<base href="/api', "")  # nur relative Links


def test_only_ingress_proxy_allowed(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, allowed_clients=["172.30.32.2"], allowed_hosts=[])
    with TestClient(create_app(settings), client=("192.168.1.20", 5000)) as client:
        assert client.get("/").status_code == 403
    with TestClient(create_app(settings), client=("172.30.32.2", 5000)) as client:
        assert client.get("/").status_code == 200


async def test_mqtt_credentials_from_supervisor(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer tok"
        assert str(request.url) == "http://supervisor/services/mqtt"
        return httpx.Response(
            200,
            json={"data": {"host": "core-mosquitto", "port": 1883, "username": "addons", "password": "pw"}},
        )

    original = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    s = await _supervisor_mqtt(Settings(_env_file=None))
    assert (s.mqtt_host, s.mqtt_username, s.mqtt_password.get_secret_value()) == (
        "core-mosquitto",
        "addons",
        "pw",
    )


@pytest.mark.docker
def test_docker_image_builds(tmp_path):
    import importlib.util
    import shutil
    import subprocess

    if not shutil.which("docker") or subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("Docker nicht verfügbar bzw. Daemon läuft nicht")

    root = Path(__file__).parents[1]
    context = tmp_path / "ctx"
    shutil.copytree(ADDON, context, ignore=shutil.ignore_patterns("app"))
    spec = importlib.util.spec_from_file_location("build_addon", root / "scripts" / "build_addon.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build(context / "app")
    subprocess.run(["docker", "build", "-t", "device-manager-test", str(context)], check=True)  # noqa: S603,S607
    out = subprocess.run(  # noqa: S603,S607
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "python3",
            "device-manager-test",
            "-c",
            "import device_manager.web.app; print('ok')",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert out.stdout.strip() == "ok"


def test_version_is_consistent(addon_config):
    """Versionsnummer in pyproject.toml und HA-App-Konfiguration muss gleich sein (bei jeder Änderung hochzählen)."""
    import tomllib

    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert str(addon_config["version"]) == version


def test_changelog_has_entry_for_current_version(addon_config):
    """Home Assistant zeigt CHANGELOG.md beim Update an – jede Version braucht einen Eintrag."""
    changelog = (ADDON / "CHANGELOG.md").read_text(encoding="utf-8")
    version = str(addon_config["version"])
    assert re.search(rf"^## {re.escape(version)}\s*$", changelog, re.MULTILINE), f"Kein Eintrag für {version}"
    first = re.search(r"^## (\S+)", changelog, re.MULTILINE)
    assert first and first.group(1) == version, "Neueste Version muss oben stehen"


def test_publish_creates_app_repository(tmp_path):
    """F9b: Das App-Repository enthält repository.yaml und einen baubaren App-Ordner; .git bleibt erhalten."""
    import importlib.util
    import sys

    scripts = Path(__file__).parents[1] / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location("publish_addon", scripts / "publish_addon.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(scripts))

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "device_manager").mkdir()
    (repo / "device_manager" / "stale.txt").write_text("alt")
    module.publish(repo, url="https://git.example/mp/apps")

    meta = yaml.safe_load((repo / "repository.yaml").read_text(encoding="utf-8"))
    assert meta == {"name": module.REPO_NAME, "url": "https://git.example/mp/apps", "maintainer": "macpo"}
    app = repo / "device_manager"
    for name in ("config.yaml", "build.yaml", "Dockerfile", "DOCS.md", "CHANGELOG.md", "translations/de.yaml"):
        assert (app / name).exists(), name
    assert (app / "app" / "pyproject.toml").exists()
    assert (app / "app" / "src" / "device_manager" / "web" / "static" / "htmx.min.js").exists()
    assert not (app / "stale.txt").exists()
    assert (repo / ".git").is_dir()
    assert not list(app.rglob("__pycache__"))
    assert "Device Manager" in (repo / "README.md").read_text(encoding="utf-8")

    (repo / "README.md").write_text("gepflegt", encoding="utf-8")
    module.publish(repo, url="https://git.example/mp/apps")
    assert (repo / "README.md").read_text(encoding="utf-8") == "gepflegt"  # README des Repos bleibt
