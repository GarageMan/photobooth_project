#!/usr/bin/env python3
"""
led_shutdown_preview.py
=======================
Vorschau der Abschalt-Animation fuer den WS2812-Ring (Schritt 1).

Spielt die Animation EINMAL ab und beendet sich dann - es wird NICHTS
heruntergefahren. Reines Ansehen/Abstimmen von Bewegung, Farbe und Timing,
bevor die identische Logik in hw_led_provider.py als LedEffect.SHUTDOWN_
SEQUENCE einzieht.

Ablauf:
  1. Der volle Ring leuchtet (warmes Amber).
  2. Er oeffnet sich langsam von oben (zwischen LED 23 und 24) nach unten
     ueber beide Flanken - wie eine sich schliessende Iris -, bis nur noch
     die unterste LED (Index 6) leuchtet.
  3. Diese letzte LED blitzt hell weiss auf und blendet dann sanft ab, aus.

Ring-Geometrie (aus der Kalibrierung bestaetigt):
  - unten  (6 Uhr): LED-Index 6   -> die zuletzt allein leuchtende LED
  - oben  (12 Uhr): zwischen LED 23 und 24
  - Index waechst im Uhrzeigersinn (0 -> 34)

WICHTIG:
  - Die Fotobox-App muss BEENDET sein (App und Werkzeug koennen sich den
    SPI-Ring nicht teilen).
  - Ausfuehrung mit sudo, wie die anderen HW-Skripte:
        sudo python3 led_shutdown_preview.py
  - Optional langsamer zum genauen Hinsehen (Faktor > 1 = langsamer):
        sudo python3 led_shutdown_preview.py 2.0
"""

from __future__ import annotations

import sys
import time

try:
    import board
    import neopixel_spi as neopixel
    _HW_AVAILABLE = True
except (ImportError, NotImplementedError):
    _HW_AVAILABLE = False


# -- Ring / Hardware (konsistent mit hw_led_provider.py) -----------------------
_LED_COUNT = 35
_LED_BRIGHTNESS = 110 / 255  # etwas Headroom, damit der End-Blitz deutlich heller wirkt als der Amber-Bogen

# -- Geometrie -----------------------------------------------------------------
_BOTTOM = 6          # 6 Uhr, letzte leuchtende LED
_MAX_SHELL = 17      # oberstes Paar (23/24) hat Abstand 17 von unten

# -- Optik ---------------------------------------------------------------------
_ARC_COLOR = (210, 95, 0)     # warmes Amber fuer den sich schliessenden Bogen
_FLASH_COLOR = (255, 255, 255)  # heller Weiss-Blitz der letzten LED
_FADE_SOFT = 1.6              # Weichheit der Iris-Kante in "Schalen"-Einheiten

# -- Timing (Sekunden, werden mit dem Kommandozeilen-Faktor skaliert) ----------
_T_OPEN = 7.0    # Dauer des Oeffnens (voller Ring -> nur untere LED)
_T_FLASH = 0.35  # Dauer des hellen Aufblitzens
_T_FADE = 1.6    # Dauer des Abblendens bis aus


def _shell(index: int) -> int:
    """
    Ring-Abstand einer LED von unten (Index 6), ueber die naeher liegende
    Flanke gemessen: 0 = unten, 17 = oberstes Paar (23/24). Zwei LEDs teilen
    sich jede Schale 1..17 (je eine links, eine rechts) - dadurch oeffnet
    sich der Bogen exakt symmetrisch.
    """
    return min((index - _BOTTOM) % _LED_COUNT, (_BOTTOM - index) % _LED_COUNT)


_SHELLS = [_shell(i) for i in range(_LED_COUNT)]


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _make_pixels():
    spi = board.SPI()
    return neopixel.NeoPixel_SPI(
        spi,
        _LED_COUNT,
        pixel_order=neopixel.GRB,
        brightness=_LED_BRIGHTNESS,
        auto_write=False,
    )


def _clear(pixels) -> None:
    pixels.fill((0, 0, 0))
    pixels.show()


def _render_open(pixels, openness: float) -> None:
    """
    openness 0.0 -> voller Ring, 1.0 -> nur noch die untere LED.
    Die "Aus-Front" wandert von oben (Schale 17) nach unten; jede Schale
    blendet ueber _FADE_SOFT weich aus, statt hart umzuschalten.
    """
    front = openness * (_MAX_SHELL + 1)  # 0 .. 18
    for i in range(_LED_COUNT):
        s = _SHELLS[i]
        if s == 0:
            level = 1.0  # untere LED bleibt waehrend des Oeffnens voll an
        else:
            x = front - (_MAX_SHELL - s)  # >0, sobald die Front diese Schale erreicht
            level = _clamp01(1.0 - x / _FADE_SOFT)
        pixels[i] = (
            int(_ARC_COLOR[0] * level),
            int(_ARC_COLOR[1] * level),
            int(_ARC_COLOR[2] * level),
        )
    pixels.show()


def _render_flash(pixels) -> None:
    pixels.fill((0, 0, 0))
    pixels[_BOTTOM] = _FLASH_COLOR
    pixels.show()


def _render_fade(pixels, factor: float) -> None:
    factor = _clamp01(factor)
    pixels.fill((0, 0, 0))
    pixels[_BOTTOM] = (
        int(_FLASH_COLOR[0] * factor),
        int(_FLASH_COLOR[1] * factor),
        int(_FLASH_COLOR[2] * factor),
    )
    pixels.show()


def _play(pixels, speed: float) -> None:
    t_open = _T_OPEN * speed
    t_flash = _T_FLASH * speed
    t_fade = _T_FADE * speed
    total = t_open + t_flash + t_fade

    print(f"Abschalt-Animation (Dauer ca. {total:.1f} s) - Strg-C bricht ab.")
    start = time.monotonic()
    phase = ""
    while True:
        elapsed = time.monotonic() - start
        if elapsed < t_open:
            if phase != "open":
                phase = "open"
                print("  Phase: Iris schliesst sich von oben ...")
            _render_open(pixels, elapsed / t_open)
        elif elapsed < t_open + t_flash:
            if phase != "flash":
                phase = "flash"
                print("  Phase: letzte LED blitzt auf ...")
            _render_flash(pixels)
        elif elapsed < total:
            if phase != "fade":
                phase = "fade"
                print("  Phase: abblenden ...")
            _render_fade(pixels, 1.0 - (elapsed - t_open - t_flash) / t_fade)
        else:
            break
        time.sleep(0.02)

    _clear(pixels)
    print("Fertig. (Es wurde nichts heruntergefahren.)")


def main() -> int:
    if not _HW_AVAILABLE:
        print("[led_shutdown_preview] board/neopixel_spi nicht verfuegbar -")
        print("dieses Werkzeug laeuft nur auf dem Raspberry Pi mit SPI.")
        return 1

    speed = 1.0
    if len(sys.argv) > 1:
        try:
            speed = max(0.2, float(sys.argv[1]))
        except ValueError:
            print("Faktor muss eine Zahl sein (z.B. 2.0 fuer langsamer).")
            return 1

    pixels = _make_pixels()
    try:
        _play(pixels, speed)
    except KeyboardInterrupt:
        print()
    finally:
        _clear(pixels)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())