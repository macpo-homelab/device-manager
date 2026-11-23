"""Web-Oberfläche: End-to-End über HTTP mit Simulatoren (Seiten, Formulare, Jobs)."""

import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from device_manager.services import inventory
from device_manager.web.app import create_app
from tests.conftest import device_password, shelly
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
    assert 'data-candidate="192.168.10.50"' in html
    # Gleiche Spalten wie die Geräteliste (plus Aktionen)
    head = html[html.index("<thead>") : html.index("</thead>")]
    assert re.findall(r"<th[^>]*>([^<]*)</th>", head) == [
        "Hersteller",
        "Name",
        "Firmware",
        "Web",
        "Zertifikat",
        "Verbindung",
    ]
    row = html[html.index('data-candidate="192.168.10.50"') :]
    assert '<td class="wide">Shelly</td>' in row and "a8:03:2a:b1:23:45" in row and "OPNsense-Lease" in row
    assert re.search(r'<td class="nowrap">\S+</td>', row)  # Firmware aus /shelly


def test_discover_hides_known_devices(client, network, opnsense):
    """F1: Die Suche listet nur neue Geräte, Geräte aus dem Inventar werden ausgeblendet."""
    shelly(network, mac="A8032AB12345", ip="192.168.10.50")
    shelly(network, mac="A8032AB99999", ip="192.168.10.51")
    opnsense.add_lease("192.168.10.50", "a8:03:2a:b1:23:45", "shelly1")
    opnsense.add_lease("192.168.10.51", "a8:03:2a:b9:99:99", "shelly2")
    assert wait_job(client, client.post("/adopt", data={"host": "192.168.10.50"}).text)["status"] == "ok"

    html = client.post("/discover", data={"source": "lease"}).text
    assert 'data-candidate="192.168.10.51"' in html
    assert 'data-candidate="192.168.10.50"' not in html
    assert "1 gefundene(s) Gerät(e) schon im Inventar" in html


def test_adopt_job_triggers_list_update(client, network):
    """F1/F2: Nach der Übernahme meldet der Job das Gerät, damit Geräte- und Suchliste sich aktualisieren."""
    shelly(network)
    html = client.post("/adopt", data={"host": "192.168.10.50"}).text
    job = wait_job(client, html)
    assert job["status"] == "ok"
    r = client.get(f"/jobs/{job['id']}")
    assert json.loads(r.headers["HX-Trigger"]) == {"device-adopted": {"ip": "192.168.10.50"}}
    index = client.get("/").text
    assert 'hx-trigger="device-adopted from:body"' in index and 'id="device-count"' in index


def test_failed_adopt_triggers_nothing(client):
    job = wait_job(client, client.post("/adopt", data={"host": "192.168.10.99"}).text)
    assert job["status"] == "error"
    assert "HX-Trigger" not in client.get(f"/jobs/{job['id']}").headers


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
    assert "gerätespezifisch" in row and 'aria-label="Passwort anzeigen"' in row and 'hx-target="#pw-shown"' in row
    assert f'data-copy-password="devices/{sim.mac_colon}/password"' in row and "data-toggle-password" in row
    # Stift-Icon zum Setzen des Passworts in derselben Zeile (mit Rückfrage)
    pencil = row[row.index(f'hx-post="devices/{sim.mac_colon}/set-password"') :]
    assert 'class="icon-button"' in row and 'aria-label="Passwort setzen"' in pencil[:400]
    assert "hx-confirm=" in pencil[:400] and ">Passwort setzen</button>" not in page
    assert device_password(sim.mac) not in page  # erst auf Klick
    assert "Sofort anwenden</label>" in page and "(Neustart)" not in page
    shown = client.post(f"/devices/{sim.mac_colon}/password").text
    assert '<code class="secret"' in shown and "<p" not in shown  # passt in die Zeile
    assert device_password(sim.mac) in shown


def test_device_page_shows_dhcp_client_id(client, ctx, network, opnsense):
    sim = _adopted(client, ctx, network)
    opnsense.add_lease(sim.ip, sim.mac_colon, "shellyplus1")
    page = client.get(f"/devices/{sim.mac_colon}").text
    assert f'hx-get="devices/{sim.mac_colon}/lease-info"' in page
    fragment = client.get(f"/devices/{sim.mac_colon}/lease-info").text
    assert "<dt>Client-ID</dt>" in fragment and f"01:{sim.mac_colon}" in fragment


def test_client_id_row_hidden_without_opnsense(client, ctx, network):
    sim = _adopted(client, ctx, network)
    ctx.settings = ctx.settings.model_copy(update={"opnsense_url": ""})
    fragment = client.get(f"/devices/{sim.mac_colon}/lease-info").text
    assert "Client-ID" not in fragment and "hidden" in fragment


