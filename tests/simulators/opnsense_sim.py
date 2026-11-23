"""Simulierte OPNsense-API (Kea DHCPv4 + Trust) mit realer Zertifikatserzeugung."""

from __future__ import annotations

import base64
import secrets
import uuid as uuidlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .pki import TestCA

API_KEY = "test-key"
API_SECRET = "test-secret-very-secret"


@dataclass
class OPNsenseSim:
    subnets: dict[str, str] = field(default_factory=dict)  # uuid -> CIDR
    reservations: dict[str, dict[str, str]] = field(default_factory=dict)
    leases: list[dict[str, Any]] = field(default_factory=list)
    cas: dict[str, tuple[str, TestCA]] = field(default_factory=dict)  # uuid -> (refid, CA)
    certs: dict[str, dict[str, Any]] = field(default_factory=dict)
    applied: dict[str, str] = field(default_factory=dict)  # mac -> ip (nach reconfigure aktiv)
    reconfigures: int = 0
    unbound_reloads: int = 0
    unbound_allowed: bool = True  # False: API-Benutzer ohne Recht 'Services: Unbound DNS'
    requests: list[tuple[str, Any]] = field(default_factory=list)
    # dynamische Leases (wie ein echter DHCP-Server): liefert (ip, mac, hostname) aller Geräte im Netz
    lease_source: Callable[[], list[tuple[str, str, str]]] = lambda: []

    def __post_init__(self) -> None:
        self.app = self._build_app()

    # Setup-Helfer ---------------------------------------------------------------
    def add_subnet(self, cidr: str) -> str:
        uid = str(uuidlib.uuid4())
        self.subnets[uid] = cidr
        return uid

    def add_ca(self, descr: str) -> TestCA:
        ca = TestCA.create(descr)
        self.cas[str(uuidlib.uuid4())] = (secrets.token_hex(7)[:13], ca)
        return ca

    def add_lease(self, ip: str, mac: str, hostname: str = "") -> None:
        self.leases.append(
            {"address": ip, "hwaddr": mac, "client_id": "01:" + mac.lower(), "hostname": hostname, "state": "0"}
        )

    def all_leases(self) -> list[dict[str, Any]]:
        static = {lease["address"] for lease in self.leases}
        dynamic = [
            {"address": ip, "hwaddr": mac, "client_id": "01:" + mac.lower(), "hostname": host, "state": "0"}
            for ip, mac, host in self.lease_source()
            if ip not in static
        ]
        return self.leases + dynamic

    def dhcp_lookup(self, mac: str) -> str | None:
        return self.applied.get(mac.lower())

    # API -------------------------------------------------------------------------
    def _rows(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {"total": len(rows), "rowCount": len(rows), "current": 1, "rows": rows}

    def _reservation_errors(self, data: dict[str, str], own_uuid: str | None) -> dict[str, str]:
        errors = {}
        if data.get("subnet") not in self.subnets:
            errors["reservation.subnet"] = "Bitte ein Subnetz wählen"
        if not data.get("ip_address"):
            errors["reservation.ip_address"] = "IP erforderlich"
        for uid, other in self.reservations.items():
            if (
                uid != own_uuid
                and other["hw_address"].lower() == data.get("hw_address", "").lower()
                and other["subnet"] == data.get("subnet")
            ):
                errors["reservation.hw_address"] = "Duplicate entry exists"
        return errors

    def _issue(self, data: dict[str, Any]) -> dict[str, Any]:
        caref = data.get("caref")
        ca = next((c for refid, c in self.cas.values() if refid == caref), None)
        if data.get("action") != "internal" or ca is None or not data.get("commonname"):
            return {"result": "failed", "validations": {"cert.caref": "invalid"}}
        if data.get("key_type") != "prime256v1":
            return {"result": "failed", "validations": {"cert.key_type": "unexpected in test"}}
        dns = [d for d in str(data.get("altnames_dns", "")).splitlines() if d]
        ips = [i for i in str(data.get("altnames_ip", "")).splitlines() if i]
        crt, prv = ca.issue(data["commonname"], dns, ips, int(data.get("lifetime", 397)))
        uid = str(uuidlib.uuid4())
        local = data.get("private_key_location") == "local"
        self.certs[uid] = {
            "descr": data.get("descr", ""),
            "crt": crt,
            "prv": None if local else prv,
            "commonname": data["commonname"],
            "caref": caref,
        }
        response: dict[str, Any] = {"result": "saved", "uuid": uid}
        if local:
            response["private_key"] = prv
        return response

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        @app.middleware("http")
        async def basic_auth(request: Request, call_next):  # type: ignore[no-untyped-def]
            header = request.headers.get("authorization", "")
            expected = "Basic " + base64.b64encode(f"{API_KEY}:{API_SECRET}".encode()).decode()
            if not secrets.compare_digest(header, expected):
                return JSONResponse({"status": 401, "message": "Authentication Failed"}, 401)
            return await call_next(request)

        @app.post("/api/{path:path}")
        async def post(path: str, request: Request) -> JSONResponse:
            body = await request.json() if await request.body() else {}
            self.requests.append((path, body))
            if path.startswith("unbound/") and not self.unbound_allowed:
                return JSONResponse({"status": 403, "message": "Forbidden"}, 403)
            return JSONResponse(self._handle(path, body))

        return app

    def _handle(self, path: str, body: dict[str, Any]) -> Any:  # noqa: C901
        parts = path.split("/")
        match parts:
            case ["kea", "dhcpv4", "search_subnet"]:
                return self._rows([{"uuid": u, "subnet": c, "description": ""} for u, c in self.subnets.items()])
            case ["kea", "dhcpv4", "search_reservation"]:
                return self._rows([{"uuid": u, **r} for u, r in self.reservations.items()])
            case ["kea", "leases4", "search"]:
                return self._rows(self.all_leases())
            case ["kea", "dhcpv4", "add_reservation"]:
                data = body.get("reservation", {})
                if errors := self._reservation_errors(data, None):
                    return {"result": "failed", "validations": errors}
                uid = str(uuidlib.uuid4())
                self.reservations[uid] = {k: str(v) for k, v in data.items()}
                return {"result": "saved", "uuid": uid}
            case ["kea", "dhcpv4", "set_reservation", uid]:
                data = body.get("reservation", {})
                if uid not in self.reservations:
                    return {"result": "failed"}
                if errors := self._reservation_errors(data, uid):
                    return {"result": "failed", "validations": errors}
                self.reservations[uid] = {k: str(v) for k, v in data.items()}
                return {"result": "saved"}
            case ["kea", "service", "reconfigure"]:
                self.reconfigures += 1
                self.applied = {r["hw_address"].lower(): r["ip_address"] for r in self.reservations.values()}
                return {"status": "ok"}
            case ["unbound", "service", "reconfigure"]:
                self.unbound_reloads += 1
                return {"status": "ok"}
            case ["trust", "ca", "search"]:
                return self._rows([{"uuid": u, "refid": ref, "descr": ca.name} for u, (ref, ca) in self.cas.items()])
            case ["trust", "ca", "generate_file", uid, "crt"]:
                if uid not in self.cas:
                    return {"status": "failed", "error": "Missing certificate"}
                return {"status": "ok", "descr": self.cas[uid][1].name, "payload": self.cas[uid][1].cert_pem}
            case ["trust", "cert", "add"]:
                return self._issue(body.get("cert", {}))
            case ["trust", "cert", "generate_file", uid, ftype]:
                cert = self.certs.get(uid)
                payload = cert and {"crt": cert["crt"], "prv": cert["prv"]}.get(ftype)
                if not payload:
                    return {"status": "failed", "error": "Missing certificate"}
                return {"status": "ok", "descr": cert["descr"], "payload": payload}
            case ["trust", "cert", "search"]:
                return self._rows(
                    [{"uuid": u, "descr": c["descr"], "commonname": c["commonname"]} for u, c in self.certs.items()]
                )
            case ["trust", "cert", "del", uid]:
                return {"result": "deleted" if self.certs.pop(uid, None) else "failed"}
        return {"status": "failed", "message": f"unbekannter Endpunkt {path}"}
