"""F4b – Update ohne automatischen Neustart (z. B. Plug M FW 1.8.99): abfragen, nie selbst neu starten,
nach `firmware_reboot_wait` den Anwender um den Neustart bitten. F3b – DHCP-Beschreibung und Unbound-Reload."""

import asyncio

import pytest

from device_manager.jobs import JobManager
from device_manager.services import reservation
from device_manager.services.adopt import adopt
from device_manager.services.common import ServiceError
from device_manager.services.firmware import update_firmware
from device_manager.shelly.client import product_name
from tests.conftest import shelly

MAC = "a8:03:2a:b1:23:45"


async def _wait_for_action(job, limit: int = 10_000):  # type: ignore[no-untyped-def]
    for _ in range(limit):
        if job.action is not None or job.done:
            return job.action
        await asyncio.sleep(0)
    raise AssertionError("Job hat keine Aktion angefordert")


async def test_manual_reboot_is_requested_and_never_automatic(ctx, network):
    sim = shelly(
        network, ver="1.8.99-plugmg3prod0", available={"stable": "2.2.0-beta2"}, update_needs_manual_reboot=True
    )
    jobs = JobManager(ctx.store)
    job = jobs.start("Firmware", MAC, lambda p: update_firmware(ctx, "192.168.10.50", "stable", p))

    action = await _wait_for_action(job)
    assert action is not None and action.label == "Gerät jetzt neu starten"
    assert sim.reboots == 0, "Der Manager darf nicht selbst neu starten"
    assert sim.restart_required  # Update ist installiert, Neustart steht aus
    assert any("restart_required" in m for m in job.messages)
    assert any(m.startswith("AKTION ERFORDERLICH") for m in job.messages)
    assert any("Vorabversion 2.2.0-beta2" in m for m in job.messages)  # Beta im Kanal stable: nur Hinweis

    await job.run_action()  # Anwender klickt den Button
    await jobs.wait(job.id)
    assert job.status == "ok", job.error
    assert sim.reboots == 1 and sim.ver == "2.2.0-beta2"
    assert job.action is None
    assert job.result.new_version == "2.2.0-beta2"


async def test_without_user_reboot_job_fails_without_rebooting(ctx, network):
    sim = shelly(network, available={"stable": "1.5.1"}, update_needs_manual_reboot=True)
    with pytest.raises(ServiceError, match="auch nach der Aufforderung nicht neu gestartet.*restart_required"):
        await update_firmware(ctx, "192.168.10.50")
    assert sim.reboots == 0 and sim.ver == "1.4.4"


async def test_reboot_with_old_firmware_is_reported(ctx, network):
    # Anwender startet neu, bevor die Installation fertig ist -> Gerät kommt mit alter Firmware zurück
    sim = shelly(network, available={"stable": "1.5.1"}, update_needs_manual_reboot=True, update_install_ticks=100_000)
    jobs = JobManager(ctx.store)
    job = jobs.start("Firmware", MAC, lambda p: update_firmware(ctx, "192.168.10.50", None, p))
    await _wait_for_action(job)
    await job.run_action()
    await jobs.wait(job.id)
    assert job.status == "error"
    assert "meldet aber weiterhin Firmware 1.4.4" in (job.error or "") and "nicht übernommen" in (job.error or "")
    assert sim.ver == "1.4.4"


async def test_normal_update_needs_no_action(ctx, network):
    sim = shelly(network, available={"stable": "1.5.1"})
    jobs = JobManager(ctx.store)
    job = jobs.start("Firmware", MAC, lambda p: update_firmware(ctx, "192.168.10.50", None, p))
    await jobs.wait(job.id)
    assert job.status == "ok" and sim.ver == "1.5.1" and sim.reboots == 1
    assert not any("AKTION" in m for m in job.messages)


