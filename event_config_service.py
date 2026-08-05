"""
event_config_service.py
========================
Reine Logik fuer den Admin-Screen "Veranstaltungsdaten": Speichern der
Event-Konfiguration (event_config.json) sowie Suche/Kopie eines
Wallpaper-Bilds von einem USB-Stick.

Bewusst OHNE Abhaengigkeit zu pygame, config oder app_with_hw - genau wie
admin_usb_service.py/admin_usb_export.py, damit diese Logik offline und
ohne Hardware testbar bleibt (siehe test_event_config_service.py).

Kein Aufruf hier wirft jemals eine Exception nach aussen - jede Funktion
faengt alle erwartbaren Fehler (fehlende Datei, kein Schreibzugriff, volle
Platte, kaputter Stick waehrend des Kopierens) ab und liefert stattdessen
ein Ergebnis-Tupel mit einer fuer Lutz verstaendlichen Meldung zurueck.
Das ist wichtig, weil app_with_hw.py diese Funktionen teils aus einem
Hintergrund-Thread heraus aufruft (siehe _wallpaper_start_import) - eine
dort unbehandelte Exception wuerde den Thread stillschweigend beenden,
ohne dass der Screen jemals ein Ergebnis anzeigt.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

# Bilddateien, die auf einem USB-Stick als Wallpaper erkannt werden -
# gleiche Endungen wie ueberall sonst im Projekt (siehe admin_usb_export.py
# _IMAGE_SUFFIXES), PNG zuerst der Uebersichtlichkeit halber.
_WALLPAPER_SUFFIXES = (".png", ".jpg", ".jpeg")

# Obergrenze fuer eine Wallpaper-Datei - schuetzt vor einer versehentlich
# falschen/riesigen Datei auf dem Stick (z.B. ein RAW-Bild statt eines
# fertigen Wallpapers). 30 MB sind fuer ein 1280x720-Hintergrundbild in
# jedem ueblichen Format grosszuegig genug.
_MAX_WALLPAPER_BYTES = 30 * 1024 * 1024


def save_event_config(path: Path, data: dict) -> tuple[bool, str]:
    """Schreibt data als JSON nach path - atomar (Temp-Datei im selben
    Verzeichnis + os.replace), damit ein Absturz/Stromausfall mitten im
    Schreiben nie eine halb geschriebene, kaputte event_config.json
    hinterlaesst.

    Faengt JEDEN OSError ab (Berechtigung, volle Platte, fehlendes
    Verzeichnis) - wirft nie, liefert immer (ok, Meldung).
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_path, path)
    except OSError as exc:
        return False, f"Konnte {path.name} nicht speichern: {exc}"
    return True, f"{path.name} gespeichert."


def find_wallpaper_on_stick(mountpoint: Path) -> Path | None:
    """Sucht auf der obersten Ebene von mountpoint (KEINE Rekursion in
    Unterordner) nach einer Bilddatei. Bei mehreren Kandidaten wird die
    alphabetisch erste genommen - deterministisch statt von der
    Dateisystem-Reihenfolge abhaengig.

    Liefert None bei leerem/fehlendem/unlesbarem Verzeichnis oder wenn
    keine Bilddatei gefunden wurde - nie eine Exception.
    """
    try:
        candidates = sorted(
            p for p in mountpoint.iterdir()
            if p.is_file() and p.suffix.lower() in _WALLPAPER_SUFFIXES
        )
    except OSError:
        return None
    return candidates[0] if candidates else None


def import_wallpaper(source: Path, target: Path) -> tuple[bool, str]:
    """Kopiert source nach target - atomar (Temp-Datei im Zielverzeichnis
    + os.replace), damit ein waehrend des Kopierens gezogener Stick nie
    ein halb geschriebenes Wallpaper hinterlaesst.

    Prueft vorab Existenz und eine Groessenobergrenze. KEINE
    Bildinhalt-Validierung (kein Import von PIL o.ae.) - das uebernimmt
    weiterhin renderer.py's bestehendes try/except beim Laden des
    Wallpapers; eine zweite Pruefung hier waere Doppelarbeit und wuerde
    dieses Modul unnoetig an pygame koppeln.
    """
    try:
        if not source.is_file():
            return False, f"Datei nicht gefunden: {source.name}"
        size = source.stat().st_size
    except OSError as exc:
        return False, f"Konnte {source.name} nicht lesen: {exc}"
    if size > _MAX_WALLPAPER_BYTES:
        return False, f"Datei zu gross ({size // (1024 * 1024)} MB) - maximal 30 MB."
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target.with_suffix(target.suffix + ".tmp")
        shutil.copyfile(source, tmp_path)
        os.replace(tmp_path, target)
    except OSError as exc:
        return False, f"Konnte Wallpaper nicht uebernehmen: {exc}"
    return True, f"Wallpaper übernommen ({source.name})."
