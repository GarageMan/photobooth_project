"""
admin_delete_service.py
========================
Loeschung aller Veranstaltungsbilder (Service-Menue-Punkt
"Alle Bilder loeschen").

Geloescht wird an drei Orten:
  1. data/photos/  - die Originale (Quelle der Galerie)
  2. data/web/     - die exportierten Kopien fuer den QR-Download. Leicht
                     zu vergessen, aber genau diese sind ueber nginx im
                     Gaeste-WLAN erreichbar und muessen mit weg.
  3. Kamera        - Speicherkarte per gphoto2 (der Capture-Provider
                     loescht zwar nach jedem Download, ein Rest kann aber
                     z.B. nach einem Abbruch liegen bleiben).

Ausgenommen bleiben die Dateien aus excluded_filenames (Standard:
testbild.png) - das Diagnosebild soll eine Loeschung ueberleben.

WICHTIG - Grenzen der "sicheren" Loeschung auf Flash-Speicher:
Diese Implementierung ueberschreibt jede Datei vor dem Entfernen einmal
mit Nullen (overwrite_before_delete=True). Auf SD-Karten und USB-Sticks
ist das allerdings KEINE Garantie: der Flash-Controller verteilt
Schreibzugriffe selbstaendig auf freie Bloecke (Wear-Leveling), das
Ueberschreiben trifft daher in aller Regel gar nicht die urspruenglichen
Speicherzellen. Rechtlich ist das normale Entfernen (unlink) eine
Loeschung; der Ueberschreibvorgang ist ein Best-Effort-Zusatz, der nicht
als technische Garantie missverstanden werden darf. Genau deshalb wird
er hier dokumentiert statt beworben.

Das Loeschprotokoll wird ausschliesslich lokal unter data/logs/ abgelegt
(bewusst NICHT auf einem USB-Stick) und listet jede geloeschte Datei mit
Groesse und Aenderungszeitpunkt auf.
"""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


# Endungen, die als "Bild" gelten - identisch zu gallery_service.list_photos().
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

# Zeitlimit fuer die gphoto2-Loeschung auf der Kamera. Grosszuegig, weil
# das Loeschen vieler Dateien ueber USB spuerbar dauern kann.
_CAMERA_TIMEOUT_SEC = 120.0


@dataclass
class DeleteProgress:
    """Fortschritt des laufenden Loeschvorgangs. Wird vom Hintergrund-
    Thread gesetzt und vom Hauptloop gepollt - eine einzelne Zuweisung auf
    int/str ist unter dem GIL unteilbar, daher kein Lock noetig.
    Gleiches Muster wie ExportProgress in admin_usb_export.py."""
    total_files: int = 0
    deleted_files: int = 0
    phase: str = "start"      # "delete", "camera", "report", "done"


@dataclass(frozen=True)
class DeletedFile:
    """Metadaten einer Datei, VOR dem Loeschen eingesammelt."""
    path: Path
    size_bytes: int
    modified: datetime
    origin: str            # "photos" oder "web"


@dataclass
class DeleteResult:
    """Ergebnis eines kompletten Loeschlaufs."""
    deleted_photos: int = 0
    deleted_web_copies: int = 0
    camera_status: str = "nicht geprüft"
    camera_ok: bool = False
    errors: list[str] = field(default_factory=list)
    report_path: Path | None = None
    files: list[DeletedFile] = field(default_factory=list)

    def summary_lines(self) -> tuple[str, ...]:
        """Kurzfassung fuer den Abschluss-Screen (max. 5 Zeilen)."""
        lines = [
            f"Fotos von der Fotobox gelöscht: {self.deleted_photos}",
            f"Web-Kopien (QR-Download) gelöscht: {self.deleted_web_copies}",
            f"Kamera: {self.camera_status}",
        ]
        if self.errors:
            lines.append(f"Fehler aufgetreten: {len(self.errors)}")
        if self.report_path is not None:
            lines.append("Ein Löschprotokoll wurde erstellt (beim Betreiber erfragbar).")
        return tuple(lines)


# ------------------------------------------------------------------------------
# Einsammeln und Loeschen
# ------------------------------------------------------------------------------

