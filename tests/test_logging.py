"""F10a – Schritt-Protokollierung und aussagekräftige Fehlermeldungen (Log, Job, Protokoll)."""

import asyncio
import logging

import pytest

from device_manager import steps
from device_manager.jobs import JobManager
from device_manager.logsetup import RedactingFormatter, configure_logging, log_file
from device_manager.services.common import ServiceError
from device_manager.services.firmware import update_firmware
from device_manager.services.provision import provision
from device_manager.shelly.client import ShellyUnreachable
from tests.conftest import shelly


@pytest.fixture(autouse=True)
def _restore_root_logging():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for h in root.handlers:
        if h not in handlers:
            root.removeHandler(h)
            h.close()
    root.setLevel(level)


def test_step_marks_innermost_failed_step(caplog):
    caplog.set_level(logging.INFO, logger="device_manager.steps")
    with pytest.raises(ServiceError) as exc, steps.step("Außen"), steps.step("Innen"):
        raise ServiceError("kaputt")
    assert steps.failed_step(exc.value) == "Außen > Innen"
    assert "Schritt START  Außen > Innen" in caplog.text
    assert "Schritt FEHLER Außen > Innen" in caplog.text and "kaputt" in caplog.text
    assert steps.current_step() == ""


def test_step_logs_success_with_duration(caplog):
    caplog.set_level(logging.INFO, logger="device_manager.steps")
    with steps.step("Prüfen"):
        assert steps.current_step() == "Prüfen"
    assert "Schritt OK     Prüfen (" in caplog.text


def test_describe_error_contains_cause_chain():
    try:
        try:
            raise ConnectionRefusedError("Verbindung abgelehnt")
        except OSError as inner:
            raise ShellyUnreachable("192.168.10.9 nicht erreichbar") from inner
    except ShellyUnreachable as exc:
        text = steps.describe_error(exc)
    assert text == (
        "ShellyUnreachable: 192.168.10.9 nicht erreichbar <- Ursache: ConnectionRefusedError: Verbindung abgelehnt"
    )


async def test_failed_job_reports_step_cause_and_traceback(ctx, network, caplog):
    caplog.set_level(logging.DEBUG)
    shelly(network, ver="1.4.4", available={"stable": "1.5.0"}, update_breaks_device=True)
    jobs = JobManager(ctx.store)
    job = jobs.start("Firmware-Update", "dev", lambda p: update_firmware(ctx, "192.168.10.50", None, p))
    await jobs.wait(job.id)

    assert job.status == "error"
    assert job.step == "Firmware-Update 192.168.10.50 (stable) > Update und Neustart abwarten"
    assert "nicht mit neuer Firmware zurückgekehrt" in (job.error or "")
    assert "zuletzt: nicht erreichbar" in (job.error or "")
    assert job.error_detail and job.error_detail.startswith("ServiceError:")

    [action] = ctx.store.actions()
    assert action.status == "error"
    assert action.message.startswith("Schritt 'Firmware-Update 192.168.10.50 (stable) > Update und Neustart")
    assert "ServiceError:" in action.message

    errors = [r for r in caplog.records if r.levelno == logging.ERROR and r.name == "device_manager.jobs"]
    assert len(errors) == 1
    assert "im Schritt 'Firmware-Update" in errors[0].getMessage()
    assert errors[0].exc_info is not None  # Traceback landet im Log
    # Vorherige Schritte sind als erfolgreich protokolliert
    assert "Schritt OK     Firmware-Update 192.168.10.50 (stable) > Update auf 1.5.0 anstoßen" in caplog.text


async def test_unreachable_error_names_host_and_call(ctx, network):
    jobs = JobManager(ctx.store)
    job = jobs.start("Firmware-Update", "x", lambda p: update_firmware(ctx, "192.168.10.99", None, p))
    await jobs.wait(job.id)
    assert job.step == "Firmware-Update 192.168.10.99 (stable) > Aktuelle Firmware abfragen"
    assert "192.168.10.99 nicht erreichbar bei Geräteinfo (GET /shelly)" in (job.error or "")
    assert "Ursache:" not in (job.error_detail or "")  # Ursache steht schon in der Meldung


