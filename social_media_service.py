"""
social_media_service.py
=======================
NEU (Sprint 15): Social-Media-Freigabe einzelner Fotos.

Nach "Speichern" fragt die Fotobox den Gast, ob das Foto auf Social Media
veroeffentlicht werden darf (nur bei config.social_media_enabled, siehe
state_machine._handle_social_media_consent). Bei "Ja" legt dieses Modul
eine Kopie in data/social_media/ ab:

    data/photos/fotobox_20261205_153012.jpg
 -> data/social_media/freigegeben_fotobox_20261205_153012.jpg

Warum ein eigener Ordner statt eines Markers am Original:
- Galerie, Attract-Modus und HDMI-Wiedergabe lesen nur data/photos/ - die
  freigegebenen Kopien erscheinen dort nie doppelt.
- Die Veranstalterin bekommt beim USB-Export einen eigenen Unterordner
  "social_media" mit genau den freigegebenen Fotos (keine Auswahl noetig,
  keine Verwechslungsgefahr mit nicht freigegebenen Bildern).
- Der Web-Export fuer den QR-Download bleibt davon voellig unberuehrt.

Wird ein Foto spaeter geloescht (Galerie aufraeumen, "Alle Bilder
loeschen"), verschwindet auch die freigegebene Kopie (delete_released_copy
bzw. admin_delete_service) - konsequent im Sinne des Gastes.

Reine Dateioperationen, keine Hardware/pygame - ohne Pi testbar (siehe
test/test_social_media.py). Nie eine Exception nach aussen.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

RELEASED_PREFIX = "freigegeben_"


def released_name(photo_name: str) -> str:
    """Dateiname der freigegebenen Kopie zu einem Foto-Dateinamen."""
    return f"{RELEASED_PREFIX}{Path(photo_name).name}"


def release_photo(source: Path, social_media_dir: Path) -> tuple[bool, str]:
    """Kopiert source als freigegebene Kopie nach social_media_dir.

    Atomar: erst in eine Temp-Datei im Zielordner, dann os.replace() - eine
    halb geschriebene Datei landet so nie unter dem endgueltigen Namen (wird
    der USB-Export waehrenddessen gestartet, sieht er entweder nichts oder
    die vollstaendige Datei). Bereits vorhandene Kopie wird ersetzt.
    Rueckgabe (ok, Meldung)."""
    try:
        if not source.is_file():
            return False, f"Foto nicht gefunden: {source.name}"
        social_media_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Ordner fuer Social-Media-Fotos nicht verfuegbar: {exc}"
    target = social_media_dir / released_name(source.name)
    tmp = target.with_name(target.name + ".tmp")
    try:
        shutil.copy2(source, tmp)
        os.replace(tmp, target)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False, f"Freigabe konnte nicht gespeichert werden: {exc}"
    return True, f"Freigegeben: {target.name}"


def delete_released_copy(photo_name: str, social_media_dir: Path) -> bool:
    """Loescht die freigegebene Kopie zu photo_name, falls vorhanden.
    True, wenn eine Kopie geloescht wurde."""
    target = social_media_dir / released_name(photo_name)
    try:
        if target.is_file():
            target.unlink()
            return True
    except OSError:
        return False
    return False


def list_released(social_media_dir: Path) -> list[Path]:
    """Alle freigegebenen Kopien (nur Dateien mit Praefix, sortiert)."""
    try:
        return sorted(
            p for p in social_media_dir.iterdir()
            if p.is_file() and p.name.startswith(RELEASED_PREFIX) and not p.name.endswith(".tmp")
        )
    except OSError:
        return []
