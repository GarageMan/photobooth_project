"""
storage_alarm.py
=================
Speicherplatz-Ueberwachung. Berechnet aus dem freien Speicherplatz und der
durchschnittlichen Fotogroesse eine grobe Schaetzung, wie viele Aufnahmen
noch moeglich sind, und leitet daraus eine Alarmstufe ab:

    0 = unauffaellig
    1 = Warnung (farbiger Hinweistext im Hauptmenue)
    2 = kritisch (Aufnahmesperre, auffaelliges Blinken von Bildschirm+LED)

Bewusst OHNE Abhaengigkeit zu pygame, config oder app_with_hw: alle
Eingaben kommen als Pfade/Zahlen herein, disk_usage_fn ist austauschbar -
dadurch bleibt die Logik offline und ohne eine echte, kuenstlich
volllaufende Partition testbar (siehe test_storage_alarm.py).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# 13 MB - Erfahrungswert aus einer echten Veranstaltung (775 MB / 63
# Aufnahmen, Nikon D3300, JPEG Fine). Nur Fallback, solange noch keine
# eigenen Fotos existieren, aus denen sich ein echter Durchschnitt bilden
# liesse - siehe assess_storage().
DEFAULT_FALLBACK_AVG_PHOTO_SIZE_BYTES = 13 * 1024 * 1024

DEFAULT_WARN_THRESHOLD_PERCENT = 10.0
DEFAULT_CRITICAL_THRESHOLD_PERCENT = 5.0


@dataclass(frozen=True)
class StorageStatus:
    """Ergebnis einer Speicherplatz-Pruefung."""
    total_bytes: int
    used_bytes: int
    free_bytes: int
    free_percent: float
    average_photo_size_bytes: int
    # True, wenn average_photo_size_bytes aus dem Fallback-Wert stammt
    # (noch keine eigenen Fotos vorhanden) statt aus echten Aufnahmen
    # berechnet - informativ fuer die Diagnoseanzeige.
    average_is_fallback: bool
    estimated_remaining_photos: int
    alarm_level: int


def _average_file_size(paths: list[Path]) -> int | None:
    """Durchschnittsgroesse in Bytes, oder None, wenn keine der Dateien
    lesbar war (z.B. leere Liste oder zwischenzeitlich geloescht)."""
    sizes: list[int] = []
    for p in paths:
        try:
            sizes.append(p.stat().st_size)
        except OSError:
            continue
    if not sizes:
        return None
    return round(sum(sizes) / len(sizes))


def assess_storage(
    photo_dir: Path,
    photo_paths: list[str],
    warn_threshold_percent: float = DEFAULT_WARN_THRESHOLD_PERCENT,
    critical_threshold_percent: float = DEFAULT_CRITICAL_THRESHOLD_PERCENT,
    fallback_avg_photo_size_bytes: int = DEFAULT_FALLBACK_AVG_PHOTO_SIZE_BYTES,
    disk_usage_fn: Callable[[str], object] = shutil.disk_usage,
) -> StorageStatus:
    """Ermittelt den aktuellen Speicherstand und leitet die Alarmstufe ab.

    photo_dir muss bereits existieren (GalleryService.list_photos() legt
    das Verzeichnis ohnehin an, bevor diese Funktion sinnvollerweise
    aufgerufen wird) - diese Funktion legt es NICHT selbst an, um eine
    reine Lese-Funktion zu bleiben.

    photo_paths: Ergebnis von GalleryService.list_photos() (volle Pfade
    als String) - daraus wird die durchschnittliche Fotogroesse aus den
    TATSAECHLICH vorhandenen Aufnahmen berechnet. Ohne (lesbare) Fotos
    faellt die Funktion auf fallback_avg_photo_size_bytes zurueck.

    disk_usage_fn: austauschbar (Standard: shutil.disk_usage) - Aufrufer
    muss ein Objekt mit .total/.used/.free liefern (wie das echte
    shutil.disk_usage es tut). Dadurch ohne eine echte, kuenstlich
    volllaufende Partition testbar.

    Schwellwerte sind INKLUSIV: free_percent == critical_threshold_percent
    zaehlt bereits als kritisch, nicht erst darunter - im Zweifel lieber
    eine Stufe zu frueh warnen als zu spaet.
    """
    usage = disk_usage_fn(str(photo_dir))
    total, used, free = usage.total, usage.used, usage.free
    free_percent = (free / total * 100.0) if total > 0 else 0.0

    real_average = _average_file_size([Path(p) for p in photo_paths])
    if real_average:
        average_size = real_average
        average_is_fallback = False
    else:
        average_size = fallback_avg_photo_size_bytes
        average_is_fallback = True

    estimated_remaining = free // average_size if average_size > 0 else 0

    if free_percent <= critical_threshold_percent:
        alarm_level = 2
    elif free_percent <= warn_threshold_percent:
        alarm_level = 1
    else:
        alarm_level = 0

    return StorageStatus(
        total_bytes=total,
        used_bytes=used,
        free_bytes=free,
        free_percent=free_percent,
        average_photo_size_bytes=average_size,
        average_is_fallback=average_is_fallback,
        estimated_remaining_photos=estimated_remaining,
        alarm_level=alarm_level,
    )
