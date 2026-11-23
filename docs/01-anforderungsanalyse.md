# Device Manager – Anforderungsanalyse

Status: **v1.0** (2026-11-06). Offene Punkte sind geklärt (Abschnitt 5.1), alle Anforderungen sind in v0.1 umgesetzt (siehe Testkonzept).

## 1. Ziel

Ein Werkzeug zur Verwaltung von Shelly-Geräten im eigenen Netz, das wiederkehrende
Administrationsaufgaben (Einbindung, IP-Reservierung, Firmware, Zertifikate) automatisiert.
Es läuft während der Entwicklung eigenständig lokal und später als App (Add-on) in Home Assistant.

## 2. Funktionale Anforderungen

| ID  | Anforderung | Beschreibung / Akzeptanzkriterien |
|-----|-------------|-----------------------------------|
| F1  | Geräte-Inventar | Bekannte Shellys auflisten (Name, Modell, Generation, MAC, IP, Firmware, Update verfügbar, Zertifikatsstatus, Verbindungsart WLAN/Zigbee). Quellen: mDNS, OPNsense-Leases, Z2M, manuelle Eingabe. Generation wird über `GET /shelly` erkannt. |
| F2  | Neue Geräte einbinden | Werkseitiges Gerät ins WLAN bringen (SSID/Passwort setzen, optional Gerätename, Cloud/BLE-Einstellungen). **Weg offen (O1).** |
| F3  | IP-Reservierung in OPNsense | Für eine MAC eine statische DHCP-Reservierung (+ Hostname) anlegen, idempotent (kein Duplikat, Konflikterkennung bei belegter IP), DHCP-Dienst neu konfigurieren. **DHCP-Dienst offen (O2).** |
| F3b | DNS-Name und Beschreibung | Beschreibung der Reservierung = Produktname mit Generation (z. B. „Shelly Plug M Gen 3“). Nach jeder geänderten Reservierung Unbound neu laden (`opnsense_reload_unbound`), damit der Hostname sofort auflösbar ist. Ein Fehler beim Neuladen bricht die Reservierung nicht ab, sondern erzeugt einen Hinweis. |
| F4  | Firmware aktualisieren | Update prüfen (`Shelly.CheckForUpdate`), auslösen (`Shelly.Update`, Kanal stable/beta), auf Neustart warten, neue Version verifizieren. Einzeln und als Sammelaktion. |
| F4b | Update ohne automatischen Neustart | Während des Updates Version, Uptime und `restart_required` abfragen (`firmware_poll_interval`, 10 s). Neustart mit alter Firmware → Fehler. Startet das Gerät nach `firmware_reboot_wait` (5 min) nicht neu, den Anwender per Button im Job um den Neustart bitten. Der Manager startet **nie selbst** neu (kein Abbruch mitten im Update). Vorabversionen im Kanal stable werden mit Hinweis installiert. |
| F5  | Zertifikat erzeugen & installieren | In OPNsense ein Zertifikat von einer internen CA ausstellen (CN = Hostname/FQDN, SAN = DNS + IP, EC P-256), PEM holen, per `Shelly.PutHTTPServerCert/-Key` hochladen, neu starten, HTTPS-Handshake gegen CA prüfen. Voraussetzung: Firmware ≥ 2.0.0. **Zweck offen (O3).** |
| F6  | Gen4 über Zigbee2MQTT aktualisieren | Bei Gen4-Geräten im Zigbee-Modus: per Z2M `wifi_config.enabled=true` (+SSID/PW) WLAN einschalten → auf `ip_address` warten → F4 ausführen → `wifi_config.enabled=false`. Zustand bei Fehler wiederherstellen (WLAN immer wieder aus). |
| F7  | Workflow „Neues Gerät komplett“ | F2 → F3 → (Neustart/DHCP-Renew) → F4 → F5 als Assistent mit Fortschrittsanzeige und Wiederaufnahme nach Fehlern. |
| F8  | Lokaler Betrieb | Start ohne Home Assistant (Konfiguration über Datei/Umgebungsvariablen, z. B. `uv run` oder Docker Compose). |
| F9  | Home-Assistant-App | Paketierung als HA-App: `config.yaml`, Dockerfile auf HA-Basis-Image, Ingress-Web-UI, Optionen über die HA-Oberfläche, Daten unter `/data`, MQTT-Zugang über den Supervisor-Service. |
| F9b | App-Repository | Verteilung über ein eigenes HA-App-Repository (GitHub `macpo-homelab/ha-apps`, `repository.yaml`, mehrere Apps möglich), das HA selbst baut; Updates samt Changelog erscheinen in HA. Erzeugt mit `scripts/publish_addon.py`. |
| F11 | Standardeinstellungen und Name | Im Assistenten nach der Firmware: Gerätename = Name, Zeitzone, Standort, NTP-Server, Eco-Modus, AP mit Geräte-Passwort absichern und deaktivieren, Bluetooth(-RPC) deaktivieren, LED-Anzeige aus (Plug M Gen3). Der **Name** erlaubt Großbuchstaben, wird gespeichert und für Kea-Reservierung und Gerätename verwendet; der DNS-Hostname ist der Name in Kleinbuchstaben. NTP-Server per Einstellung, per Button auf alle Geräte verteilbar. Datumsformat über `ui_data.df` (nicht dokumentiert, am Gerät ausgelesen; übrige `ui_data` bleiben erhalten). |
| F12 | Gerätestatus | „Status abfragen“ (alle Geräte bzw. ein Gerät) prüft Erreichbarkeit, liest die Geräteinfo und die verfügbaren Firmware-Versionen (stable/beta) und speichert sie mit Zeitpunkt im Inventar. Nicht erreichbare Geräte werden als offline gespeichert, eine andere MAC unter der IP wird erkannt. Die Geräteliste zeigt den Zustand als LED (grün ok, bernstein Update verfügbar bzw. Zertifikat fehlt oder läuft in < 30 Tagen ab, rot nicht erreichbar bzw. Zertifikat abgelaufen, grau nicht abgefragt). |
| F10 | Protokoll | Jede Aktion pro Gerät mit Zeitpunkt, Ergebnis und Fehlertext nachvollziehbar. |
| F10a | Schritt-Logging und Fehlerdiagnose | Jeder Arbeitsschritt wird mit Start, Ende und Dauer geloggt; die Job-Anzeige zeigt den laufenden Schritt. Bei einem Fehler nennen Log, Job-Anzeige und Protokoll den fehlgeschlagenen Schritt, Fehlertyp, Meldung mit betroffenem Host/Aufruf und die Ursachenkette; das Log enthält zusätzlich den Traceback. Log-Level einstellbar (`log_level`), Log-Datei unter `<data_dir>/logs/`. Secrets werden geschwärzt. |

