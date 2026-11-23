"""F6 – Gen4 im Zigbee-Modus: WLAN per Z2M an → Firmware-Update → WLAN aus."""

import json

import pytest

from device_manager.services import inventory
from device_manager.services.common import ServiceError
from device_manager.services.zigbee import zigbee_firmware_update
from tests.simulators.shelly_sim import ShellySim
from tests.simulators.z2m_sim import ZigbeeShelly


@pytest.fixture
async def gen4(z2m):
    dev = z2m.add(
        ZigbeeShelly(
            "Kueche",
            "0xabc",
            ShellySim(
                mac="CCDDEEFF0011",
                ip=None,
                gen=4,
                app="S1G4",
                model="S4SW-001X16EU",
                ver="1.6.2",
                available={"stable": "1.7.1"},
            ),
            wifi_ip="192.168.10.70",
        )
    )
    await z2m.start()
    return dev


async def test_wifi_enabled_update_wifi_disabled(ctx, gen4, bus):
    [device] = await inventory.sync_zigbee(ctx)
    messages = []
    result = await zigbee_firmware_update(ctx, device.key, "stable", messages.append)

    assert result.firmware.updated and gen4.shelly.ver == "1.7.1"
    assert result.ip == "192.168.10.70" and result.mac == "cc:dd:ee:ff:00:11"
    assert gen4.history == [True, False] and not gen4.wifi_enabled and result.wifi_disabled
    # WLAN-Daten wurden über wifi_config gesendet
    sets = [json.loads(p) for t, p in bus.published if t == "zigbee2mqtt/Kueche/set"]
    assert sets[0] == {"wifi_config": {"enabled": True, "ssid": "HomeWLAN", "password": "wlan-secret"}}
    # Inventar: Schlüssel ist jetzt die MAC, Verbindung bleibt Zigbee, keine dauerhafte IP
    stored = ctx.store.get_device("cc:dd:ee:ff:00:11")
    assert stored and stored.connection == "zigbee" and stored.firmware == "1.7.1" and stored.ip is None
    assert ctx.store.get_device("z2m:0xabc") is None


async def test_wifi_disabled_even_when_update_fails(ctx, gen4):
    gen4.shelly.update_breaks_device = True
    [device] = await inventory.sync_zigbee(ctx)
    with pytest.raises(ServiceError):
        await zigbee_firmware_update(ctx, device.key)
    assert gen4.history[-1] is False and not gen4.wifi_enabled


async def test_wifi_disabled_when_no_ip(ctx, gen4, z2m):
    z2m.expected = ("AnderesWLAN", "x")  # Gerät kommt nicht ins WLAN
    [device] = await inventory.sync_zigbee(ctx)
    with pytest.raises(Exception, match="keine IP"):
        await zigbee_firmware_update(ctx, device.key)
    assert gen4.history == [True, False]


async def test_error_if_wifi_cannot_be_disabled(ctx, gen4):
    gen4.ignore_disable = True
    [device] = await inventory.sync_zigbee(ctx)
    messages = []
    with pytest.raises(ServiceError, match="nicht deaktiviert"):
        await zigbee_firmware_update(ctx, device.key, progress=messages.append)
    assert any("FEHLER" in m for m in messages)


async def test_requires_wifi_credentials(ctx, gen4):
    ctx.settings = ctx.settings.model_copy(update={"wifi_ssid": ""})
    [device] = await inventory.sync_zigbee(ctx)
    with pytest.raises(ServiceError, match="WLAN-Zugangsdaten"):
        await zigbee_firmware_update(ctx, device.key)
    assert gen4.history == []


async def test_second_run_uses_mac_key(ctx, gen4):
    [device] = await inventory.sync_zigbee(ctx)
    await zigbee_firmware_update(ctx, device.key)
    gen4.shelly.available = {"stable": "1.8.0"}
    await inventory.sync_zigbee(ctx)  # findet Gerät über IEEE, kein Duplikat
    assert len(ctx.store.list_devices()) == 1
    result = await zigbee_firmware_update(ctx, "cc:dd:ee:ff:00:11")
    assert result.firmware.new_version == "1.8.0"
