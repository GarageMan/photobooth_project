from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Echte Zugangsdaten liegen NICHT im Code, sondern in local_secrets.py
# (nicht versioniert, siehe .gitignore und local_secrets_example.py).
# Jeder Wert wird EINZELN mit getattr geladen und faellt fuer sich auf
# einen Standard zurueck. Wichtig: ein fehlender neuer Wert (z.B. in einer
# aelteren local_secrets.py) darf NICHT die bereits vorhandenen Werte mit
# in den Fallback reissen - genau das wuerde ein "from local_secrets import
# A, B" tun, sobald B fehlt.
try:
    import local_secrets as _secrets
except ImportError:
    _secrets = None
    print("[Config] WARNUNG: local_secrets.py fehlt - siehe local_secrets_example.py")

_PLACEHOLDER = "BITTE_local_secrets.py_ANLEGEN"

# GEAENDERT (Sprint 11): SHUTDOWN_PIN -> SERVICE_MENU_PIN (und ebenso fuer
# die drei Geste-Parameter unten) - die PIN schuetzt den Zugang zum
# GESAMTEN Service-Menue (Status/Diagnose, USB-Export, Bilder loeschen,
# Kamera-Einstellungen, Herunterfahren, ...), nicht nur das Herunterfahren,
# das urspruenglich der einzige Menuepunkt dahinter war (siehe
# admin_service.py, vormals shutdown_service.py). Liest bevorzugt den
# neuen Namen aus local_secrets.py, faellt aber - fuer bereits im Einsatz
# befindliche local_secrets.py-Dateien auf dem Pi, die noch nicht manuell
# umbenannt wurden - auf den alten Namen zurueck, bevor der eingebaute
# Standard greift. Dieser Fallback kann entfernt werden, sobald
# local_secrets.py ueberall auf die neuen Namen umgestellt ist.
def _secret(new_name: str, old_name: str, default):
    return getattr(_secrets, new_name, getattr(_secrets, old_name, default))


SERVICE_MENU_PIN = _secret("SERVICE_MENU_PIN", "SHUTDOWN_PIN", _PLACEHOLDER)

# Parameter der Geheim-Geste - ebenfalls aus local_secrets.py, damit weder
# Muster noch Position im Repo stehen. Sinnvolle Standards, falls nicht
# gesetzt (die Geste funktioniert dann trotzdem, nur eben mit den hier
# hinterlegten Default-Werten).
SERVICE_MENU_GESTURE_ZONE = _secret("SERVICE_MENU_GESTURE_ZONE", "SHUTDOWN_GESTURE_ZONE", "rechts")
SERVICE_MENU_GESTURE_PATTERN = _secret(
    "SERVICE_MENU_GESTURE_PATTERN", "SHUTDOWN_GESTURE_PATTERN",
    ("kurz", "kurz", "kurz", "lang", "kurz", "kurz"),
)
SERVICE_MENU_LONG_PRESS_SECONDS = _secret(
    "SERVICE_MENU_LONG_PRESS_SECONDS", "SHUTDOWN_LONG_PRESS_SECONDS", 0.6,
)


# Vier waehlbare, unsichtbare Zonen fuer die Geste - jeweils als Bruchteil
# der Bildschirmflaeche (x, y, Breite, Hoehe). Alle vier sind so gelegt,
# dass sie KEINEN der vier diagonalen Hauptmenue-Buttons ueberlappen (sonst
# wuerde ein Tipp doppelt interpretiert). Buttons liegen in x[0.06..0.92],
# y[0.53..0.885]; die Zonen weichen dem aus.
_GESTURE_ZONE_FRACTIONS = {
    "oben":   (0.40, 0.00, 0.20, 0.12),  # oberer Rand, mittig
    "unten":  (0.40, 0.88, 0.20, 0.12),  # unterer Rand, mittig
    "links":  (0.00, 0.15, 0.12, 0.16),  # linker Rand, oben
    "rechts": (0.88, 0.15, 0.12, 0.16),  # rechter Rand, oben
}


def _resolve_gesture_zone(name: str) -> tuple[float, float, float, float]:
    key = str(name).strip().lower()
    if key not in _GESTURE_ZONE_FRACTIONS:
        print(f"[Config] WARNUNG: unbekannte Shutdown-Geste-Zone '{name}' - nutze 'rechts'")
        key = "rechts"
    return _GESTURE_ZONE_FRACTIONS[key]


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PHOTO_DIR = DATA_DIR / "photos"
WEB_DIR = DATA_DIR / "web"
CACHE_DIR = DATA_DIR / "cache"
LOG_DIR = DATA_DIR / "logs"
ASSETS_DIR = BASE_DIR / "assets"


