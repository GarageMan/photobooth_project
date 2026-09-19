"""
wallpaper_theme_service.py
============================
Reine Logik fuer den Admin-Screen "Farbstil vorschlagen" (Sprint 13):
berechnet aus dem aktuellen Hauptmenue-Wallpaper per Pillow bis zu
CANDIDATE_COUNT Farbpaletten-Vorschlaege (Button-, Hintergrund- und
Textfarbe je Vorschlag).

Bewusst OHNE Abhaengigkeit zu pygame, config oder app - genau wie
event_config_service.py, damit diese Logik offline und ohne Hardware
testbar bleibt (siehe test_wallpaper_theme_service.py).

Kein Aufruf hier wirft jemals eine Exception nach aussen - jede Funktion
faengt alle erwartbaren Fehler (fehlendes/kaputtes Bild, Pillow fehlt) ab
und liefert stattdessen ein ThemeResult mit ok=False und einer fuer Lutz
verstaendlichen Meldung zurueck. Wichtig, weil app.py compute_theme_
candidates() aus einem Hintergrund-Thread heraus aufruft (siehe
_color_style_start_compute in app.py) - eine dort unbehandelte Exception
wuerde den Thread stillschweigend beenden, ohne dass der Screen jemals ein
Ergebnis anzeigt (gleiches Prinzip wie ueberall sonst im Projekt, siehe
Docstring von event_config_service.py).

NEU (Sprint 13, Lehre aus Sprint 12): die Palette-Berechnung laeuft bewusst
IMMER in einem Hintergrund-Thread (siehe ADMIN_EVENT_COLOR_STYLE_LOADING in
states.py), auch wenn eine einzelne Pillow-quantize()-Berechnung auf einem
bildschirmgrossen Wallpaper (ca. 1280x720, hier vor der Analyse zusaetzlich
auf _ANALYSIS_MAX_WIDTH herunterskaliert) normalerweise deutlich unter einer
Sekunde dauert. Der Sprint-12-Zoom-Bug (Vollbild-Zoom fror die UI fuer
~10 Sekunden ein, weil ein Hardware-Aufruf synchron im Haupt-/Event-Thread
lief, siehe hw_camera_settings_provider.py) hat gezeigt, wie leicht ein an
sich "kurzer" synchroner Aufruf zum spuerbaren Haenger wird - deshalb hier
von Anfang an ausschliesslich asynchron, unabhaengig davon, wie schnell die
Berechnung tatsaechlich ist.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image as PILImage
except ImportError:  # pragma: no cover - Pillow ist im Projekt ueberall sonst bereits Pflicht (siehe renderer.py)
    PILImage = None  # type: ignore[assignment]

RGB = tuple[int, int, int]

# Wieviele Kandidaten-Paletten dem Admin vorgeschlagen werden.
CANDIDATE_COUNT = 3

# Downscale-Zielbreite vor der Farbanalyse - beschleunigt quantize()/
# getcolors() spuerbar, ohne die grobe Farbverteilung des Bilds zu
# veraendern (fuer eine Handvoll dominanter Farben ist das Original in
# voller Aufloesung ohnehin weit mehr Detail als noetig).
_ANALYSIS_MAX_WIDTH = 160

# Anzahl der Cluster, die Pillows quantize() zunaechst bildet - grosszuegig
# gewaehlt, damit auch bei mehrfarbigen Wallpapern genug unterschiedliche
# Kandidaten-Farbtoene uebrig bleiben, nachdem zu dunkle/zu helle/zu graue
# Cluster unten aussortiert wurden.
_QUANTIZE_COLORS = 12

# Cluster, die (in HSL) heller als dies oder dunkler als dies sind, taugen
# nicht als Akzentfarbe (naeher an "fast Schwarz"/"fast Weiss" als an einer
# erkennbaren Farbe) - werden aus der Kandidatenliste gefiltert.
_MIN_LIGHTNESS = 0.08
_MAX_LIGHTNESS = 0.92
# Cluster mit sehr geringer Saettigung (Grau/Beige/Sand) werden ebenfalls
# uebersprungen - sie ergaeben eine kaum vom bisherigen neutralen Grau/Blau
# unterscheidbare "Akzent"-Farbe.
_MIN_SATURATION = 0.12

# Zwei Cluster gelten als "zu aehnlich" (nur der jeweils dominantere bleibt
# in der Liste), wenn ihr Farbton (Hue, 0..1, zyklisch) naeher beieinander
# liegt als dies - verhindert, dass alle Vorschlaege aus nur einer
# Bildregion stammen (z.B. drei Blautoene eines Himmels).
_MIN_HUE_DISTANCE = 1.0 / 12.0

# Ziel-Helligkeit (HSL-Lightness) fuer die abgeleitete Button- bzw.
# Hintergrundfarbe je Kandidat - kraeftig genug fuer einen Button, aber
# dunkel genug, um zum insgesamt dunklen Look der App zu passen (siehe
# renderer._background_color: fast alle bestehenden Werte liegen deutlich
# unter 25% Helligkeit).
_BUTTON_LIGHTNESS = 0.32
_BACKGROUND_LIGHTNESS = 0.11
# Saettigung wird fuer Button/Hintergrund gedaempft (nicht die oft sehr
# kraeftige Original-Saettigung des Wallpaper-Clusters) - wirkt dadurch
# weniger grell und passt besser zu den bisherigen, eher gedeckten
# Button-Farben der App (siehe renderer._draw_buttons).
_BUTTON_SATURATION_FACTOR = 0.75
_BACKGROUND_SATURATION_FACTOR = 0.55


@dataclass(frozen=True, slots=True)
class ThemeCandidate:
    button_color: RGB
    background_color: RGB
    text_color: RGB


@dataclass(frozen=True, slots=True)
class ThemeResult:
    ok: bool
    candidates: tuple[ThemeCandidate, ...] = ()
    message: str = ""


def _relative_luminance(color: RGB) -> float:
    """WCAG-relative Luminanz (0..1) - Grundlage der Kontrastberechnung
    unten. Siehe https://www.w3.org/TR/WCAG21/#dfn-relative-luminance"""
    def _channel(c: int) -> float:
        v = c / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = color
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _contrast_ratio(a: RGB, b: RGB) -> float:
    """WCAG-Kontrastverhaeltnis (1..21) zwischen zwei Farben."""
    la, lb = _relative_luminance(a), _relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def best_text_color(background: RGB) -> RGB:
    """Waehlt Weiss oder Schwarz als Textfarbe - je nachdem, was gegen
    background den hoeheren WCAG-Kontrast ergibt. Bei einem (praktisch nie
    exakten) Unentschieden gewinnt Weiss, das zum insgesamt dunklen
    Erscheinungsbild der App besser passt."""
    white: RGB = (255, 255, 255)
    black: RGB = (0, 0, 0)
    if _contrast_ratio(background, white) >= _contrast_ratio(background, black):
        return white
    return black


