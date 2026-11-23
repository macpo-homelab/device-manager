"""Integration: PahoBus + Zigbee2Mqtt gegen einen echten Mosquitto-Broker (Docker)."""

import asyncio
import json
import shutil
import socket
import subprocess
import time
import uuid

import pytest

from device_manager.z2m.bus import PahoBus
from device_manager.z2m.client import Zigbee2Mqtt

pytestmark = pytest.mark.docker


@pytest.fixture(scope="module")
def mosquitto():
    if not shutil.which("docker") or subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("Docker nicht verfügbar bzw. Daemon läuft nicht")
    name = f"sm-mosquitto-{uuid.uuid4().hex[:6]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-p",
            "127.0.0.1::1883",
            "eclipse-mosquitto:2",
            "mosquitto",
            "-c",
            "/mosquitto-no-auth.conf",
        ],
        check=True,
        capture_output=True,
    )
    try:
        port_line = subprocess.run(["docker", "port", name, "1883"], check=True, capture_output=True, text=True)
        port = int(port_line.stdout.strip().splitlines()[0].rsplit(":", 1)[1])
        deadline = time.time() + 20
        while time.time() < deadline:
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.3)
        time.sleep(0.5)
        yield port
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


async def test_paho_bus_roundtrip_and_z2m_get(mosquitto):
    bus = PahoBus("127.0.0.1", mosquitto)
    await bus.connect()
    responder = PahoBus("127.0.0.1", mosquitto)
    await responder.connect()
    try:
        z2m = Zigbee2Mqtt(bus, "z2mtest", timeout=5)

        async def fake_device() -> None:
            async with responder.subscribe("z2mtest/Kueche/get") as queue:
                ready.set()
                _, payload = await queue.get()
                assert json.loads(payload) == {"ip_address": ""}
                await responder.publish("z2mtest/Kueche", json.dumps({"ip_address": "192.168.10.70"}))

        ready = asyncio.Event()
        task = asyncio.create_task(fake_device())
        await ready.wait()
        await asyncio.sleep(0.3)  # Subscription beim Broker aktiv
        assert await z2m.get("Kueche", "ip_address") == "192.168.10.70"
        await task

        await responder.publish(
            "z2mtest/bridge/devices",
            json.dumps(
                [
                    {
                        "friendly_name": "Kueche",
                        "ieee_address": "0x1",
                        "definition": {"vendor": "Shelly", "model": "S4"},
                    },
                ]
            ),
            retain=True,
        )
        assert [d.friendly_name for d in await z2m.shelly_devices()] == ["Kueche"]
    finally:
        await bus.close()
        await responder.close()


async def test_paho_bus_unreachable():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(ConnectionError):
        await PahoBus("127.0.0.1", port).connect(timeout=1)