# --- Etappe 8: konfigurierbare Event-Parameter -----------------------------
# Titel, Foto-Praefix und Gaeste-WLAN-Passwort aendern sich JEDE Veranstaltung
# - anders als PIN/Geheim-Geste (local_secrets.py) sind das keine Geheimnisse,
# sondern reine Event-Parameter. Damit dafuer nicht bei jeder Party Code
# angefasst werden muss, liegen sie in einer eigenen JSON-Datei.
#
# BEWUSST im Hauptverzeichnis (wie local_secrets.py), NICHT unter data/:
# data/ ist fuer Laufzeitdaten reserviert, die die App selbst erzeugt/
# aktualisiert (Fotos, Logs, shutdown_lockout.json). event_config.json wird
# dagegen wie local_secrets.py von Hand vor jedem Event angepasst - beide
# "Setup-Dateien" liegen daher konsistent am selben Ort (nicht versioniert,
# siehe .gitignore und event_config_example.json).
_EVENT_CONFIG_PATH = BASE_DIR / "event_config.json"


def load_event_config(path: Path) -> dict:
    """Laedt die Event-Konfiguration aus path.

    Gibt bei fehlender, nicht lesbarer oder inhaltlich falscher Datei ein
    LEERES dict zurueck (nie eine Exception) - die Aufrufer entscheiden
    danach jeweils EINZELN ueber ihren Fallback-Wert, genau wie beim
    local_secrets-Muster oben. Eigenstaendige, parametrisierte Funktion
    (statt Modul-Code, der beim Import einmalig laeuft) - dadurch ohne
    echte Datei und ohne Neu-Import des Moduls offline testbar
    (siehe test_config.py).
    """
    if not path.exists():
        print(f"[Config] WARNUNG: {path.name} fehlt - siehe event_config_example.json. Nutze Standardwerte.")
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[Config] WARNUNG: {path.name} konnte nicht gelesen werden ({exc}) - nutze Standardwerte.")
        return {}
    if not isinstance(data, dict):
        print(f"[Config] WARNUNG: {path.name} hat kein JSON-Objekt als Wurzel - nutze Standardwerte.")
        return {}
    return data


_event_config = load_event_config(_EVENT_CONFIG_PATH)

# "Fotobox" als generischer Fallback - die App hat keine Versionsbezeichnung
# und keinen Namen, solange kein Event-Titel konfiguriert ist.
EVENT_TITLE = str(_event_config.get("event_title") or "Fotobox")
PHOTO_PREFIX = str(_event_config.get("photo_prefix") or "foto_")

# NEU (Etappe 8): das Gaeste-WLAN-Passwort zieht von local_secrets.py in
# diese Event-Konfiguration um - es ist kein Geraete-Geheimnis wie der
# Shutdown-PIN, sondern aendert sich mit jeder Veranstaltung. UEBERGANGS-
# WEISE (fuer bestehende Installationen, die event_config.json noch nicht
# angelegt haben) faellt der Wert, falls in der JSON nicht gesetzt, noch auf
# ein eventuell vorhandenes local_secrets.GUEST_WIFI_PASSWORD zurueck, bevor
# der generische Platzhalter greift. Neu eingerichtete Installationen tragen
# das Passwort nur noch in event_config.json ein.
_event_wifi_password = _event_config.get("guest_wifi_password")
if _event_wifi_password:
    GUEST_WIFI_PASSWORD = str(_event_wifi_password)
else:
    GUEST_WIFI_PASSWORD = getattr(_secrets, "GUEST_WIFI_PASSWORD", _PLACEHOLDER)

