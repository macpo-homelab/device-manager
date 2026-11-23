"""F3 – DHCP-Reservierung in OPNsense (Kea)."""

import pytest

from device_manager.opnsense.client import OPNsenseError
from device_manager.services import reservation
from device_manager.services.common import ServiceError

MAC = "a8:03:2a:b1:23:45"


async def test_reserves_next_free_ip_from_range(ctx, opnsense):
    other = opnsense.add_subnet("10.0.0.0/24")  # weiteres Subnetz darf nicht gewählt werden
    [subnet_uuid] = [u for u, c in opnsense.subnets.items() if c == "192.168.10.0/24"]
    await reservation.reserve(ctx, "AA:AA:AA:AA:AA:01", "belegt1", "192.168.10.100")
    opnsense.add_lease("192.168.10.101", "aa:aa:aa:aa:aa:02", "laptop")

    result = await reservation.reserve(ctx, "A8032AB12345", "flur-licht")

    assert result.ip == "192.168.10.102"
    stored = opnsense.reservations[result.uuid]
    assert stored == {
        "subnet": subnet_uuid,
        "ip_address": "192.168.10.102",
        "hw_address": MAC,
        "hostname": "flur-licht",
        "description": "Shelly",  # Gerät nicht im Inventar -> kein Modell bekannt
    }
    assert stored["subnet"] != other
    assert opnsense.applied[MAC] == "192.168.10.102"  # Kea reconfigure ausgeführt


async def test_manual_ip_is_used(ctx, opnsense):
    result = await reservation.reserve(ctx, MAC, "flur", "192.168.10.150")
    assert result.ip == "192.168.10.150"


async def test_manual_ip_conflict_is_rejected(ctx, opnsense):
    opnsense.add_lease("192.168.10.120", "11:11:11:11:11:11", "pc")
    with pytest.raises(ServiceError, match="bereits"):
        await reservation.reserve(ctx, MAC, "flur", "192.168.10.120")
    assert opnsense.reservations == {}


async def test_own_lease_is_not_a_conflict(ctx, opnsense):
    opnsense.add_lease("192.168.10.100", MAC, "shelly")
    result = await reservation.reserve(ctx, MAC, "flur")
    assert result.ip == "192.168.10.100"


async def test_idempotent_second_call_changes_nothing(ctx, opnsense):
    first = await reservation.reserve(ctx, MAC, "flur")
    count = opnsense.reconfigures
    second = await reservation.reserve(ctx, MAC, "flur")
    assert (second.ip, second.uuid, second.changed) == (first.ip, first.uuid, False)
    assert opnsense.reconfigures == count
    assert len(opnsense.reservations) == 1


async def test_existing_reservation_is_updated(ctx, opnsense):
    first = await reservation.reserve(ctx, MAC, "flur")
    second = await reservation.reserve(ctx, MAC, "flur-neu", "192.168.10.105")
    assert second.uuid == first.uuid and second.changed
    assert opnsense.reservations[first.uuid]["hostname"] == "flur-neu"
    assert len(opnsense.reservations) == 1


async def test_hostname_must_be_unique(ctx, opnsense):
    await reservation.reserve(ctx, "11:22:33:44:55:66", "flur")
    with pytest.raises(ServiceError, match="Name 'Flur' ist bereits"):
        await reservation.reserve(ctx, MAC, "Flur")  # Groß-/Kleinschreibung zählt nicht


@pytest.mark.parametrize("hostname", ["", "Flur Licht", "-flur", "flur_licht", "a" * 64])
async def test_invalid_hostname_rejected(ctx, hostname):
    with pytest.raises(ServiceError, match="Ungültiger Name"):
        await reservation.reserve(ctx, MAC, hostname)


async def test_range_exhausted(ctx, opnsense):
    for i in range(11):
        opnsense.add_lease(f"192.168.10.{100 + i}", f"11:11:11:11:11:{i:02x}")
    with pytest.raises(ServiceError, match="Keine freie IP"):
        await reservation.reserve(ctx, MAC, "flur")


async def test_ip_outside_any_subnet(ctx):
    with pytest.raises(ServiceError, match="Subnetz"):
        await reservation.reserve(ctx, MAC, "flur", "172.16.0.5")


async def test_wrong_api_credentials(ctx):
    from pydantic import SecretStr

    ctx.settings = ctx.settings.model_copy(update={"opnsense_api_secret": SecretStr("falsch")})
    with pytest.raises(OPNsenseError, match="abgelehnt"):
        await reservation.reserve(ctx, MAC, "flur")


async def test_apply_reservation_reboots_to_new_ip(ctx, network, opnsense):
    from device_manager.services.inventory import refresh_device
    from tests.conftest import shelly

    sim = shelly(network, ip="192.168.10.50")
    await refresh_device(ctx, "192.168.10.50")
    result = await reservation.reserve(ctx, MAC, "flur")
    await reservation.apply_reservation(ctx, "192.168.10.50", result)
    assert sim.ip == result.ip == "192.168.10.100" and sim.reboots == 1
    assert ctx.store.find_device(mac=MAC).ip == "192.168.10.100"


async def test_next_free_ip_suggestion(ctx, opnsense):
    opnsense.add_lease("192.168.10.100", "11:11:11:11:11:11")
    assert await reservation.next_free_ip(ctx) == "192.168.10.101"