async def test_web_shows_reboot_button_and_runs_it(ctx, network):
    from fastapi.testclient import TestClient

    from device_manager.web.app import create_app

    sim = shelly(network, available={"stable": "1.5.1"}, update_needs_manual_reboot=True)
    app = create_app(ctx.settings, ctx)
    with TestClient(app, headers={"HX-Request": "true"}) as client:
        jobs: JobManager = app.state.jobs
        job = jobs.start("Firmware", MAC, lambda p: update_firmware(ctx, "192.168.10.50", None, p))
        await _wait_for_action(job)
        html = client.get(f"/jobs/{job.id}").text
        assert "Aktion erforderlich" in html and f'hx-post="jobs/{job.id}/action"' in html and "hx-confirm" in html
        assert sim.reboots == 0
        html = client.post(f"/jobs/{job.id}/action").text
        assert sim.reboots == 1 and "durch Anwender ausgelöst" in html
        # Zweiter Klick löst keinen zweiten Neustart aus
        client.post(f"/jobs/{job.id}/action")
        assert sim.reboots == 1
        await jobs.wait(job.id)
    assert job.status == "ok", job.error


@pytest.mark.parametrize(
    ("app", "gen", "model", "expected"),
    [
        ("PlugMG3", 3, "S3PL-20112EU", "Shelly Plug M Gen 3"),
        ("Mini1PMG3", 3, "", "Shelly Mini 1PM Gen 3"),
        ("PlusPlugS", 2, "", "Shelly Plus Plug S Gen 2"),
        ("Pro4PM", 2, "", "Shelly Pro 4PM Gen 2"),
        ("1PMG4", 4, "", "Shelly 1PM Gen 4"),
        ("", 3, "S3SW-001P16EU", "Shelly S3SW-001P16EU Gen 3"),
    ],
)
def test_product_name(app, gen, model, expected):
    assert product_name(app, gen, model) == expected


async def test_reservation_description_is_product_name_and_dns_is_reloaded(ctx, network, opnsense):
    shelly(network, app="PlugMG3", gen=3)
    await adopt(ctx, "192.168.10.50")
    messages: list[str] = []
    result = await reservation.reserve(ctx, MAC, "steckdose", progress=messages.append)
    assert opnsense.reservations[result.uuid]["description"] == "Shelly Plug M Gen 3"
    assert opnsense.unbound_reloads == 1 and "Unbound-DNS neu geladen" in messages
    # Ohne Änderung kein erneutes Neuladen
    again = await reservation.reserve(ctx, MAC, "steckdose")
    assert not again.changed and opnsense.unbound_reloads == 1


async def test_old_description_is_updated(ctx, network, opnsense):
    shelly(network, app="PlugMG3", gen=3)
    await adopt(ctx, "192.168.10.50")
    [subnet] = [u for u, c in opnsense.subnets.items() if c == "192.168.10.0/24"]
    opnsense.reservations["r1"] = {
        "subnet": subnet,
        "ip_address": "192.168.10.105",
        "hw_address": MAC,
        "hostname": "steckdose",
        "description": "Device Manager",
    }
    result = await reservation.reserve(ctx, MAC, "steckdose")
    assert result.changed and opnsense.reservations["r1"]["description"] == "Shelly Plug M Gen 3"
    assert opnsense.unbound_reloads == 1


async def test_unbound_without_permission_keeps_reservation(ctx, opnsense, caplog):
    opnsense.unbound_allowed = False
    messages: list[str] = []
    result = await reservation.reserve(ctx, MAC, "flur", progress=messages.append)
    assert result.changed and opnsense.applied[MAC] == result.ip
    hint = next(m for m in messages if m.startswith("Hinweis: DNS-Name flur"))
    assert "Services: Unbound DNS" in hint
    assert "Schritt FEHLER IP-Reservierung" in caplog.text and "Unbound-DNS neu laden" in caplog.text


async def test_unbound_reload_can_be_disabled(ctx, opnsense):
    ctx.settings.opnsense_reload_unbound = False
    await reservation.reserve(ctx, MAC, "flur")
    assert opnsense.unbound_reloads == 0
