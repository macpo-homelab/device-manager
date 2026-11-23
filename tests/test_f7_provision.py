"""F7 – Assistent „Neues Gerät komplett“."""

from device_manager.services.provision import provision
from device_manager.tls import cert_info
from tests.conftest import shelly


async def test_full_provisioning(ctx, network, opnsense):
    sim = shelly(network, ip="192.168.10.200", ver="1.7.1", available={"stable": "2.0.1"})
    messages = []
    result = await provision(ctx, "192.168.10.200", "wohnzimmer", progress=messages.append)

    # Übernommen + Passwort
    assert sim.ha1 is not None
    # Reservierung + neue IP nach Neustart
    assert result.reservation.ip == "192.168.10.100" and sim.ip == "192.168.10.100"
    # Firmware (2.0.1 ist Voraussetzung für das Zertifikat)
    assert result.firmware.new_version == "2.0.1"
    # Zertifikat aktiv mit neuer IP im SAN
    info = cert_info(sim.active_cert)
    assert info.subject_cn == "wohnzimmer.home.lan" and info.ip_addresses == ("192.168.10.100",)
    device = ctx.store.get_device(sim.mac_colon)
    assert (device.ip, device.hostname, device.firmware) == ("192.168.10.100", "wohnzimmer", "2.0.1")
    assert messages[-1] == "Gerät vollständig eingerichtet"


async def test_provisioning_without_optional_steps(ctx, network):
    sim = shelly(network, ip="192.168.10.105")
    result = await provision(
        ctx, "192.168.10.105", "keller", ip="192.168.10.105", update=False, defaults=False, certificate=False
    )
    assert result.firmware is None and result.certificate is None
    assert sim.reboots == 0  # IP unverändert -> kein Neustart
