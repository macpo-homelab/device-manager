# Device Manager – Testkonzept

## Teststufen

| Stufe | Ausführung | Externe Systeme | Zweck |
|-------|------------|-----------------|-------|
| Unit / Akzeptanz (Standard) | `pytest` | Simulatoren (in-process) | Jede Anforderung F1–F10 / N1–N3 automatisiert prüfen, ohne Hardware, in wenigen Sekunden |
| Integration Docker | `pytest -m docker` | Mosquitto-Container; Docker-Build der HA-App | echter MQTT-Weg (PahoBus) und baubares HA-Image |
| Live-Smoke | `pytest -m live` | echte OPNsense, optional ein echtes Shelly (`DM_LIVE_SHELLY_IP`) | **nur lesend**: API-Formate, CA vorhanden, Shelly-Auth |

## Simulatoren (`tests/simulators/`)

- **`ShellySim`**: Gen2+-JSON-RPC mit Digest-Auth SHA-256 (`Shelly.SetAuth`), `CheckForUpdate`/`Update` samt simuliertem
  Neustart, `PutHTTPServerCert/-Key` mit `append`-Chunks. Das Zertifikat wird nach dem Neustart nur aktiv, wenn Zertifikat und Schlüssel
  zusammenpassen (wie beim echten Gerät). Bei FW < 2.0.0 gibt es keinen HTTPS-Handler.
- **`SimNetwork`**: httpx-Transport, der nach Host routet. Jeder Request lässt simulierte Zeit vergehen, damit Neustarts fortschreiten.
  DHCP: Nach einem Neustart erhält ein Gerät die in Kea reservierte IP (erst nach `reconfigure`).
- **`OPNsenseSim`**: Kea-Subnetze, Reservierungen (Validierung wie im Model: Duplikat-MAC je Subnetz), Leases,
  Trust-API mit **echter** CA (EC P-256) und Zertifikatsausstellung (`private_key_location` firewall/local), Basic-Auth.
- **`Z2MSim`**: Zigbee2MQTT auf dem `InMemoryBus`: `bridge/devices` (retained), `wifi_config` set/get, `ip_address`.
  Wird WLAN aktiviert und stimmen die Zugangsdaten, erscheint das Gen4-Gerät im `SimNetwork`.
- **TLS**: In den Ablauftests ersetzt ein Verifier den Handshake. Er prüft das *aktive* Gerätezertifikat kryptografisch
  gegen die CA und den Hostnamen. `verify_https` selbst wird in `test_tls.py` mit einem echten lokalen TLS-Server getestet.

## Traceability-Matrix