def _hsl_to_rgb(h: float, s: float, l: float) -> RGB:
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return (round(r * 255), round(g * 255), round(b * 255))


def _rgb_to_hsl(color: RGB) -> tuple[float, float, float]:
    r, g, b = color
    h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    return h, s, l


def _hue_distance(a: float, b: float) -> float:
    """Kuerzester Abstand zweier Farbtoene auf dem Farbkreis (0..1, hue ist
    zyklisch - 0.98 und 0.02 liegen nah beieinander, nicht 0.96 auseinander)."""
    diff = abs(a - b)
    return min(diff, 1.0 - diff)


def _dominant_hues(image: "PILImage.Image") -> list[tuple[float, float, int]]:
    """Liefert eine nach Haeufigkeit sortierte Liste von (hue, saturation,
    count) - je EIN Eintrag pro Farb-Cluster, das quantize() gefunden hat
    und das die obigen Lightness-/Saturation-Filter besteht sowie sich von
    bereits aufgenommenen Eintraegen ausreichend im Farbton unterscheidet
    (siehe _MIN_HUE_DISTANCE). count ist die Pixelzahl des Clusters, dient
    nur der Sortierung nach Dominanz."""
    small = image.convert("RGB")
    if small.width > _ANALYSIS_MAX_WIDTH:
        ratio = _ANALYSIS_MAX_WIDTH / small.width
        new_size = (_ANALYSIS_MAX_WIDTH, max(1, round(small.height * ratio)))
        small = small.resize(new_size, PILImage.BILINEAR)

    quantized = small.quantize(colors=_QUANTIZE_COLORS, method=PILImage.MEDIANCUT)
    palette = quantized.getpalette() or []
    counts = quantized.getcolors() or []  # [(count, palette_index), ...]

    entries: list[tuple[float, float, int]] = []
    for count, index in counts:
        offset = index * 3
        if offset + 2 >= len(palette):
            continue
        rgb = (palette[offset], palette[offset + 1], palette[offset + 2])
        hue, sat, lightness = _rgb_to_hsl(rgb)
        if lightness < _MIN_LIGHTNESS or lightness > _MAX_LIGHTNESS or sat < _MIN_SATURATION:
            continue
        entries.append((hue, sat, count))

    entries.sort(key=lambda e: e[2], reverse=True)

    filtered: list[tuple[float, float, int]] = []
    for hue, sat, count in entries:
        if any(_hue_distance(hue, existing_hue) < _MIN_HUE_DISTANCE for existing_hue, _existing_sat, _c in filtered):
            continue
        filtered.append((hue, sat, count))
    return filtered


