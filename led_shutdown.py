#!/usr/bin/env python3
"""
led_shutdown.py
===============
Produktive Render-Logik der Abschalt-Animation des WS2812-Rings.

Ablauf (wie ein Sonnenuntergang):
  1. Der volle Ring leuchtet WEISS.
  2. Er oeffnet sich langsam von oben (zwischen LED 23 und 24) nach unten
     ueber beide Flanken (Iris), bis nur noch die unterste LED (Index 6)
     leuchtet. Dabei wandert die Bogenfarbe ueber die Zeit von
     WEISS -> GELB -> ORANGE -> ROT.
  3. Die letzte, rote LED haelt kurz, blitzt dann ganz kurz hell weiss auf
     und blendet anschliessend sanft ab, aus.

Aufbau:
  - Der Kern ist HARDWAREFREI: frame_colors(t) liefert fuer eine absolute
    Zeit t (Sekunden seit Sequenzstart) eine Liste von 35 RGB-Tupeln
    (0..255). Der HW-Provider (hw_led_provider.py) ruft dies pro Frame auf,
    schreibt die Farben auf den Ring und stellt die globale Helligkeit ein.
  - TOTAL_SECONDS gibt die Gesamtdauer an; danach liefert frame_colors nur
    noch "alles aus". app_with_hw.py nutzt diese Dauer, um nach Ablauf der
    Animation das eigentliche poweroff auszuloesen.

Eigenstaendiger Sichttest auf dem Pi (App vorher beenden, kein poweroff):
    sudo python3 led_shutdown.py           # Normalgeschwindigkeit
    sudo python3 led_shutdown.py 2.0        # Faktor > 1 = langsamer

Ring-Geometrie (aus der Kalibrierung bestaetigt):
  - unten  (6 Uhr): LED-Index 6   -> die zuletzt allein leuchtende LED
  - oben  (12 Uhr): zwischen LED 23 und 24
  - Index waechst im Uhrzeigersinn (0 -> 34)
"""

from __future__ import annotations

# -- Ring / Geometrie ----------------------------------------------------------
LED_COUNT = 35
_BOTTOM = 6       # 6 Uhr, letzte leuchtende LED
_MAX_SHELL = 17   # oberstes Paar (23/24) hat Abstand 17 von unten

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

# -- Timing (Sekunden) ---------------------------------------------------------
_T_OPEN = 7.0    # Oeffnen: voller Ring -> nur untere LED
_T_HOLD = 0.20   # kurzes rotes Halten, damit der Blitz danach knallt
_T_FLASH = 0.12  # ganz kurzes, helles Aufblitzen
_T_FADE = 1.40   # Abblenden bis aus

TOTAL_SECONDS = _T_OPEN + _T_HOLD + _T_FLASH + _T_FADE


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _sunset_color(progress: float):
    """Lineare Interpolation durch die _SUNSET-Stuetzfarben.
    progress 0.0 -> weiss, 1.0 -> rot."""
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


def _shell(index: int) -> int:
    """Ring-Abstand einer LED von unten (Index 6), ueber die naeher liegende
    Flanke gemessen: 0 = unten, 17 = oberstes Paar (23/24). Zwei LEDs teilen
    sich jede Schale 1..17 (je eine links, eine rechts) - dadurch oeffnet
    sich der Bogen exakt symmetrisch."""
    return min((index - _BOTTOM) % LED_COUNT, (_BOTTOM - index) % LED_COUNT)


_SHELLS = [_shell(i) for i in range(LED_COUNT)]


