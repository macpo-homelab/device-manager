"""F5 – Zertifikat in OPNsense ausstellen und als HTTPS-Zertifikat auf dem Shelly installieren."""

import pytest

from device_manager.opnsense.client import OPNsenseError
from device_manager.services.adopt import adopt
from device_manager.services.certificate import install_certificate
from device_manager.services.common import ServiceError
from device_manager.services.reservation import reserve
from device_manager.tls import cert_info
from tests.conftest import shelly

IP = "192.168.10.50"


async def prepared(ctx, network, hostname="flur", **kw):
    """Gerät übernommen + Kea-Reservierung auf seine aktuelle IP (Voraussetzung für Zertifikate)."""
    sim = shelly(network, ip=IP, **kw)
    device = await adopt(ctx, IP)
    await reserve(ctx, device.mac, hostname, IP)
    return sim


async def test_install_certificate_end_to_end(ctx, network, opnsense):
    sim = await prepared(ctx, network, "flur-licht", ver="2.0.1")
    result = await install_certificate(ctx, IP, "flur-licht")

    assert sim.https_active and sim.reboots == 1
    info = cert_info(sim.active_cert)
    assert info.subject_cn == "flur-licht.home.lan"
    assert set(info.dns_names) == {"flur-licht.home.lan", "flur-licht"}
    assert info.ip_addresses == (IP,)
    assert result.fqdn == "flur-licht.home.lan"
    # Zertifikat wurde von OPNsense mit EC P-256 als Serverzertifikat angefordert
    [(_, body)] = [r for r in opnsense.requests if r[0] == "trust/cert/add"]
    assert body["cert"]["key_type"] == "prime256v1" and body["cert"]["cert_type"] == "server_cert"
    assert body["cert"]["caref"] == next(iter(opnsense.cas.values()))[0]


async def test_pem_is_uploaded(ctx, network):
    sim = await prepared(ctx, network, ver="2.0.1")
    await install_certificate(ctx, IP, "flur")
    assert sim.calls.count("Shelly.PutHTTPServerCert") >= 1
    assert sim.active_cert.startswith("-----BEGIN CERTIFICATE-----")


async def test_requires_firmware_2(ctx, network, opnsense):
    await prepared(ctx, network, ver="1.7.1")
    with pytest.raises(ServiceError, match="2.0.0"):
        await install_certificate(ctx, IP, "flur")
    assert opnsense.certs == {}


async def test_unknown_ca(ctx, network):
    await prepared(ctx, network, ver="2.0.1")
    ctx.settings = ctx.settings.model_copy(update={"cert_ca_name": "Gibt es nicht"})
    with pytest.raises(OPNsenseError, match="nicht gefunden"):
        await install_certificate(ctx, IP, "flur")


async def test_renewal_removes_previous_manager_cert(ctx, network, opnsense):
    await prepared(ctx, network, ver="2.0.1")
    first = await install_certificate(ctx, IP, "flur")
    second = await install_certificate(ctx, IP, "flur")
    assert second.removed_old == 1
    assert list(opnsense.certs) == [second.cert_uuid] and first.cert_uuid not in opnsense.certs


async def test_renewal_removes_cert_from_shelly_manager_era(ctx, network, opnsense):
    """Zertifikate von vor der Umbenennung („Shelly-Manager <fqdn>“) werden beim Erneuern ebenfalls ersetzt."""
    await prepared(ctx, network, ver="2.0.1")
    first = await install_certificate(ctx, IP, "flur")
    assert opnsense.certs[first.cert_uuid]["descr"] == "Device Manager flur.home.lan"
    opnsense.certs[first.cert_uuid]["descr"] = "Shelly-Manager flur.home.lan"
    second = await install_certificate(ctx, IP, "flur")
    assert second.removed_old == 1 and list(opnsense.certs) == [second.cert_uuid]


async def test_key_location_local_does_not_store_key_in_opnsense(ctx, network, opnsense):
    sim = await prepared(ctx, network, ver="2.0.1")
    ctx.settings = ctx.settings.model_copy(update={"cert_key_location": "local"})
    result = await install_certificate(ctx, IP, "flur")
    assert opnsense.certs[result.cert_uuid]["prv"] is None
    assert sim.https_active


async def test_failed_https_check_is_reported(ctx, network):
    await prepared(ctx, network, ver="2.0.1")

    async def broken(*_a, **_k):
        raise ConnectionRefusedError("refused")

    ctx.tls_verifier = broken
    with pytest.raises(ServiceError, match="HTTPS-Prüfung"):
        await install_certificate(ctx, IP, "flur")


async def test_inventory_records_expiry(ctx, network):
    sim = await prepared(ctx, network, ver="2.0.1")
    await install_certificate(ctx, IP, "flur")
    stored = ctx.store.get_device(sim.mac_colon)
    assert stored.cert_not_after and stored.hostname == "flur"


# Sicherheit: Schlüssel nur an verifizierte, reservierte Geräte (Security-Review, Schwachstelle 2) ---------


async def test_rejects_device_not_in_inventory(ctx, network, opnsense):
    sim = shelly(network, ip=IP, ver="2.0.1")
    with pytest.raises(ServiceError, match="kein übernommenes Gerät"):
        await install_certificate(ctx, IP, "flur")
    assert opnsense.certs == {} and sim.uploaded_key == ""


async def test_rejects_without_reservation(ctx, network, opnsense):
    sim = shelly(network, ip=IP, ver="2.0.1")
    await adopt(ctx, IP)
    with pytest.raises(ServiceError, match="Kea-Reservierung"):
        await install_certificate(ctx, IP, "flur")
    assert opnsense.certs == {} and sim.uploaded_key == ""


async def test_rejects_hostname_other_than_reservation(ctx, network, opnsense):
    sim = await prepared(ctx, network, "flur", ver="2.0.1")
    with pytest.raises(ServiceError, match="Reservierung entsprechen"):
        await install_certificate(ctx, IP, "opnsense")
    assert opnsense.certs == {} and sim.uploaded_key == ""


async def test_rejects_when_ip_now_answers_as_other_device(ctx, network, opnsense):
    sim = await prepared(ctx, network, ver="2.0.1")
    sim.online = False
    other = shelly(network, mac="DEADBEEF0001", ip=IP, ver="2.0.1")
    with pytest.raises(ServiceError, match="anderes Gerät"):
        await install_certificate(ctx, IP, "flur")
    assert other.uploaded_key == "" and opnsense.certs == {}


async def test_rejects_hostname_instead_of_ip(ctx):
    with pytest.raises(ServiceError, match="keine IPv4-Adresse"):
        await install_certificate(ctx, "evil.example", "flur")
