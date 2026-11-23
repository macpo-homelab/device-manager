"""Regressionstests zum Code-Review nach dem Security-Fix (Punkte 1–10)."""

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from device_manager.__main__ import warn_host_mismatch
from device_manager.config import Settings
from device_manager.services import inventory
from device_manager.services.adopt import adopt
from device_manager.services.certificate import install_certificate
from device_manager.services.firmware import update_firmware
from device_manager.services.identity import rotate_to_device_password
from device_manager.services.reservation import reserve
from device_manager.services.zigbee import zigbee_firmware_update
from device_manager.store import Device, Store
from device_manager.web.app import create_app
from tests.conftest import SHELLY_PASSWORD, device_password, ha1_for, shelly
from tests.simulators.shelly_sim import ShellySim
from tests.simulators.z2m_sim import ZigbeeShelly

V01_SCHEMA = """
CREATE TABLE devices (key TEXT PRIMARY KEY, mac TEXT, device_id TEXT, name TEXT, model TEXT, app TEXT,
  gen INTEGER, firmware TEXT, ip TEXT, hostname TEXT, connection TEXT NOT NULL DEFAULT 'wifi', z2m_name TEXT,
  ieee TEXT, cert_not_after TEXT, updated_at TEXT NOT NULL);
CREATE TABLE actions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, device_key TEXT,
  action TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL);
"""


# 1 + 2: Migration und unbekannter auth_mode ---------------------------------------------------------


def test_migration_marks_v01_devices_as_shared(tmp_path):
    db = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript(V01_SCHEMA)
    conn.execute("INSERT INTO devices (key, mac, ip, connection, updated_at) VALUES ('a', 'a', '1.2.3.4', 'wifi', 'x')")
    conn.execute("INSERT INTO devices (key, connection, updated_at) VALUES ('z2m:0x1', 'zigbee', 'x')")
    conn.commit()
    conn.close()
    store = Store(db)
    assert store.get_device("a").auth_mode == "shared"
    assert store.get_device("z2m:0x1").auth_mode is None
    store.close()


async def test_upgraded_v01_device_still_works_with_shared_password(ctx, network):
    sim = shelly(network, available={"stable": "1.5.0"})
    sim.ha1 = ha1_for(sim, SHELLY_PASSWORD)
    ctx.store.upsert_device(
        Device(key=sim.mac_colon, mac=sim.mac_colon, device_id=sim.device_id, ip="192.168.10.50", auth_mode="shared")
    )
    assert (await update_firmware(ctx, "192.168.10.50")).updated


def test_unknown_auth_mode_sends_no_credentials(ctx):
    device = Device(key="aa:bb:cc:dd:ee:ff", mac="aa:bb:cc:dd:ee:ff", device_id="x-aabbccddeeff", auth_mode=None)
    assert ctx.device_password(device) == ""


# 3: veraltete IP-Zuordnungen -------------------------------------------------------------------------


async def test_ip_belongs_to_one_device_only(ctx, network):
    a = shelly(network, mac="A8032AB12345", ip="192.168.10.50")
    await adopt(ctx, "192.168.10.50")
    a.ip = "192.168.10.51"  # A zieht außerhalb des Managers um
    b = shelly(network, mac="A8032AB99999", ip="192.168.10.50", available={"stable": "1.5.0"})
    await adopt(ctx, "192.168.10.50")
    assert ctx.store.get_device(a.mac_colon).ip is None  # veraltete Zuordnung entfernt
    device_b = ctx.store.get_device(b.mac_colon)
    result = await update_firmware(ctx, "192.168.10.50", device=device_b)
    assert result.updated and b.ver == "1.5.0"


# 4: Meldung ohne OPNsense ----------------------------------------------------------------------------


async def test_adopt_message_without_opnsense(ctx, network):
    shelly(network)
    ctx.settings = ctx.settings.model_copy(update={"opnsense_url": ""})
    messages: list[str] = []
    await adopt(ctx, "192.168.10.50", messages.append)
    assert any("ohne OPNsense kein Abgleich" in m for m in messages)
    assert not any("DHCP-Lease)" in m for m in messages)


# 5: Gen4 nach Werksreset wieder schützen -----------------------------------------------------------