## 3. Nicht-funktionale Anforderungen

| ID  | Anforderung |
|-----|-------------|
| N1a | **Zugangsdaten nur an verifizierte Geräte:** private IP im Gerätenetz, Geräte-ID passt zur MAC, MAC passt zur Kea-Lease bzw. -Reservierung; Digest nur für den erwarteten Realm. Zertifikate nur für reservierte IP mit passendem Hostnamen. |
| N1b | **Web-Schutz:** kein CSRF und kein DNS-Rebinding (Host-Allowlist, Pflicht-Header `HX-Request`, Origin-Prüfung). |
| N1  | **Sicherheit:** OPNsense-API-Key/Secret, WLAN-Passwort und Shelly-Passwörter nie im Log und nie im Repo; TLS-Prüfung gegen OPNsense mit konfigurierbarer CA. Private Schlüssel nur so kurz wie nötig im Speicher, nicht persistiert. |
| N2  | **Idempotenz & Robustheit:** Jede Aktion kann mehrfach ausgeführt werden; Timeouts und Retries bei Neustarts; definierter Endzustand bei Abbruch (insb. F6). |
| N3  | **Testbarkeit ohne Hardware:** Alle externen Systeme (Shelly, OPNsense, MQTT/Z2M) über Schnittstellen gekapselt und durch Simulatoren ersetzbar. |
| N4  | **Automatisierte Tests:** Jede Anforderung F1–F10 hat mindestens einen Akzeptanztest (Traceability-Matrix in `docs/03-testkonzept.md`). |
| N5  | **Wartbarkeit:** Typisierung, Linter, reproduzierbare Abhängigkeiten. |
| N6  | **Bedienbarkeit:** Web-UI auf Deutsch, auch innerhalb des HA-Ingress-Frames nutzbar. |

## 4. Priorisierung

Kriterien: Nutzen × Abhängigkeiten × Risiko. Riskante, unklare Punkte werden nach hinten
geschoben, Fundament zuerst.

| Phase | Inhalt | Prio (MoSCoW) | Begründung |
|-------|--------|---------------|------------|
| 1 | Fundament: Projektstruktur, Konfiguration, Shelly-RPC-Client, F1 Inventar, F8 lokaler Start, Test-Infrastruktur inkl. Shelly-Simulator | Must | Basis für alles |
| 2 | F3 OPNsense-Reservierung | Must | geringe Komplexität, hoher Nutzen |
| 3 | F4 Firmware-Update | Must | Kernfunktion, Basis für F6 |
| 4 | F6 Gen4/Z2M-WLAN-Workflow | Must | baut auf F4 auf |
| 5 | F5 Zertifikate | Should | Trust-API-Felder müssen am echten System verifiziert werden |
| 6 | F2 Onboarding + F7 Assistent | Must | höchstes technisches Risiko (Weg ins Geräte-AP) |
| 7 | F9 HA-App-Paketierung | Must | erst wenn Funktionen stabil |
| – | F10 Protokoll | Should | läuft in jeder Phase mit |

