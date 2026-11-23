# Device Manager – Technologieauswahl

Stand: 2026-11-06

## Rahmenbedingungen
- Muss als Home-Assistant-App (Docker, HA-Basis-Image, Ingress) **und** lokal unter Windows laufen.
- Externe Systeme: Shelly Gen2–4 (JSON-RPC über HTTP, Digest-Auth SHA-256), OPNsense-REST-API
  (Basic-Auth Key/Secret), Zigbee2MQTT über MQTT.
- Alle Anforderungen sollen automatisiert und **ohne Hardware** testbar sein.

## Entscheidungen

| Bereich | Wahl | Begründung | Verworfene Alternativen |
|---------|------|------------|-------------------------|
| Sprache | **Python ≥ 3.12** | HA-Ökosystem ist Python, HA-Basis-Images bringen Python mit, gute async-HTTP-/MQTT-Bibliotheken | Node/TypeScript (zusätzlicher Stack, kein Vorteil) |
| Web-Framework | **FastAPI + Uvicorn** | async, typisiert, eingebauter TestClient | Flask (sync), Django (zu schwer) |
| UI | **Jinja2 + htmx** (htmx lokal ausgeliefert) | kein JS-Build, Ingress-tauglich mit `<base href>`, einfach testbar | Vue/React SPA (Build-Kette, mehr Testaufwand) |
| HTTP-Client | **httpx** (async) | `DigestAuth` mit SHA-256, austauschbarer Transport → Simulatoren in Tests ohne Netzwerk | aiohttp (kein austauschbarer Transport) |
| MQTT | **paho-mqtt 2.x** in einem Thread + asyncio-Brücke | läuft unter Windows **und** Linux zuverlässig (aiomqtt braucht unter Windows eine Selector-Event-Loop) | aiomqtt |
| Konfiguration | **pydantic-settings** | `.env`/Umgebungsvariablen lokal, `/data/options.json` in HA, validiert | eigene Parser |
| Persistenz | **SQLite** (stdlib `sqlite3`) | ohne Server, liegt in HA unter `/data` | JSON-Dateien (keine Abfragen), Postgres (Overkill) |
| Discovery | **zeroconf** (mDNS `_shelly._tcp`) + Kea-Leases + manuelle IP | Gen2+ melden sich per mDNS; Leases als Fallback | Netzwerk-Scan (langsam, invasiv) |
| Zertifikatsprüfung | **cryptography** + stdlib `ssl` | prüfen, ob Schlüssel und Zertifikat zusammenpassen, Kette + TLS-Handshake gegen die OPNsense-CA | – |
| Tests | **pytest, pytest-asyncio** | Standard | unittest |
| Simulatoren | eigene ASGI-Apps (Shelly, OPNsense) + In-Memory-MQTT-Bus mit Z2M-Gerätesimulation | deterministisch, ohne Docker, unter Windows lauffähig | Docker-Mocks (langsam), Hardware (nicht automatisierbar) |
| Qualität | **ruff** (Lint und Format), **mypy** | schnell, verbreitet | flake8/black |
| Paketierung | `pyproject.toml` (setuptools), venv + pip; HA-App unter `addon/` | ohne Zusatzwerkzeuge reproduzierbar | Poetry/uv (nicht installiert) |

## Architektur

```
web (FastAPI + Jinja2/htmx)  ──►  services (Workflows, Jobs)  ──►  adapters
                                   adopt / reservation / firmware   shelly.ShellyClient   (httpx)
                                   certificate / zigbee_update      opnsense.OPNsenseClient (httpx)
                                   workflow (Gesamtassistent)       z2m.Zigbee2Mqtt  (MqttBus)
                                          │
                                          ▼
                                   store (SQLite: Geräte + Aktionsprotokoll)
```

- Lange Aktionen (Firmware, Gen4-Workflow) laufen als **Jobs** (asyncio-Tasks). Das UI fragt den Status per htmx ab.
- Adapter werden über eine `Context`-Fabrik erzeugt. Tests setzen dort den Transport der Simulatoren ein.

## Wichtige API-Details (aus dem OPNsense-Quellcode bzw. der Shelly-Doku verifiziert)
- Kea: `POST /api/kea/dhcpv4/add_reservation` `{"reservation": {subnet, ip_address, hw_address, hostname, description}}`,
  `search_subnet`, `search_reservation`, `GET /api/kea/leases4/search`, `POST /api/kea/service/reconfigure`.
- Trust: `GET /api/trust/ca/search` und `ca_list` (`caref`, `descr`), `POST /api/trust/cert/add`
  `{"cert": {action:"internal", caref, key_type:"prime256v1", digest:"sha256", cert_type:"server_cert",
  lifetime, commonname, altnames_dns, altnames_ip, descr}}`, `POST /api/trust/cert/generate_file/{uuid}/crt|prv`
  → `{"status":"ok","payload":"<PEM>"}`.
- Shelly: `Shelly.PutHTTPServerCert`/`Shelly.PutHTTPServerKey` (`data`, `append`), danach `Shelly.Reboot`; Firmware ≥ 2.0.0.

## Risiken
| Risiko | Gegenmaßnahme |
|--------|---------------|
| Trust-API-Feldwerte (`altnames_*`-Format, `key_type`) weichen je nach OPNsense-Version ab | Mapping zentral in `opnsense/client.py`, Live-Smoke-Test (`pytest -m live`) gegen das echte System |
| Shelly-HTTPS nur mit FW ≥ 2.0.0 | Vorbedingung prüfen und vorher F4 ausführen |
| Gen4 schaltet WLAN nicht ab (Fehler im Ablauf) | `finally`-Block mit Wiederholungen + Protokolleintrag + Warnung im UI |
