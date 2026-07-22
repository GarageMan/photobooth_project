from __future__ import annotations

from enum import Enum, auto


class AppState(Enum):
    BOOT = auto()
    MAIN_MENU = auto()
    ATTRACT_GALLERY = auto()
    GALLERY_GRID = auto()
    GALLERY_FULLSCREEN = auto()
    PHOTO_INTRO = auto()
    PHOTO_PREVIEW = auto()
    COUNTDOWN = auto()
    CAPTURE_PENDING = auto()
    REVIEW = auto()
    DELETE_CONFIRM = auto()
    QR_DISPLAY = auto()
    INSTRUCTIONS = auto()
    TERMS = auto()
    ERROR_SCREEN = auto()
    MAINTENANCE = auto()
    # --- Verstecktes Herunterfahren (Schritt 3) ---
    # PIN_ENTRY: Ziffernfeld, erscheint nach erkannter Geheim-Geste im
    # Hauptmenue (siehe shutdown_service.SecretGestureDetector).
    PIN_ENTRY = auto()
    # SHUTDOWN_GOODBYE: Abschieds-Animation (Wallpaper shutdown_wallpaper.png
    # + LED-Sonnenuntergang led_shutdown.py); danach faehrt der Pi herunter.
    SHUTDOWN_GOODBYE = auto()