"""F4 – Firmware-Update (stable/beta)."""

import pytest

from device_manager.services.common import ServiceError
from device_manager.services.firmware import update_firmware
from tests.conftest import shelly


async def test_stable_update(ctx, network):
    sim = shelly(network, ver="1.4.4", available={"stable": "1.5.1", "beta": "1.6.0-beta1"})
    result = await update_firmware(ctx, "192.168.10.50")
    assert (result.old_version, result.new_version, result.updated) == ("1.4.4", "1.5.1", True)
    assert sim.ver == "1.5.1"
    assert ctx.store.get_device(sim.mac_colon).firmware == "1.5.1"


async def test_beta_update_selectable(ctx, network):
    sim = shelly(network, ver="1.4.4", available={"stable": "1.5.1", "beta": "1.6.0-beta1"})
    result = await update_firmware(ctx, "192.168.10.50", "beta")
    assert result.new_version == "1.6.0-beta1" and sim.ver == "1.6.0-beta1"


async def test_no_update_available(ctx, network):
    sim = shelly(network, ver="1.5.1", available={"beta": "1.6.0-beta1"})
    result = await update_firmware(ctx, "192.168.10.50", "stable")
    assert not result.updated
    assert "Shelly.Update" not in sim.calls


async def test_update_with_authentication(ctx, network):
    from device_manager.services.adopt import adopt
    from tests.conftest import device_password, ha1_for

    sim = shelly(network, available={"stable": "1.5.1"})
    sim.ha1 = ha1_for(sim, device_password(sim.mac))
    await adopt(ctx, "192.168.10.50")
    result = await update_firmware(ctx, "192.168.10.50")
    assert result.updated


async def test_unknown_protected_device_gets_no_credentials(ctx, network):
    """Ohne Inventar-Eintrag werden keine Zugangsdaten gesendet."""
    from device_manager.shelly.client import ShellyAuthError
    from tests.conftest import device_password, ha1_for

    sim = shelly(network, available={"stable": "1.5.1"})
    sim.ha1 = ha1_for(sim, device_password(sim.mac))
    with pytest.raises(ShellyAuthError):
        await update_firmware(ctx, "192.168.10.50")
    assert not any("authorization" in r.headers for r in network.requests)


async def test_device_not_returning_times_out(ctx, network):
    shelly(network, available={"stable": "1.5.1"}, update_breaks_device=True)
    with pytest.raises(ServiceError, match="nicht mit neuer Firmware zurückgekehrt.*nicht erreichbar"):
        await update_firmware(ctx, "192.168.10.50")


async def test_invalid_stage(ctx, network):
    shelly(network)
    with pytest.raises(ServiceError, match="Kanal"):
        await update_firmware(ctx, "192.168.10.50", "nightly")
