"""Mini-PKI für Tests: CA und von ihr signierte Serverzertifikate (EC P-256)."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def _pem_key(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


@dataclass
class TestCA:
    __test__ = False  # keine Pytest-Testklasse

    name: str
    key: ec.EllipticCurvePrivateKey
    cert: x509.Certificate

    @classmethod
    def create(cls, name: str = "Test CA") -> TestCA:
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        now = datetime.now(UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256())
        )
        return cls(name, key, cert)

    @property
    def cert_pem(self) -> str:
        return self.cert.public_bytes(serialization.Encoding.PEM).decode()

    def issue(self, common_name: str, dns: list[str], ips: list[str], days: int = 397) -> tuple[str, str]:
        key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.now(UTC)
        san: list[x509.GeneralName] = [x509.DNSName(d) for d in dns]
        san += [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
        builder = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
            .issuer_name(self.cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self.key.public_key()), critical=False)
        )
        if san:
            builder = builder.add_extension(x509.SubjectAlternativeName(san), critical=False)
        cert = builder.sign(self.key, hashes.SHA256())
        return cert.public_bytes(serialization.Encoding.PEM).decode(), _pem_key(key)
