"""Simuliertes Shelly-Gerät (Gen2+ JSON-RPC inkl. Digest-Auth SHA-256, Firmware-Update, HTTPS-Zertifikat)."""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from device_manager.shelly.client import supports_custom_https
from device_manager.tls import key_matches_cert

if TYPE_CHECKING:
    from .network import SimNetwork

AUTH_FREE = {"Shelly.GetDeviceInfo"}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class ShellySim:
    mac: str  # 'A8032AB12345'
    ip: str | None
    model: str = "S3SW-001P16EU"
    app: str = "Mini1PMG3"
    gen: int = 3
    ver: str = "1.4.4"
    name: str | None = None
    available: dict[str, str] = field(default_factory=dict)  # stage -> version
    ha1: str | None = None
    reboot_ticks: int = 3
    update_breaks_device: bool = False
    online: bool = True
    id_override: str | None = None  # z. B. Fake-Gerät, das eine fremde Geräte-ID meldet
    lose_setauth_response: bool = False
    # Wie Plug M mit FW 1.8.99: Update wird installiert, das Gerät startet danach aber nicht von selbst neu
    update_needs_manual_reboot: bool = False
    update_install_ticks: int = 3  # Anfragen, bis das Update installiert ist (restart_required)
    # Zustand
    network: SimNetwork | None = None
    offline_ticks: int = 0
    pending_version: str | None = None
    uploaded_cert: str = ""
    uploaded_key: str = ""
    active_cert: str | None = None
    active_key: str | None = None
    calls: list[str] = field(default_factory=list)
    reboots: int = 0
    uptime: int = 1000
    install_ticks: int = 0
    restart_required: bool = False
    config: dict[str, dict[str, Any]] = field(default_factory=dict)  # Komponente -> Konfiguration
    nonces: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.transport = httpx.ASGITransport(app=self._build_app())

    @property
    def device_id(self) -> str:
        return self.id_override or f"shelly{self.app.lower()}-{self.mac.lower()}"

    @property
    def mac_colon(self) -> str:
        m = self.mac.lower()
        return ":".join(m[i : i + 2] for i in range(0, 12, 2))

    @property
    def https_active(self) -> bool:
        return bool(self.active_cert and self.active_key)

    # Zeit / Neustart ----------------------------------------------------------
    def start_reboot(self) -> None:
        self.reboots += 1
        self.online = False
        self.offline_ticks = self.reboot_ticks

    def tick(self) -> None:
        if self.online:
            self.uptime += 1
        if self.install_ticks > 0:
            self.install_ticks -= 1
            if self.install_ticks == 0:
                self.restart_required = True
        if self.offline_ticks > 0:
            self.offline_ticks -= 1
            if self.offline_ticks == 0:
                self._finish_reboot()

    def _finish_reboot(self) -> None:
        self.uptime = 0
        self.restart_required = False
        if self.install_ticks > 0:  # Neustart mitten in der Installation: Update verworfen
            self.pending_version = None
            self.install_ticks = 0
        if self.pending_version and not self.update_breaks_device:
            self.ver = self.pending_version
            self.available = {k: v for k, v in self.available.items() if v != self.ver}
        self.pending_version = None
        if self.update_breaks_device:
            return  # bleibt offline
        # Zertifikat wird wie beim echten Gerät nur aktiv, wenn Cert+Key zusammenpassen
        if self.uploaded_cert and self.uploaded_key and key_matches_cert(self.uploaded_cert, self.uploaded_key):
            self.active_cert, self.active_key = self.uploaded_cert, self.uploaded_key
        if self.network:
            reserved = self.network.dhcp_lookup(self.mac_colon)
            if reserved:
                self.ip = reserved
        self.online = True

    # HTTP --------------------------------------------------------------------
    def _info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "id": self.device_id,
            "mac": self.mac,
            "slot": 0,
            "model": self.model,
            "gen": self.gen,
            "fw_id": f"20240101-000000/{self.ver}-g0000000",
            "ver": self.ver,
            "app": self.app,
            "auth_en": self.ha1 is not None,
            "auth_domain": self.device_id if self.ha1 else None,
        }

    def _challenge(self) -> JSONResponse:
        nonce = secrets.token_hex(8)
        self.nonces.add(nonce)
        header = f'Digest qop="auth", realm="{self.device_id}", nonce="{nonce}", algorithm=SHA-256'
        return JSONResponse({"code": 401, "message": "unauthorized"}, 401, {"WWW-Authenticate": header})

    def _authorized(self, request: Request) -> bool:
        header = request.headers.get("authorization", "")
        if not header.startswith("Digest "):
            return False
        parts = dict(re.findall(r'(\w+)="?([^",]+)"?', header[7:]))
        if parts.get("nonce") not in self.nonces or parts.get("username") != "admin":
            return False
        ha2 = _sha256(f"{request.method}:{parts.get('uri')}")
        expected = _sha256(f"{self.ha1}:{parts['nonce']}:{parts['nc']}:{parts['cnonce']}:{parts['qop']}:{ha2}")
        return secrets.compare_digest(expected, parts.get("response", ""))

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/shelly")
        async def shelly() -> dict[str, Any]:
            return self._info()

        @app.post("/rpc")
        async def rpc(request: Request) -> JSONResponse:
            body = await request.json()
            method = body.get("method")
            if self.ha1 and method not in AUTH_FREE and not self._authorized(request):
                return self._challenge()
            self.calls.append(method)
            try:
                result = self._dispatch(method, body.get("params") or {})
            except RpcError as err:
                return JSONResponse({"id": body.get("id"), "error": {"code": err.code, "message": err.message}})
            if method == "Shelly.SetAuth" and self.lose_setauth_response:
                return Response(status_code=502)  # Gerät hat übernommen, Antwort geht verloren
            return JSONResponse({"id": body.get("id"), "src": self.device_id, "result": result})

        return app

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        match method:
            case "Shelly.GetDeviceInfo":
                return self._info()
            case "Sys.GetConfig":
                return {"device": {"name": self.name, "mac": self.mac}, **self.config.get("Sys", {})}
            case "Sys.SetConfig" | "WiFi.SetConfig" | "BLE.SetConfig" | "PLUGS_UI.SetConfig":
                component = method.split(".")[0]
                if component == "PLUGS_UI" and not self.app.startswith("PlugM"):
                    raise RpcError(404, f"No handler for {method}")
                cfg = params.get("config") or {}
                if component == "BLE" and "enable" in cfg and supports_custom_https(self.ver):
                    raise RpcError(-103, "Invalid argument 'enable'")  # ab 2.0.0 entfernt
                if component == "WiFi" and "pass" in cfg.get("ap", {}) and len(cfg["ap"]["pass"] or "") < 8:
                    raise RpcError(-103, "Invalid argument 'pass'")
                if component == "Sys" and "ui_data" in cfg:
                    # ungünstigster Fall: ui_data wird als Ganzes ersetzt, nicht zusammengeführt
                    self.config.setdefault("Sys", {})["ui_data"] = dict(cfg["ui_data"])
                    cfg = {k: v for k, v in cfg.items() if k != "ui_data"}
                _merge(self.config.setdefault(component, {}), cfg)
                if component == "Sys" and "name" in cfg.get("device", {}):
                    self.name = cfg["device"]["name"]
                needs_restart = component == "BLE"
                self.restart_required |= needs_restart
                return {"restart_required": needs_restart}
            case "Shelly.SetAuth":
                if params.get("user") != "admin" or params.get("realm") != self.device_id:
                    raise RpcError(-103, "Invalid argument")
                self.ha1 = params.get("ha1")
                return None
            case "Shelly.CheckForUpdate":
                return {stage: {"version": v, "build_id": f"build-{v}"} for stage, v in self.available.items()}
            case "Shelly.Update":
                stage = params.get("stage", "stable")
                if stage not in self.available:
                    raise RpcError(-114, "No update available")
                self.pending_version = self.available[stage]
                if self.update_needs_manual_reboot:
                    self.install_ticks = self.update_install_ticks
                else:
                    self.start_reboot()
                return None
            case "Sys.GetStatus":
                return {"mac": self.mac, "uptime": self.uptime, "restart_required": self.restart_required}
            case "Shelly.Reboot":
                self.start_reboot()
                return None
            case "Shelly.PutHTTPServerCert" | "Shelly.PutHTTPServerKey":
                if not supports_custom_https(self.ver):
                    raise RpcError(404, f"No handler for {method}")
                attr = "uploaded_cert" if method.endswith("Cert") else "uploaded_key"
                data = params.get("data") or ""
                value = (getattr(self, attr) + data) if params.get("append") else data
                setattr(self, attr, value)
                complete = bool(self.uploaded_cert and self.uploaded_key)
                return {"len": len(value), "restart_required": complete}
            case _:
                raise RpcError(404, f"No handler for {method}")


def _merge(target: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message