## 5. Offene Punkte

| ID | Frage | Auswirkung |
|----|-------|------------|
| O1 | Wie erreicht die App ein werkseitiges Gerät? (eigener AP 192.168.33.1 über WLAN-Adapter / BLE / Gerät wird per Shelly-App eingebunden, App übernimmt) | Architektur von F2, Machbarkeit als HA-App |
| O2 | Welcher DHCP-Dienst in OPNsense (Kea / Dnsmasq / ISC legacy), welche OPNsense-Version? | API von F3 |
| O3 | Wofür das Zertifikat: HTTPS der Web-UI, MQTT-Client-Zertifikat (mTLS) oder beides? Welche CA in OPNsense? | F5 |
| O4 | Welche Shelly-Generationen sind im Bestand (Gen1 hat eine andere API und kein HTTPS)? | Umfang F1/F4/F5 |
| O5 | Z2M-Zugriff direkt per MQTT oder über die HA-API? | F6, Konfiguration |
| O6 | Sind die Shellys passwortgeschützt (Digest-Auth)? Soll die App Passwörter setzen? | Shelly-Client, N1 |
| O7 | Welcher Firmware-Kanal: nur stable oder auch beta? | F4 |
| O8 | Namens-/Hostname-Schema und DNS-Domain für Reservierung/Zertifikat? | F3, F5 |

### 5.1 Entscheidungen (geklärt am 2026-11-06)

| ID | Entscheidung |
|----|--------------|
| O1 | Werkseitige Geräte werden per **Shelly-App/Taster** ins WLAN gebracht. Der Manager **übernimmt** sie (Discovery per mDNS oder IP, setzt das gemeinsame Passwort). Ein AP- oder BLE-Weg ist nicht vorgesehen. → F2 = „Gerät übernehmen“. |
| O2 | **Kea DHCP**. |
| O3 | Zertifikat für **HTTPS der Web-UI** (`Shelly.PutHTTPServerCert/-Key`). |
| O4 | Unterstützt: **Gen2, Gen3, Gen4** (nur JSON-RPC). Gen1 nicht. |
| O5 | Z2M **direkt per MQTT**. |
| O6 | ~~Ein gemeinsames Passwort~~ → **geändert (Security-Review 2026-11-06): Master-Passwort, daraus pro Gerät ein eigenes Passwort** (HMAC mit MAC, Benutzer `admin`). Geräte mit dem bisherigen gemeinsamen Passwort werden akzeptiert und können umgestellt werden. |
| O7 | Firmware: Standard **stable**, **beta** pro Aktion wählbar. |
| O8 | **Hostname wird immer manuell eingegeben.** Die Domain ist konfigurierbar (CN/SAN = FQDN). **IP = nächste freie aus einem konfigurierten Bereich**, im UI überschreibbar. |
| –  | In OPNsense gibt es bereits eine **interne CA**. Sie wird in der Konfiguration über ihren Namen (`descr`) ausgewählt. |

Damit ändert sich F2: Statt „ins WLAN bringen“ heißt es jetzt „Gerät übernehmen“ (erkennen, Passwort setzen, ins Inventar aufnehmen).

## 6. Erkenntnisse aus der Recherche

- **Shelly HTTPS:** ab Firmware 2.0.0 über `Shelly.PutHTTPServerCert`, `Shelly.PutHTTPServerKey`
  (optional `Shelly.PutHTTPServerCABundle` für mTLS); Parameter `data` (+ `append` zum Stückeln);
  Neustart nötig; EC P-256 empfohlen; HTTP:80 bleibt aktiv. Ein fehlerhaftes Paar wird beim Booten stillschweigend
  ignoriert → nach der Installation immer per TLS-Handshake prüfen.
  [Doku](https://shelly-api-docs.shelly.cloud/gen2/General/CustomHTTPSCertificates/)
- **Z2M Gen4:** Exposes `wifi_config` (enabled, ssid, password, static_ip, net_mask, gateway,
  name_server; les- und schreibbar), `wifi_status`, `ip_address`, `dhcp_enabled`. Keine MAC über Zigbee →
  MAC nach WLAN-Aktivierung per RPC lesen. Firmware-Updates nur über WLAN.
  [S4SW-001X16EU](https://www.zigbee2mqtt.io/devices/S4SW-001X16EU.html)
- **OPNsense:** Kea `/api/kea/dhcpv4/{add,search,set,del}_reservation` + `/api/kea/service/reconfigure`;
  Dnsmasq `/api/dnsmasq/settings/{add,search,set,del}_host` + `/api/dnsmasq/service/reconfigure`;
  Trust `/api/trust/cert/add`, `/api/trust/cert/generate_file/{uuid}/{type}`, `/api/trust/ca/ca_list`.
  Feldnamen der Trust-API sind nicht dokumentiert → am echten System bzw. im Model `Cert.xml` verifizieren.