def test_apply_defaults_from_device_page(client, ctx, network):
    sim = _adopted(client, ctx, network)
    page = client.get(f"/devices/{sim.mac_colon}").text
    assert "<h3>Standardwerte setzen</h3>" in page and f'hx-post="devices/{sim.mac_colon}/defaults"' in page
    assert page.index("<h3>HTTPS-Zertifikat</h3>") < page.index("<h3>Standardwerte setzen</h3>")
    job = wait_job(client, client.post(f"/devices/{sim.mac_colon}/defaults", data={"name": "Flur"}).text)
    assert job["status"] == "ok", job["error"]
    assert sim.config["Sys"]["device"] == {"name": "Flur", "eco_mode": True}
    assert sim.config["WiFi"]["ap"] == {"enable": False, "pass": device_password(sim.mac)}
    assert ctx.store.get_device(sim.mac_colon).name == "Flur"


def test_apply_defaults_refuses_other_device_at_ip(client, ctx, network):
    sim = _adopted(client, ctx, network)
    network.devices.remove(sim)
    other = shelly(network, mac="A8032AB99999")
    job = wait_job(client, client.post(f"/devices/{sim.mac_colon}/defaults", data={"name": "Flur"}).text)
    assert job["status"] == "error" and "anderes Gerät" in job["error"]
    assert "WiFi" not in other.config


def test_firmware_info_shows_installed_and_available(client, ctx, network):
    sim = _adopted(client, ctx, network, ver="1.8.99-plugmg3prod0", available={"stable": "2.2.0-beta2"})
    page = client.get(f"/devices/{sim.mac_colon}").text
    assert f'hx-get="devices/{sim.mac_colon}/firmware-info"' in page and "data-fw-button" in page
    html = client.get(f"/devices/{sim.mac_colon}/firmware-info").text
    assert '<dl class="fw-versions">' in html  # Installiert, stable, beta nebeneinander
    assert '<dt>Installiert</dt><dd class="mono">1.8.99-plugmg3prod0' in html
    assert ">2.2.0-beta2</span>" in html and "(Vorabversion)" in html
    assert 'data-stable="2.2.0-beta2"' in html and 'data-beta=""' in html and 'data-checked="yes"' in html
    assert "keine neuere" in html  # Kanal beta
    assert "„Aktualisieren“ installiert" in html  # Hinweis nur, wenn ein Update möglich ist
    assert sim.calls.count("Shelly.Update") == 0  # nur lesend


def test_firmware_info_without_update_hides_hint(client, ctx, network):
    sim = _adopted(client, ctx, network, ver="2.2.0")
    html = client.get(f"/devices/{sim.mac_colon}/firmware-info").text
    assert html.count("keine neuere") == 2 and "„Aktualisieren“ installiert" not in html


def test_reservation_ip_prefilled_when_reservation_matches(client, ctx, network, opnsense):
    sim = _adopted(client, ctx, network)
    page = client.get(f"/devices/{sim.mac_colon}").text
    # Eigenes Ziel: sonst erbt das Feld hx-target="#msg" vom Formular und landet oben auf der Seite
    assert f'hx-get="devices/{sim.mac_colon}/reservation-ip" hx-trigger="load" hx-target="this"' in page
    html = client.get(f"/devices/{sim.mac_colon}/reservation-ip").text
    assert 'value=""' in html and "Noch keine Reservierung" in html

    job = wait_job(
        client, client.post(f"/devices/{sim.mac_colon}/reserve", data={"name": "flur", "apply": "true"}).text
    )
    assert job["status"] == "ok"
    html = client.get(f"/devices/{sim.mac_colon}/reservation-ip").text
    assert f'value="{sim.ip}"' in html and "entspricht der aktuellen IP" in html and "diese MAC" in html


def test_reservation_ip_empty_when_reservation_differs(client, ctx, network, opnsense):
    sim = _adopted(client, ctx, network)
    job = wait_job(
        client, client.post(f"/devices/{sim.mac_colon}/reserve", data={"name": "flur", "apply": "false"}).text
    )
    assert job["status"] == "ok"
    html = client.get(f"/devices/{sim.mac_colon}/reservation-ip").text
    assert 'value=""' in html and "Reserviert ist 192.168.10.100, aktuell 192.168.10.50" in html


def test_reservation_found_by_client_id(client, ctx, network, opnsense):
    sim = _adopted(client, ctx, network)
    opnsense.reservations["r1"] = {
        "subnet": "s",
        "ip_address": sim.ip,
        "hw_address": "",
        "client_id": f"01:{sim.mac_colon}",
        "hostname": "flur",
    }
    html = client.get(f"/devices/{sim.mac_colon}/reservation-ip").text
    assert f'value="{sim.ip}"' in html and "diese Client-ID" in html


