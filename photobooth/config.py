from __future__ import annotations

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
GUEST_WIFI_PASSWORD = getattr(_secrets, "GUEST_WIFI_PASSWORD", _PLACEHOLDER)
SHUTDOWN_PIN = getattr(_secrets, "SHUTDOWN_PIN", _PLACEHOLDER)

# Parameter der Geheim-Geste - ebenfalls aus local_secrets.py, damit weder
# Muster noch Position im Repo stehen. Sinnvolle Standards, falls nicht
# gesetzt (die Geste funktioniert dann trotzdem, nur eben mit den hier
# hinterlegten Default-Werten).
SHUTDOWN_GESTURE_ZONE = getattr(_secrets, "SHUTDOWN_GESTURE_ZONE", "rechts")
SHUTDOWN_GESTURE_PATTERN = getattr(
    _secrets, "SHUTDOWN_GESTURE_PATTERN",
    ("kurz", "kurz", "kurz", "lang", "kurz", "kurz"),
)
SHUTDOWN_LONG_PRESS_SECONDS = getattr(_secrets, "SHUTDOWN_LONG_PRESS_SECONDS", 0.6)


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


@dataclass(frozen=True)
class ScreenConfig:
    # Touch Display V2: physisch 720x1280 (Hochformat), per OS-Rotation
    # (Pi-Einstellungen, 90 Grad) auf dem Pi bereits gedreht. Dadurch sieht
    # Pygame den Bildschirm als normale 1280x720-Fläche (Querformat) - die App
    # muss sich um die Drehung selbst nicht kümmern, nur diese Werte stimmen.
    width: int = 1280
    height: int = 720
    fullscreen: bool = True
    title: str = "Minas Geburtstags-Fotobox"
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
    qr_display_seconds: float = 60.0
    delete_confirm_seconds: float = 30.0
    attract_frame_seconds: float = 5.0
    countdown_seconds: tuple[int, ...] = (5, 4, 3, 2, 1)


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
    raspi_ip: str = "192.168.0.100"
    photo_url_prefix: str = "http://192.168.0.100/fotos"
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


@dataclass(frozen=True)
class ShutdownConfig:
    # Verstecktes Herunterfahren per Geheim-Geste im Hauptmenue + PIN.
    # PIN, Zone, Muster und Long-Press-Dauer kommen aus local_secrets.py
    # (Fallbacks siehe oben) - stehen bewusst NICHT im Repo.
    pin: str = SHUTDOWN_PIN

    # Gewaehlte Zone als Schluesselwort (nur informativ / fuer Debug-Ausgaben).
    gesture_zone: str = SHUTDOWN_GESTURE_ZONE
    # Zone als konkretes Bruchteil-Rechteck (x, y, Breite, Hoehe), aus dem
    # Schluesselwort aufgeloest. Der Detector rechnet das mit der aktuellen
    # Bildschirmgroesse in Pixel um (SecretGestureDetector.from_config).
    gesture_corner_fraction: tuple[float, float, float, float] = _resolve_gesture_zone(SHUTDOWN_GESTURE_ZONE)

    # Muster der Geste ("Anzahl"): Reihenfolge aus "kurz"/"lang".
    gesture_pattern: tuple[str, ...] = SHUTDOWN_GESTURE_PATTERN
    # Dauer: ab dieser Haltedauer gilt ein Tipp als "lang" (Sekunden).
    long_press_seconds: float = SHUTDOWN_LONG_PRESS_SECONDS
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
    shutdown: ShutdownConfig = field(default_factory=ShutdownConfig)
    photo_dir: Path = PHOTO_DIR
    web_dir: Path = WEB_DIR
    cache_dir: Path = CACHE_DIR
    log_dir: Path = LOG_DIR
    assets_dir: Path = ASSETS_DIR
    # Praefix fuer die Dateinamen der gespeicherten Fotos (siehe
    # hw_capture_provider.py _fetch_image) - "mina" ist das Kuerzel der
    # Person, die zur aktuellen Party eingeladen hat. Ergebnis-Schema:
    # {photo_prefix}{JJJJMMTTHHMMSS}.jpg, z.B. "mina_20260711153045.jpg".
    photo_prefix: str = "mina_"

    def ensure_directories(self) -> None:
        for path in (self.photo_dir, self.web_dir, self.cache_dir, self.log_dir, self.assets_dir):
            path.mkdir(parents=True, exist_ok=True)


DEFAULT_CONFIG = AppConfig()