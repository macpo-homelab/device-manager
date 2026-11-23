"""Echter TLS-Handshake (verify_https) gegen einen lokalen Server mit CA-signiertem Zertifikat."""

import asyncio
import ssl
from pathlib import Path

import pytest

from device_manager.tls import key_matches_cert, verify_https
from tests.simulators.pki import TestCA


@pytest.fixture
async def tls_server(tmp_path: Path):
    ca = TestCA.create("Heimnetz CA")
    cert, key = ca.issue("flur.home.lan", ["flur.home.lan", "flur"], ["127.0.0.1"])
    (tmp_path / "c.pem").write_text(cert)
    (tmp_path / "k.pem").write_text(key)
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(tmp_path / "c.pem", tmp_path / "k.pem")

    async def handle(reader, writer):
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=ctx)
    port = server.sockets[0].getsockname()[1]
    yield ca, port, cert, key
    server.close()
    await server.wait_closed()


async def test_verify_https_success(tls_server):
    ca, port, _, _ = tls_server
    info = await verify_https("127.0.0.1", server_hostname="flur.home.lan", ca_pem=ca.cert_pem, port=port)
    assert info.subject_cn == "flur.home.lan"
    assert "flur" in info.dns_names


async def test_verify_https_wrong_hostname(tls_server):
    ca, port, _, _ = tls_server
    with pytest.raises(ssl.SSLCertVerificationError):
        await verify_https("127.0.0.1", server_hostname="andere.home.lan", ca_pem=ca.cert_pem, port=port)


async def test_verify_https_wrong_ca(tls_server):
    _, port, _, _ = tls_server
    other = TestCA.create("Fremde CA")
    with pytest.raises(ssl.SSLCertVerificationError):
        await verify_https("127.0.0.1", server_hostname="flur.home.lan", ca_pem=other.cert_pem, port=port)


def test_key_matches_cert():
    ca = TestCA.create()
    cert, key = ca.issue("a", ["a"], [])
    _, other_key = ca.issue("b", ["b"], [])
    assert key_matches_cert(cert, key)
    assert not key_matches_cert(cert, other_key)
