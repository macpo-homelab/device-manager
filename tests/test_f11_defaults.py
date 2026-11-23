"""F11 – Standardeinstellungen für neue Geräte, Name mit Groß-/Kleinschreibung, NTP-Server verteilen."""

import logging

import pytest
from pydantic import ValidationError

from device_manager.config import Settings
from device_manager.jobs import JobManager
from device_manager.services.common import ServiceError, validate_name
from device_manager.services.defaults import distribute_ntp
from device_manager.services.provision import provision
from tests.conftest import device_password, shelly


async def test_new_device_gets_all_defaults(ctx, network, opnsense, caplog):
    caplog.set_level(logging.DEBUG)
    sim = shelly(network, ip="192.168.10.200", app="PlugMG3", ver="1.8.99", available={"stable": "2.2.0"})
    sim.config["Sys"] = {"ui_data": {"device_revision": "1-10"}}  # wie am echten Plug M Gen3
    messages: list[str] = []
    result = await provision(ctx, "192.168.10.200", "Kueche-Licht", progress=messages.append)

    # Name: Groß-/Kleinschreibung in Kea und im Gerät, Hostname klein für DNS/Zertifikat
    [reservation] = opnsense.reservations.values()
    assert reservation["hostname"] == "Kueche-Licht"
    assert reservation["description"] == "Shelly Plug M Gen 3"
    device = ctx.store.get_device(sim.mac_colon)
    assert (device.name, device.hostname) == ("Kueche-Licht", "kueche-licht")
    assert sim.name == "Kueche-Licht"
    assert result.certificate is not None and result.certificate.fqdn == "kueche-licht.home.lan"

    # Reihenfolge: Firmware vor den Einstellungen
    assert messages.index("Schritt 3/5: Firmware") < messages.index("Schritt 4/5: Standardeinstellungen")
    assert sim.ver == "2.2.0"

    sys_cfg = sim.config["Sys"]
    assert sys_cfg["device"] == {"name": "Kueche-Licht", "eco_mode": True}
    assert sys_cfg["location"] == {"tz": "Europe/Berlin"}  # ohne Koordinaten kein Standort
    assert sys_cfg["sntp"] == {"server": "time.cloudflare.com"}
    assert sim.config["WiFi"]["ap"] == {"enable": False, "pass": device_password(sim.mac)}
    assert sim.config["BLE"] == {"rpc": {"enable": False}}  # ab 2.0.0 kein 'enable' mehr
    assert sim.config["PLUGS_UI"] == {"leds": {"mode": "off"}}
    assert result.defaults is not None and result.defaults.rebooted  # BLE meldet restart_required
    assert sys_cfg["ui_data"] == {"device_revision": "1-10", "df": "YYYY-MM-DD"}  # übrige ui_data erhalten
    assert not any("Datumsformat" in m and m.startswith("Hinweis") for m in messages)

    # Das Geräte-Passwort (auch als AP-Passwort) taucht nirgends im Log auf
    assert device_password(sim.mac) not in caplog.text + "\n".join(messages)


async def test_device_without_led_component_gets_hint(ctx, network):
    sim = shelly(network, ip="192.168.10.200", app="Mini1PMG3", ver="2.0.1")
    messages: list[str] = []
    result = await provision(ctx, "192.168.10.200", "Flur", update=False, certificate=False, progress=messages.append)
    assert "PLUGS_UI" not in sim.config
    assert any("LED-Anzeige: vom Gerät nicht unterstützt" in m for m in messages)
    assert result.defaults is not None and "LED-Anzeige aus" not in result.defaults.applied


async def test_old_firmware_disables_bluetooth_completely(ctx, network):
    sim = shelly(network, ip="192.168.10.200", ver="1.4.4")
    await provision(ctx, "192.168.10.200", "Flur", update=False, certificate=False)
    assert sim.config["BLE"] == {"rpc": {"enable": False}, "enable": False}