# NEU (Sprint-11-Nachbesserung): ob QR-Codes fuer diese Veranstaltung
# ueberhaupt erzeugt/angezeigt werden sollen - Speicher-Bestaetigung
# (state_machine._SAVE_CONFIRMATION_TEXT_*), das Icon/der Doppeltap "QR-Code
# anfordern" in der Galerie-Vollansicht sowie AppState.GALLERY_PHOTO_QR
# richten sich alle danach (siehe state_machine.py, app_with_hw.py). Manche
# Veranstaltungsorte haben kein Gaeste-WLAN oder der Gastgeber moechte
# grundsaetzlich keinen digitalen Download anbieten. Default True - das
# bisherige Verhalten bleibt fuer bestehende Installationen unveraendert,
# solange event_config.json diesen Schluessel nicht explizit auf false setzt.
QR_CODES_ENABLED = bool(_event_config.get("qr_codes_enabled", True))

# NEU (Sprint 11): ob die Galerie (Durchblaettern bisheriger Fotos + Voll-
# ansicht) fuer diese Veranstaltung ueberhaupt angeboten wird. Manche
# Veranstaltungen wollen/duerfen aus Datenschutz- oder Praesentationsgruenden
# nicht, dass Gaeste die Fotos anderer Gaeste auf dem Display durchsehen
# koennen. Betrifft den "Galerie"-Button im Hauptmenue, den automatischen
# Attract-Modus (ATTRACT_GALLERY zeigt bisherige Fotos als Einladung - ohne
# Galerie-Funktion inhaltlich nicht mehr passend) sowie Anleitung und
# Nutzungsbedingungen (siehe renderer.py). Default True - bestehende
# Installationen bleiben unveraendert, solange event_config.json diesen
# Schluessel nicht explizit auf false setzt.
#
# WICHTIG: der foto-spezifische QR-Download (Sprint 11, Feature 4) ist
# ausschliesslich aus der Galerie-Vollansicht heraus erreichbar - ist die
# Galerie deaktiviert, ist der QR-Download damit automatisch ebenfalls
# unerreichbar, unabhaengig vom eigenen Schalter QR_CODES_ENABLED. Beide
# Schalter wirken daher an manchen Stellen (siehe renderer._draw_terms)
# gemeinsam (UND-verknuepft).
GALLERY_ENABLED = bool(_event_config.get("gallery_enabled", True))

# NEU (Etappe 8, Feedback): erkennt, ob die Event-Konfiguration noch auf den
# generischen Platzhaltern steht - entweder weil data/event_config.json
# komplett fehlt, oder weil sie 1:1 aus event_config_example.json kopiert
# wurde, ohne die Werte anzupassen. Gedacht als Hinweis fuer andere GitHub-
# Nutzer, die das Projekt frisch aufsetzen: Konsolen-Warnungen beim Start
# werden leicht uebersehen, ein sichtbarer Hinweis im Hauptmenue (siehe
# renderer.py) und in der Diagnose (ADMIN_STATUS, siehe app_with_hw.py)
# nicht. Verschwindet automatisch, sobald echte Werte eingetragen sind -
# kein manuelles Wegklicken noetig.
NEEDS_EVENT_SETUP = (
    EVENT_TITLE == "Fotobox"
    or GUEST_WIFI_PASSWORD in ("BITTE_ANPASSEN", _PLACEHOLDER)
)


@dataclass(frozen=True)
class ScreenConfig:
    # Touch Display V2: physisch 720x1280 (Hochformat), per OS-Rotation
    # (Pi-Einstellungen, 90 Grad) auf dem Pi bereits gedreht. Dadurch sieht
    # Pygame den Bildschirm als normale 1280x720-Fläche (Querformat) - die App
    # muss sich um die Drehung selbst nicht kümmern, nur diese Werte stimmen.
    width: int = 1280
    height: int = 720
    fullscreen: bool = True
    # NEU (Etappe 8): kommt aus data/event_config.json (Fallback "Fotobox"),
    # nicht mehr fest im Code - siehe load_event_config() oben.
    title: str = EVENT_TITLE
    target_fps: int = 30
    hide_mouse: bool = True


