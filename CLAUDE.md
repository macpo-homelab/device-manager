# CLAUDE.md – Device Manager

## Projektziel
Device Manager (bis 0.3.x „Shelly Manager“): Geräteverwaltung im Heimnetz, später auch herstellerunabhängige Funktionen
(z. B. Gerät austauschen). Bisher: Verwaltung von Shelly-Geräten (Gen2/3/4): übernehmen, DHCP-Reservierung in OPNsense (Kea), Firmware-Updates,
HTTPS-Zertifikate (OPNsense-CA → Shelly), Gen4-Zigbee-Geräte per Zigbee2MQTT kurz ins WLAN holen, um sie zu aktualisieren.
Läuft lokal (Entwicklung) und als Home-Assistant-App (Ingress).

## Stand (2026-11-23)
- v0.4.1: Öffentliches GitHub-Repo `macpo-homelab/device-manager` vorbereitet, SECURITY.md.
- v0.4.0: Umbenennung in Device Manager (Paket `device_manager`, Slug `device_manager`, Präfix `DM_`).
- v0.3.2: Veröffentlichung auf GitHub vorbereitet (URLs, Lizenzhinweise, FUNDING.yml, Koordinaten optional).
- v0.3.1: Verteilung über eigenes App-Repository (F9b, `scripts/publish_addon.py`).
- v0.3.0: Oberfläche überarbeitet (Skills `web-design-guidelines` + `frontend-design`), F12 Gerätestatus.
- Analyse, Technologieauswahl und Umsetzung v0.1 sind fertig. Alle Anforderungen F1–F10 sind implementiert und durch Simulatoren getestet.
- Das Security-Review ist umgesetzt (Identitätsprüfung, Passwort pro Gerät, CSRF- und Rebinding-Schutz).
- Secrets in der `.env` per DPAPI verschlüsselbar. Schritt-Logging mit Fehlerdiagnose (F10a).
- Nicht an echter Hardware verifiziert, siehe „Offene Punkte“.
- Doku: [docs/01-anforderungsanalyse.md](docs/01-anforderungsanalyse.md) (inkl. Entscheidungen O1–O8),
  [docs/02-technologieauswahl.md](docs/02-technologieauswahl.md), [docs/03-testkonzept.md](docs/03-testkonzept.md) (Traceability).

## Getroffene Entscheidungen (vom Nutzer)
- Onboarding: Gerät kommt per Shelly-App ins WLAN, der Manager *übernimmt* es (kein AP- oder BLE-Weg).
- DHCP: Kea. Zertifikat: nur HTTPS der Web-UI. Gen1: nicht unterstützt.
- Z2M direkt per MQTT. Firmware: stable, beta wählbar. Bietet ein Gerät im Kanal stable eine Vorabversion an, wird sie mit Hinweis installiert.
- Firmware-Update: nie selbst neu starten. Alle 10 s Version/Uptime/`restart_required` abfragen; ohne Neustart nach 5 min den Anwender
  per Button im Job bitten (`jobs.request_user_action`). Shelly hat keine abfragbare Update-Statusinformation.
- Neue Geräte (Assistent, nach Firmware): Standardeinstellungen F11 (`services/defaults.py`), AP-Passwort = Geräte-Passwort.
  NTP-Server (`DM_SHELLY_NTP_SERVER`) per Button „an alle Geräte verteilen“; übrige Standardwerte nur beim Einrichten.
- Name: wie Hostname, aber Großbuchstaben erlaubt (`validate_name`); Kea-Hostname und Gerätename = Name,
  DNS-/Zertifikats-Hostname = Name klein (`Device.name` / `Device.hostname`).
- DHCP-Beschreibung = Produktname mit Generation („Shelly Plug M Gen 3“, `product_name`). Nach Reservierungsänderung Unbound neu laden.
- Passwörter: `DM_SHELLY_PASSWORD` ist das Master-Passwort, pro Gerät `derive_password(master, mac)` (HMAC). `auth_mode` im Inventar: device | shared (Altbestand) | none.
- IP = nächste freie aus dem Bereich (überschreibbar). Hostname immer manuell. CA existiert in OPNsense (Auswahl per Name).
- UI: FastAPI + Jinja2 + htmx (htmx lokal ausgeliefert).
- Verteilung (F9b): öffentliches App-Repo auf GitHub (`macpo-homelab/ha-apps`, Organisation des Nutzers; Maintainer und Copyright `macpo`), HA baut lokal
  (keine Registry/CI). Inhalt erzeugt `scripts/publish_addon.py <klon> --commit`, danach pushen. Lizenz MIT,
  Fremdlizenzen in `THIRD_PARTY_NOTICES.md`, Spenden über `.github/FUNDING.yml` (GitHub Sponsors `macpo`).
- Keine privaten Daten im Repo (wird öffentlich): keine Standortkoordinaten als Standard, keine internen Hostnamen.

