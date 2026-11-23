"""Web-Oberfläche: End-to-End über HTTP mit Simulatoren (Seiten, Formulare, Jobs)."""

import re
import time

import pytest
from fastapi.testclient import TestClient

from device_manager.web.app import create_app
from tests.conftest import shelly
from tests.simulators.shelly_sim import ShellySim
from tests.simulators.z2m_sim import ZigbeeShelly

HTMX = {"HX-Request": "true"}


@pytest.fixture
def client(ctx):
    with TestClient(create_app(ctx.settings, ctx), headers=HTMX) as c:
        yield c


def wait_job(client, html: str) -> dict:
    job_id = int(re.search(r'id="job-(\d+)"', html).group(1))
    for _ in range(500):
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["status"] != "running":
            return data
        time.sleep(0.01)
    raise AssertionError("Job läuft zu lange")


def test_pages_render(client):
    for path in ("/", "/provision", "/log"):
        r = client.get(path)
        assert r.status_code == 200 and "Device Manager" in r.text


def test_adopt_reserve_update_certificate_via_ui(client, network, opnsense):
    sim = shelly(network, ver="1.7.1", available={"stable": "2.0.1"})
    job = wait_job(client, client.post("/adopt", data={"host": "192.168.10.50"}).text)
    assert job["status"] == "ok"
    key = sim.mac_colon
    assert key in client.get("/").text

    page = client.get(f"/devices/{key}")
    assert page.status_code == 200 and "IP-Reservierung" in page.text

    job = wait_job(
        client,
        client.post(f"/devices/{key}/reserve", data={"name": "flur", "ip": "", "apply": "true"}).text,
    )
    assert job["status"] == "ok" and job["result"]["ip"] == "192.168.10.100"
    assert sim.ip == "192.168.10.100"
    assert client.get("/api/devices").json()[0]["ip"] == "192.168.10.100"

    job = wait_job(client, client.post(f"/devices/{key}/firmware", data={"stage": "stable"}).text)
    assert job["status"] == "ok" and job["result"]["new_version"] == "2.0.1"

    job = wait_job(client, client.post(f"/devices/{key}/certificate", data={"name": "flur"}).text)
    assert job["status"] == "ok", job["error"]
    assert sim.https_active

    log = client.get("/log").text
    assert "IP-Reservierung" in log and "Zertifikat installieren" in log


def test_job_fragment_shows_error(client):
    html = client.post("/adopt", data={"host": "192.168.10.99"}).text
    job = wait_job(client, html)
    assert job["status"] == "error"
    fragment = client.get(f"/jobs/{job['id']}").text
    assert "Fehler" in fragment and "nicht erreichbar" in fragment
    assert "hx-trigger" not in fragment  # Polling endet


def test_discover_leases_fragment(client, network, opnsense):
    shelly(network)
    opnsense.add_lease("192.168.10.50", "a8:03:2a:b1:23:45", "shellyplus1")
    html = client.post("/discover", data={"source": "lease"}).text
    assert "192.168.10.50" in html and "Nur übernehmen" in html and "Mit Assistent einrichten" in html


def test_provision_wizard(client, network, opnsense):
    sim = shelly(network, ip="192.168.10.200", ver="2.0.1")
    html = client.post(
        "/provision",
        data={
            "host": "192.168.10.200",
            "name": "bad",
            "ip": "",
            "stage": "stable",
            "update": "true",
            "certificate": "true",
        },
    ).text
    job = wait_job(client, html)
    assert job["status"] == "ok", job["error"]
    assert sim.ip == "192.168.10.100" and sim.https_active


async def test_zigbee_update_via_ui(ctx, z2m):
    dev = z2m.add(
        ZigbeeShelly(
            "Bad",
            "0xdef",
            ShellySim(mac="CCDDEEFF0022", ip=None, gen=4, available={"stable": "1.7.1"}),
            "192.168.10.71",
        )
    )
    await z2m.start()
    from httpx import ASGITransport, AsyncClient

    app = create_app(ctx.settings, ctx)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app), base_url="http://testserver", headers=HTMX) as c,
    ):
        r = await c.post("/zigbee/sync")
        assert "1 Zigbee-Shelly" in r.text
        html = (await c.post("/devices/z2m:0xdef/firmware", data={"stage": "stable"})).text
        job_id = int(re.search(r'id="job-(\d+)"', html).group(1))
        job = await app.state.jobs.wait(job_id)
    assert job.status == "ok", job.error
    assert dev.history == [True, False]


