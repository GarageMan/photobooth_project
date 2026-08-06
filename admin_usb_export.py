"""
admin_usb_export.py
====================
Fotos auf USB-Stick exportieren. Kopiert alle Bilder aus data/photos/ in
einen nach der Veranstaltung benannten Unterordner auf dem Stick
(config.screen.title) und verifiziert jede Kopie per SHA256-Pruefsumme.

Der Ordnername enthaelt bewusst KEINEN Zeitstempel: pro Veranstaltung
gibt es genau einen Export. Ein gleichbleibender Name ist ausserdem die
Voraussetzung dafuer, dass ein wiederholter Lauf bereits vorhandene
Dateien ueberspringen kann, statt alles erneut in einen neuen Ordner zu
kopieren.

Die Loeschung der Originale ist bewusst NICHT Teil dieses Moduls - sie
wird nach dem Export als eigenstaendiger Schritt angeboten (Uebergang zu
ADMIN_DELETE_CONFIRM), damit der Nutzer die Ergebnisse auf dem Stick
zuerst pruefen kann.

Bewusst OHNE Abhaengigkeit zu pygame, config oder app: alle
Eingaben kommen als Pfade/Zahlen herein, damit die Logik offline und
ohne Hardware testbar bleibt.

Etappe 6a - Konfliktbehandlung
------------------------------
Frueher wurde eine bereits auf dem Stick vorhandene Datei allein anhand
der GROESSE uebersprungen und andernfalls kommentarlos ueberschrieben.
Neu (nur wenn collect_conflicts=True):
  - Datei fehlt auf dem Stick          -> kopieren
  - Datei vorhanden und BYTEWEISE gleich (SHA256) -> still ueberspringen
  - Datei vorhanden, Inhalt WEICHT AB  -> als Konflikt sammeln, NICHT
    kopieren; die Entscheidung (ueberschreiben / umbenennen) faellt spaeter
    interaktiv, danach wendet apply_conflict_resolutions() sie an.

Das Flag collect_conflicts ist bewusst standardmaessig AUS: mit
collect_conflicts=False verhaelt sich export_photos() exakt wie bisher
(groessenbasiertes Ueberspringen, Verify-Pass ueber alle Quellen). So
bleibt die neue Logik dormant, bis die Zustandsverdrahtung (Etappe 6b)
sie einschaltet.

Byteweise identische Dateien landen NICHT in der Konfliktliste: bei einem
Re-Export von 150 unveraenderten Fotos stuenden dort sonst 150 Zeilen,
die alle "ist eh dasselbe" bedeuten.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# Endungen, die exportiert werden - identisch zu gallery_service.list_photos().
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

# Standardentscheidung fuer einen frisch erkannten Konflikt. "rename" ist
# nicht-destruktiv: die bereits auf dem Stick liegende Datei bleibt in
# jedem Fall erhalten. Die interaktive Liste (Etappe 6c) kann pro Datei
# oder per Sammelaktion auf "overwrite" umgestellt werden.
DEFAULT_CONFLICT_DECISION = "rename"


@dataclass
class ExportProgress:
    """Fortschritt des laufenden Exports. Wird vom Hintergrund-Thread
    gesetzt und vom Hauptloop gepollt - eine einzelne Referenzzuweisung
    auf Strings/Ints ist unter dem GIL unteilbar, daher kein Lock noetig."""
    total_files: int = 0
    copied_files: int = 0
    skipped_files: int = 0
    verified_files: int = 0
    # NEU (6a): Fortschritt der Konfliktaufloesung (Phase 2).
    resolved_files: int = 0
    current_file: str = ""
    # "start", "copy", "verify", "conflicts", "resolve", "done", "error".
    # "conflicts" = Kopieren fertig, aber es bleiben ungeloeste Konflikte;
    # der Ablauf pausiert fuer die interaktive Auswahl.
    phase: str = "start"


@dataclass(frozen=True)
class ExportConflict:
    """Eine Datei, die auf dem Stick bereits mit ABWEICHENDEM Inhalt liegt.

    Frozen, damit sie gefahrlos im (ebenfalls frozen) AppModel liegen kann.
    Das Umschalten der Entscheidung erzeugt per dataclasses.replace() eine
    neue Instanz - dieselbe Disziplin wie im restlichen Zustandsmodell.
    """
    name: str                   # Dateiname (auf Quelle und Ziel identisch)
    src_size: int               # Groesse der neuen Datei (data/photos/)
    dst_size: int               # Groesse der vorhandenen Datei (Stick)
    src_mtime: float            # Aenderungszeit Quelle (Unix-Zeit)
    dst_mtime: float            # Aenderungszeit Ziel (Unix-Zeit)
    # "overwrite" = vorhandene Datei ersetzen; "rename" = neue Datei unter
    # name_001.jpg, name_002.jpg ... ablegen, vorhandene behalten.
    decision: str = DEFAULT_CONFLICT_DECISION


@dataclass
class ExportResult:
    """Ergebnis eines abgeschlossenen Exports."""
    copied: int = 0
    skipped: int = 0
    verified: int = 0
    # NEU (6a): aufgeloeste Konflikte.
    overwritten: int = 0
    renamed: int = 0
    failed_verify: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    target_dir: Path | None = None
    # NEU (6a): noch OFFENE Konflikte (Phase 1 gefuellt, nach
    # apply_conflict_resolutions() wieder leer).
    conflicts: list[ExportConflict] = field(default_factory=list)
    # NEU (6a): menschenlesbare Protokollzeilen der ausgefuehrten Aktionen
    # (Kopieren/Ueberschreiben/Umbenennen) - werden von app ins
    # Export-Logfile geschrieben.
    log_actions: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        # Auch offene Konflikte machen das Ergebnis "nicht ok": das Loeschen
        # der Originale darf erst nach vollstaendiger Aufloesung UND
        # bestandener Pruefung angeboten werden.
        return not self.failed_verify and not self.errors and not self.conflicts

    def summary_lines(self) -> tuple[str, ...]:
        lines: list[str] = []
        lines.append(f"Exportiert: {self.copied} Bilder")
        if self.overwritten:
            lines.append(f"Überschrieben: {self.overwritten}")
        if self.renamed:
            lines.append(f"Umbenannt (Original auf Stick behalten): {self.renamed}")
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


def _same_content(a: Path, b: Path) -> bool:
    """True, wenn a und b byteweise identisch sind.

    Zuerst die (billige) Groesse: bei Unterschied kann der Inhalt gar
    nicht gleich sein, dann muss nicht gehasht werden. Nur bei gleicher
    Groesse wird der (teure) SHA256-Vergleich faellig.
    """
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
    except OSError:
        return False
    return _sha256(a) == _sha256(b)


def _make_conflict(src: Path, dst: Path) -> ExportConflict:
    src_stat = src.stat()
    dst_stat = dst.stat()
    return ExportConflict(
        name=src.name,
        src_size=src_stat.st_size,
        dst_size=dst_stat.st_size,
        src_mtime=src_stat.st_mtime,
        dst_mtime=dst_stat.st_mtime,
        decision=DEFAULT_CONFLICT_DECISION,
    )


def next_free_rename(target_dir: Path, name: str) -> Path:
    """Naechster freier Pfad nach dem Schema stem_001.suffix, stem_002 ...

    Zaehlt hoch, bis ein Name gefunden ist, den es im Zielordner noch nicht
    gibt - beruecksichtigt damit sowohl bereits vorhandene Dateien auf dem
    Stick als auch in derselben Aufloesungsrunde bereits angelegte. Mindestens
    dreistellig (001); ab 1000 waechst die Ziffernzahl natuerlich mit.
    """
    stem = Path(name).stem
    suffix = Path(name).suffix
    i = 1
    while True:
        candidate = target_dir / f"{stem}_{i:03d}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


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
    collect_conflicts: bool = False,
) -> ExportResult:
    """Kopiert alle Bilder in einen nach dem Event benannten Ordner.

    folder_name kommt aus config.screen.title (dem Titel, der auch im
    Hauptmenue steht) und wird per sanitize_folder_name() von Zeichen
    befreit, die FAT/exFAT nicht erlauben.

    collect_conflicts=False (Standard): bisheriges Verhalten. Eine bereits
    vorhandene Datei mit GLEICHER Groesse wird uebersprungen, sonst
    kopiert (und dabei ueberschrieben). Danach werden alle Quellen per
    SHA256 gegen ihre Kopie geprueft.

    collect_conflicts=True: eine bereits vorhandene, byteweise identische
    Datei wird uebersprungen; eine vorhandene mit ABWEICHENDEM Inhalt wird
    NICHT kopiert, sondern als Konflikt gesammelt (result.conflicts). Die
    frisch kopierten Neu-Dateien werden geprueft; bleiben Konflikte offen,
    endet die Funktion in progress.phase == "conflicts", damit der Aufrufer
    die interaktive Auswahl einblenden kann.

    Diese Funktion laeuft im Hintergrund-Thread (siehe app.py).
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

    if collect_conflicts:
        return _export_with_conflicts(photo_dir, sources, target, result, progress, verify)

    # ----------------------------------------------------------------------
    # Bisheriges Verhalten (collect_conflicts=False) - UNVERAENDERT.
    # ----------------------------------------------------------------------
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