## Befehle (Windows, Git Bash)
```bash
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m device_manager                 # http://127.0.0.1:8099, Konfig über .env (DM_*)
.venv/Scripts/python -m pytest                         # Standard-Suite (ohne docker/live)
.venv/Scripts/python -m pytest -m docker               # Docker-Daemon nötig
.venv/Scripts/python -m pytest -m live                 # echte OPNsense, nur lesend
.venv/Scripts/ruff check src tests scripts && .venv/Scripts/ruff format src tests scripts && .venv/Scripts/mypy
python scripts/build_addon.py                          # HA-App-Build-Kontext erzeugen
python scripts/publish_addon.py ../ha-apps --commit   # App-Repo füllen (danach push)
```
`uv` ist nicht installiert → venv + pip.

## Architektur
- `src/device_manager/`
  - `config.py`: Settings (Präfix `DM_`, `.env`; in HA `/data/options.json` mit Vorrang)
  - `context.py`: Fabrik für Adapter. Tests setzen hier Transporte, Bus, TLS-Verifier und `sleep` ein.
  - `shelly/client.py` (RPC, Digest SHA-256), `opnsense/client.py` (Kea + Trust), `z2m/bus.py` (PahoBus/InMemoryBus), `z2m/client.py`
  - `services/identity.py`: Identitätsprüfung (IP/ID/MAC/Lease), Passwörter setzen, prüfen und umstellen
  - `services/`: `inventory` F1 + F12 (`check_status`), `adopt` F2, `reservation` F3, `firmware` F4, `certificate` F5, `zigbee` F6, `provision` F7
  - `jobs.py`: Hintergrund-Jobs und Protokolleintrag (F10). `store.py`: SQLite. `tls.py`: Zertifikatsprüfungen.
  - `steps.py`: Schritt-Logging (`with step("…")`, ContextVar), `failed_step`, `describe_error` (F10a).
    `logsetup.py`: Konsole + rotierende Datei `<data_dir>/logs/device_manager.log`, Level `DM_LOG_LEVEL`, Secrets geschwärzt.
  - `envcrypt.py`: DPAPI-Verschlüsselung von `.env`-Secrets (Windows).
  - `web/`: FastAPI-App, Templates, Static. **Nur relative Links** (Ingress über `<base href>`).
    `web/view.py`: Gerätezustand/LED (F12), Zertifikatsstatus, Protokolleinträge. Design „Verteilerkasten“: Farb-Tokens
    in `static/style.css` (hell/dunkel), IBM Plex lokal in `static/fonts/` (OFL). Buttons: Standard umrandet,
    `.primary` = eine Hauptaktion je Bereich, `.danger-link` zum Entfernen; `hx-disabled-elt` an Aktionen.
    Zeiten als `<time datetime data-local>`, Formatierung im Browser (`forms.js`, Intl; kein tzdata unter Windows).
    `static/`-Links mit `?v={{ asset_version }}` (Cache-Buster).
- `tests/simulators/`: ShellySim, OPNsenseSim (echte PKI), Z2MSim, SimNetwork (Routing, Neustarts, DHCP)
- `addon/device_manager/`: HA-App (config.yaml, build.yaml, Dockerfile, translations, DOCS.md)

## Altlasten aus der Umbenennung (nicht ändern)
- `envcrypt._ENTROPY = b"shelly-manager/v1"`: sonst lassen sich bestehende `dpapi:`-Werte nicht mehr entschlüsseln.
- `certificate.LEGACY_DESCR_PREFIXES = ("Shelly-Manager",)`: alte Zertifikate in OPNsense werden beim Erneuern ersetzt.
- `Settings.db_path` übernimmt `shelly_manager.sqlite3` einmalig als `device_manager.sqlite3`.
- Shelly-spezifische Namen (`shelly/`, Optionen `shelly_*`, `DM_SHELLY_*`) bleiben, weil sie wirklich Shelly betreffen.

## Sicherheitsregeln (Security-Review, [docs/04-security-review.md](docs/04-security-review.md))
- Zugangsdaten **nie** an ungeprüfte Hosts: Shelly-Clients nur über `ctx.shelly(host)` (Inventar) oder nach `verify_identity` über `ctx.shelly_with(...)` mit `expected_realm`.
- Private Schlüssel nur an Geräte mit Inventar-Eintrag und Kea-Reservierung (IP + Hostname); vorher `verify_inventory_device`.
- Das Inventar-`Device` in Services durchreichen (`update_firmware(..., device)`, `install_certificate(..., device)`), statt es über die IP zu suchen.
- `auth_mode` None/unbekannt → keine Zugangsdaten. Das Master-Passwort nie anzeigen oder loggen.
- Logs: nie Request-/Response-Bodies loggen (enthalten ha1, private Schlüssel, WLAN-Passwort), nur Methode/Pfad/Status.
- Neue zustandsändernde Endpunkte müssen POST sein, damit der Request-Guard greift (HX-Request, Origin, Host). Formulare nur über htmx (`hx-post`).