def test_unknown_device_404(client):
    assert client.get("/devices/nope").status_code == 404


def test_delete_device(client, network):
    sim = shelly(network)
    wait_job(client, client.post("/adopt", data={"host": "192.168.10.50"}).text)
    r = client.post(f"/devices/{sim.mac_colon}/delete")
    assert r.headers["HX-Redirect"] == "/"
    assert client.get("/api/devices").json() == []


def test_next_ip_api(client, opnsense):
    assert client.get("/api/next-ip").json() == {"ip": "192.168.10.100"}


def test_hostname_field_explains_format(client, ctx, network):
    shelly(network)
    client.post("/adopt", data={"host": "192.168.10.50"})
    pages = [client.get("/provision").text]
    device_page = client.get("/devices/a8:03:2a:b1:23:45")
    assert device_page.status_code == 200
    pages.append(device_page.text)
    assert device_page.text.count("data-hostname") == 1  # ein Namensfeld für Reservierung und Zertifikat
    assert 'hx-include="#dev-name"' in device_page.text
    for html in pages:
        assert "data-hostname" in html and r'pattern="[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"' in html
        assert "Name (A–Z, a–z, 0–9 und Bindestrich)" in html and "keine Umlaute, Leerzeichen oder Punkte" in html
    assert 'src="static/forms.js?v=' in pages[0]
    js = client.get("/static/forms.js")
    assert js.status_code == 200 and "ohne Domain" in js.text


@pytest.mark.parametrize(
    ("value", "expected"),
    [("kueche.lan", "ohne Domain"), ("küche", "keine Umlaute"), ("-x", "nicht mit Bindestrich beginnen")],
)
def test_server_hostname_messages_are_clear(value, expected):
    from device_manager.services.common import ServiceError, validate_hostname

    with pytest.raises(ServiceError, match=expected):
        validate_hostname(value)


def _adopted(client, ctx, network, **kw):  # type: ignore[no-untyped-def]
    sim = shelly(network, **kw)
    client.post("/adopt", data={"host": "192.168.10.50"})
    for _ in range(500):
        device = ctx.store.get_device(sim.mac_colon)
        if device and device.auth_mode:
            break
        time.sleep(0.01)
    return sim


def test_device_page_password_inline_and_apply_label(client, ctx, network):
    sim = _adopted(client, ctx, network)
    page = client.get(f"/devices/{sim.mac_colon}").text
    row = page[page.index('id="pw"') : page.index("</dd>", page.index('id="pw"'))]
    assert "gerätespezifisch" in row and "Passwort anzeigen" in row and 'hx-target="#pw"' in row
    assert "Sofort anwenden</label>" in page and "(Neustart)" not in page
    shown = client.post(f"/devices/{sim.mac_colon}/password").text
    assert '<code class="secret"' in shown and "<p" not in shown and "data-copy" in shown  # passt in die Zeile


def test_firmware_info_shows_installed_and_available(client, ctx, network):
    sim = _adopted(client, ctx, network, ver="1.8.99-plugmg3prod0", available={"stable": "2.2.0-beta2"})
    page = client.get(f"/devices/{sim.mac_colon}").text
    assert f'hx-get="devices/{sim.mac_colon}/firmware-info"' in page and "data-fw-button" in page
    html = client.get(f"/devices/{sim.mac_colon}/firmware-info").text
    assert "Installiert</dt><dd>1.8.99-plugmg3prod0" in html
    assert "2.2.0-beta2 verfügbar" in html and "(Vorabversion)" in html
    assert 'data-stable="2.2.0-beta2"' in html and 'data-beta=""' in html and 'data-checked="yes"' in html
    assert "keine neuere Version" in html  # Kanal beta
    assert sim.calls.count("Shelly.Update") == 0  # nur lesend


def test_firmware_info_reports_unreachable_device(client, ctx, network):
    sim = _adopted(client, ctx, network)
    sim.online = False
    html = client.get(f"/devices/{sim.mac_colon}/firmware-info").text
    assert 'data-checked="no"' in html and "konnten nicht abgefragt werden" in html and "nicht erreichbar" in html
