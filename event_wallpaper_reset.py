"""
event_wallpaper_reset.py
========================
NEU (Sprint 14, Nutzer-Feedback 2026-11-14): veranstaltungsspezifische
Wallpaper beim "Standardwerte"-Reset der Veranstaltungsdaten wieder auf den
Grundzustand setzen.

Dateien (alle im Ordner assets/):

| Datei                                  | Rolle                                   |
|----------------------------------------|-----------------------------------------|
| systemstart_wallpaper.png              | Start-Bild (Boot-Screen Touch-Display), |
|                                        | wird NIE veraendert                     |
| hdmi_monitor_wallpaper.png             | Standbild fuer HDMI-Monitor/Beamer,     |
|                                        | veranstaltungsspezifisch                |
| hauptmenu_wallpaper.png                | Hauptmenue-Hintergrund,                 |
|                                        | veranstaltungsspezifisch                |
| hauptmenu_wallpaper_original.png       | Original-Hauptmenue-Hintergrund,        |
|                                        | wird NIE veraendert                     |

Reset:
- hdmi_monitor_wallpaper.png  <- Kopie von systemstart_wallpaper.png
- hauptmenu_wallpaper.png     <- Kopie von hauptmenu_wallpaper_original.png

Kopiert wird jeweils zuerst in eine temporaere Datei im selben Ordner, die
dann per os.replace() die alte Datei ersetzt (atomar). Schlaegt das Kopieren
fehl (z.B. Speicher voll), bleibt die bisherige Datei unveraendert erhalten
statt zu verschwinden. Fehlt eine Quelldatei, wird das jeweilige Ziel nicht
angefasst und eine Meldung geliefert. Nie eine Exception nach aussen
(gleiche Regel wie event_config_service).

Reine Dateioperationen - ohne Hardware/pygame testbar (siehe
test/test_event_wallpaper_reset.py).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

STARTUP_WALLPAPER = "systemstart_wallpaper.png"
HDMI_WALLPAPER = "hdmi_monitor_wallpaper.png"
MAIN_MENU_WALLPAPER = "hauptmenu_wallpaper.png"
MAIN_MENU_WALLPAPER_ORIGINAL = "hauptmenu_wallpaper_original.png"


@dataclass(frozen=True)
class WallpaperResetResult:
    hdmi_reset: bool          # hdmi_monitor_wallpaper.png neu geschrieben?
    main_menu_reset: bool     # hauptmenu_wallpaper.png neu geschrieben?
    messages: tuple[str, ...]  # Hinweise/Fehler, leer bei vollem Erfolg

    @property
    def ok(self) -> bool:
        return self.hdmi_reset and self.main_menu_reset


def _replace_with_copy(source: Path, target: Path) -> str:
    """Ersetzt target atomar durch eine Kopie von source. Rueckgabe "" bei
    Erfolg, sonst eine Meldung."""
    if not source.is_file():
        return f"{source.name} fehlt - {target.name} unveraendert."
    tmp = target.with_name(target.name + ".tmp")
    try:
        shutil.copyfile(source, tmp)
        os.replace(tmp, target)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return f"{target.name} konnte nicht zurueckgesetzt werden: {exc}"
    return ""


def reset_event_wallpapers(assets_dir: Path) -> WallpaperResetResult:
    """Setzt HDMI- und Hauptmenue-Wallpaper auf den Grundzustand zurueck
    (siehe Modul-Docstring)."""
    hdmi_error = _replace_with_copy(assets_dir / STARTUP_WALLPAPER, assets_dir / HDMI_WALLPAPER)
    main_error = _replace_with_copy(
        assets_dir / MAIN_MENU_WALLPAPER_ORIGINAL, assets_dir / MAIN_MENU_WALLPAPER,
    )
    messages = tuple(m for m in (hdmi_error, main_error) if m)
    return WallpaperResetResult(
        hdmi_reset=not hdmi_error, main_menu_reset=not main_error, messages=messages,
    )


def hdmi_wallpaper_path(assets_dir: Path) -> Path:
    """Pfad des Standbilds fuer den HDMI-Ausgang: hdmi_monitor_wallpaper.png,
    falls vorhanden - sonst Rueckfall auf das Start-Bild (z.B. frische
    Installation, in der die HDMI-Datei noch nie angelegt wurde)."""
    preferred = assets_dir / HDMI_WALLPAPER
    return preferred if preferred.is_file() else assets_dir / STARTUP_WALLPAPER
