"""Verschlüsselte Secrets in der .env (Windows DPAPI)."""

import sys

import pytest

from device_manager import envcrypt
from device_manager.config import Settings, load_settings

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI nur unter Windows")


@windows_only
def test_roundtrip():
    token = envcrypt.protect("geheim-äöü-123")
    assert token.startswith("dpapi:") and "geheim" not in token
    assert envcrypt.unprotect(token) == "geheim-äöü-123"


@windows_only
def test_each_encryption_differs():
    assert envcrypt.protect("x") != envcrypt.protect("x")


def test_plain_values_unchanged():
    assert envcrypt.resolve("klartext") == "klartext"


@windows_only
def test_settings_decrypt_all_secret_fields(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                f"DM_SHELLY_PASSWORD={envcrypt.protect('master-pw')}",
                f"DM_WIFI_PASSWORD={envcrypt.protect('wlan-pw')}",
                f"DM_OPNSENSE_API_KEY={envcrypt.protect('api-key')}",
                f"DM_OPNSENSE_API_SECRET={envcrypt.protect('api-secret')}",
                f"DM_MQTT_PASSWORD={envcrypt.protect('mqtt-pw')}",
                "DM_WIFI_SSID=HomeWLAN",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    s = load_settings(options_file=tmp_path / "none.json")
    assert sorted(s.secrets()) == sorted(["master-pw", "wlan-pw", "api-key", "api-secret", "mqtt-pw"])
    assert s.wifi_ssid == "HomeWLAN"
    assert "master-pw" not in repr(s)


@windows_only
def test_tampered_value_gives_clear_error_without_leaking():
    token = envcrypt.protect("geheim")
    broken = token[:-8] + "AAAAAAA="
    with pytest.raises(ValueError, match="DPAPI fehlgeschlagen") as exc:
        Settings(_env_file=None, shelly_password=broken)
    assert "geheim" not in str(exc.value)


def test_invalid_base64_is_reported():
    with pytest.raises(ValueError, match="Base64"):
        envcrypt.unprotect("dpapi:%%%")


@pytest.mark.skipif(sys.platform == "win32", reason="Verhalten außerhalb von Windows")
def test_non_windows_reports_unsupported():
    with pytest.raises(ValueError, match="nur unter Windows"):
        envcrypt.protect("x")


@windows_only
def test_cli_prints_env_line(monkeypatch, capsys):
    answers = iter(["mein-pw", "mein-pw"])
    monkeypatch.setattr(envcrypt.getpass, "getpass", lambda _prompt: next(answers))
    assert envcrypt.main(["DM_SHELLY_PASSWORD"]) == 0
    line = capsys.readouterr().out.strip()
    name, token = line.split("=", 1)
    assert name == "DM_SHELLY_PASSWORD" and envcrypt.unprotect(token) == "mein-pw"
    assert "mein-pw" not in line


@windows_only
def test_cli_rejects_mismatch(monkeypatch, capsys):
    answers = iter(["a", "b"])
    monkeypatch.setattr(envcrypt.getpass, "getpass", lambda _prompt: next(answers))
    assert envcrypt.main([]) == 1
    assert capsys.readouterr().out == ""
