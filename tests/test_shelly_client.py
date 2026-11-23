"""Unit-Tests für den Shelly-Client."""

import httpx
import pytest

from device_manager.shelly.client import (
    CHECK_FOR_UPDATE_TIMEOUT,
    ShellyAuthError,
    ShellyClient,
    ShellyError,
    ShellyUnreachable,
    format_mac,
    parse_version,
    supports_custom_https,
)
from tests.conftest import shelly


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1.4.4", (1, 4, 4)), ("1.6.0-beta1", (1, 6, 0)), ("20250924-062804/1.7.1-gd336f31", (1, 7, 1))],
)
def test_parse_version(raw, expected):
    assert parse_version(raw) == expected


def test_supports_custom_https():
    assert not supports_custom_https("1.7.1")
    assert supports_custom_https("2.0.0")
    assert supports_custom_https("2.1.0-beta1")


@pytest.mark.parametrize("raw", ["A8032AB12345", "a8:03:2a:b1:23:45", "A8-03-2A-B1-23-45"])
def test_format_mac(raw):
    assert format_mac(raw) == "a8:03:2a:b1:23:45"


def test_format_mac_invalid():
    with pytest.raises(ValueError):
        format_mac("1234")


def client(network, password=""):
    import httpx

    return ShellyClient("192.168.10.50", password, http_factory=lambda **kw: httpx.AsyncClient(transport=network, **kw))


async def test_pem_upload_in_chunks(network):
    sim = shelly(network, ver="2.0.1")
    pem = "-----BEGIN CERTIFICATE-----\n" + "A" * 2500 + "\n-----END CERTIFICATE-----\n"
    await client(network).put_http_server_cert(pem)
    assert sim.calls.count("Shelly.PutHTTPServerCert") == 3
    assert sim.uploaded_cert == pem


async def test_wrong_password_raises_auth_error(network):
    import hashlib

    sim = shelly(network)
    sim.ha1 = hashlib.sha256(f"admin:{sim.device_id}:richtig".encode()).hexdigest()
    with pytest.raises(ShellyAuthError):
        await client(network, "falsch").check_auth()
    with pytest.raises(ShellyAuthError):
        await client(network, "").check_auth()
    await client(network, "richtig").check_auth()


async def test_unreachable(network):
    with pytest.raises(ShellyUnreachable):
        await client(network).get_device_info()


async def test_rpc_error_is_reported(network):
    shelly(network)
    with pytest.raises(ShellyError, match="No handler"):
        await client(network).rpc("Foo.Bar")


async def test_check_for_update_waits_longer_than_other_calls(network):
    """Am echten Gerät dauert CheckForUpdate kurz nach einem Neustart > 10 s (Abfrage beim Update-Server)."""
    shelly(network, available={"stable": "2.0.1"})
    timeouts: list[float] = []

    def factory(**kwargs):
        timeouts.append(kwargs["timeout"])
        return httpx.AsyncClient(transport=network, **kwargs)

    client = ShellyClient("192.168.10.50", http_factory=factory, timeout=10.0)
    await client.rpc("Sys.GetStatus")
    assert await client.check_for_update() == {"stable": "2.0.1"}
    assert timeouts == [10.0, CHECK_FOR_UPDATE_TIMEOUT] and CHECK_FOR_UPDATE_TIMEOUT >= 30