def _open_colors(openness: float):
    """openness 0.0 -> voller Ring (weiss), 1.0 -> nur noch untere LED (rot).
    Die Aus-Front wandert von oben (Schale 17) nach unten; jede Schale
    blendet ueber _FADE_SOFT weich aus. Die Bogenfarbe folgt dem
    Sonnenuntergang und haengt allein an openness (der gesamte Bogen hat also
    zu jedem Zeitpunkt eine einheitliche Farbe)."""
    r, g, b = _sunset_color(openness)
    front = openness * (_MAX_SHELL + 1)
    colors = []
    for i in range(LED_COUNT):
        s = _SHELLS[i]
        if s == 0:
            level = 1.0  # untere LED bleibt waehrend des Oeffnens voll an
        else:
            level = _clamp01(1.0 - (front - (_MAX_SHELL - s)) / _FADE_SOFT)
        colors.append((int(r * level), int(g * level), int(b * level)))
    return colors


def _solid_bottom(color):
    colors = [(0, 0, 0)] * LED_COUNT
    colors[_BOTTOM] = (int(color[0]), int(color[1]), int(color[2]))
    return colors


def frame_colors(t: float):
    """Liefert die 35 RGB-Tupel (0..255) fuer die absolute Zeit t (Sekunden
    seit Sequenzstart). Vor Beginn (t < 0) voller weisser Ring, nach Ablauf
    (t >= TOTAL_SECONDS) alles aus."""
    if t < _T_OPEN:
        return _open_colors(max(0.0, t) / _T_OPEN)
    t -= _T_OPEN
    if t < _T_HOLD:
        return _solid_bottom(_SUNSET[-1])
    t -= _T_HOLD
    if t < _T_FLASH:
        return _solid_bottom(_FLASH_COLOR)
    t -= _T_FLASH
    if t < _T_FADE:
        factor = _clamp01(1.0 - t / _T_FADE)
        return _solid_bottom((
            _FLASH_COLOR[0] * factor,
            _FLASH_COLOR[1] * factor,
            _FLASH_COLOR[2] * factor,
        ))
    return [(0, 0, 0)] * LED_COUNT


def is_done(t: float) -> bool:
    return t >= TOTAL_SECONDS


# -----------------------------------------------------------------------------
# Eigenstaendiger Sichttest (nur auf dem Pi mit SPI). Der HW-Provider nutzt im
# Betrieb frame_colors() direkt und braucht diesen Block nicht.
# -----------------------------------------------------------------------------
def _selftest(speed: float) -> int:
    import time

    try:
        import board
        import neopixel_spi as neopixel
    except (ImportError, NotImplementedError):
        print("[led_shutdown] board/neopixel_spi nicht verfuegbar -")
        print("der Selbsttest laeuft nur auf dem Raspberry Pi mit SPI.")
        return 1

    # Fuer den Sichttest bewusst mit Headroom (110/255), damit der End-Blitz
    # deutlich heller wirkt als der Bogen. Im Betrieb bestimmt der Provider
    # die globale Helligkeit selbst.
    spi = board.SPI()
    pixels = neopixel.NeoPixel_SPI(
        spi, LED_COUNT, pixel_order=neopixel.GRB, brightness=110 / 255, auto_write=False
    )

    print(f"Abschalt-Animation (Dauer ca. {TOTAL_SECONDS:.1f} s bei Faktor 1) - Strg-C bricht ab.")
    print("(Es wird nichts heruntergefahren.)")
    start = time.monotonic()
    try:
        while True:
            t = (time.monotonic() - start) / speed
            for i, color in enumerate(frame_colors(t)):
                pixels[i] = color
            pixels.show()
            if is_done(t):
                break
            time.sleep(0.02)
    except KeyboardInterrupt:
        print()
    finally:
        pixels.fill((0, 0, 0))
        pixels.show()
    print("Fertig.")
    return 0


if __name__ == "__main__":
    import sys

    factor = 1.0
    if len(sys.argv) > 1:
        try:
            factor = max(0.2, float(sys.argv[1]))
        except ValueError:
            print("Faktor muss eine Zahl sein (z.B. 2.0 fuer langsamer).")
            raise SystemExit(1)
    raise SystemExit(_selftest(factor))
