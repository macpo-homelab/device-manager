"""F12 – Gerätestatus: Erreichbarkeit, verfügbare Firmware und Zertifikat abfragen, speichern und anzeigen."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from device_manager.services import inventory
from device_manager.services.common import ServiceError
from device_manager.shelly.client import ShellyError
from device_manager.store import Action, Device, Store
from device_manager.web import view
from tests.conftest import shelly
from tests.test_web import client, wait_job  # noqa: F401  (Fixture)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


async def test_check_status_stores_reachability_and_updates(ctx, network):
    shelly(network, ver="1.7.1", available={"stable": "2.0.1", "beta": "2.1.0-beta1"})
    device = await inventory.refresh_device(ctx, "192.168.10.50")
    checked = await inventory.check_status(ctx, device)
    stored = ctx.store.get_device(device.key)
    assert (stored.online, stored.fw_stable, stored.fw_beta) == (1, "2.0.1", "2.1.0-beta1")
    assert stored.checked_at and checked.firmware == "1.7.1"


async def test_check_status_marks_unreachable_device_offline(ctx, network):
    sim = shelly(network)
    device = await inventory.refresh_device(ctx, "192.168.10.50")
    sim.online = False
    with pytest.raises(ShellyError):
        await inventory.check_status(ctx, device)
    assert ctx.store.get_device(device.key).online == 0


async def test_check_status_detects_other_device_at_ip(ctx, network):
    old = shelly(network)
    device = await inventory.refresh_device(ctx, "192.168.10.50")
    network.devices.remove(old)
    shelly(network, mac="A8032AB99999", ip="192.168.10.50")
    with pytest.raises(ServiceError, match="anderes Gerät"):
        await inventory.check_status(ctx, device)
    assert ctx.store.get_device(device.key).online == 0


async def test_refresh_all_stores_status_for_all_devices(ctx, network):
    sim = shelly(network, available={"stable": "2.0.1"})
    shelly(network, mac="A8032AB99999", ip="192.168.10.51")
    await inventory.refresh_device(ctx, "192.168.10.50")
    await inventory.refresh_device(ctx, "192.168.10.51")
    sim.online = False
    errors = await inventory.refresh_all(ctx)
    assert list(errors) == ["a8:03:2a:b1:23:45"]
    assert ctx.store.get_device("a8:03:2a:b1:23:45").online == 0
    assert ctx.store.get_device("a8:03:2a:b9:99:99").online == 1


def test_old_database_gets_status_columns(tmp_path):
    path = tmp_path / "alt.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE devices (key TEXT PRIMARY KEY, mac TEXT, device_id TEXT, name TEXT, model TEXT, "
        "app TEXT, gen INTEGER, firmware TEXT, ip TEXT, hostname TEXT, "
        "connection TEXT NOT NULL DEFAULT 'wifi', "
        "z2m_name TEXT, ieee TEXT, cert_not_after TEXT, auth_mode TEXT, updated_at TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO devices (key, updated_at) VALUES ('aa', 'x')")
    conn.commit()
    conn.close()
    device = Store(path).get_device("aa")
    assert device is not None and device.online is None and device.fw_stable is None


def _dev(**kw) -> Device:
    base = {"key": "k", "firmware": "1.0.0", "online": 1, "cert_not_after": (NOW + timedelta(days=200)).isoformat()}
    return Device(**{**base, **kw})


@pytest.mark.parametrize(
    ("kw", "level", "note"),
    [
        ({}, "ok", None),
        ({"online": None}, "unknown", None),
        ({"online": 0}, "error", "nicht erreichbar"),
        ({"fw_stable": "1.1.0"}, "warn", "Firmware 1.1.0 verfügbar"),
        ({"fw_stable": "1.0.0"}, "ok", None),  # bereits installiert
        ({"fw_beta": "1.1.0-beta1"}, "ok", None),  # anderer Kanal
        ({"cert_not_after": None}, "warn", "Zertifikat fehlt"),
        ({"cert_not_after": (NOW + timedelta(days=10)).isoformat()}, "warn", "Zertifikat läuft in 10 Tagen ab"),
        ({"cert_not_after": (NOW - timedelta(days=1)).isoformat()}, "error", "Zertifikat abgelaufen am 08.11.2026"),
    ],
)
def test_device_state_led(kw, level, note):
    state = view.device_state(_dev(**kw), "stable", NOW)
    assert state.level == level
    assert (note in state.notes) if note else not state.notes


def test_zigbee_device_has_no_led_status():
    state = view.device_state(_dev(connection="zigbee", online=None), "stable", NOW)
    assert state.level == "unknown" and "Zigbee" in state.label


def test_log_entries_use_names_and_shorten_cause_chains():
    long_cause = "OPNsense nicht erreichbar" + view.CAUSE_SEPARATOR + "ConnectError: x" * 3
    actions = [
        Action(1, "2026-11-09T10:00:00+00:00", "k", "Übernehmen", "error", long_cause),
        Action(2, "2026-11-09T10:00:00+00:00", "gone", "Firmware", "ok", "fertig"),
    ]
    err, ok = view.log_entries(actions, [Device(key="k", name="Flur")])
    assert (err.device_name, err.known, err.ok, err.short) == ("Flur", True, False, "OPNsense nicht erreichbar")
    assert err.detail == long_cause
    assert (ok.device_name, ok.known, ok.detail) == ("gone", False, "")


def test_ui_shows_status_after_refresh(client, network):  # noqa: F811
    sim = shelly(network, ver="1.7.1", available={"stable": "2.0.1"})
    assert wait_job(client, client.post("/adopt", data={"host": "192.168.10.50"}).text)["status"] == "ok"
    r = client.post("/refresh")
    assert "Status aller Geräte abgefragt" in r.text
    page = client.get("/").text
    assert 'class="led warn"' in page and "Update 2.0.1 verfügbar" in page and "Status zuletzt abgefragt" in page

    sim.online = False
    r = client.post(f"/devices/{sim.mac_colon}/refresh")
    assert "data-reload" in r.text
    device_page = client.get(f"/devices/{sim.mac_colon}").text
    assert 'class="led error"' in device_page and "nicht erreichbar" in device_page


def test_log_page_filters_by_device(client, ctx, network):  # noqa: F811
    sim = shelly(network)
    wait_job(client, client.post("/adopt", data={"host": "192.168.10.50"}).text)
    ctx.store.log_action(sim.mac_colon, "Firmware-Update (stable)", "ok", "fertig")
    ctx.store.log_action("andere", "Zertifikat installieren", "error", "kaputt")
    page = client.get("/log", params={"device": sim.mac_colon}).text
    assert "Alle Geräte anzeigen" in page and "Firmware-Update (stable)" in page
    assert "Zertifikat installieren" not in page and 'aria-current="page"' in page
    assert f'href="devices/{sim.mac_colon}"' in client.get("/log").text  # Gerätename verlinkt
