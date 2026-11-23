"""F1 – Geräte-Inventar."""

import pytest

from device_manager.services import inventory
from device_manager.shelly.client import ShellyUnsupported
from tests.conftest import shelly


async def test_refresh_device_reads_info_into_inventory(ctx, network):
    shelly(network, ver="1.4.4", gen=3, name="Flur")
    device = await inventory.refresh_device(ctx, "192.168.10.50")
    assert device.key == "a8:03:2a:b1:23:45"
    stored = ctx.store.get_device(device.key)
    assert (stored.gen, stored.firmware, stored.ip, stored.name) == (3, "1.4.4", "192.168.10.50", "Flur")


async def test_refresh_keeps_manual_fields(ctx, network):
    sim = shelly(network)
    device = await inventory.refresh_device(ctx, "192.168.10.50")
    device.hostname = "flur-licht"
    ctx.store.upsert_device(device)
    sim.ver = "1.5.0"
    updated = await inventory.refresh_device(ctx, "192.168.10.50")
    assert updated.hostname == "flur-licht" and updated.firmware == "1.5.0"


async def test_gen1_is_rejected(ctx, network):
    shelly(network, gen=1)
    with pytest.raises(ShellyUnsupported):
        await inventory.refresh_device(ctx, "192.168.10.50")


async def test_discover_from_leases_confirms_shellys(ctx, network, opnsense):
    shelly(network, mac="A8032AB12345", ip="192.168.10.50")
    shelly(network, mac="A8032AB99999", ip="192.168.10.51")
    opnsense.add_lease("192.168.10.50", "a8:03:2a:b1:23:45", "shellymini1pmg3-a8032ab12345")
    opnsense.add_lease("192.168.10.51", "a8:03:2a:b9:99:99", "ShellyPlus1")
    opnsense.add_lease("192.168.10.60", "11:22:33:44:55:66", "laptop")
    opnsense.add_lease("192.168.10.61", "11:22:33:44:55:77", "shelly-offline")  # nicht erreichbar

    found = await inventory.discover_from_leases(ctx)
    assert sorted(c.ip for c in found) == ["192.168.10.50", "192.168.10.51"]
    assert all(not c.known for c in found)


async def test_discover_marks_known_devices(ctx, network, opnsense):
    shelly(network)
    opnsense.add_lease("192.168.10.50", "a8:03:2a:b1:23:45", "shelly1")
    await inventory.refresh_device(ctx, "192.168.10.50")
    [candidate] = await inventory.discover_from_leases(ctx)
    assert candidate.known


async def test_refresh_all_reports_unreachable(ctx, network):
    sim = shelly(network)
    await inventory.refresh_device(ctx, "192.168.10.50")
    sim.online = False
    errors = await inventory.refresh_all(ctx)
    assert "a8:03:2a:b1:23:45" in errors


async def test_sync_zigbee_imports_only_shelly_devices(ctx, z2m, network):
    from tests.simulators.shelly_sim import ShellySim
    from tests.simulators.z2m_sim import ZigbeeShelly

    z2m.add(ZigbeeShelly("Kueche", "0xabc", ShellySim(mac="CCDDEEFF0011", ip=None, gen=4), "192.168.10.70"))
    await z2m.start()
    devices = await inventory.sync_zigbee(ctx)
    assert [(d.key, d.connection, d.z2m_name) for d in devices] == [("z2m:0xabc", "zigbee", "Kueche")]
    # zweiter Abgleich erzeugt keine Duplikate
    await inventory.sync_zigbee(ctx)
    assert len(ctx.store.list_devices()) == 1