def _export_with_conflicts(
    photo_dir: Path,
    sources: list[Path],
    target: Path,
    result: ExportResult,
    progress: ExportProgress,
    verify: bool,
) -> ExportResult:
    """Kopierlauf mit inhaltsbasierter Konflikterkennung (Etappe 6a).

    Kopiert alle NEUEN Dateien, ueberspringt byteweise identische still
    und sammelt inhaltlich abweichende Namensgleichheiten als Konflikte.
    """
    progress.phase = "copy"
    newly_copied: list[str] = []          # Namen frisch kopierter Neu-Dateien
    for index, src in enumerate(sources):
        progress.current_file = src.name
        progress.copied_files = index

        dst = target / src.name
        try:
            if not dst.exists():
                shutil.copy2(src, dst)
                result.copied += 1
                result.log_actions.append(f"kopiert: {src.name}")
                newly_copied.append(src.name)
                continue
            if _same_content(src, dst):
                # Byteweise identisch: still ueberspringen. Da wir den Inhalt
                # gerade nachweislich verglichen haben, gilt die Datei als
                # geprueft (kein zweiter Hash-Durchlauf noetig).
                result.skipped += 1
                result.verified += 1
                progress.skipped_files += 1
                continue
            # Namensgleich, aber Inhalt weicht ab -> Konflikt, NICHT kopieren.
            result.conflicts.append(_make_conflict(src, dst))
        except OSError as exc:
            result.errors.append(f"Kopieren fehlgeschlagen ({src.name}): {exc}")

    progress.copied_files = len(sources)

    # -- Verifikation der frisch kopierten Neu-Dateien ---------------------
    if verify and newly_copied:
        progress.phase = "verify"
        for index, name in enumerate(newly_copied):
            src = photo_dir / name
            dst = target / name
            progress.current_file = name
            progress.verified_files = index
            if not dst.exists():
                continue
            try:
                if _sha256(src) == _sha256(dst):
                    result.verified += 1
                else:
                    result.failed_verify.append(name)
            except OSError as exc:
                result.errors.append(f"Prüfung fehlgeschlagen ({name}): {exc}")
        progress.verified_files = len(newly_copied)

    # Offene Konflikte pausieren den Ablauf fuer die interaktive Auswahl.
    if result.conflicts:
        progress.phase = "conflicts"
        return result

    progress.phase = "done"
    return result


