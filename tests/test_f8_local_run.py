"""F8 – Lokaler Betrieb ohne Home Assistant."""

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from device_manager.config import Settings, load_settings
from device_manager.web.app import create_app


def test_settings_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("DM_OPNSENSE_URL", "https://fw.lan/")
    monkeypatch.setenv("DM_IP_RANGE_START", "10.0.0.10")
    monkeypatch.setenv("DM_IP_RANGE_END", "10.0.0.20")
    monkeypatch.setenv("DM_SHELLY_PASSWORD", "geheim")
    s = load_settings(options_file=tmp_path / "missing.json")
    assert s.opnsense_url == "https://fw.lan"
    assert s.shelly_password.get_secret_value() == "geheim"
    assert "geheim" not in repr(s)  # SecretStr maskiert
    assert s.bind_host == "127.0.0.1" and s.allowed_clients == []


def test_invalid_range_rejected(tmp_path):
    with pytest.raises(ValueError):
        Settings(_env_file=None, ip_range_start="10.0.0.20", ip_range_end="10.0.0.10")


def test_app_starts_without_ha_and_without_mqtt(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    settings = Settings(_env_file=None, data_dir=tmp_path, allowed_hosts=["testserver"])
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").json() == {"status": "ok", "mqtt": False, "opnsense": False}
        assert client.get("/").status_code == 200
    assert (tmp_path / "device_manager.sqlite3").exists()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_cli_starts_server(tmp_path):
    """`python -m device_manager` startet einen echten HTTP-Server."""
    port = _free_port()
    env = {
        "DM_DATA_DIR": str(tmp_path),
        "DM_OPTIONS_FILE": str(tmp_path / "none.json"),
        "PATH": "",
        "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
    }
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "device_manager", "--port", str(port), "--log-level", "warning"],
        env=env,
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1)
                assert r.json()["status"] == "ok"
                return
            except httpx.TransportError:
                time.sleep(0.3)
        pytest.fail("Server nicht gestartet: " + (proc.stdout.read().decode() if proc.poll() is not None else ""))
    finally:
        proc.terminate()
        proc.wait(10)


def test_env_example_lists_all_settings():
    text = (Path(__file__).parents[1] / ".env.example").read_text(encoding="utf-8")
    important = [
        "SHELLY_PASSWORD",
        "WIFI_SSID",
        "OPNSENSE_URL",
        "OPNSENSE_API_KEY",
        "IP_RANGE_START",
        "DNS_DOMAIN",
        "CERT_CA_NAME",
        "MQTT_HOST",
        "Z2M_BASE_TOPIC",
    ]
    assert all(f"DM_{name}=" in text for name in important)
    assert json.dumps  # noqa: B018


def test_database_from_shelly_manager_era_is_taken_over(tmp_path):
    """Bis 0.3.x hieß die Datenbank shelly_manager.sqlite3 – sie wird einmalig umbenannt."""
    (tmp_path / "shelly_manager.sqlite3").write_bytes(b"alt")
    path = Settings(_env_file=None, data_dir=tmp_path).db_path
    assert path.name == "device_manager.sqlite3" and path.read_bytes() == b"alt"
    assert not (tmp_path / "shelly_manager.sqlite3").exists()