@dataclass(frozen=True)
class TimeoutConfig:
    # Mindestdauer des Systemstart-Bildschirms (Wallpaper "Werbung") - siehe
    # renderer.py _draw_boot_background(). Bewusst 5.0 statt vorher 4.0.
    boot_seconds: float = 5.0
    main_menu_idle_seconds: float = 180.0
    preview_warning_seconds: float = 30.0
    preview_total_seconds: float = 180.0
    # Wartezeit in PHOTO_PREVIEW, bevor der Countdown automatisch startet
    # (Zeit, um sich auf die Markierung zu stellen - der Countdown selbst
    # laeuft danach noch zusaetzlich 5,4,3,2,1 Sekunden).
    preview_auto_start_seconds: float = 2.0
    # Nutzungsbedingungen-Ansicht: anders als INSTRUCTIONS (kein Auto-Timeout)
    # soll diese Ansicht nach Untaetigkeit automatisch verlassen werden, damit
    # die Fotobox nicht dauerhaft auf dem Bedingungen-Screen "haengen bleibt".
    terms_idle_seconds: float = 180.0
    gallery_idle_seconds: float = 180.0
    gallery_fullscreen_idle_seconds: float = 30.0
    review_idle_seconds: float = 180.0
    # GEAENDERT (Sprint 11, Feature 3): dieser Screen zeigt seit diesem Umbau
    # keinen QR-Code mehr, nur noch einen kurzen Hinweistext (siehe
    # state_machine._SAVE_CONFIRMATION_TEXT) - der Name bleibt bewusst
    # unveraendert (kein Enum-Rename, siehe dortiger Kommentar). Von 60s auf
    # 20s verkuerzt: zum Lesen eines kurzen Absatzes reicht das, ohne den
    # Ablauf unnoetig zu verlangsamen (anders als vorher, wo Gaeste Zeit zum
    # Scannen eines QR-Codes brauchten).
    qr_display_seconds: float = 20.0
    delete_confirm_seconds: float = 30.0
    attract_frame_seconds: float = 5.0
    countdown_seconds: tuple[int, ...] = (5, 4, 3, 2, 1)
    # Service-/Admin-Menue: wird es so lange nicht bedient, geht es
    # automatisch zurueck ins Hauptmenue. Bewusst kurz - das Menue soll
    # waehrend einer Veranstaltung nie versehentlich offen stehen bleiben.
    # ACHTUNG: Bei langlaufenden Aktionen (Loeschen, USB-Export, Etappe 3/4)
    # muss dieser Timer pausiert werden, sonst reisst er die laufende
    # Aktion mittendrin weg.
    admin_menu_idle_seconds: float = 30.0
    # NEU (4.3): so lange steht der "App wird neu gestartet ..."-Screen,
    # bevor die App sich tatsaechlich beendet - reines Feedback, damit der
    # Bildschirm nicht unvermittelt schwarz wird.
    admin_restart_delay_seconds: float = 1.5
    # NEU (4.6): Wartebildschirm "Bitte USB-Stick einstecken". Bewusst
    # deutlich laenger als admin_menu_idle_seconds - Stick suchen,
    # Gehaeuse aufklappen und einstecken dauert laenger als 30 Sekunden.
    admin_usb_wait_seconds: float = 120.0
    # NEU (Feedback nach 6c): eigener, deutlich laengerer Idle-Timeout fuer
    # die uebrigen USB-Export-Screens (bereit/Problem/Export fertig/
    # entfernen/Konfliktauswahl) - bewusst GETRENNT von
    # admin_menu_idle_seconds (30s), das fuer die schnellen Admin-
    # Bestaetigungen (Status, Loeschen) angemessen bleibt. Beim
    # USB-Export - besonders auf dem Konflikt-Screen, wo womoeglich mehrere
    # Dateien einzeln durchgegangen werden - reissen 30s zu leicht mitten
    # in der Bedienung ab.
    admin_usb_idle_seconds: float = 120.0
    # NEU (Sprint 11, Feature 4): so lange bleibt der QR-Code eines einzelnen
    # Galerie-Fotos eingeblendet (Doppeltap/Icon in GALLERY_FULLSCREEN),
    # bevor automatisch zurueck zur Fotoansicht gewechselt wird - wie vom
    # Nutzer vorgegeben.
    gallery_qr_seconds: float = 30.0
    # NEU (Sprint 11, Feature 1): Cold-Start-Schaetzwert fuer die Dauer der
    # Bilduebertragung (Ausloesen + gphoto2-Download), bevor die erste echte
    # Messung vorliegt. Wird danach laufend durch echte Stoppuhr-Messungen
    # ersetzt/verfeinert (siehe capture_timing.py) - steuert Tempo der
    # Uebertragungs-Animation (Datei-Symbol + LED-Punkt), damit beide
    # halbwegs synchron zur tatsaechlichen Uebertragung laufen.
    capture_transfer_estimate_seconds: float = 4.0