def _collect_files(directory: Path, excluded: set[str], origin: str) -> list[DeletedFile]:
    """Metadaten aller loeschbaren Bilddateien einsammeln - VOR dem Loeschen,
    weil sie danach nicht mehr auslesbar sind."""
    collected: list[DeletedFile] = []
    if not directory.exists():
        return collected
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        if path.name.lower() in excluded:
            continue
        try:
            stat = path.stat()
            collected.append(DeletedFile(
                path=path,
                size_bytes=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime),
                origin=origin,
            ))
        except OSError:
            # Datei zwischenzeitlich verschwunden - kein Grund zum Abbruch.
            continue
    return collected


def _overwrite_with_zeros(path: Path) -> None:
    """Best-Effort-Ueberschreiben vor dem Entfernen. Siehe Modul-Docstring:
    auf Flash-Speicher keine technische Garantie."""
    size = path.stat().st_size
    with open(path, "r+b", buffering=0) as handle:
        chunk = b"\0" * 65536
        written = 0
        while written < size:
            block = min(len(chunk), size - written)
            handle.write(chunk[:block])
            written += block
        handle.flush()
        import os
        os.fsync(handle.fileno())


def _delete_files(
    files: list[DeletedFile],
    overwrite_before_delete: bool,
    errors: list[str],
    progress: DeleteProgress | None = None,
) -> int:
    deleted = 0
    for entry in files:
        if progress is not None:
            progress.deleted_files += 1
        try:
            if overwrite_before_delete:
                try:
                    _overwrite_with_zeros(entry.path)
                except OSError as exc:
                    # Ueberschreiben ist Zusatz, kein Muss - trotzdem loeschen.
                    errors.append(f"Überschreiben fehlgeschlagen ({entry.path.name}): {exc}")
            entry.path.unlink()
            deleted += 1
        except OSError as exc:
            errors.append(f"Löschen fehlgeschlagen ({entry.path.name}): {exc}")
    return deleted


def _delete_from_camera(camera_lock: threading.Lock | None) -> tuple[str, bool]:
    """Alle Dateien auf der Kamera-Speicherkarte loeschen.

    Bewusst ueber das gphoto2-Kommandozeilenwerkzeug statt der Python-API:
    --delete-all-files --recurse erledigt in einem Aufruf, was ueber die
    API ein manuelles Durchlaufen aller Ordner erfordern wuerde.

    Gibt (Statustext, ok) zurueck. Eine nicht erreichbare Kamera ist KEIN
    Fehlerfall, der den restlichen Lauf abbricht - der Statustext sagt dann
    ehrlich, dass nur der Pi geleert wurde.
    """
    lock = camera_lock if camera_lock is not None else threading.Lock()
    acquired = lock.acquire(timeout=10.0)
    if not acquired:
        return "nicht erreichbar (Kamera gerade belegt)", False
    try:
        result = subprocess.run(
            ["gphoto2", "--delete-all-files", "--recurse"],
            capture_output=True, text=True, timeout=_CAMERA_TIMEOUT_SEC,
        )
    except FileNotFoundError:
        return "nicht geprüft (gphoto2 nicht installiert)", False
    except subprocess.TimeoutExpired:
        return "Zeitüberschreitung beim Löschen", False
    except Exception as exc:  # defensiv - darf den Lauf nie sprengen
        return f"Fehler ({exc})", False
    finally:
        lock.release()

    combined = (result.stdout + result.stderr).lower()
    if result.returncode == 0:
        return "Speicherkarte geleert", True
    # Haeufigster Fall bei bereits leerer Karte bzw. abgezogener Kamera.
    if "could not detect" in combined or "no camera" in combined:
        return "NICHT erreichbar - nur die Fotobox wurde geleert", False
    if "not found" in combined or "no files" in combined:
        return "keine Dateien vorhanden", True
    first_line = (result.stderr.strip().splitlines() or ["unbekannter Fehler"])[0]
    return f"Fehler ({first_line[:60]})", False


# ------------------------------------------------------------------------------
# Protokoll
# ------------------------------------------------------------------------------

