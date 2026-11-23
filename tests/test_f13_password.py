"""F13 – Geräte-Passwort setzen und Nachfrage, wenn das Gerät die bekannten Passwörter ablehnt."""

import logging
import re

import pytest
from fastapi.testclient import TestClient

from device_manager.services.adopt import adopt
from device_manager.services.common import PasswordRequired
from device_manager.services.identity import set_device_password
from device_manager.web.app import create_app
from tests.conftest import SHELLY_PASSWORD, device_password, ha1_for, shelly
from tests.test_web import HTMX, wait_job


@pytest.fixture
def client(ctx):
    with TestClient(create_app(ctx.settings, ctx), headers=HTMX) as c:
        yield c


async def test_adopt_asks_for_unknown_password(ctx, network):
    sim = shelly(network)
    sim.ha1 = ha1_for(sim, "altes-passwort")
    with pytest.raises(PasswordRequired, match="aktuelle Passwort"):
        await adopt(ctx, "192.168.10.50")
    assert ctx.store.list_devices() == []


async def test_adopt_with_entered_password_switches_to_device_password(ctx, network, caplog):
    caplog.set_level(logging.DEBUG)
    sim = shelly(network)
    sim.ha1 = ha1_for(sim, "altes-passwort")
    messages: list[str] = []
    device = await adopt(ctx, "192.168.10.50", messages.append, current_password="altes-passwort")
    assert device.auth_mode == "device"
    assert sim.ha1 == ha1_for(sim, device_password(sim.mac))
    assert "Gerätespezifisches Passwort gesetzt und geprüft" in messages
    assert "altes-passwort" not in caplog.text and device_password(sim.mac) not in caplog.text


async def test_adopt_with_wrong_entered_password_asks_again(ctx, network):
    sim = shelly(network)
    sim.ha1 = ha1_for(sim, "altes-passwort")
    with pytest.raises(PasswordRequired, match="abgelehnt"):
        await adopt(ctx, "192.168.10.50", current_password="falsch")
    assert sim.ha1 == ha1_for(sim, "altes-passwort")


async def _inventory_device_with_password(ctx, network, password: str):
    sim = shelly(network)
    device = await adopt(ctx, "192.168.10.50")  # setzt das abgeleitete Passwort
    sim.ha1 = ha1_for(sim, password)  # z. B. anderes Master-Passwort beim Einrichten
    return sim, device


async def test_set_password_with_current_password(ctx, network):
    sim, device = await _inventory_device_with_password(ctx, network, "anders")
    with pytest.raises(PasswordRequired):
        await set_device_password(ctx, device)
    device = await set_device_password(ctx, device, "anders")
    assert device.auth_mode == "device" and sim.ha1 == ha1_for(sim, device_password(sim.mac))
    await ctx.shelly("192.168.10.50").check_auth()


async def test_set_password_uses_master_password_without_input(ctx, network):
    sim, device = await _inventory_device_with_password(ctx, network, SHELLY_PASSWORD)
    device.auth_mode = "shared"
    device = await set_device_password(ctx, device)
    assert device.auth_mode == "device" and sim.ha1 == ha1_for(sim, device_password(sim.mac))


async def test_set_password_when_already_correct(ctx, network):
    sim, device = await _inventory_device_with_password(ctx, network, device_password("A8032AB12345"))
    messages: list[str] = []
    await set_device_password(ctx, device, progress=messages.append)
    assert "Gerät verwendet bereits sein gerätespezifisches Passwort" in messages


def test_job_shows_password_prompt_and_retries(client, network):
    sim = shelly(network)
    sim.ha1 = ha1_for(sim, "altes-passwort")
    job = wait_job(client, client.post("/adopt", data={"host": "192.168.10.50"}).text)
    assert job["status"] == "error"
    fragment = client.get(f"/jobs/{job['id']}").text
    assert 'name="current_password"' in fragment and 'hx-post="adopt"' in fragment
    assert "192.168.10.50" in re.search(r"hx-vals='([^']*)'", fragment).group(1)

    job = wait_job(
        client, client.post("/adopt", data={"host": "192.168.10.50", "current_password": "altes-passwort"}).text
    )
    assert job["status"] == "ok"
    assert sim.ha1 == ha1_for(sim, device_password(sim.mac))


def test_provision_prompt_resends_wizard_form(client, network):
    sim = shelly(network, ip="192.168.10.200")
    sim.ha1 = ha1_for(sim, "altes-passwort")
    data = {"host": "192.168.10.200", "name": "bad", "ip": "", "stage": "stable"}
    job = wait_job(client, client.post("/provision", data=data).text)
    fragment = client.get(f"/jobs/{job['id']}").text
    assert 'hx-post="provision"' in fragment and 'hx-include="#provision-form"' in fragment
    assert 'id="provision-form"' in client.get("/provision").text


def test_other_errors_show_no_prompt(client):
    job = wait_job(client, client.post("/adopt", data={"host": "192.168.10.99"}).text)
    assert 'name="current_password"' not in client.get(f"/jobs/{job['id']}").text


def test_device_page_set_password_button_and_dns_link(client, network, ctx):
    sim = shelly(network)
    assert wait_job(client, client.post("/adopt", data={"host": "192.168.10.50"}).text)["status"] == "ok"
    device = ctx.store.get_device(sim.mac_colon)
    device.hostname = "flur"
    ctx.store.upsert_device(device)
    page = client.get(f"/devices/{sim.mac_colon}").text
    assert f'hx-post="devices/{sim.mac_colon}/set-password"' in page
    domain = ctx.settings.dns_domain
    assert f'href="http://flur.{domain}/"' in page and 'rel="noopener noreferrer"' in page

    device.cert_not_after = "2030-01-01T00:00:00+00:00"
    ctx.store.upsert_device(device)
    assert f'href="https://flur.{domain}/"' in client.get(f"/devices/{sim.mac_colon}").text

    sim.ha1 = ha1_for(sim, "anders")
    job = wait_job(client, client.post(f"/devices/{sim.mac_colon}/set-password").text)
    assert job["status"] == "error"
    fragment = client.get(f"/jobs/{job['id']}").text
    assert f'hx-post="devices/{sim.mac_colon}/set-password"' in fragment and 'name="current_password"' in fragment
    job = wait_job(
        client, client.post(f"/devices/{sim.mac_colon}/set-password", data={"current_password": "anders"}).text
    )
    assert job["status"] == "ok"
