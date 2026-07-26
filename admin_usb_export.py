"""
admin_usb_export.py
====================
Fotos auf USB-Stick exportieren (Etappe 4b). Kopiert alle Bilder aus
data/photos/ in einen nach der Veranstaltung benannten Unterordner auf
dem Stick (config.screen.title) und verifiziert jede Kopie per
SHA256-Pruefsumme.

Der Ordnername enthaelt bewusst KEINEN Zeitstempel: pro Veranstaltung
gibt es genau einen Export. Ein gleichbleibender Name ist ausserdem die
Voraussetzung dafuer, dass ein wiederholter Lauf bereits vorhandene
Dateien ueberspringen kann, statt alles erneut in einen neuen Ordner zu
kopieren.

Die Loeschung der Originale ist bewusst NICHT Teil dieses Moduls - sie
wird nach dem Export als eigenstaendiger Schritt angeboten (Uebergang zu
ADMIN_DELETE_CONFIRM), damit der Nutzer die Ergebnisse auf dem Stick
zuerst pruefen kann.

Bewusst OHNE Abhaengigkeit zu pygame, config oder app_with_hw: alle
Eingaben kommen als Pfade/Zahlen herein, damit die Logik offline und
ohne Hardware testbar bleibt.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# Endungen, die exportiert werden - identisch zu gallery_service.list_photos().
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


@dataclass
class ExportProgress:
    """Fortschritt des laufenden Exports. Wird vom Hintergrund-Thread
    gesetzt und vom Hauptloop gepollt - eine einzelne Referenzzuweisung
    auf Strings/Ints ist unter dem GIL unteilbar, daher kein Lock noetig."""
    total_files: int = 0
    copied_files: int = 0
    skipped_files: int = 0
    verified_files: int = 0
    current_file: str = ""
    phase: str = "start"       # "copy", "verify", "done", "error"


@dataclass
class ExportResult:
    """Ergebnis eines abgeschlossenen Exports."""
    copied: int = 0
    skipped: int = 0
    verified: int = 0
    failed_verify: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    target_dir: Path | None = None

    @property
    def ok(self) -> bool:
        return not self.failed_verify and not self.errors

    def summary_lines(self) -> tuple[str, ...]:
        lines: list[str] = []
        lines.append(f"Exportiert: {self.copied} Bilder")
        if self.skipped:
            lines.append(f"Übersprungen (bereits vorhanden): {self.skipped}")
        if self.failed_verify:
            lines.append(f"PRÜFSUMMENFEHLER: {len(self.failed_verify)} Dateien!")
            lines.append("Originale werden NICHT zum Löschen angeboten.")
        elif self.verified:
            # Bewusst ohne Haken-Sonderzeichen: die Pygame-Schriftart auf
            # dem Pi kennt U+2713 nicht und zeichnet ein leeres Kaestchen.
            lines.append(f"Alle {self.verified} Dateien geprüft (SHA256) - in Ordnung")
        if self.errors:
            lines.append(f"Fehler: {len(self.errors)}")
        if self.target_dir:
            lines.append(f"Zielordner: {self.target_dir.name}")
        return tuple(lines)


# Zeichen, die FAT32/exFAT/NTFS in Datei- und Ordnernamen verbieten.
# Der Event-Titel ist Freitext aus config.py und kann alles enthalten -
# ein Doppelpunkt in "Minas 10. Geburtstag: Fotobox" wuerde das Anlegen
# des Ordners sonst kommentarlos scheitern lassen.
_FORBIDDEN_NAME_CHARS = '<>:"/\\|?*'


def sanitize_folder_name(name: str, fallback: str = "Fotobox") -> str:
    """Macht aus einem beliebigen Event-Titel einen gueltigen Ordnernamen.

    Umlaute bleiben erhalten - vfat und exfat werden unter Linux mit
    UTF-8 eingebunden, und ein lesbarer Ordnername ist mehr wert als
    maximale Portabilitaet.

    Windows stolpert ueber Namen, die auf Punkt oder Leerzeichen enden;
    beides wird daher abgeschnitten.
    """
    cleaned = "".join("_" if ch in _FORBIDDEN_NAME_CHARS else ch for ch in name)
    # Steuerzeichen entfernen, Mehrfach-Leerzeichen zusammenfassen.
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned.strip(" .")
    # Laenge begrenzen: manche FAT-Implementierungen kommen mit sehr
    # langen Namen nicht zurecht.
    cleaned = cleaned[:80].strip(" .")
    return cleaned or fallback


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect_sources(
    photo_dir: Path,
    excluded_filenames: frozenset[str] | set[str],
) -> list[Path]:
    excluded = {name.lower() for name in excluded_filenames}
    return sorted(
        p for p in photo_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in _IMAGE_SUFFIXES
        and p.name.lower() not in excluded
    )


def export_photos(
    photo_dir: Path,
    mountpoint: Path,
    excluded_filenames: frozenset[str] | set[str],
    progress: ExportProgress,
    folder_name: str = "Fotobox",
    verify: bool = True,
) -> ExportResult:
    """Kopiert alle Bilder in einen nach dem Event benannten Ordner.

    folder_name kommt aus config.screen.title (dem Titel, der auch im
    Hauptmenue steht) und wird per sanitize_folder_name() von Zeichen
    befreit, die FAT/exFAT nicht erlauben.

    Bereits vorhandene Dateien (gleicher Name UND gleiche Groesse) werden
    uebersprungen - ein erneuter Export auf denselben Stick kopiert nur
    die fehlenden Bilder.

    Jede kopierte Datei wird danach per SHA256 gegen das Original geprueft.
    Das kostet bei ~150 Fotos a 5 MB etwa eine Minute, schuetzt aber vor
    defekten Sticks - und nur bei bestandener Pruefung wird das Loeschen
    der Originale angeboten.

    Diese Funktion laeuft im Hintergrund-Thread (siehe app_with_hw.py).
    Fortschritt wird ueber das uebergebene ExportProgress-Objekt
    kommuniziert - der Hauptloop pollt es in _emit_due_timers.
    """
    result = ExportResult()
    sources = _collect_sources(photo_dir, excluded_filenames)
    progress.total_files = len(sources)

    if not sources:
        progress.phase = "done"
        return result

    # Zielordner traegt den Event-Titel (config.screen.title). Bewusst OHNE
    # Zeitstempel: pro Veranstaltung gibt es genau einen Export, und ein
    # gleichbleibender Ordnername ist die Voraussetzung dafuer, dass die
    # Uebersprung-Logik unten ueberhaupt greifen kann - mit wechselndem
    # Namen waere jeder Lauf ein Vollexport in einen neuen Ordner.
    target = mountpoint / sanitize_folder_name(folder_name)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result.errors.append(f"Zielordner konnte nicht angelegt werden: {exc}")
        progress.phase = "error"
        return result
    result.target_dir = target

    # -- Kopieren ----------------------------------------------------------
    progress.phase = "copy"
    for index, src in enumerate(sources):
        progress.current_file = src.name
        progress.copied_files = index

        dst = target / src.name
        try:
            # Uebersprung-Logik: gleicher Name UND gleiche Groesse.
            if dst.exists() and dst.stat().st_size == src.stat().st_size:
                result.skipped += 1
                progress.skipped_files += 1
                continue
            shutil.copy2(src, dst)
            result.copied += 1
        except OSError as exc:
            result.errors.append(f"Kopieren fehlgeschlagen ({src.name}): {exc}")

    progress.copied_files = len(sources)

    # -- Verifikation ------------------------------------------------------
    if verify and (result.copied > 0 or result.skipped > 0):
        progress.phase = "verify"
        for index, src in enumerate(sources):
            dst = target / src.name
            progress.current_file = src.name
            progress.verified_files = index

            if not dst.exists():
                continue
            try:
                src_hash = _sha256(src)
                dst_hash = _sha256(dst)
                if src_hash == dst_hash:
                    result.verified += 1
                else:
                    result.failed_verify.append(src.name)
            except OSError as exc:
                result.errors.append(f"Prüfung fehlgeschlagen ({src.name}): {exc}")

        progress.verified_files = len(sources)

    progress.phase = "done"
    return result


def clear_stick(mountpoint: Path) -> tuple[int, list[str]]:
    """Alle Dateien und Ordner auf dem Stick loeschen.

    Gibt (Anzahl geloeschter Eintraege, Fehlerliste) zurueck. Der
    Loeschvorgang wird NICHT abgebrochen, wenn einzelne Dateien/Ordner
    nicht entfernt werden koennen (z.B. Systemdateien mancher Sticks) -
    stattdessen werden die Fehler gesammelt und zurueckgegeben.
    """
    deleted = 0
    errors: list[str] = []
    if not mountpoint.exists():
        return 0, ["Einhängepunkt existiert nicht."]
    for item in sorted(mountpoint.iterdir()):
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            deleted += 1
        except OSError as exc:
            errors.append(f"{item.name}: {exc}")
    return deleted, errors
