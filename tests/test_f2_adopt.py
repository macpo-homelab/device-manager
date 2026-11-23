"""F2 – Gerät übernehmen (per Shelly-App ins WLAN gebracht), inkl. Identitätsprüfung und Geräte-Passwort."""

import pytest

from device_manager.services.adopt import adopt
from device_manager.services.common import ServiceError
from device_manager.services.identity import rotate_to_device_password
from device_manager.shelly.client import derive_password
from tests.conftest import SHELLY_PASSWORD, device_password, ha1_for, shelly


async def test_adopt_sets_device_specific_password(ctx, network):
    sim = shelly(network)
    assert sim.ha1 is None
    messages = []
    device = await adopt(ctx, "192.168.10.50", messages.append)

    derived = device_password(sim.mac)
    assert derived != SHELLY_PASSWORD and len(derived) >= 20
    assert sim.ha1 == ha1_for(sim, derived)
    assert device.mac == "a8:03:2a:b1:23:45" and device.auth_mode == "device"
    assert any("Gerätespezifisches Passwort gesetzt" in m for m in messages)
    # Danach funktionieren authentifizierte Aufrufe über das Inventar (Digest SHA-256 + Realm-Prüfung)
    await ctx.shelly("192.168.10.50").check_auth()


def test_derived_passwords_differ_per_device():
    assert derive_password("m", "A8032AB12345") != derive_password("m", "A8032AB12346")
    assert derive_password("m", "A8032AB12345") == derive_password("m", "a8:03:2a:b1:23:45")


async def test_adopt_accepts_existing_device_password(ctx, network):
    sim = shelly(network)
    sim.ha1 = ha1_for(sim, device_password(sim.mac))
    device = await adopt(ctx, "192.168.10.50")
    assert device.auth_mode == "device"
    assert "Shelly.SetAuth" not in sim.calls


async def test_adopt_accepts_legacy_shared_password_and_rotates(ctx, network):
    sim = shelly(network)
    sim.ha1 = ha1_for(sim, SHELLY_PASSWORD)
    messages = []
    device = await adopt(ctx, "192.168.10.50", messages.append)
    assert device.auth_mode == "shared"
    assert any("Umstellung empfohlen" in m for m in messages)
    await ctx.shelly("192.168.10.50").check_auth()  # mit gemeinsamem Passwort

    rotated = await rotate_to_device_password(ctx, device)
    assert rotated.auth_mode == "device"
    assert sim.ha1 == ha1_for(sim, device_password(sim.mac))
    await ctx.shelly("192.168.10.50").check_auth()


async def test_adopt_rejects_foreign_password(ctx, network):
    sim = shelly(network)
    sim.ha1 = ha1_for(sim, "anderes")
    with pytest.raises(ServiceError, match="anderen Passwort"):
        await adopt(ctx, "192.168.10.50")
    assert ctx.store.list_devices() == []


async def test_adopt_is_idempotent(ctx, network):
    shelly(network)
    await adopt(ctx, "192.168.10.50")
    await adopt(ctx, "192.168.10.50")
    assert len(ctx.store.list_devices()) == 1


# Sicherheit: Identitätsprüfung (Security-Review, Schwachstelle 1) -----------------------------


async def test_lease_mac_mismatch_blocks_adoption(ctx, network, opnsense):
    victim = shelly(network, mac="A8032AB12345", ip="192.168.10.50")
    fake = shelly(network, mac="A8032AB12345", ip="192.168.10.66")  # meldet ID+MAC des Opfers
    opnsense.add_lease("192.168.10.66", "de:ad:be:ef:00:01", "shelly-fake")  # echte MAC des Angreifers
    with pytest.raises(ServiceError, match="passt nicht zur DHCP-Lease"):
        await adopt(ctx, "192.168.10.66")
    assert fake.calls == [] and fake.ha1 is None  # keinerlei RPC/Credentials an den Angreifer
    assert victim.ha1 is None and ctx.store.list_devices() == []


async def test_id_not_matching_mac_is_rejected(ctx, network):
    victim = shelly(network, mac="A8032AB12345", ip="192.168.10.50")
    # Angreifer meldet die ID des Opfers, aber seine eigene MAC (passend zur Lease)
    fake = shelly(network, mac="DEADBEEF0001", ip="192.168.10.66", id_override=victim.device_id)
    with pytest.raises(ServiceError, match="passt nicht zur MAC"):
        await adopt(ctx, "192.168.10.66")
    assert fake.calls == []


async def test_duplicate_device_id_is_rejected(ctx, network):
    from device_manager.store import Device

    clone = shelly(network, mac="A8032AB12345", ip="192.168.10.51")
    # Inventar kennt diese Geräte-ID bereits unter einer anderen MAC
    ctx.store.upsert_device(Device(key="11:22:33:44:55:66", mac="11:22:33:44:55:66", device_id=clone.device_id))
    with pytest.raises(ServiceError, match="gehört bereits"):
        await adopt(ctx, "192.168.10.51")
    assert clone.calls == [] and clone.ha1 is None


@pytest.mark.parametrize(
    ("host", "message"),
    [
        ("evil.example", "keine IPv4-Adresse"),
        ("8.8.8.8", "keine private LAN-Adresse"),
        ("127.0.0.1", "keine private LAN-Adresse"),
    ],
)
async def test_only_private_ipv4_targets(ctx, network, host, message):
    with pytest.raises(ServiceError, match=message):
        await adopt(ctx, host)
    assert network.requests == []  # nicht einmal /shelly wird abgefragt


async def test_device_networks_restrict_targets(ctx, network):
    shelly(network, ip="192.168.10.50")
    ctx.settings = ctx.settings.model_copy(update={"device_networks": ["192.168.20.0/24"]})
    with pytest.raises(ServiceError, match="nicht in den Gerätenetzen"):
        await adopt(ctx, "192.168.10.50")