def compute_theme_candidates(image_path: Path) -> ThemeResult:
    """Liest image_path (das aktuelle Hauptmenue-Wallpaper bzw. ein noch
    nicht uebernommenes, aber bereits zwischengelagertes neues Wallpaper -
    WELCHE Datei das ist, entscheidet app.py) und liefert bis zu
    CANDIDATE_COUNT Farbpaletten-Vorschlaege, nach Dominanz im Bild
    sortiert (Kandidat 1 = dominanteste geeignete Akzentfarbe).

    Nie eine Exception nach aussen - jeder erwartbare Fehler (Datei fehlt,
    kaputtes/nicht unterstuetztes Bildformat, Pillow fehlt, kein
    geeigneter Farbton gefunden) liefert stattdessen ok=False mit einer
    verstaendlichen Meldung."""
    if PILImage is None:
        return ThemeResult(ok=False, message="Pillow (PIL) ist nicht installiert.")
    if not image_path.is_file():
        return ThemeResult(ok=False, message="Kein Wallpaper vorhanden - bitte zuerst eines auswählen.")
    try:
        with PILImage.open(image_path) as image:
            hues = _dominant_hues(image)
    except Exception as exc:
        return ThemeResult(ok=False, message=f"Wallpaper konnte nicht gelesen werden: {str(exc)[:70]}")

    if not hues:
        return ThemeResult(
            ok=False,
            message="Im Wallpaper wurde keine geeignete Akzentfarbe gefunden (zu grau, zu hell oder zu dunkel).",
        )

    candidates: list[ThemeCandidate] = []
    for hue, sat, _count in hues[:CANDIDATE_COUNT]:
        button = _hsl_to_rgb(hue, min(1.0, sat * _BUTTON_SATURATION_FACTOR), _BUTTON_LIGHTNESS)
        background = _hsl_to_rgb(hue, min(1.0, sat * _BACKGROUND_SATURATION_FACTOR), _BACKGROUND_LIGHTNESS)
        text = best_text_color(button)
        candidates.append(ThemeCandidate(button_color=button, background_color=background, text_color=text))

    if not candidates:
        return ThemeResult(ok=False, message="Im Wallpaper wurde keine geeignete Akzentfarbe gefunden.")
    return ThemeResult(ok=True, candidates=tuple(candidates))


def color_to_hex(color: RGB) -> str:
    """Formatiert eine RGB-Farbe als "#rrggbb" - fuer die Ablage in
    event_config.json (siehe config.py fuer die Gegenrichtung beim Laden)."""
    return "#{:02x}{:02x}{:02x}".format(*color)


def hex_to_color(value: str) -> RGB | None:
    """Gegenstueck zu color_to_hex() - liefert None statt einer Exception
    bei jedem ungueltigen/fehlenden Wert (fehlender Schluessel in
    event_config.json, Tippfehler bei manueller Bearbeitung der Datei)."""
    text = value.strip()
    if text.startswith("#"):
        text = text[1:]
    if len(text) != 6:
        return None
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return None
