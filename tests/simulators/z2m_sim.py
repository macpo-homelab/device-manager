"""Simuliertes Zigbee2MQTT mit Shelly-Gen4-Geräten auf einem InMemoryBus."""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from typing import Any

from device_manager.z2m.bus import InMemoryBus

from .network import SimNetwork
from .shelly_sim import ShellySim


@dataclass
class ZigbeeShelly:
    friendly_name: str
    ieee: str
    shelly: ShellySim  # Gerät im WLAN, solange wifi aktiv ist
    wifi_ip: str
    model: str = "S4SW-001X16EU"
    wifi_enabled: bool = False
    ssid: str = ""
    ignore_disable: bool = False
    history: list[bool] = field(default_factory=list)


class Z2MSim:
    def __init__(
        self,
        bus: InMemoryBus,
        network: SimNetwork,
        base: str = "zigbee2mqtt",
        expected_ssid: str = "HomeWLAN",
        expected_password: str = "wlan-secret",
    ) -> None:
        self.bus, self.network, self.base = bus, network, base
        self.expected = (expected_ssid, expected_password)
        self.devices: dict[str, ZigbeeShelly] = {}
        self._task: asyncio.Task[None] | None = None

    def add(self, device: ZigbeeShelly) -> ZigbeeShelly:
        device.shelly.online = False
        device.shelly.ip = None
        self.network.add_device(device.shelly)
        self.devices[device.friendly_name] = device
        return device

    async def start(self) -> None:
        payload = [
            {
                "friendly_name": "Coordinator",
                "ieee_address": "0x00",
                "type": "Coordinator",
                "definition": None,
            }
        ]
        payload += [
            {
                "friendly_name": d.friendly_name,
                "ieee_address": d.ieee,
                "type": "Router",
                "definition": {"vendor": "Shelly", "model": d.model},
            }
            for d in self.devices.values()
        ]
        payload.append(
            {
                "friendly_name": "lamp",
                "ieee_address": "0x99",
                "type": "Router",
                "definition": {"vendor": "IKEA", "model": "LED"},
            }
        )
        await self.bus.publish(f"{self.base}/bridge/devices", json.dumps(payload), retain=True)
        ready = asyncio.Event()
        self._task = asyncio.create_task(self._serve(ready))
        await ready.wait()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def _state(self, dev: ZigbeeShelly) -> dict[str, Any]:
        return {
            "wifi_config": {
                "enabled": dev.wifi_enabled,
                "ssid": dev.ssid,
                "password": "",
                "static_ip": "",
                "net_mask": "",
                "gateway": "",
                "name_server": "",
            },
            "wifi_status": "Connected" if dev.shelly.online else "Disconnected",
            "ip_address": dev.shelly.ip if dev.wifi_enabled and dev.shelly.online else "0.0.0.0",
        }

    async def _serve(self, ready: asyncio.Event) -> None:
        async with self.bus.subscribe(f"{self.base}/+/+") as queue:
            ready.set()
            while True:
                topic, payload = await queue.get()
                _, name, action = topic.split("/")
                dev = self.devices.get(name)
                if dev is None or action not in ("set", "get"):
                    continue
                data = json.loads(payload)
                if action == "set" and "wifi_config" in data:
                    self._apply_wifi(dev, data["wifi_config"])
                state = self._state(dev)
                reply = state if action == "set" else {k: state[k] for k in data if k in state}
                await self.bus.publish(f"{self.base}/{name}", json.dumps(reply))

    def _apply_wifi(self, dev: ZigbeeShelly, cfg: dict[str, Any]) -> None:
        enabled = bool(cfg.get("enabled"))
        if not enabled and dev.ignore_disable:
            return
        dev.history.append(enabled)
        dev.wifi_enabled = enabled
        dev.ssid = cfg.get("ssid", dev.ssid)
        if enabled and (cfg.get("ssid"), cfg.get("password")) == self.expected:
            dev.shelly.ip = dev.wifi_ip
            dev.shelly.online = True
        elif not enabled:
            dev.shelly.online = False
            dev.shelly.ip = None
