from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class LedEffect(Enum):
    OFF = auto()
    # Weicher, "hochfahrender" Komet in Blau-Weiss, einmal rundherum -
    # nur waehrend BOOT, bis das Hauptmenue erscheint.
    BOOT = auto()
    # Amber-Atmen, zusaetzlich synchron zur Taster-LED 3x gelb aufblitzend
    # (identischer 10s-Zyklus wie app_with_hw.py::_sync_button_led) - genutzt
    # fuer MAIN_MENU, ATTRACT_GALLERY, TERMS.
    MAIN_MENU = auto()
    # Zwei gegenlaeufig rotierende Kometen in Tuerkis - PHOTO_INTRO
    # ("Vorfreude/Energie", bewusst anders als das ruhige Amber-Atmen).
    PHOTO_INTRO = auto()
    # Sanfte Sinus-Lichtwelle in Violett-Blau, laeuft einmal rundherum -
    # INSTRUCTIONS (kein Blinken/Blitzen, lenkt beim Lesen nicht ab).
    INSTRUCTIONS_WAVE = auto()
    # Dauerhaft Weiss - PHOTO_PREVIEW ("Bitte auf die Markierung stellen!")
    PREVIEW = auto()
    # Countdown-Ziffern 5-4-3-2: rotierendes 5x5+5x2-Bloeckemuster in
    # aufsteigend "warmer" Farbe (kuehles Violett bis Gelb).
    COUNTDOWN_5 = auto()
    COUNTDOWN_4 = auto()
    COUNTDOWN_3 = auto()
    COUNTDOWN_2 = auto()
    # Countdown-Ziffer 1 + Anfang von CAPTURE_PENDING: schnelles Weiss-Blitzen.
    COUNTDOWN_1_FLASH = auto()
    # Letztes Stueck von CAPTURE_PENDING, kurz vor dem eigentlichen
    # GPIO-Ausloeseimpuls: Ring dunkel (keine Reflexionen in Brillen).
    PRE_TRIGGER_DARK = auto()
    # Waehrend des blockierenden gphoto2-Downloads (in _do_capture direkt
    # gesetzt, siehe app_with_hw.py) bis das Foto in REVIEW angezeigt wird.
    CAPTURE_PROCESSING = auto()
    # REVIEW: gelb atmend.
    REVIEW_BREATHE = auto()
    DELETE_CONFIRM = auto()
    QR = auto()
    # GALLERY_GRID: ruhiges Atmen wie MAIN_MENU, aber in Blau-Tuerkis statt
    # Amber (bewusst andere Farbe, damit Grid optisch nicht mit dem
    # Hauptmenue verwechselt wird).
    GALLERY_GRID_BREATHE = auto()
    # GALLERY_FULLSCREEN: Sternenhimmel-Twinkle (gelegentlich ein Stern in
    # Orange statt Weiss-Grau).
    GALLERY_STARFIELD = auto()
    ERROR = auto()


@dataclass
class LedService:
    current_effect: LedEffect = field(default=LedEffect.OFF)

    def set_effect(self, effect: LedEffect) -> None:
        self.current_effect = effect

    def get_effect(self) -> LedEffect:
        return self.current_effect

    def turn_off(self) -> None:
        self.current_effect = LedEffect.OFF
