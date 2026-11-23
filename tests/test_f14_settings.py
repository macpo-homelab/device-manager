"""F14 – Seite „Einstellungen“: Standardwerte, Assistent, Netz/Zertifikat und Zeitlimits in der Datenbank."""

import pytest
from fastapi.testclient import TestClient

from device_manager.config import EDITABLE_FIELDS, Settings
from device_manager.services.provision import provision
from device_manager.web.app import create_app
from tests.conftest import device_password, shelly
from tests.test_web import HTMX


@pytest.fixture
def client(ctx):
    with TestClient(create_app(ctx.settings, ctx), headers=HTMX) as c:
        yield c


def form_of(ctx, **changes):
    """Formular wie im Browser: alle Felder mit aktuellem Wert, Checkboxen nur wenn an."""
    data = {}
    for name, value in ctx.settings.model_dump(mode="json").items():
        if name not in EDITABLE_FIELDS:
            continue
        value = changes.get(name, value)
        if isinstance(value, bool) or Settings.model_fields[name].annotation is bool:
            if value:
                data[name] = "true"
        else:
            data[name] = "" if value is None else str(value)
    return data


def test_editable_fields_exist_and_contain_no_secrets():
    for name in EDITABLE_FIELDS:
        assert name in Settings.model_fields
    assert not {"shelly_password", "wifi_password", "opnsense_api_key", "opnsense_api_secret", "mqtt_password"} & set(
        EDITABLE_FIELDS
    )


def test_settings_page_saves_overrides(client, ctx):
    page = client.get("/settings")
    assert page.status_code == 200 and 'name="shelly_ntp_server"' in page.text and "Einstellungen" in page.text

    r = client.post(
        "/settings", data=form_of(ctx, shelly_ntp_server="ntp.example", shelly_led_off=False, poll_interval="5")
    )
    assert "Einstellungen gespeichert" in r.text
    assert ctx.settings.shelly_ntp_server == "ntp.example"
    assert ctx.settings.shelly_led_off is False and ctx.settings.poll_interval == 5.0
    assert ctx.base_settings.shelly_ntp_server == "time.cloudflare.com"  # .env/Optionen unverändert
    assert ctx.store.setting_overrides() == {
        "shelly_ntp_server": "ntp.example",
        "shelly_led_off": False,
        "poll_interval": 5.0,
    }
    page = client.get("/settings").text
    assert "Zurücksetzen" in page and 'value="ntp.example"' in page
    # Die Geräteliste zeigt den neuen NTP-Server beim Verteilen-Button
    assert "ntp.example" in client.get("/").text


def test_reset_single_value(client, ctx):
    client.post("/settings", data=form_of(ctx, shelly_ntp_server="ntp.example", shelly_timezone="Europe/Vienna"))
    client.post("/settings", data=form_of(ctx) | {"reset": "shelly_ntp_server"})
    assert ctx.settings.shelly_ntp_server == "time.cloudflare.com"
    assert ctx.store.setting_overrides() == {"shelly_timezone": "Europe/Vienna"}


def test_invalid_values_are_rejected(client, ctx):
    r = client.post("/settings", data=form_of(ctx, ip_range_start="kein-ip", shelly_latitude="100"))
    assert "Nicht gespeichert" in r.text and "IP-Bereich von" in r.text and "Breitengrad" in r.text
    assert ctx.store.setting_overrides() == {}


def test_reset_of_non_editable_field_is_rejected(client, ctx):
    r = client.post("/settings", data=form_of(ctx) | {"reset": "shelly_password"})
    assert "Nicht gespeichert" in r.text


def test_overrides_survive_restart(ctx, settings):
    ctx.save_settings({"shelly_ntp_server": "ntp.example"})
    with TestClient(create_app(settings, ctx.__class__(settings=settings, store=ctx.store)), headers=HTMX) as c:
        assert c.app.state.ctx.settings.shelly_ntp_server == "ntp.example"


def test_provision_page_uses_preselection(client, ctx):
    client.post("/settings", data=form_of(ctx, provision_certificate=False, shelly_disable_ble=False))
    page = client.get("/provision").text
    assert 'name="certificate" value="true" data-step-toggle' in page  # nicht vorausgewählt
    assert 'name="update" value="true" checked' in page
    assert "Bluetooth aus" not in page and "LED aus" in page


async def test_defaults_respect_switches(ctx, network, opnsense):
    ctx.save_settings(
        {
            "shelly_eco_mode": False,
            "shelly_disable_ap": False,
            "shelly_disable_ble": False,
            "shelly_led_off": False,
            "shelly_ntp_server": "ntp.example",
        }
    )
    sim = shelly(network, ip="192.168.10.200", app="PlugMG3", ver="2.2.0")
    messages: list[str] = []
    await provision(ctx, "192.168.10.200", "Flur", update=False, certificate=False, progress=messages.append)
    assert sim.config["Sys"]["device"] == {"name": "Flur"}
    assert sim.config["Sys"]["sntp"] == {"server": "ntp.example"}
    assert sim.config["WiFi"]["ap"] == {"pass": device_password(sim.mac)}  # AP bleibt an, aber abgesichert
    assert "BLE" not in sim.config and "PLUGS_UI" not in sim.config
    assert any("Bluetooth: laut Einstellungen nicht ändern" in m for m in messages)