async def test_zigbee_factory_reset_device_gets_password_again(ctx, z2m):
    dev = z2m.add(
        ZigbeeShelly(
            "Flur",
            "0xaaa",
            ShellySim(mac="CCDDEEFF0033", ip=None, gen=4, available={"stable": "1.7.1"}),
            "192.168.10.72",
        )
    )
    await z2m.start()
    [device] = await inventory.sync_zigbee(ctx)
    await zigbee_firmware_update(ctx, device.key)
    assert dev.shelly.ha1 == ha1_for(dev.shelly, device_password(dev.shelly.mac))

    dev.shelly.ha1 = None  # Werksreset: Gerät wieder ohne Passwort
    dev.shelly.available = {"stable": "1.8.0"}
    await zigbee_firmware_update(ctx, "cc:dd:ee:ff:00:33")
    assert dev.shelly.ha1 == ha1_for(dev.shelly, device_password(dev.shelly.mac))
    assert ctx.store.get_device("cc:dd:ee:ff:00:33").auth_mode == "device"


# 6: Hostname-Vergleich normalisiert -----------------------------------------------------------------


async def test_certificate_accepts_external_reservation_hostname_variants(ctx, network, opnsense):
    sim = shelly(network, ver="2.0.1")
    device = await adopt(ctx, "192.168.10.50")
    result = await reserve(ctx, device.mac, "flur", "192.168.10.50")
    opnsense.reservations[result.uuid]["hostname"] = "Flur.home.arpa"  # außerhalb angelegt/geändert
    await install_certificate(ctx, "192.168.10.50", "flur")
    assert sim.https_active


# 7: Passwort-Umstellung bei verlorener Antwort -----------------------------------------------------


async def test_rotate_survives_lost_setauth_response(ctx, network):
    sim = shelly(network)
    sim.ha1 = ha1_for(sim, SHELLY_PASSWORD)
    device = await adopt(ctx, "192.168.10.50")
    assert device.auth_mode == "shared"
    sim.lose_setauth_response = True
    messages: list[str] = []
    rotated = await rotate_to_device_password(ctx, device, messages.append)
    assert rotated.auth_mode == "device"
    assert any("ohne Bestätigung" in m for m in messages)
    await ctx.shelly("192.168.10.50").check_auth()


async def test_rotate_rejects_other_device_behind_ip(ctx, network):
    from device_manager.services.common import ServiceError

    sim = shelly(network)
    sim.ha1 = ha1_for(sim, SHELLY_PASSWORD)
    device = await adopt(ctx, "192.168.10.50")
    sim.online = False
    other = shelly(network, mac="DEADBEEF0001", ip="192.168.10.50")
    with pytest.raises(ServiceError, match="anderes Gerät"):
        await rotate_to_device_password(ctx, device)
    assert other.calls == []


# 8: Warnung bei LAN-Bind ohne passende Hosts -------------------------------------------------------


@pytest.mark.parametrize(
    ("bind", "hosts", "warn"),
    [
        ("0.0.0.0", ["127.0.0.1", "localhost", "::1"], True),
        ("0.0.0.0", ["127.0.0.1", "manager.lan"], False),
        ("127.0.0.1", ["127.0.0.1", "localhost", "::1"], False),
        ("0.0.0.0", [], False),
    ],
)
def test_warn_host_mismatch(bind, hosts, warn):
    assert warn_host_mismatch(Settings(_env_file=None, bind_host=bind, allowed_hosts=hosts)) is warn


# 9: Master-Passwort wird nie angezeigt ---------------------------------------------------------------


def test_shared_device_password_is_not_shown(ctx, network):
    sim = shelly(network)
    sim.ha1 = ha1_for(sim, SHELLY_PASSWORD)
    with TestClient(create_app(ctx.settings, ctx), headers={"HX-Request": "true"}) as client:
        client.post("/adopt", data={"host": "192.168.10.50"})
        for _ in range(200):
            if ctx.store.get_device(sim.mac_colon):
                break
            time.sleep(0.01)
        assert ctx.store.get_device(sim.mac_colon).auth_mode == "shared"
        html = client.post(f"/devices/{sim.mac_colon}/password").text
        page = client.get(f"/devices/{sim.mac_colon}").text
    assert SHELLY_PASSWORD not in html and "umstellen" in html
    assert "Passwort anzeigen" not in page and "Auf eigenes Passwort umstellen" in page
