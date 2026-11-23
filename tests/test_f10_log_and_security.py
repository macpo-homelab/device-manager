"""F10 – Protokoll; N1 – keine Geheimnisse in Logs/Protokoll/Oberfläche."""

import logging

import pytest

from device_manager.jobs import JobManager
from device_manager.services import inventory
from device_manager.services.certificate import install_certificate
from device_manager.services.provision import provision
from device_manager.services.zigbee import zigbee_firmware_update
from tests.conftest import shelly
from tests.simulators.shelly_sim import ShellySim
from tests.simulators.z2m_sim import ZigbeeShelly


async def test_jobs_write_action_log(ctx, network):
    shelly(network, ver="1.4.4", available={"stable": "1.5.0"})
    jobs = JobManager(ctx.store)
    from device_manager.services.firmware import update_firmware

    ok = jobs.start("Firmware-Update", "a8:03:2a:b1:23:45", lambda p: update_firmware(ctx, "192.168.10.50", None, p))
    bad = jobs.start("Firmware-Update", "x", lambda p: update_firmware(ctx, "192.168.10.99", None, p))
    await jobs.wait(ok.id)
    await jobs.wait(bad.id)
    assert ok.status == "ok" and bad.status == "error"
    log = {a.device_key: a for a in ctx.store.actions()}
    assert log["a8:03:2a:b1:23:45"].status == "ok" and "1.4.4 → 1.5.0" in log["a8:03:2a:b1:23:45"].message
    assert log["x"].status == "error" and "nicht erreichbar" in log["x"].message


async def test_no_secrets_anywhere(ctx, network, z2m, caplog):
    caplog.set_level(logging.DEBUG)
    shelly(network, ip="192.168.10.200", ver="1.7.1", available={"stable": "2.0.1"})
    z2m.add(
        ZigbeeShelly(
            "Kueche",
            "0xabc",
            ShellySim(mac="CCDDEEFF0011", ip=None, gen=4, available={"stable": "1.7.1"}),
            "192.168.10.70",
        )
    )
    await z2m.start()

    messages: list[str] = []
    jobs = JobManager(ctx.store)
    job = jobs.start("Neu", None, lambda p: provision(ctx, "192.168.10.200", "wohnzimmer", progress=p))
    await jobs.wait(job.id)
    assert job.status == "ok", job.error
    messages += job.messages
    [zdev] = await inventory.sync_zigbee(ctx)
    await zigbee_firmware_update(ctx, zdev.key, progress=messages.append)
    await install_certificate(ctx, "192.168.10.100", "wohnzimmer", messages.append)

    haystack = "\n".join([caplog.text, *messages, *(a.message for a in ctx.store.actions())])
    for secret in ctx.settings.secrets():
        assert secret not in haystack, "Geheimnis im Protokoll gefunden"
    # Auch der private Schlüssel taucht nirgends auf
    assert "PRIVATE KEY" not in haystack


def test_settings_repr_masks_secrets(settings):
    text = repr(settings) + str(settings.model_dump())
    for secret in settings.secrets():
        assert secret not in text


@pytest.mark.parametrize("path", ["/", "/log", "/provision"])
def test_ui_does_not_render_secrets(settings, path):
    from fastapi.testclient import TestClient

    from device_manager.web.app import create_app

    with TestClient(create_app(settings)) as client:
        html = client.get(path).text
    for secret in settings.secrets():
        assert secret not in html