| Anforderung | Testdatei(en) | Wichtige Fälle |
|-------------|---------------|----------------|
| F1 Inventar | `test_f1_inventory.py`, `test_web.py` | Geräteinfo → Inventar, manuelle Felder bleiben erhalten, Gen1 abgelehnt, Discovery über Leases, bekannte Geräte (in der Suche ausgeblendet, `test_web.py`), nicht erreichbare Geräte, Zigbee-Abgleich ohne Duplikate, Geräte- und Suchliste nach Übernahme aktualisiert (`test_web.py`) |
| F2 Übernehmen | `test_f2_adopt.py`, `test_security.py` | Gerätespezifisches Passwort setzen (ha1 korrekt, je Gerät verschieden), vorhandenes Geräte-Passwort, gemeinsames Altpasswort + Umstellung, fremdes Passwort → Fehler, Idempotenz, Passwort anzeigen (nur per POST, no-store), keine abgeleiteten Passwörter in Logs |
| F3 Reservierung | `test_f3_reservation.py` | Nächste freie IP (Reservierungen + Leases), richtiges Subnetz, manuelle IP, Konflikt, eigener Lease ist kein Konflikt, Idempotenz, Update bestehender Reservierung, Hostname eindeutig/gültig, Bereich erschöpft, falsche Credentials, sofort anwenden (Neustart → neue IP) |
| F4 Firmware | `test_f4_firmware.py` | stable, beta, kein Update, mit Auth, Gerät kommt nicht zurück (Timeout), ungültiger Kanal |
| F4b Update ohne automatischen Neustart | `test_f4b_update_reboot.py` | Aufforderung nach Wartezeit, nie automatischer Neustart, `restart_required`-Hinweis, Neustart per Button (einmalig, auch über die Web-UI), Neustart mit alter Firmware → Fehler, ohne Anwender → Fehler ohne Neustart, normales Update ohne Aufforderung, Vorabversion im Kanal stable |
| Geräteseite | `test_web.py` | Passwort-Button in der Passwortzeile, Passwort ersetzt die Anzeige, „Sofort anwenden“, installierte und verfügbare Firmware je Kanal (nur lesend, Vorabversion markiert), Gerät nicht erreichbar |
| F3b DNS-Name und Beschreibung | `test_f4b_update_reboot.py`, `test_f3_reservation.py` | Produktname als Beschreibung (Plug M Gen 3, Mini 1PM, Plus, Pro, Gen 4, Fallback Modellcode), alte Beschreibung wird aktualisiert, Unbound-Reload nur bei Änderung, fehlendes API-Recht → Hinweis statt Abbruch, Reload abschaltbar |
| F5 Zertifikat | `test_f5_certificate.py`, `test_tls.py`, `test_shelly_client.py` | End-to-End (CN/SAN/IP, EC P-256, server_cert, CA-Ref), Chunk-Upload, FW < 2.0.0, unbekannte CA, Erneuerung löscht altes Zertifikat (auch „Shelly-Manager …“ von vor der Umbenennung), Schlüssel nur lokal, fehlgeschlagene HTTPS-Prüfung, Ablaufdatum im Inventar, echter TLS-Handshake (ok / falscher Host / falsche CA) |
| F6 Gen4/Z2M | `test_f6_zigbee.py`, `test_mqtt_broker.py` | WLAN an → Update → WLAN aus, WLAN aus auch bei Update-Fehler und ohne IP, Fehler wenn Abschalten scheitert, fehlende WLAN-Daten, Folgelauf über MAC; echter Broker (docker) |
| F7 Assistent | `test_f7_provision.py`, `test_web.py` | Komplettablauf inkl. IP-Wechsel, FW-Update vor Zertifikat, optionale Schritte |
| F8 Lokal | `test_f8_local_run.py` | Konfiguration aus Umgebung, Validierung, App ohne HA/MQTT, CLI startet echten Server, `.env.example` vollständig, alte Datenbank `shelly_manager.sqlite3` wird übernommen |
| F9 HA-App | `test_f9_ha_app.py` | options.json hat Vorrang, Add-on-Schema ↔ Settings, Passwortfelder, Übersetzungen, Ingress-`<base href>`, nur Ingress-Proxy zugelassen, MQTT vom Supervisor, Build-Kontext, Docker-Build (docker) |
| F9b App-Repository | `test_f9_ha_app.py::test_publish_creates_app_repository` | `repository.yaml`, vollständiger App-Ordner mit Quellcode, alte Dateien entfernt, `.git` und gepflegte README bleiben |
| F11 Standardeinstellungen und Name | `test_f11_defaults.py`, `test_f3_reservation.py` | Alle Werte nach Firmware gesetzt (Sys, Standort nur wenn Koordinaten gesetzt, WiFi-AP mit Geräte-Passwort, BLE je FW-Version, PLUGS_UI), Neustart bei restart_required, LED-Hinweis bei Geräten ohne PLUGS_UI, Schritt abschaltbar, Name mit Großbuchstaben in Kea/Gerät, Hostname klein, Eindeutigkeit ohne Groß-/Kleinschreibung, NTP verteilen (alle Geräte, Fehler je Gerät, Zigbee übersprungen), Button, Koordinaten validiert, Geräte-Passwort nie im Log, Standardwerte aus den Gerätedetails (nur nach Identitätsprüfung, `test_web.py`) |
| F12 Gerätestatus | `test_f12_status.py`, `test_web.py` | Erreichbarkeit und verfügbare Versionen gespeichert, offline bei Fehler, andere MAC unter der IP erkannt, alle Geräte mit Fehlern je Gerät, Migration alter Datenbanken, LED-Zustand (erreichbar, offline, Update im Standardkanal, Zertifikat fehlt/läuft ab/abgelaufen, Zigbee), Protokoll mit Gerätenamen und gekürzten Ursachenketten, Anzeige in Liste und Detailseite, Protokoll je Gerät, DHCP-Client-ID aus der Kea-Lease im Steckbrief (ohne OPNsense ausgeblendet), Passwort per Auge/Kopieren erst auf Klick |
| F13 Geräte-Passwort setzen und nachfragen | `test_f13_password.py` | Nachfrage bei unbekanntem Passwort (Übernahme, Assistent, Gerätedetails), Umstellung mit eingegebenem Passwort, falsche Eingabe fragt erneut, Master-Passwort ohne Eingabe, bereits korrektes Passwort, Eingabefeld nur bei Passwortfehlern, Formular des Assistenten wird erneut gesendet, eingegebenes Passwort nie im Log, DNS-Link http/https |
| F14 Einstellungen in der Oberfläche | `test_f14_settings.py` | Speichern mit Vorrang vor .env, nur Abweichungen gespeichert, einzeln zurücksetzen, ungültige Werte mit Feldnamen abgelehnt, keine Secrets änderbar, nach Neustart wirksam, Vorauswahl im Assistenten, Schalter für Eco/AP/Bluetooth/LED |
| F10 Protokoll | `test_f10_log_and_security.py`, `test_web.py` | Erfolg/Fehler im Protokoll, Protokollseite |
| F10a Schritt-Logging und Fehlerdiagnose | `test_logging.py` | Verschachtelte Schritte mit Start/OK/FEHLER und Dauer, innerster fehlgeschlagener Schritt, Ursachenkette, Traceback im Log, Schritt + Ursache im Protokoll, aktueller Schritt im laufenden Job, Fehlerschritt in der Job-Anzeige, Host und Aufruf in Verbindungsfehlern, Log-Datei, Secrets auch in Tracebacks geschwärzt |
| N1a Zugangsdaten nur an verifizierte Geräte | `test_f2_adopt.py`, `test_f4_firmware.py`, `test_f5_certificate.py`, `test_security.py` | Lease-MAC ≠ gemeldete MAC, ID ≠ MAC, doppelte ID, nur private IPv4 (vor jeder Anfrage), Gerätenetze, keine Credentials an unbekannte Hosts, Digest-Relay mit fremdem Realm, SetAuth nur für geprüften Realm, Zertifikat nur mit Inventar + Reservierung + passendem Hostnamen, Gerät hinter IP gewechselt |
| N1b Web-Schutz (CSRF/Rebinding) | `test_security.py` | POST ohne HX-Request, fremder Origin/Referer, Sec-Fetch-Site cross-site, fremder Host-Header, Loopback erlaubt, HA-Modus, keine Seiteneffekte bei Ablehnung |
| Code-Review-Nacharbeiten | `test_review_fixes.py` | Migration v0.1 → `shared`, unbekannter auth_mode sendet nichts, eine IP gehört nur einem Gerät, Meldung ohne OPNsense, Gen4 nach Werksreset wieder geschützt, Hostname-Vergleich normalisiert, Passwortumstellung bei verlorener Antwort, Umstellung nur für das Inventar-Gerät, Warnung bei LAN-Bind, Master-Passwort wird nie angezeigt |
| N1 Sicherheit | `test_f10_log_and_security.py`, `test_f8_local_run.py` | Keine Geheimnisse/privaten Schlüssel in Logs, Job-Meldungen, Protokoll, HTML; SecretStr maskiert |
| N2 Idempotenz/Robustheit | F2/F3/F5/F6-Tests | Mehrfachausführung, Wiederherstellung (WLAN aus) |
| N3 Testbarkeit | gesamte Suite | läuft ohne Hardware/Netzwerk |
| N5 Wartbarkeit | `ruff check`, `mypy` (strict) | – |

## Ausführen

```bash
.venv/Scripts/python -m pytest              # Standard (ohne docker/live)
.venv/Scripts/python -m pytest -m docker    # benötigt laufenden Docker-Daemon
.venv/Scripts/python -m pytest -m live      # benötigt .env mit echter OPNsense
.venv/Scripts/ruff check src tests scripts && .venv/Scripts/mypy
```

## Bekannte Grenzen der Simulation
- Die Trust-API-Felder `altnames_dns`/`altnames_ip` werden zeilengetrennt gesendet. Das Format ist aus dem Quellcode
  nicht eindeutig ableitbar → **beim ersten Live-Einsatz prüfen** (SAN im ausgestellten Zertifikat kontrollieren).
- Das Verhalten der Z2M-Exposes (`wifi_config` beim Ausschalten, Antwortformat) ist nach der Z2M-Doku modelliert, nicht an Hardware verifiziert.
- Der Zugriff über den Ingress-Proxy (172.30.32.2) bei `host_network: true` muss in einer echten HA-Installation geprüft werden.
