from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Echte Zugangsdaten liegen NICHT im Code, sondern in local_secrets.py
# (nicht versioniert, siehe .gitignore und local_secrets_example.py).
# Fallback auf einen auffaelligen Platzhalter, falls die Datei auf einem
# frischen Checkout noch fehlt - fuehrt zu einer klar erkennbaren
# Fehlanzeige statt eines stillen Fehlers.
try:
    from local_secrets import GUEST_WIFI_PASSWORD
except ImportError:
    GUEST_WIFI_PASSWORD = "BITTE_local_secrets.py_ANLEGEN"
    print("[Config] WARNUNG: local_secrets.py fehlt - siehe local_secrets_example.py")


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
    title: str = "Fotobox - 150 Jahre-Feier"
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
    # geplanten PWM/GPIO12-Ansteuerung (rpi_ws281x). Seit der Umstellung auf
    # SPI (siehe hw_led_provider.py) laeuft der LED-Ring fest ueber SPI0/GPIO10
    # (Pin 19); dieses Feld wurde nirgends mehr gelesen und haette bei einer
    # kuenftigen Doku/Diagnose faelschlich wieder auf das obsolete GPIO12/Pin32
    # verwiesen.
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
class AppConfig:
    screen: ScreenConfig = field(default_factory=ScreenConfig)
    timeouts: TimeoutConfig = field(default_factory=TimeoutConfig)
    features: FeatureFlags = field(default_factory=FeatureFlags)
    gpio: GpioConfig = field(default_factory=GpioConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    gallery: GalleryConfig = field(default_factory=GalleryConfig)
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