@dataclass(frozen=True)
class FeatureFlags:
    use_fake_preview: bool = False
    use_fake_capture: bool = False
    debug_overlay: bool = False
    enable_leds: bool = True
    enable_gpio_button: bool = True


@dataclass(frozen=True)
class GpioConfig:
    trigger_button_pin: int = 15
    shutter_pin: int = 17
    # HINWEIS: Kein separates focus_pin-Feld mehr - FOCUS- und SHUTTER-Kontakt
    # des Nikon-Steckers sind hardwareseitig zusammengeloetet und werden beide
    # gemeinsam ueber denselben Optokoppler an shutter_pin (GPIO17) ausgeloest.
    #
    # HINWEIS: Kein led_ring_pin-Feld mehr - das war ein Relikt der urspruenglich
    # geplanten PWM-Ansteuerung (rpi_ws281x). Seit der Umstellung auf SPI
    # (siehe hw_led_provider.py) laeuft der LED-Ring fest ueber SPI0/GPIO10
    # (Pin 19); dieses Feld wurde nirgends mehr gelesen.
    led_count: int = 35


@dataclass(frozen=True)
class NetworkConfig:
    # NEU: Pi-Adresse von .100 auf .10 umgestellt - Gaeste-DHCP-Pool auf dem
    # TP-Link liegt jetzt bei .50-.254, .10/.17 (Admin-Laptop) liegen als
    # feste Reservierungen ausserhalb davon. Portweiterleitungen (22/80/5900)
    # und Adressreservierung auf dem TP-Link bereits entsprechend angepasst.
    raspi_ip: str = "192.168.0.10"
    photo_url_prefix: str = "http://192.168.0.10/fotos"
    guest_wifi_password: str = GUEST_WIFI_PASSWORD


@dataclass(frozen=True)
class GalleryConfig:
    # Hoehe reduziert (war 165): bei grid_columns=4 und dem reservierten
    # Grid-Bereich in renderer.py (30%-77% der Bildschirmhoehe = ca. 338px)
    # passte rechnerisch nur eine Zeile Thumbnails hinein. Mit 140px Hoehe
    # passen zwei Zeilen gleichzeitig auf den Screen, weitere Zeilen sind
    # per Swipe hoch/runter erreichbar (gallery_scroll_offset).
    thumbnail_size: tuple[int, int] = (240, 140)
    grid_columns: int = 4
    max_fullscreen_cache_items: int = 12
    max_thumbnail_cache_items: int = 200
    # Dateinamen, die NIE in der Galerie (Grid/Vollbild) angezeigt werden
    # und NICHT als "echte" Fotos zaehlen (session.photos/GALLERY_EMPTY-
    # Entscheidung unberuehrt). testbild.png: Diagnosebild, per nginx unter
    # /fotos/ erreichbar, aber nirgends in der App sichtbar. example_01-03:
    # Beispielbilder - erscheinen NICHT im Grid, aber siehe
    # example_fly_in_filenames unten fuer ihren einzigen Auftritt (Attract-
    # Modus, nur solange keine echten Fotos existieren). Vergleich
    # case-insensitiv - daher klein schreiben.
    excluded_filenames: frozenset[str] = frozenset({
        "testbild.png", "example_01.jpg", "example_02.jpg", "example_03.jpg",
    })
    # NEU (Feedback): dieselben drei Beispielbilder wie oben in
    # excluded_filenames - hier als eigene, geordnete Liste, damit
    # renderer._draw_attract_gallery() weiss, WELCHE Dateien es als
    # Fly-In-Fallback laden soll, wenn noch keine echten Fotos existieren.
    # Bewusst eine SEPARATE Liste statt excluded_filenames wiederzu-
    # verwenden: excluded_filenames ist nicht geordnet (frozenset) und
    # enthaelt auch testbild.png, das NICHT im Fly-In auftauchen soll.
    example_fly_in_filenames: tuple[str, ...] = ("example_01.jpg", "example_02.jpg", "example_03.jpg")


