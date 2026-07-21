#!/usr/bin/env python3
"""
led_shutdown_preview.py
=======================
Vorschau der Abschalt-Animation fuer den WS2812-Ring (Schritt 1).

Spielt die Animation EINMAL ab und beendet sich dann - es wird NICHTS
heruntergefahren. Reines Ansehen/Abstimmen von Bewegung, Farbe und Timing,
bevor die identische Logik in hw_led_provider.py als LedEffect.SHUTDOWN_
SEQUENCE einzieht.

Ablauf (wie ein Sonnenuntergang):
  1. Der volle Ring leuchtet zunaechst WEISS.
  2. Er oeffnet sich langsam von oben (zwischen LED 23 und 24) nach unten
     ueber beide Flanken - wie eine sich schliessende Iris -, bis nur noch
     die unterste LED (Index 6) leuchtet. Dabei wandert die Farbe des
     gesamten Bogens ueber die Zeit von WEISS -> GELB -> ORANGE -> ROT.
  3. Die letzte, rote LED haelt kurz, blitzt dann ganz kurz hell weiss auf
     und blendet anschliessend sanft ab, aus.

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
_LED_BRIGHTNESS = 110 / 255  # etwas Headroom, damit der End-Blitz deutlich heller wirkt als der Bogen

# -- Geometrie -----------------------------------------------------------------
_BOTTOM = 6          # 6 Uhr, letzte leuchtende LED
_MAX_SHELL = 17      # oberstes Paar (23/24) hat Abstand 17 von unten

# -- Optik ---------------------------------------------------------------------
# Sonnenuntergang: der Bogen wandert ueber die Zeit durch diese Stuetzfarben.
_SUNSET = (
    (255, 255, 255),  # weiss   (Start, voller Ring)
    (255, 210, 0),    # gelb
    (255, 110, 0),    # orange
    (255, 0, 0),      # rot     (Ende, nur noch untere LED)
)
_FLASH_COLOR = (255, 255, 255)  # kurzer, harter Weiss-Blitz der letzten LED
_FADE_SOFT = 1.6                # Weichheit der Iris-Kante in "Schalen"-Einheiten

# -- Timing (Sekunden, werden mit dem Kommandozeilen-Faktor skaliert) ----------
_T_OPEN = 7.0    # Dauer des Oeffnens (voller Ring -> nur untere LED)
_T_HOLD = 0.20   # kurzes rotes Halten, damit der Blitz danach knallt
_T_FLASH = 0.12  # ganz kurzes, helles Aufblitzen
_T_FADE = 1.40   # Dauer des Abblendens bis aus


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


def _sunset_color(progress: float):
    """
    Lineare Interpolation durch die _SUNSET-Stuetzfarben.
    progress 0.0 -> weiss, 1.0 -> rot.
    """
    progress = _clamp01(progress)
    segments = len(_SUNSET) - 1
    scaled = progress * segments
    idx = int(scaled)
    if idx >= segments:
        return _SUNSET[-1]
    frac = scaled - idx
    a = _SUNSET[idx]
    b = _SUNSET[idx + 1]
    return (
        a[0] + (b[0] - a[0]) * frac,
        a[1] + (b[1] - a[1]) * frac,
        a[2] + (b[2] - a[2]) * frac,
    )


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
    openness 0.0 -> voller Ring (weiss), 1.0 -> nur noch die untere LED (rot).
    Die "Aus-Front" wandert von oben (Schale 17) nach unten; jede Schale
    blendet ueber _FADE_SOFT weich aus. Die Bogenfarbe folgt dem Sonnen-
    untergang und haengt allein an openness (der gesamte Bogen hat also zu
    jedem Zeitpunkt eine einheitliche Farbe).
    """
    r, g, b = _sunset_color(openness)
    for i in range(_LED_COUNT):
        s = _SHELLS[i]
        if s == 0:
            level = 1.0  # untere LED bleibt waehrend des Oeffnens voll an
        else:
            x = openness * (_MAX_SHELL + 1) - (_MAX_SHELL - s)
            level = _clamp01(1.0 - x / _FADE_SOFT)
        pixels[i] = (int(r * level), int(g * level), int(b * level))
    pixels.show()


def _render_solid_bottom(pixels, color) -> None:
    pixels.fill((0, 0, 0))
    pixels[_BOTTOM] = (int(color[0]), int(color[1]), int(color[2]))
    pixels.show()


def _play(pixels, speed: float) -> None:
    t_open = _T_OPEN * speed
    t_hold = _T_HOLD * speed
    t_flash = _T_FLASH * speed
    t_fade = _T_FADE * speed
    total = t_open + t_hold + t_flash + t_fade

    red = _SUNSET[-1]
    print(f"Abschalt-Animation (Dauer ca. {total:.1f} s) - Strg-C bricht ab.")
    start = time.monotonic()
    phase = ""
    while True:
        elapsed = time.monotonic() - start
        if elapsed < t_open:
            if phase != "open":
                phase = "open"
                print("  Phase: Iris schliesst sich von oben (weiss -> rot) ...")
            _render_open(pixels, elapsed / t_open)
        elif elapsed < t_open + t_hold:
            if phase != "hold":
                phase = "hold"
                print("  Phase: untere LED haelt rot ...")
            _render_solid_bottom(pixels, red)
        elif elapsed < t_open + t_hold + t_flash:
            if phase != "flash":
                phase = "flash"
                print("  Phase: kurzer Weiss-Blitz!")
            _render_solid_bottom(pixels, _FLASH_COLOR)
        elif elapsed < total:
            if phase != "fade":
                phase = "fade"
                print("  Phase: abblenden ...")
            factor = 1.0 - (elapsed - t_open - t_hold - t_flash) / t_fade
            _render_solid_bottom(
                pixels,
                (
                    _FLASH_COLOR[0] * _clamp01(factor),
                    _FLASH_COLOR[1] * _clamp01(factor),
                    _FLASH_COLOR[2] * _clamp01(factor),
                ),
            )
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