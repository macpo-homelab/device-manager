"""Regressionstests zum Security-Review (2026-11-06).

Schwachstelle 1: Zugangsdaten an nicht verifizierte Hosts (SetAuth/Digest-Relay).
Schwachstelle 2: CSRF / DNS-Rebinding im lokalen Betrieb.
"""

import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from device_manager.config import Settings
from device_manager.services.adopt import adopt
from device_manager.shelly.client import ShellyAuthError, ShellyClient
from device_manager.web.app import create_app
from tests.conftest import device_password, ha1_for, shelly

HTMX = {"HX-Request": "true"}


@pytest.fixture
def local_client(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)  # Standard: allowed_hosts = loopback
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8099") as client:
        yield client


# Schwachstelle 2: Web-Guard -----------------------------------------------------------------------


def test_cross_site_form_post_is_rejected(local_client):
    """Ein Cross-Site-<form> kann keinen HX-Request-Header setzen."""
    r = local_client.post("/adopt", data={"host": "192.168.10.66"})
    assert r.status_code == 403 and "HX-Request" in r.text


def test_foreign_origin_is_rejected(local_client):
    r = local_client.post("/adopt", data={"host": "192.168.10.66"}, headers={**HTMX, "Origin": "https://evil.example"})
    assert r.status_code == 403 and "Origin" in r.text


def test_foreign_referer_is_rejected(local_client):
    r = local_client.post("/adopt", data={"host": "x"}, headers={**HTMX, "Referer": "https://evil.example/a"})
    assert r.status_code == 403


def test_sec_fetch_site_cross_site_is_rejected(local_client):
    r = local_client.post("/adopt", data={"host": "x"}, headers={**HTMX, "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_same_origin_htmx_post_is_accepted(local_client):
    r = local_client.post(
        "/adopt",
        data={"host": "8.8.8.8"},
        headers={**HTMX, "Origin": "http://127.0.0.1:8099", "Sec-Fetch-Site": "same-origin"},
    )
    assert r.status_code == 200 and 'id="job-' in r.text


@pytest.mark.parametrize("host", ["evil.example", "rebind.attacker.net:8099", "192.168.1.5:8099"])
def test_dns_rebinding_host_is_rejected(local_client, host):
    r = local_client.get("/api/devices", headers={"Host": host})
    assert r.status_code == 403 and "Host" in r.text


@pytest.mark.parametrize("host", ["127.0.0.1:8099", "localhost:8099", "[::1]:8099"])
def test_loopback_hosts_are_allowed(local_client, host):
    assert local_client.get("/api/health", headers={"Host": host}).status_code == 200


def test_ha_mode_still_requires_htmx_header(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, allowed_clients=["172.30.32.2"], allowed_hosts=[])
    with TestClient(create_app(settings), client=("172.30.32.2", 5000)) as client:
        assert client.post("/adopt", data={"host": "x"}).status_code == 403
        assert client.post("/adopt", data={"host": "8.8.8.8"}, headers=HTMX).status_code == 200


def test_rejected_post_has_no_side_effect(local_client):
    from device_manager.store import Device

    store = local_client.app.state.ctx.store
    store.upsert_device(Device(key="aa:bb:cc:dd:ee:ff", mac="aa:bb:cc:dd:ee:ff"))
    assert local_client.post("/devices/aa:bb:cc:dd:ee:ff/delete").status_code == 403
    assert store.get_device("aa:bb:cc:dd:ee:ff") is not None
    assert local_client.app.state.jobs.jobs == {}


# Schwachstelle 1: Credentials nur an verifizierte Geräte ---------------------------------------------


async def test_digest_relay_with_foreign_realm_is_refused(network):
    """Fremder Host reicht die Challenge eines anderen Geräts durch -> keine Digest-Antwort."""
    victim = shelly(network, mac="A8032AB12345", ip="192.168.10.50")
    victim.ha1 = ha1_for(victim, "pw")
    client = ShellyClient(
        "192.168.10.50",
        "pw",
        expected_realm="shellymini1pmg3-deadbeef0001",
        http_factory=lambda **kw: httpx.AsyncClient(transport=network, **kw),
    )
    with pytest.raises(ShellyAuthError, match="unerwarteter Digest-Realm"):
        await client.check_auth()
    assert not any("authorization" in r.headers for r in network.requests)


async def test_set_auth_only_for_verified_realm(network):
    sim = shelly(network)
    client = ShellyClient(
        "192.168.10.50",
        expected_realm=sim.device_id,
        http_factory=lambda **kw: httpx.AsyncClient(transport=network, **kw),
    )
    with pytest.raises(ShellyAuthError, match="nur für das geprüfte Gerät"):
        await client.set_auth("x", "shellyplus1-victim")
    assert sim.ha1 is None


async def test_impostor_with_victim_id_receives_nothing(ctx, network, opnsense):
    """Vollständiges Szenario aus dem Review: Fake meldet ID + MAC eines echten Geräts."""
    victim = shelly(network, mac="A8032AB12345", ip="192.168.10.50")
    fake = shelly(network, mac="A8032AB12345", ip="192.168.10.66", id_override=victim.device_id)
    opnsense.add_lease("192.168.10.66", "de:ad:be:ef:00:01", "shelly-neu")
    with pytest.raises(Exception, match="DHCP-Lease"):
        await adopt(ctx, "192.168.10.66")
    rpc_requests = [r for r in network.requests if r.url.host == "192.168.10.66" and r.url.path == "/rpc"]
    assert rpc_requests == [] and fake.ha1 is None


def test_password_endpoint_shows_device_password(ctx, network):
    sim = shelly(network)
    with TestClient(create_app(ctx.settings, ctx), headers=HTMX) as client:
        r = client.post("/adopt", data={"host": "192.168.10.50"})
        assert r.status_code == 200
        import time

        for _ in range(200):
            if ctx.store.get_device(sim.mac_colon):
                break
            time.sleep(0.01)
        page = client.get(f"/devices/{sim.mac_colon}").text
        assert device_password(sim.mac) not in page  # nicht automatisch im HTML
        shown = client.post(f"/devices/{sim.mac_colon}/password")
        assert device_password(sim.mac) in shown.text
        assert shown.headers["Cache-Control"] == "no-store"
        assert client.get(f"/devices/{sim.mac_colon}/password").status_code == 405


async def test_derived_passwords_never_logged(ctx, network, caplog):
    caplog.set_level(logging.DEBUG)
    sim = shelly(network, available={"stable": "1.5.0"})
    messages: list[str] = []
    await adopt(ctx, "192.168.10.50", messages.append)
    from device_manager.services.firmware import update_firmware

    await update_firmware(ctx, "192.168.10.50", None, messages.append)
    haystack = caplog.text + "\n".join(messages) + "".join(a.message for a in ctx.store.actions())
    assert device_password(sim.mac) not in haystack