def test_firmware_info_reports_unreachable_device(client, ctx, network):
    sim = _adopted(client, ctx, network)
    sim.online = False
    html = client.get(f"/devices/{sim.mac_colon}/firmware-info").text
    assert 'data-checked="no"' in html and "konnten nicht abgefragt werden" in html and "nicht erreichbar" in html


def test_device_list_columns(client, ctx, network):
    sim = _adopted(client, ctx, network)
    device = ctx.store.get_device(sim.mac_colon)
    device.hostname = "flur"
    ctx.store.upsert_device(device)
    page = client.get("/").text
    head = page[page.index('<table class="devices"') : page.index("</thead>")]
    headers = re.findall(r"<th[^>]*>([^<]*)</th>", head)
    assert headers == ["Hersteller", "Name", "Firmware", "Web", "Zertifikat", "Verbindung"]
    row = page[page.index("<tbody>", page.index('<table class="devices"')) : page.index("</tbody>")]
    assert '<td class="wide">Shelly</td>' in row
    assert f'href="http://flur.{ctx.settings.dns_domain}/"' in row
    assert 'class="icon-button web-link"' in row and f">flur.{ctx.settings.dns_domain}<" not in row
    assert "WLAN" in row and sim.ip in row and sim.mac_colon not in row.replace(f"devices/{sim.mac_colon}", "")

    device.online = 0
    ctx.store.upsert_device(device)
    assert "nicht erreichbar" in client.get("/").text


def _row(html: str, ip: str) -> str:
    start = html.index(f'data-candidate="{ip}"')
    end = html.find("<tr data-candidate=", start + 1)
    return html[start : end if end != -1 else len(html)]


def _self_signed(cn: str, days: int = 100) -> tuple[str, str]:
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, cn)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=days))
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return pem, key_pem


def test_discover_shows_reservation_dns_and_certificate(client, ctx, network, opnsense):
    """Suchtreffer: reservierte IP als Symbol, DNS-Name per PTR, HTTPS-Zertifikat ausgelesen."""
    sim = shelly(network, mac="A8032AB12345", ip="192.168.10.50")
    other = shelly(network, mac="A8032AB99999", ip="192.168.10.51")
    opnsense.add_lease("192.168.10.50", "a8:03:2a:b1:23:45", "shelly1")
    opnsense.add_lease("192.168.10.51", "a8:03:2a:b9:99:99", "shelly2")
    opnsense.reservations["r1"] = {
        "subnet": "s",
        "ip_address": "192.168.10.50",
        "hw_address": sim.mac_colon,
        "hostname": "flur",
    }
    opnsense.reservations["r2"] = {
        "subnet": "s",
        "ip_address": "192.168.10.99",
        "hw_address": other.mac_colon,
        "hostname": "bad",
    }
    network.ptr["192.168.10.50"] = "flur.home.lan"
    sim.active_cert, sim.active_key = _self_signed("flur.home.lan")

    html = client.post("/discover", data={"source": "lease"}).text
    first, second = _row(html, "192.168.10.50"), _row(html, "192.168.10.51")
    assert 'aria-label="IP-Adresse in Kea reserviert"' in first
    assert 'href="https://flur.home.lan/"' in first and 'title="Ausgestellt für flur.home.lan"' in first
    assert re.search(r">\d{2}\.\d{2}\.\d{4}</td>", first)  # Ablaufdatum wie in der Geräteliste
    assert 'aria-label="IP-Adresse in Kea reserviert"' not in second
    assert 'reserviert: <span class="mono">192.168.10.99</span>' in second
    assert "fehlt" in second and 'href="http://192.168.10.51/"' in second  # kein HTTPS, kein PTR: Link über die IP
    assert 'class="icon-button web-link"' in first and 'target="_blank"' in first
    assert ">flur.home.lan<" not in first  # nur Icon, Name im Tooltip/Label


async def test_enrich_without_opnsense(ctx, network):
    """Ohne OPNsense: keine Reservierung, die Suche funktioniert trotzdem."""
    shelly(network)
    ctx.settings = ctx.settings.model_copy(update={"opnsense_url": ""})
    candidate = inventory.Candidate("192.168.10.50", "a8:03:2a:b1:23:45", "x", "mdns")
    [c] = await inventory.enrich_candidates(ctx, [candidate])
    assert c.reserved_ip is None and not c.reserved and c.cert_not_after is None and c.dns_name == ""
