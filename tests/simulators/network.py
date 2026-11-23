"""Simuliertes Netzwerk: leitet httpx-Requests per Host an Simulatoren, simuliert Neustarts und DHCP."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from .shelly_sim import ShellySim


class SimNetwork(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.devices: list[ShellySim] = []
        self.servers: dict[str, httpx.ASGITransport] = {}
        # mac -> reservierte IP (wird von OPNsense-Sim nach Kea-Reconfigure gesetzt)
        self.dhcp_lookup: Callable[[str], str | None] = lambda mac: None
        self.requests: list[httpx.Request] = []

    def add_device(self, device: ShellySim) -> ShellySim:
        device.network = self
        self.devices.append(device)
        return device

    def add_server(self, host: str, app: object) -> None:
        self.servers[host] = httpx.ASGITransport(app=app)  # type: ignore[arg-type]

    def by_ip(self, ip: str) -> ShellySim | None:
        return next((d for d in self.devices if d.online and d.ip == ip), None)

    def tick(self) -> None:
        """Jeder Request lässt simulierte Zeit vergehen (Neustarts schreiten voran)."""
        for device in self.devices:
            device.tick()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.tick()
        host = request.url.host
        if host in self.servers:
            return await self.servers[host].handle_async_request(request)
        device = self.by_ip(host)
        if device is None:
            raise httpx.ConnectError(f"Host {host} nicht erreichbar", request=request)
        return await device.transport.handle_async_request(request)