@dataclass(frozen=True)
class StorageConfig:
    # Speicherplatz-Alarm (Feedback nach Etappe 8/Netzwerk-Umstellung):
    # bei wenig freiem Speicher warnt zuerst nur Stufe 1 (Text im
    # Hauptmenue), ab critical_threshold_percent greift Stufe 2 (Aufnahme-
    # Sperre + auffaelliges Blinken von Bildschirm und LED). Schwellwerte
    # sind INKLUSIV (siehe storage_service.assess_storage()) - im Zweifel
    # lieber eine Stufe zu frueh warnen als zu spaet.
    warn_threshold_percent: float = 10.0
    critical_threshold_percent: float = 5.0
    # Durchschnittliche JPEG-Groesse (Fine-Qualitaet) der Nikon D3300 - nur
    # Fallback, solange noch keine eigenen Fotos existieren, aus denen sich
    # ein echter Durchschnitt bilden liesse (siehe assess_storage()).
    # Erfahrungswert aus einer echten Veranstaltung: 775 MB / 63 Aufnahmen.
    fallback_avg_photo_size_bytes: int = 13 * 1024 * 1024
    # Wie oft der Speicherstand neu geprueft wird. shutil.disk_usage() ist
    # zwar billig, aber es gibt keinen Grund, das bei jedem einzelnen Frame
    # (30x/Sekunde) zu tun.
    check_interval_seconds: float = 30.0


@dataclass(frozen=True)
class ShutdownConfig:
    # NEU (Sprint 11): Klassenname/Feldname (AppConfig.shutdown) bewusst
    # unveraendert gelassen, obwohl die PIN laengst den gesamten
    # Service-Bereich schuetzt, nicht nur das Herunterfahren - anders als
    # bei den lokalen Secrets-Variablen (SERVICE_MENU_PIN u.a., siehe
    # unten) haette eine Umbenennung hier deutlich mehr Dateien beruehrt
    # (jede Stelle, die self.config.shutdown.* liest). Auf Wunsch spaeter
    # in einem eigenen Schritt nachziehbar.
    #
    # Verstecktes Herunterfahren per Geheim-Geste im Hauptmenue + PIN.
    # PIN, Zone, Muster und Long-Press-Dauer kommen aus local_secrets.py
    # (Fallbacks siehe oben) - stehen bewusst NICHT im Repo.
    pin: str = SERVICE_MENU_PIN

    # Gewaehlte Zone als Schluesselwort (nur informativ / fuer Debug-Ausgaben).
    gesture_zone: str = SERVICE_MENU_GESTURE_ZONE
    # Zone als konkretes Bruchteil-Rechteck (x, y, Breite, Hoehe), aus dem
    # Schluesselwort aufgeloest. Der Detector rechnet das mit der aktuellen
    # Bildschirmgroesse in Pixel um (SecretGestureDetector.from_config).
    gesture_corner_fraction: tuple[float, float, float, float] = _resolve_gesture_zone(SERVICE_MENU_GESTURE_ZONE)

    # Muster der Geste ("Anzahl"): Reihenfolge aus "kurz"/"lang".
    gesture_pattern: tuple[str, ...] = SERVICE_MENU_GESTURE_PATTERN
    # Dauer: ab dieser Haltedauer gilt ein Tipp als "lang" (Sekunden).
    long_press_seconds: float = SERVICE_MENU_LONG_PRESS_SECONDS
    # Groesste erlaubte Pause zwischen zwei Tipps; danach beginnt die Geste
    # von vorn. Bewusst in config (Robustheits-Konstante, kein Geheimnis).
    gesture_max_gap_seconds: float = 2.0

    # PIN-Eingabe: nach so vielen Fehlversuchen wird gesperrt ...
    max_pin_attempts: int = 3
    # ... und zwar fuer so viele Sekunden (30 Minuten). Persistent, siehe
    # lockout_file - ein Neustart der App/des Pi setzt die Sperre NICHT
    # zurueck.
    lockout_seconds: int = 30 * 60

    # --- Ablauf-Zeiten des PIN-/Shutdown-Flows (Schritt 3) ---
    # Idle-Timeout der PIN-Eingabe: wird der Screen so lange nicht bedient,
    # geht es automatisch zurueck ins Hauptmenue (die getippte PIN wird
    # dabei verworfen).
    pin_entry_idle_seconds: float = 30.0
    # Dauer der Fehler-Optik (rot/gelb + Taster-Blitz) nach einer falschen PIN.
    error_flash_seconds: float = 1.2
    # Dauer der Abschieds-Animation (SHUTDOWN_GOODBYE), bevor der Pi
    # tatsaechlich heruntergefahren wird. Muss >= der Laufzeit von
    # led_shutdown.py (TOTAL_SECONDS ~ 8.72s) sein, damit die
    # Sonnenuntergangs-Animation vollstaendig durchlaeuft.
    goodbye_seconds: float = 9.0

    # Fehler-Optik bei falscher PIN (rot/gelb am LED-Ring + Taster-LED-Blitz).
    # Nur die Parameter; die eigentliche Ausgabe erfolgt state-derived im
    # LED-/App-Layer (Integrationsschritt).
    error_ring_color_rgb: tuple[int, int, int] = (200, 0, 0)      # Rot
    error_accent_color_rgb: tuple[int, int, int] = (220, 160, 0)  # Gelb/Amber
    error_button_flash_count: int = 3
    error_button_flash_hz: float = 6.0

    # Persistente Sperr-/Zaehlerdatei. Liegt unter data/ (in .gitignore,
    # ueberlebt Neustart/Reboot). Bewusst NICHT im Repo.
    lockout_file: Path = DATA_DIR / "shutdown_lockout.json"


