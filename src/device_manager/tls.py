"""Zertifikatsprüfungen: Schlüssel und Zertifikat passen zusammen, Ablaufdatum, echter TLS-Handshake."""

from __future__ import annotations

import asyncio
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization


@dataclass(frozen=True)
class CertInfo:
    subject_cn: str
    not_after: datetime
    sha256: str
    dns_names: tuple[str, ...]
    ip_addresses: tuple[str, ...]


def cert_info(cert_pem: str | bytes) -> CertInfo:
    data = cert_pem.encode() if isinstance(cert_pem, str) else cert_pem
    cert = x509.load_pem_x509_certificate(data)
    cn_attrs = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        dns = tuple(san.get_values_for_type(x509.DNSName))
        ips = tuple(str(ip) for ip in san.get_values_for_type(x509.IPAddress))
    except x509.ExtensionNotFound:
        dns, ips = (), ()
    return CertInfo(
        subject_cn=str(cn_attrs[0].value) if cn_attrs else "",
        not_after=cert.not_valid_after_utc,
        sha256=cert.fingerprint(hashes.SHA256()).hex(":"),
        dns_names=dns,
        ip_addresses=ips,
    )


def key_matches_cert(cert_pem: str, key_pem: str) -> bool:
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    fmt = serialization.PublicFormat.SubjectPublicKeyInfo
    enc = serialization.Encoding.DER
    return bool(cert.public_key().public_bytes(enc, fmt) == key.public_key().public_bytes(enc, fmt))


async def fetch_server_cert(host: str, *, port: int = 443, timeout: float = 3.0) -> CertInfo:
    """Liest das HTTPS-Zertifikat eines Hosts nur aus (ohne Prüfung, es werden keine Daten gesendet).

    Für die Anzeige bei gefundenen Geräten; Fehler (kein HTTPS, Timeout) werden an den Aufrufer weitergereicht.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # nur anzeigen, nichts vertrauen
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port, ssl=ctx), timeout)
    try:
        ssl_obj: ssl.SSLObject = writer.get_extra_info("ssl_object")
        der = ssl_obj.getpeercert(binary_form=True)
        if der is None:
            raise ssl.SSLError("Kein Server-Zertifikat erhalten")
        return cert_info(ssl.DER_cert_to_PEM_cert(der))
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ssl.SSLError, ConnectionError):
            pass


async def reverse_dns(ip: str, *, timeout: float = 2.0) -> str | None:
    """PTR-Abfrage (DNS-Name zu einer IP). None, wenn kein Eintrag oder keine Antwort."""
    try:
        name, _, _ = await asyncio.wait_for(asyncio.to_thread(socket.gethostbyaddr, ip), timeout)
    except (OSError, TimeoutError):
        return None
    return name.rstrip(".") or None


async def verify_https(
    host: str, *, server_hostname: str, ca_pem: str, port: int = 443, timeout: float = 10.0
) -> CertInfo:
    """Baut eine TLS-Verbindung auf und prüft Kette (gegen ca_pem) und Hostname. Liefert das Server-Zertifikat."""
    ctx = ssl.create_default_context(cadata=ca_pem)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=ctx, server_hostname=server_hostname), timeout
    )
    try:
        ssl_obj: ssl.SSLObject = writer.get_extra_info("ssl_object")
        der = ssl_obj.getpeercert(binary_form=True)
        if der is None:
            raise ssl.SSLError("Kein Server-Zertifikat erhalten")
        return cert_info(ssl.DER_cert_to_PEM_cert(der))
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ssl.SSLError, ConnectionError):
            pass
