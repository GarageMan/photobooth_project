# Fotobox

Eine Raspberry-Pi-5-basierte Fotobox für Events. Python/pygame-App mit
sauberer State-Machine-Architektur, Nikon-D3300-Kamera (gphoto2), WS2812-
LED-Ring, beleuchtetem Auslöse-Taster, Touch-Display und Optokoppler-
Kameraauslösung.

Die App läuft im Vollbild auf einem angeschlossenen Touch-Display, führt
Gäste per Countdown durch die Aufnahme, zeigt das Ergebnis zur
Bestätigung/Löschung an, exportiert gespeicherte Fotos für den QR-Code-
Download und zeigt zwischendurch eine "Fliegende Galerie" bereits
aufgenommener Fotos als Blickfang.

> Aktuell im Einsatz für private Veranstaltungen (u. a. eine 150-Jahre-
> Feier und eine Familienfeier). Alle UI-Texte und Code-Kommentare sind
> auf Deutsch.

## Inhaltsverzeichnis

- [Funktionsumfang](#funktionsumfang)
- [Hardware](#hardware)
- [Architektur](#architektur)
- [Projektstruktur](#projektstruktur)
- [Installation auf dem Raspberry Pi](#installation-auf-dem-raspberry-pi)
- [Konfiguration](#konfiguration)
- [Autostart](#autostart)
- [Netzwerk-Setup](#netzwerk-setup)
- [Tests](#tests)
- [Bekannte Einschränkungen & Learnings](#bekannte-einschränkungen--learnings)
- [Entwicklungs-Workflow](#entwicklungs-workflow)
- [Datenschutz](#datenschutz)

## Funktionsumfang

- Geführter Aufnahme-Ablauf: Hauptmenü → Countdown → Aufnahme → Vorschau
  (Speichern/Verwerfen) → QR-Code zum Download
- Live-Vorschau der Kamera vor der Aufnahme (gphoto2 `capture_preview`)
- Farbige LED-Choreografie über einen `LedEffect`-Enum pro `AppState`
  (Sternenhimmel-Idle-Animation, Kometen-Effekte, Countdown-Ring u. a.)
- Beleuchteter Hardware-Auslöse-Taster (synchron zur LED-Ring-Choreografie)
- Galerie mit Grid- und Vollbild-Ansicht, Lösch-Bestätigung mit Timeout
- "Fliegende Galerie" (Attract-Mode): zeigt zufällig bereits aufgenommene
  Fotos an, mit gelegentlich eingestreutem Werbe-Wallpaper
- QR-Code-Anzeige zum Download des eigenen Fotos über das lokale Netzwerk
- DSGVO/GDPR-Hinweis als eigener, scrollbarer Bildschirm (`TERMS`)
- Touch- und Hardware-Button-Bedienung, Swipe-Gesten in der Galerie
- Automatischer Neustart bei Absturz (`start_fotobox.sh`)

## Hardware

| Komponente | Details |
|---|---|
| Rechner | Raspberry Pi 5 |
| Kamera | Nikon D3300, Ansteuerung über `gphoto2`/USB |
| LED-Ring | WS2812, 35 LEDs, SPI0 (`neopixel_spi`) |
| Display | Kapazitives Touch-Display (Goodix), DSI-2, physisch 90° gedreht |
| Auslöse-Taster | Beleuchtet, Ansteuerung über Transistor |
| Kamera-Auslösung | Optokoppler (PC817) an Nikon MC-DC2-Buchse |

### GPIO-Pin-Tabelle (autoritativ)

| Pin | GPIO | Funktion |
|---|---|---|
| 10 | GPIO15 | Taster-Signal (Eingang, Pull-up) |
| 14 | GND | Taster-Rückleitung |
| 11 | GPIO17 | Auslöser/Optokoppler (aktiv LOW) |
| 9 | GND | Optokoppler-Rückleitung |
| 19 | GPIO10 | LED-Ring-Daten (SPI0 MOSI, `neopixel_spi`) |
| 20 | GND | LED-Ring |
| 36 | GPIO16 | Taster-LED (Transistor-Basis, 1 kΩ) |
| 34 | GND | Transistor-Emitter |
| 4 | 5V | Taster-LED-Anode |
| 2 | 5V | Display |
| 6 | GND | Display |

> **Wichtig:** Der LED-Ring läuft über SPI0/GPIO10 (Pin 19,
> `neopixel_spi`) — **nicht** über GPIO12/Pin 32 (PWM/`rpi_ws281x`). Das
> war eine frühere Planung und ist obsolet.

### Wichtige Kamera-Einstellungen (Nikon D3300)

- Modus: M (Manuell)
- Fokus: MF (manuell, einmalig eingestellt)
- LiveView: EIN
- Auto-Off/Energiesparen: AUS
- Bildformat: JPEG (Fine)

Die Kamera deaktiviert HDMI-LiveView, sobald USB angeschlossen ist
(Firmware-Verhalten, nicht softwareseitig umgehbar). Die Live-Vorschau
läuft deshalb ausschließlich über `gphoto2.capture_preview()` per USB
(~640×424 JPEG-Frames, ~8 fps). Download und Live-Vorschau teilen sich
ein `threading.Lock`, da `gphoto2` nur eine aktive Kamera-Verbindung
gleichzeitig erlaubt.

## Architektur

Unidirektionaler Datenfluss nach dem Muster
`AppModel` → `StateMachine` → `TransitionResult`:

```
Event (Touch/Taster/Timer)
        │
        ▼
   StateMachine.transition(model, event, now)
        │
        ├── neues AppModel (state, session, ui, timers)
        └── Actions (Strings, z.B. "delete_photo", "export_photo")
        │
        ▼
   app_with_hw.py: Actions ausführen, Hardware ansteuern
        │
        ▼
   renderer.py: aktuellen State auf das Display zeichnen
```

Layout, Rendering, Event-Handling und Hardware-Abstraktion sind bewusst
in getrennte Module aufgeteilt (siehe [Projektstruktur](#projektstruktur)).
Die eigentliche Ablauflogik (`state_machine.py`) kennt keine Hardware-
Details — Hardware-Zugriffe stecken ausschließlich in den `hw_*`-Modulen.

### Zustände (`AppState`)

`BOOT → MAIN_MENU → ATTRACT_GALLERY / GALLERY_GRID / GALLERY_FULLSCREEN /
PHOTO_INTRO → PHOTO_PREVIEW → COUNTDOWN → CAPTURE_PENDING → REVIEW →
DELETE_CONFIRM → QR_DISPLAY`, außerdem `INSTRUCTIONS`, `TERMS`,
`ERROR_SCREEN`, `MAINTENANCE`.

## Projektstruktur

| Datei | Zweck |
|---|---|
| `app_with_hw.py` | Einstiegspunkt, Event-Loop, Hardware-Wiring, Action-Ausführung |
| `state_machine.py` | Zustandsübergänge, reine Logik ohne Hardware-Bezug |
| `states.py` / `events.py` | `AppState`- und `EventType`-Enums |
| `models.py` | `AppModel`, `TimerState`, `SessionState`, `UiState` |
| `renderer.py` | Zeichnet den aktuellen State auf das Display (pygame) |
| `layout.py` | Layout-Konstanten/Bounding-Boxes für Buttons & Elemente |
| `config.py` | Zentrale Konfiguration (`AppConfig` und Unter-Configs) |
| `led_service.py` / `hw_led_provider.py` | LED-Choreografie (Enum-Pipeline) und SPI-Ansteuerung |
| `hw_button_provider.py` | GPIO-Taster inkl. Taster-LED-Sync |
| `hw_capture_provider.py` | Kameraauslösung (GPIO/Optokoppler) + gphoto2-Download |
| `hw_gphoto2_preview_provider.py` | Live-Vorschau per gphoto2 |
| `hw_grabber_provider.py` | (nicht mehr verwendet — HDMI-Grabber-Ansatz wurde verworfen) |
| `camera_capture.py` / `camera_preview.py` | Provider-Protokolle/Wrapper |
| `fake_capture_service.py` / `fake_preview_service.py` | Fixture-basierte Provider für Entwicklung ohne Hardware |
| `gallery_service.py` | Foto-Listing, Thumbnail-/Vollbild-Cache, Löschen |
| `storage_service.py` | Export ins Web-Verzeichnis, Datei-Operationen |
| `qr_service.py` | QR-Code-Erzeugung für den Foto-Download |
| `button_service.py` | Hilfslogik für Taster-Events |
| `start_fotobox.sh` | Autostart-Skript mit Neustart-Schleife |
| `test_*.py` | Unit-Tests (pytest) |

## Installation auf dem Raspberry Pi

```bash
# Systempakete
sudo apt update
sudo apt install python3-gphoto2 libgphoto2-6 -y

# Python-Abhängigkeiten (mit sudo installieren, da app_with_hw.py mit
# sudo läuft und sonst KEINE user-lokalen ~/.local-Pakete sieht!)
sudo pip3 install gphoto2 neopixel_spi qrcode pillow pygame --break-system-packages
```

Bekannter Konflikt: `gvfs-gphoto2-volume-monitor` blockiert den
`gphoto2`-Zugriff. Wird von `hw_capture_provider.py` beim Start
automatisch beendet (`kill_gvfs()`); alternativ per systemd-Unit dauerhaft
deaktivieren.

### Berechtigungen

Nur `app_with_hw.py`, `hw_led_provider.py`, `hw_button_provider.py` und
`hw_capture_provider.py` benötigen zur Laufzeit `sudo` (GPIO-/SPI-
Hardwarezugriff). Reine Logik-Module werden nie direkt ausgeführt.

`nginx` muss als `user photobox;` laufen (nicht `www-data`), sonst gibt
es 403-Fehler auf Dateien unter `/home/photobox/`.

## Konfiguration

Zentrale Einstellungen in `config.py` (`AppConfig` und Unter-Configs
`ScreenConfig`, `TimeoutConfig`, `FeatureFlags`, `GpioConfig`,
`NetworkConfig`, `GalleryConfig`). Wichtige Felder:

- `photo_prefix` — Präfix für Dateinamen gespeicherter Fotos, Schema
  `{photo_prefix}{JJJJMMTTHHMMSS}.jpg`
- `features.use_fake_capture` / `use_fake_preview` — auf `True` setzen,
  um ohne angeschlossene Kamera zu entwickeln (nutzt Fixtures aus
  `assets/`)
- `timeouts.*` — sämtliche Timeout-/Countdown-Zeiten
- `gpio.*` — Pin-Belegung (siehe [GPIO-Tabelle](#gpio-pin-tabelle-autoritativ))
- `network.*` — statische IP, Foto-URL-Präfix, WLAN-Zugangsdaten
  (Zugangsdaten hier bewusst nicht in dieser README dokumentiert)

## Autostart

- `raspi-config` → Desktop-Autologin aktivieren
- `~/.config/autostart/fotobox.desktop` startet `start_fotobox.sh`
- `start_fotobox.sh` startet `app_with_hw.py` in einer Endlosschleife mit
  automatischem Neustart bei Absturz, Logs unter
  `~/photobooth/data/logs/fotobox.log`
- Passwortloses `sudo` ist strikt auf
  `python3 /home/photobox/photobooth/app_with_hw.py` beschränkt

## Netzwerk-Setup

Zwei getrennte Netze:

- **Heimnetz** (Fritz!Box, `192.168.178.0/24`)
- **Fotobox-Netz** (TP-Link WR802N im WISP-Modus, `192.168.0.0/24`), Pi
  darin statisch erreichbar

Eine statische Route auf der Fritz!Box leitet `192.168.0.0/24` über die
TP-Link-WAN-IP. `ufw`-Regeln: Port 80 aus beiden Subnetzen offen,
SSH/VNC nur aus dem Heimnetz.

## Tests

```bash
python3 -m pytest test_state_machine.py test_gallery_service.py \
    test_storage_service.py test_button_service.py
```

Vor jeder Auslieferung/jedem Deployment zusätzlich ein reiner
Syntax-Check aller geänderten Dateien:

```bash
python3 -m py_compile <geänderte_dateien.py>
```

## Bekannte Einschränkungen & Learnings

- **Kamera/USB:** siehe [Hardware](#hardware) — LiveView nur per USB/
  gphoto2 möglich, kein HDMI parallel zu USB.
- **Enum-getriebene Pipelines konsequent pflegen:** Beim Erweitern eines
  Enums (z. B. `AppState`, `LedEffect`) alle Dateien mit hartcodierten
  Wert-Tupeln prüfen (`grep -n "value in ("` über alle `.py`-Dateien).
- **Encoding-Disziplin:** Keine Unicode-Sonderzeichen (z. B.
  Box-Drawing-Zeichen `─`) in Kommentaren/Docstrings oder `print()`-
  Ausgaben — SSH-Terminals können Latin-1-beschränkt sein. Nur ASCII.
- **SFTP-Transfer:** Kann unsichtbare Whitespace-Fehler einschleusen, die
  zu stillen `IndentationError`s führen — im Zweifel mit
  `sed -n 'X,Yp' datei.py | cat -A` prüfen.
- **Touchscreen-Rotation:** Funktioniert zuverlässig nur über eine
  udev-Regel (`/etc/udev/rules.d/99-touch-rotate.rules`,
  `LIBINPUT_CALIBRATION_MATRIX`). Der `labwc-rc.xml`-Ansatz über
  `<calibrationMatrix>` funktioniert **nicht**.
- **`sudo`-Pakete:** Mit `sudo pip3 install ... --break-system-packages`
  installieren, da `sudo python3` keine user-lokalen (`~/.local`)-Pakete
  sieht.

## Entwicklungs-Workflow

Entwicklung findet direkt auf dem Pi statt: Dateien werden per SFTP
bearbeitet, danach wird neu gestartet/getestet. Für KI-gestützte
Weiterentwicklung (z. B. mit Claude) gilt:

1. Änderungen auf dem Pi vornehmen und testen.
2. Bei Zufriedenheit: committen und ins Repository pushen.
3. Repository-Anbindung im jeweiligen Tool synchronisieren, damit immer
   der aktuelle, released Stand als Grundlage für weitere Anpassungen
   dient.

## Datenschutz

Ein DSGVO/GDPR-konformer Hinweistext für den privaten Veranstaltungs-
kontext liegt als eigener Bildschirm in der App (`TERMS`-State) sowie
als Dokument im Repository vor
(`Nutzungsbedingungen_zur_Fotobox.docx`).