def _write_report(log_dir: Path, result: DeleteResult, started: datetime) -> Path | None:
    log_dir.mkdir(parents=True, exist_ok=True)
    filename = f"loeschprotokoll_{started:%Y-%m-%d_%H%M%S}.txt"
    path = log_dir / filename

    total_bytes = sum(entry.size_bytes for entry in result.files)

    lines: list[str] = []
    lines.append("LOESCHPROTOKOLL FOTOBOX")
    lines.append("=" * 70)
    lines.append(f"Zeitpunkt:        {started:%Y-%m-%d %H:%M:%S}")
    lines.append("Ausgeloest ueber: Service-Menue (nach Eingabe der Wartungs-PIN)")
    lines.append("")
    lines.append("ZUSAMMENFASSUNG")
    lines.append("-" * 70)
    lines.append(f"Fotos (data/photos/) geloescht:     {result.deleted_photos}")
    lines.append(f"Web-Kopien (data/web/) geloescht:   {result.deleted_web_copies}")
    lines.append(f"Gesamtgroesse der Dateien:          {total_bytes} Bytes")
    lines.append(f"Kamera-Speicherkarte:               {result.camera_status}")
    lines.append(f"Fehler waehrend des Laufs:          {len(result.errors)}")
    lines.append("")
    lines.append("HINWEIS ZUR LOESCHUNG")
    lines.append("-" * 70)
    lines.append("Die Dateien wurden vor dem Entfernen einmal mit Nullen ueberschrieben.")
    lines.append("Auf Flash-Speicher (SD-Karte) ist das durch Wear-Leveling KEINE")
    lines.append("technische Garantie dafuer, dass keine Restdaten mehr vorhanden sind.")
    lines.append("Etwaige Sicherungen (raspiBackup) sind von dieser Loeschung NICHT")
    lines.append("betroffen und muessen separat behandelt werden.")
    lines.append("")

    if result.errors:
        lines.append("FEHLER")
        lines.append("-" * 70)
        for message in result.errors:
            lines.append(f"  - {message}")
        lines.append("")

    lines.append("GELOESCHTE DATEIEN")
    lines.append("-" * 70)
    if not result.files:
        lines.append("(keine)")
    else:
        lines.append(f"{'Nr.':>4}  {'Herkunft':<8}  {'Dateiname':<34}  {'Bytes':>12}  Geaendert")
        for index, entry in enumerate(result.files, start=1):
            lines.append(
                f"{index:>4}  {entry.origin:<8}  {entry.path.name:<34}  "
                f"{entry.size_bytes:>12}  {entry.modified:%Y-%m-%d %H:%M:%S}"
            )
    lines.append("")
    lines.append("Ende des Protokolls.")

    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path
    except OSError as exc:
        result.errors.append(f"Protokoll konnte nicht geschrieben werden: {exc}")
        return None


# ------------------------------------------------------------------------------
# Oeffentliche Schnittstelle
# ------------------------------------------------------------------------------

def delete_all_photos(
    photo_dir: Path,
    web_dir: Path,
    log_dir: Path,
    excluded_filenames: frozenset[str] | set[str] = frozenset(),
    camera_lock: threading.Lock | None = None,
    delete_from_camera: bool = True,
    overwrite_before_delete: bool = True,
    progress: DeleteProgress | None = None,
) -> DeleteResult:
    """Loescht alle Bilder von Pi und Kamera und schreibt ein Protokoll.

    Wirft bewusst KEINE Ausnahmen nach aussen: jeder Teilschritt kann
    fehlschlagen, ohne die uebrigen zu verhindern. Fehler landen in
    result.errors und im Protokoll, damit der Abschluss-Screen ehrlich
    berichten kann statt einen Erfolg vorzutaeuschen.
    """
    started = datetime.now()
    excluded = {name.lower() for name in excluded_filenames}
    result = DeleteResult()

    photo_files = _collect_files(photo_dir, excluded, "photos")
    web_files = _collect_files(web_dir, excluded, "web")
    result.files = photo_files + web_files

    if progress is not None:
        progress.total_files = len(result.files)
        progress.phase = "delete"

    result.deleted_photos = _delete_files(photo_files, overwrite_before_delete, result.errors, progress)
    result.deleted_web_copies = _delete_files(web_files, overwrite_before_delete, result.errors, progress)

    if delete_from_camera:
        # Fuer die Kamera gibt es keinen Zwischenstand - gphoto2 loescht in
        # einem Rutsch und meldet erst am Ende. Der Balken bleibt hier
        # bewusst stehen, statt einen erfundenen Fortschritt vorzugaukeln.
        if progress is not None:
            progress.phase = "camera"
        result.camera_status, result.camera_ok = _delete_from_camera(camera_lock)
    else:
        result.camera_status = "übersprungen"
        result.camera_ok = False

    if progress is not None:
        progress.phase = "report"
    result.report_path = _write_report(log_dir, result, started)
    if progress is not None:
        progress.phase = "done"
    return result
