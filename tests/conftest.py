from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509

from device_manager.config import Settings
from device_manager.context import Context
from device_manager.store import Store
from device_manager.tls import CertInfo, cert_info
from device_manager.z2m.bus import InMemoryBus
from tests.simulators.network import SimNetwork
from tests.simulators.opnsense_sim import API_KEY, API_SECRET, OPNsenseSim
from tests.simulators.shelly_sim import ShellySim
from tests.simulators.z2m_sim import Z2MSim

OPN_HOST = "opnsense.test"
CA_NAME = "Heimnetz CA"
SHELLY_PASSWORD = "shelly-shared-pw"
WIFI_SSID, WIFI_PASSWORD = "HomeWLAN", "wlan-secret"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        data_dir=tmp_path,
        shelly_password=SHELLY_PASSWORD,
        poll_interval=0.01,
        reboot_timeout=1.0,
        firmware_timeout=1.0,
        firmware_poll_interval=0.01,
        firmware_reboot_wait=0.5,
        zigbee_wifi_timeout=1.0,
        opnsense_url=f"https://{OPN_HOST}",
        opnsense_api_key=API_KEY,
        opnsense_api_secret=API_SECRET,
        ip_range_start="192.168.10.100",
        ip_range_end="192.168.10.110",
        dns_domain="home.lan",
        cert_ca_name=CA_NAME,
        wifi_ssid=WIFI_SSID,
        wifi_password=WIFI_PASSWORD,
        allowed_hosts=["testserver"],
    )


@pytest.fixture
def network() -> SimNetwork:
    return SimNetwork()


@pytest.fixture
def opnsense(network: SimNetwork) -> OPNsenseSim:
    sim = OPNsenseSim()
    sim.add_subnet("192.168.10.0/24")
    sim.add_ca(CA_NAME)
    network.add_server(OPN_HOST, sim.app)
    network.dhcp_lookup = sim.dhcp_lookup
    sim.lease_source = lambda: [(d.ip, d.mac_colon, d.device_id) for d in network.devices if d.online and d.ip]
    return sim


def make_tls_verifier(network: SimNetwork):  # type: ignore[no-untyped-def]
    """Ersetzt den TLS-Handshake: prüft das *aktive* Zertifikat des Simulators gegen die CA und den Hostnamen."""

    async def verify(host: str, *, server_hostname: str, ca_pem: str, **_: Any) -> CertInfo:
        device = network.by_ip(host)
        if device is None or not device.https_active or not device.active_cert:
            raise ConnectionRefusedError(f"{host}:443 – kein HTTPS aktiv")
        cert = x509.load_pem_x509_certificate(device.active_cert.encode())
        ca = x509.load_pem_x509_certificate(ca_pem.encode())
        cert.verify_directly_issued_by(ca)
        info = cert_info(device.active_cert)
        if server_hostname not in info.dns_names:
            raise ValueError(f"Hostname {server_hostname} nicht im Zertifikat")
        return info

    return verify


async def fast_sleep(_: float) -> None:
    await asyncio.sleep(0)


@pytest.fixture
def store(settings: Settings) -> Iterator[Store]:
    s = Store(settings.db_path)
    yield s
    s.close()


@pytest.fixture
def bus() -> InMemoryBus:
    return InMemoryBus()


@pytest.fixture
def ctx(settings: Settings, store: Store, network: SimNetwork, opnsense: OPNsenseSim, bus: InMemoryBus) -> Context:
    return Context(
        settings=settings,
        store=store,
        shelly_transport=network,
        opnsense_transport=network,
        mqtt_bus=bus,
        tls_verifier=make_tls_verifier(network),
        sleep=fast_sleep,
    )


@pytest.fixture
async def z2m(bus: InMemoryBus, network: SimNetwork) -> AsyncIterator[Z2MSim]:
    sim = Z2MSim(bus, network, expected_ssid=WIFI_SSID, expected_password=WIFI_PASSWORD)
    yield sim
    await sim.stop()


def device_password(mac: str) -> str:
    from device_manager.shelly.client import derive_password

    return derive_password(SHELLY_PASSWORD, mac)


def ha1_for(sim: ShellySim, password: str) -> str:
    import hashlib

    return hashlib.sha256(f"admin:{sim.device_id}:{password}".encode()).hexdigest()


def shelly(network: SimNetwork, mac: str = "A8032AB12345", ip: str = "192.168.10.50", **kw: Any) -> ShellySim:
    return network.add_device(ShellySim(mac=mac, ip=ip, **kw))
