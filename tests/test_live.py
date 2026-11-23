"""Live-Smoke-Tests gegen die echte OPNsense bzw. ein echtes Shelly – nur lesend.

Aktivieren:  pytest -m live
Benötigt die normale Konfiguration (.env) und optional DM_LIVE_SHELLY_IP.
"""

import os

import pytest

from device_manager.config import load_settings
from device_manager.context import Context
from device_manager.store import Store

pytestmark = pytest.mark.live


@pytest.fixture
def live_ctx(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    if not settings.opnsense_url:
        pytest.skip("DM_OPNSENSE_URL nicht gesetzt")
    return Context(settings=settings, store=Store(tmp_path / "live.sqlite3"))


async def test_kea_api_shapes(live_ctx):
    api = live_ctx.opnsense()
    subnets = await api.kea_subnets()
    assert subnets, "Kein Kea-Subnetz gefunden"
    reservations = await api.kea_reservations()
    leases = await api.kea_leases()
    print(f"{len(subnets)} Subnetze, {len(reservations)} Reservierungen, {len(leases)} Leases")
    if leases:
        assert "address" in leases[0] and "hwaddr" in leases[0]


async def test_configured_ca_exists(live_ctx):
    api = live_ctx.opnsense()
    ca = await api.find_ca(live_ctx.settings.cert_ca_name)
    pem = await api.ca_certificate(ca)
    assert pem.startswith("-----BEGIN CERTIFICATE-----")


async def test_shelly_info():
    ip = os.environ.get("DM_LIVE_SHELLY_IP")
    if not ip:
        pytest.skip("DM_LIVE_SHELLY_IP nicht gesetzt")
    from device_manager.shelly.client import ShellyClient

    settings = load_settings()
    client = ShellyClient(ip, settings.shelly_password.get_secret_value())
    info = await client.get_device_info()
    assert info.gen >= 2
    await client.check_auth()
    print(info, await client.check_for_update())