## Arbeitsregeln
- Öffentliches Repo `macpo-homelab/device-manager` (seit 0.4.1, frische Historie). Commits mit der Noreply-Adresse
  des Nutzers (lokal in `.git/config` gesetzt), keine privaten Daten (Mailadressen, interne Hostnamen, Standort,
  Secrets) in Code, Doku, Tests oder Commit-Messages.
- Doku, UI-Texte und Commit-Messages auf Deutsch, Code-Bezeichner auf Englisch.
- **Versionsnummer bei jeder Änderung hochzählen** (vom Nutzer gefordert), in `pyproject.toml` und
  `addon/device_manager/config.yaml` (Test prüft Gleichheit): kleine Anpassung → dritte Stelle, größere Änderung →
  zweite Stelle (dritte auf 0), „Release“ → erste Stelle (nur auf Ansage des Nutzers). Version in der Commit-Message nennen.
- **Bei jeder Änderung einen Eintrag in `addon/device_manager/CHANGELOG.md`** (neueste Version oben, `## x.y.z`,
  kurze Stichpunkte aus Anwendersicht auf Deutsch). Home Assistant zeigt ihn beim Update an; ein Test prüft den Eintrag.
- Bei offenen fachlichen Fragen den Nutzer fragen statt Annahmen zu treffen.
- Keine Secrets im Repo oder in Logs. Neue geheime Settings als `SecretStr` anlegen und in `Settings.secrets()` aufnehmen (der N1-Test prüft das).
  Außerdem in den `_decrypt_secret`-Validator (`config.py`) aufnehmen, damit `dpapi:`-Werte aus der `.env` funktionieren
  (`envcrypt.py`, Windows DPAPI; Verschlüsseln mit `python -m device_manager.envcrypt DM_NAME`).
- Jede neue Anforderung bekommt einen Akzeptanztest und einen Eintrag in der Traceability-Matrix (`docs/03-testkonzept.md`).
- Neue Settings-Felder auch in `.env.example`, `addon/.../config.yaml` (options/schema) und `translations/de.yaml` eintragen (Tests prüfen das).
- Arbeitsschritte in Services mit `with step("…")` markieren. Fehlermeldungen nennen Host, Aufruf und Ursache und
  verketten die Original-Ausnahme mit `raise … from exc`.
- Externe Systeme nur über Adapter ansprechen. Das Verhalten im passenden Simulator nachbilden.
- Diese Datei bei jeder Architektur- oder Phasenänderung aktualisieren.

## Externe APIs (verifiziert über Doku bzw. OPNsense-Quellcode)
- Shelly: `GET /shelly`, `POST /rpc` (`Shelly.CheckForUpdate`, `Shelly.Update {stage}`, `Sys.GetStatus` (`uptime`, `restart_required`),
  `Sys.SetConfig {device.name, device.eco_mode, location.tz/lat/lon, sntp.server, ui_data.df}`
  (`ui_data.df` = Datumsformat der Weboberfläche, nicht dokumentiert, am Plug M Gen3 ausgelesen; übrige ui_data-Werte erhalten), `WiFi.SetConfig {ap.enable, ap.pass}`,
  `BLE.SetConfig {rpc.enable}` (ab 2.0.0 kein `enable`), `PLUGS_UI.SetConfig {leds.mode: off}` (nur Plug M Gen3), `Shelly.SetAuth {user,realm,ha1}`,
  `Shelly.PutHTTPServerCert/Key {data,append}` ab FW 2.0.0, danach `Shelly.Reboot`).
- Z2M (Gen4): `zigbee2mqtt/<name>/set {"wifi_config": {enabled, ssid, password}}`, `/get {"ip_address": ""}`. Keine MAC über Zigbee.
- OPNsense: `kea/dhcpv4/{search_subnet,search_reservation,add_reservation,set_reservation/<uuid>}`, `kea/leases4/search`,
  `kea/service/reconfigure`, `unbound/service/reconfigure`, `trust/ca/search`, `trust/{ca,cert}/generate_file/<uuid>/<crt|prv>`, `trust/cert/{add,search,del/<uuid>}`.
  Zertifikat: `{"cert": {action: internal, caref, key_type: prime256v1, digest: sha256, cert_type: server_cert, ...}}`.

## Offene Punkte
- Kea-Hostname mit Großbuchstaben an echter OPNsense prüfen (Validierung des Felds).
- Live-Verifikation: Format von `altnames_dns`/`altnames_ip` (zeilengetrennt?), Z2M-`wifi_config` an echter Gen4-Hardware,
  Ingress-Quell-IP bei `host_network`.
- GitHub-Repos `macpo-homelab/device-manager` und `macpo-homelab/ha-apps` anlegen und erstmals pushen.