def apply_conflict_resolutions(
    photo_dir: Path,
    result: ExportResult,
    progress: ExportProgress,
    verify: bool = True,
) -> ExportResult:
    """Wendet die getroffenen Entscheidungen (Phase 2) an.

    Erwartet ein ExportResult aus export_photos(..., collect_conflicts=True)
    mit gefuellter result.conflicts-Liste, deren Eintraege jeweils eine
    Entscheidung ("overwrite"/"rename") tragen. Kopiert entsprechend,
    protokolliert jede Aktion in result.log_actions, prueft die frisch
    geschriebenen Dateien per SHA256 und leert result.conflicts.

    Erweitert das uebergebene result und gibt es zurueck (die Zaehler aus
    Phase 1 - copied/skipped/verified - bleiben erhalten).
    """
    target = result.target_dir
    if target is None:
        return result

    conflicts = list(result.conflicts)
    result.conflicts = []                 # werden jetzt aufgeloest
    progress.phase = "resolve"
    written: list[tuple[str, Path]] = []  # (Quellname, Zielpfad)

    for index, conflict in enumerate(conflicts):
        progress.current_file = conflict.name
        progress.resolved_files = index
        src = photo_dir / conflict.name
        try:
            if conflict.decision == "overwrite":
                dst = target / conflict.name
                shutil.copy2(src, dst)
                result.overwritten += 1
                result.log_actions.append(f"überschrieben: {conflict.name}")
                written.append((conflict.name, dst))
            else:
                # "rename" ist Default und Fallback fuer jede unbekannte
                # Entscheidung - im Zweifel die nicht-destruktive Variante.
                dst = next_free_rename(target, conflict.name)
                shutil.copy2(src, dst)
                result.renamed += 1
                result.log_actions.append(f"umbenannt: {conflict.name} -> {dst.name}")
                written.append((conflict.name, dst))
        except OSError as exc:
            result.errors.append(
                f"Konfliktauflösung fehlgeschlagen ({conflict.name}): {exc}"
            )

    progress.resolved_files = len(conflicts)

    # -- Verifikation der frisch geschriebenen Dateien ---------------------
    if verify and written:
        progress.phase = "verify"
        for index, (srcname, dst) in enumerate(written):
            src = photo_dir / srcname
            progress.current_file = srcname
            progress.verified_files = index
            try:
                if _sha256(src) == _sha256(dst):
                    result.verified += 1
                else:
                    result.failed_verify.append(dst.name)
            except OSError as exc:
                result.errors.append(f"Prüfung fehlgeschlagen ({dst.name}): {exc}")

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