async def test_job_shows_current_step_while_running(ctx):
    gate = asyncio.Event()
    seen: list[str | None] = []

    async def work(_progress):  # type: ignore[no-untyped-def]
        with steps.step("Eins"):
            with steps.step("Zwei"):
                await gate.wait()
            seen.append(job.step)

    jobs = JobManager(ctx.store)
    job = jobs.start("Test", None, work)
    await asyncio.sleep(0)
    assert job.step == "Eins > Zwei"
    gate.set()
    await jobs.wait(job.id)
    assert seen == ["Eins"] and job.status == "ok" and job.step is None


async def test_provision_steps_are_nested_and_logged(ctx, network, caplog):
    caplog.set_level(logging.INFO, logger="device_manager")
    shelly(network, ip="192.168.10.200", ver="2.0.1")
    await provision(ctx, "192.168.10.200", "wohnzimmer", update=False, certificate=False)
    text = caplog.text
    assert (
        "Schritt START  Neues Gerät wohnzimmer > 1/5 Übernehmen > Übernehmen 192.168.10.200 > Identität prüfen" in text
    )
    assert (
        "Schritt OK     Neues Gerät wohnzimmer > 2/5 IP-Reservierung > IP-Reservierung a8:03:2a:b1:23:45 > Reservierung"
        in text
    )


async def test_web_job_fragment_shows_failed_step(ctx, network):
    from fastapi.testclient import TestClient

    from device_manager.web.app import create_app

    app = create_app(ctx.settings, ctx)
    with TestClient(app) as client:
        jobs: JobManager = app.state.jobs
        job = jobs.start("Firmware-Update", "x", lambda p: update_firmware(ctx, "192.168.10.99", None, p))
        await jobs.wait(job.id)
        html = client.get(f"/jobs/{job.id}").text
    assert "Fehler im Schritt „Firmware-Update 192.168.10.99 (stable) &gt; Aktuelle Firmware abfragen“" in html
    assert "<summary>Details</summary>" in html


def test_redacting_formatter_hides_secrets_also_in_tracebacks():
    formatter = RedactingFormatter(["geheim-123"])
    try:
        raise ValueError("Passwort geheim-123 abgelehnt")
    except ValueError:
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "Wert %s", ("geheim-123",), exc_info=True)
        import sys

        record.exc_info = sys.exc_info()
    text = formatter.format(record)
    assert "geheim-123" not in text and text.count("***") >= 2


def test_configure_logging_writes_file(settings):
    settings = settings.model_copy(update={"log_level": "debug"})
    path = configure_logging(settings)
    assert path == log_file(settings)
    logging.getLogger("device_manager.test").debug("Testzeile mit %s", settings.shelly_password.get_secret_value())
    for handler in logging.getLogger().handlers:
        handler.flush()
    content = path.read_text(encoding="utf-8")
    assert "Testzeile mit ***" in content
    assert settings.shelly_password.get_secret_value() not in content


def test_describe_error_skips_repeated_causes():
    import httpcore
    import httpx

    from device_manager.opnsense.client import OPNsenseError

    text = "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"
    try:
        try:
            try:
                raise httpcore.ConnectError(text)
            except httpcore.ConnectError as low:
                raise httpx.ConnectError(text) from low
        except httpx.ConnectError as mid:
            raise OPNsenseError(f"OPNsense nicht erreichbar: {text} (ConnectError)") from mid
    except OPNsenseError as exc:
        described = steps.describe_error(exc)
    assert described == f"OPNsenseError: OPNsense nicht erreichbar: {text} (ConnectError)"
    assert "Ursache" not in described


def test_opnsense_missing_ca_file_gives_clear_error(tmp_path):
    from device_manager.opnsense.client import OPNsenseClient, OPNsenseError

    missing = tmp_path / "fehlt.pem"
    with pytest.raises(OPNsenseError, match="CA-Datei für OPNsense nicht lesbar") as exc:
        OPNsenseClient("https://fw", "k", "s", verify=str(missing))
    assert str(missing) in str(exc.value) and "PEM" in str(exc.value)


async def test_opnsense_certificate_error_hints_at_ca_file():
    import httpx

    from device_manager.opnsense.client import OPNsenseClient, OPNsenseError

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate in certificate chain")

    def factory(**kw):  # type: ignore[no-untyped-def]
        kw.pop("verify", None)
        return httpx.AsyncClient(transport=httpx.MockTransport(fail), **kw)

    client = OPNsenseClient("https://fw", "k", "s", verify=True, http_factory=factory)
    with pytest.raises(OPNsenseError, match="opnsense_ca_file eintragen"):
        await client.kea_leases()
