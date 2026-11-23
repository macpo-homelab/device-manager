# Security-Review v0.1 (2026-11-06)

Geprüft wurde Commit `2c98064`. Es gab zwei bestätigte Schwachstellen. Beide sind behoben, Regressionstests liegen in `tests/test_security.py`, `tests/test_f2_adopt.py` und `tests/test_f5_certificate.py`.

## 1. Zugangsdaten an nicht verifizierte Hosts (hoch)

**Problem:** Beim Übernehmen wurden `id` und `auth_en` aus dem unauthentifizierten `/shelly` eines beliebigen Hosts übernommen. Danach ging an diesen Host entweder ein `Shelly.SetAuth` mit `ha1 = sha256(admin:<id>:<Passwort>)` oder eine Antwort auf seine Digest-Challenge (Relay möglich).

- Weil `realm` = Geräte-ID ist, wirkt ein solches `ha1` wie ein Passwort für ein Gerät, das sich der Angreifer aussuchen kann.
- Einstieg ins System: eine gefälschte mDNS-Ankündigung oder ein DHCP-Hostname `shelly…`, jeweils in der Gerätesuche angezeigt.

**Behebung:**
- `services/identity.py`:
  - `check_target_ip`: Das Ziel muss eine private IPv4-Adresse sein, optional eingeschränkt auf `device_networks`. Die Prüfung läuft vor der ersten Anfrage.
  - `verify_identity`:
    - Die Geräte-ID muss auf die MAC enden.
    - Die ID darf nicht schon einem anderen Inventar-Gerät gehören.
    - Die MAC muss zur Kea-Lease bzw. -Reservierung der IP passen.
- Zugangsdaten gehen erst nach dieser Prüfung an das Gerät.
- `shelly/client.py: RealmCheckedDigestAuth` beantwortet nur Challenges mit dem erwarteten Realm. `set_auth` arbeitet nur für den geprüften Realm.
- `Context.shelly()` sendet Zugangsdaten nur an Geräte aus dem Inventar. Unbekannte Hosts bekommen nichts.
- **Passwort pro Gerät** (Nutzerentscheidung): `derive_password(master, mac) = base64url(HMAC-SHA256)[:22]`.
  - Ein abgeflossener Hash betrifft dadurch nur noch ein Gerät.
  - Geräte, die noch das gemeinsame Passwort haben, werden als `auth_mode = shared` geführt und lassen sich in der UI umstellen.

## 2. CSRF / DNS-Rebinding im lokalen Betrieb (hoch)

**Problem:** Lokal gab es weder Authentifizierung noch Origin- oder Host-Prüfung. Eine fremde Webseite konnte Formular-POSTs an `127.0.0.1:8099` schicken.

- Damit ließ sich ein Fake-Gerät übernehmen.
- Anschließend konnte die Seite für einen beliebigen internen Hostnamen ein Zertifikat ausstellen lassen. Zertifikat und privater Schlüssel wären dabei an den Fake-Host gegangen.

**Behebung:**
- `web/app.py: request_rejection`:
  - Host-Allowlist (`allowed_hosts`, lokal standardmäßig nur Loopback).
  - POSTs nur mit `HX-Request: true`.
  - `Sec-Fetch-Site` darf nicht cross-site sein.
  - Im lokalen Betrieb muss `Origin`/`Referer` zum eigenen Host passen.
- In HA bleibt die Prüfung über Ingress-IP und HA-Session bestehen, `HX-Request` ist auch dort Pflicht.
- `services/certificate.py`: Zertifikate gibt es nur noch, wenn alles zusammenpasst:
  - Das Gerät steht im Inventar.
  - Es meldet dieselbe ID und MAC wie im Inventar.
  - Es gibt eine Kea-Reservierung auf genau diese IP.
  - Der Hostname entspricht der Reservierung.
- Die Schlüssel werden erst übertragen, wenn alle diese Prüfungen bestanden sind.

## Nacharbeiten aus dem Code-Review (2026-11-06)
- Migration: Bestandsgeräte aus v0.1 werden als `shared` geführt. Bei unbekanntem `auth_mode` werden keine Zugangsdaten gesendet.
- Routen und Abläufe reichen das Inventar-Gerät durch, statt es über eine eventuell veraltete IP zu suchen. Eine IP gehört immer nur einem Gerät.
- Gen4: Passwortstatus wird bei jedem Lauf gegen das Gerät geprüft (nach einem Werksreset wird das Passwort neu gesetzt).
- Das Master-Passwort wird in der UI nie angezeigt.
- Passwortumstellung: Geht die SetAuth-Antwort verloren, wird anschließend das neue Passwort geprüft.
- Gemeinsamer Helfer `verify_inventory_device` (für Zertifikat und Umstellung), `lease_mac` gegen doppelten Code.

## Bekannte Restrisiken
- Die Kommunikation mit Shellys läuft über Klartext-HTTP, so ist die Herstellerschnittstelle gebaut. Wer im selben Netz mitlauscht, sieht das `ha1` beim Setzen des Passworts. Durch das Passwort pro Gerät ist der Schaden auf ein Gerät begrenzt.
- Wer im LAN die MAC und die IP eines echten Geräts vollständig fälscht (ARP/DHCP-Spoofing), kann die Identitätsprüfung umgehen.
