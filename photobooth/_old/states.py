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
    ERROR_SCREEN = auto()
    MAINTENANCE = auto()