async def test_defaults_can_be_skipped(ctx, network):
    sim = shelly(network, ip="192.168.10.200", ver="2.0.1")
    await provision(ctx, "192.168.10.200", "Flur", update=False, defaults=False, certificate=False)
    assert sim.config == {}


@pytest.mark.parametrize("value", ["Kueche-Licht", "LAMPE1", "a"])
def test_name_keeps_case(value):
    assert validate_name(f"  {value} ") == value


@pytest.mark.parametrize("value", ["Küche", "Kueche Licht", "Kueche.lan", "-Kueche", "x" * 64])
def test_invalid_names(value):
    with pytest.raises(ServiceError, match="Ungültiger Name"):
        validate_name(value)


async def test_distribute_ntp_to_all_devices(ctx, network):
    a = shelly(network, mac="A8032AB10001", ip="192.168.10.200", ver="2.0.1")
    b = shelly(network, mac="A8032AB10002", ip="192.168.10.201", ver="2.0.1")
    for sim in (a, b):
        await provision(ctx, sim.ip, f"Geraet-{sim.mac[-1]}", ip=sim.ip, update=False, certificate=False)
    ctx.settings.shelly_ntp_server = "ntp.example.org"
    messages: list[str] = []
    results = await distribute_ntp(ctx, messages.append)
    assert set(results.values()) == {"ok"}
    assert a.config["Sys"]["sntp"] == b.config["Sys"]["sntp"] == {"server": "ntp.example.org"}
    assert a.config["Sys"]["device"]["name"] == "Geraet-1"  # übrige Einstellungen bleiben unverändert
    assert messages[-1] == "NTP-Server ntp.example.org auf 2 Gerät(en) gesetzt"


async def test_distribute_ntp_reports_failures_but_continues(ctx, network):
    a = shelly(network, mac="A8032AB10001", ip="192.168.10.200", ver="2.0.1")
    b = shelly(network, mac="A8032AB10002", ip="192.168.10.201", ver="2.0.1")
    for sim in (a, b):
        await provision(ctx, sim.ip, f"Geraet-{sim.mac[-1]}", ip=sim.ip, update=False, certificate=False)
    a.online = False
    ctx.settings.shelly_ntp_server = "ntp.example.org"
    jobs = JobManager(ctx.store)
    job = jobs.start("NTP", None, lambda p: distribute_ntp(ctx, p))
    await jobs.wait(job.id)
    assert job.status == "error"
    assert "auf 1 Gerät(en) gesetzt, 1 fehlgeschlagen" in (job.error or "") and "nicht erreichbar" in (job.error or "")
    assert b.config["Sys"]["sntp"] == {"server": "ntp.example.org"}


def test_ntp_button_in_device_list(ctx):
    from fastapi.testclient import TestClient

    from device_manager.web.app import create_app

    with TestClient(create_app(ctx.settings, ctx), headers={"HX-Request": "true"}) as client:
        page = client.get("/").text
        assert 'hx-post="ntp/distribute"' in page and "time.cloudflare.com" in page
        html = client.post("/ntp/distribute").text
        assert "NTP-Server time.cloudflare.com verteilen" in html
        provision_page = client.get("/provision").text
        assert 'name="defaults"' in provision_page and "Europe/Berlin" in provision_page


@pytest.mark.parametrize(("field", "value"), [("shelly_latitude", 91), ("shelly_longitude", -181)])
def test_coordinates_are_validated(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


async def test_coordinates_are_set_when_configured(ctx, network, opnsense):
    ctx.settings.shelly_latitude, ctx.settings.shelly_longitude = 52.52, 13.4
    sim = shelly(network, ip="192.168.10.201", app="PlugMG3", ver="2.2.0")
    await provision(ctx, "192.168.10.201", "Flur")
    assert sim.config["Sys"]["location"] == {"tz": "Europe/Berlin", "lat": 52.52, "lon": 13.4}


def test_empty_coordinates_mean_unset():
    s = Settings(_env_file=None, shelly_latitude="", shelly_longitude=" ")
    assert (s.shelly_latitude, s.shelly_longitude) == (None, None)