@dataclass(frozen=True)
class AppConfig:
    screen: ScreenConfig = field(default_factory=ScreenConfig)
    timeouts: TimeoutConfig = field(default_factory=TimeoutConfig)
    features: FeatureFlags = field(default_factory=FeatureFlags)
    gpio: GpioConfig = field(default_factory=GpioConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    gallery: GalleryConfig = field(default_factory=GalleryConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    # NEU (Feedback): Loesch- UND Kopierschutz - unabhaengig von
    # gallery.excluded_filenames (das nur die ANZEIGE betrifft), aber
    # inhaltlich ueberschneidend (dieselben vier Dateien). Wird von
    # app_with_hw.py sowohl an delete_all_photos() als auch an
    # export_photos() als excluded_filenames uebergeben - beide Routinen
    # unterstuetzen das Parameter bereits, keine Aenderung an
    # admin_delete_service.py/admin_usb_export.py noetig. testbild.png
    # liegt unter data/web/, die drei Beispielbilder unter data/photos/ -
    # eine gemeinsame Liste schadet nicht, da die jeweils andere Datei in
    # dem durchsuchten Verzeichnis ohnehin nicht existiert (kein Treffer,
    # kein Effekt).
    protected_filenames: frozenset[str] = frozenset({
        "testbild.png", "example_01.jpg", "example_02.jpg", "example_03.jpg",
    })
    shutdown: ShutdownConfig = field(default_factory=ShutdownConfig)
    photo_dir: Path = PHOTO_DIR
    web_dir: Path = WEB_DIR
    cache_dir: Path = CACHE_DIR
    log_dir: Path = LOG_DIR
    assets_dir: Path = ASSETS_DIR
    # NEU (Sprint 11, Feature 1): persistierte Schaetzung der
    # Bilduebertragungsdauer, siehe capture_timing.py.
    capture_timing_file: Path = DATA_DIR / "capture_timing.json"
    # Praefix fuer die Dateinamen der gespeicherten Fotos (siehe
    # hw_capture_provider.py _fetch_image). Ergebnis-Schema:
    # {photo_prefix}{JJJJMMTTHHMMSS}.jpg, z.B. "mina_20260711153045.jpg".
    # NEU (Etappe 8): kommt aus data/event_config.json (Fallback "foto_"),
    # nicht mehr fest im Code - siehe load_event_config() oben.
    photo_prefix: str = PHOTO_PREFIX
    # NEU (Etappe 8, Feedback): True, solange Titel oder Gaeste-WLAN-
    # Passwort noch auf den generischen Platzhaltern stehen - siehe
    # NEEDS_EVENT_SETUP oben fuer die genaue Bedingung.
    needs_event_setup: bool = NEEDS_EVENT_SETUP
    # NEU (Sprint-11-Nachbesserung): kommt aus data/event_config.json
    # (Fallback True), nicht mehr fest im Code - siehe QR_CODES_ENABLED oben.
    qr_codes_enabled: bool = QR_CODES_ENABLED
    # NEU (Sprint 11): kommt aus data/event_config.json (Fallback True),
    # siehe GALLERY_ENABLED oben.
    gallery_enabled: bool = GALLERY_ENABLED

    def ensure_directories(self) -> None:
        for path in (self.photo_dir, self.web_dir, self.cache_dir, self.log_dir, self.assets_dir):
            path.mkdir(parents=True, exist_ok=True)


DEFAULT_CONFIG = AppConfig()
