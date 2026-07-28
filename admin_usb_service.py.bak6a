"""
admin_usb_service.py
=====================
USB-Stick-Handling fuer den Service-Menue-Punkt "Bilder auf USB-Stick"
(Etappe 4a: erkennen, einbinden, Kapazitaet und freien Platz pruefen,
sauber wieder aushaengen - das Kopieren selbst folgt in Etappe 4b).

Bewusst OHNE Abhaengigkeit zu pygame, config oder app_with_hw: alle
Eingaben kommen als Pfade/Zahlen herein, damit die Logik offline und
ohne Hardware testbar bleibt (siehe test_admin_usb_service.py).

Sicherheit:
  - Eingehaengt wird mit nosuid,nodev,noexec. Ein fremder Stick, den ein
    Gast oder Kunde ansteckt, darf auf der Fotobox keine ausfuehrbaren
    Dateien oder Geraetedateien einschleusen koennen.
  - Systemdatentraeger (mmcblk*, also die SD-Karte des Pi selbst) werden
    grundsaetzlich ausgefiltert, unabhaengig davon, was lsblk meldet.
    Ein Bedienfehler darf niemals das Wurzeldateisystem treffen.

Berechtigungen: Die App laeuft als root (siehe app_with_hw._power_off),
daher genuegen die direkten mount/umount-Aufrufe - die sudoers-Regel muss
dafuer NICHT erweitert werden.

Voraussetzung fuer moderne Sticks:
    sudo apt install exfatprogs ntfs-3g -y
Ohne diese Pakete schlaegt das Einbinden von exFAT-/NTFS-Sticks fehl.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


# Wohin eingehaengt wird, wenn der Stick nicht bereits vom Desktop
# automatisch eingebunden wurde.
DEFAULT_MOUNTPOINT = Path("/mnt/fotobox-usb")

# Mount-Optionen - siehe Sicherheitshinweis im Modul-Docstring.
_MOUNT_OPTIONS = "nosuid,nodev,noexec"

# Zeichensatz-Optionen je Dateisystem. OHNE diese schlaegt das Anlegen von
# Dateien mit Umlauten auf FAT-Datentraegern fehl - genau das ist im
# Praxistest passiert ("Standort Koeln_...png" wurde nicht exportiert).
# udisks2 setzt beim Automount selbst iocharset=utf8; beim eigenen Mount
# muss man es explizit angeben.
# ntfs-3g und ext4 brauchen nichts davon - sie speichern Namen ohnehin
# als UTF-8 und wuerden die Optionen mit einem Fehler ablehnen.
_CHARSET_OPTIONS = {
    "vfat": "iocharset=utf8,utf8=1",
    "exfat": "iocharset=utf8",
}

# Endungen, die exportiert werden - identisch zu gallery_service.list_photos().
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

# Aufschlag auf den reinen Nettobedarf: Cluster-Verschnitt und
# Verzeichniseintraege. Lieber etwas zu viel verlangen als mitten im
# Kopiervorgang vollzulaufen.
_SIZE_MARGIN_FACTOR = 1.10
_SIZE_MARGIN_BYTES = 4 * 1024 * 1024

_CMD_TIMEOUT_SEC = 30.0


@dataclass(frozen=True)
class UsbPartition:
    """Eine einbindbare Partition auf einem Wechseldatentraeger."""
    device: str                 # z.B. "/dev/sda1"
    label: str                  # Datentraegerbezeichnung, ggf. leer
    fstype: str                 # "vfat", "exfat", "ntfs", ...
    size_bytes: int             # Gesamtkapazitaet der Partition
    mountpoint: str | None      # bereits eingehaengt? (Desktop-Automount)

    def display_name(self) -> str:
        name = self.label.strip() or self.device
        return f"{name} ({format_bytes(self.size_bytes)}, {self.fstype or 'unbekannt'})"


@dataclass
class MountedStick:
    """Ergebnis eines erfolgreichen Einbindens."""
    partition: UsbPartition
    mountpoint: Path
    mounted_by_us: bool         # False = war schon eingehaengt (Automount)


def format_bytes(num_bytes: float) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0:
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} PB"


# ------------------------------------------------------------------------------
# Platzbedarf
# ------------------------------------------------------------------------------

def required_export_bytes(
    photo_dir: Path,
    excluded_filenames: frozenset[str] | set[str] = frozenset(),
) -> tuple[int, int, int]:
    """Ermittelt (Anzahl Dateien, Nettobytes, Bruttobedarf mit Aufschlag).

    Exportiert wird ausschliesslich data/photos/ - data/web/ enthaelt nur
    Kopien fuer den QR-Download und gehoert nicht auf den Stick.
    """
    excluded = {name.lower() for name in excluded_filenames}
    count = 0
    net = 0
    if photo_dir.exists():
        for path in photo_dir.iterdir():
            if not path.is_file():
                continue
            if path.suffix.lower() not in _IMAGE_SUFFIXES:
                continue
            if path.name.lower() in excluded:
                continue
            try:
                net += path.stat().st_size
            except OSError:
                continue
            count += 1
    gross = int(net * _SIZE_MARGIN_FACTOR) + _SIZE_MARGIN_BYTES if count else 0
    return count, net, gross


# ------------------------------------------------------------------------------
# Erkennung
# ------------------------------------------------------------------------------

def _parse_lsblk(data: dict) -> list[UsbPartition]:
    """Wandelt die lsblk-JSON-Ausgabe in eine Liste einbindbarer Partitionen.

    Als eigene, reine Funktion herausgezogen, damit die Filterlogik ohne
    echten USB-Stick testbar ist (siehe test_admin_usb_service.py).
    """
    found: list[UsbPartition] = []

    def is_removable(node: dict) -> bool:
        # lsblk liefert je nach Version bool oder "0"/"1".
        for key in ("rm", "hotplug"):
            value = node.get(key)
            if value in (True, 1, "1"):
                return True
        # Zusaetzlich der Anschlusstyp: USB-Festplatten und manche Sticks
        # melden rm=0 UND hotplug=0, sind aber trotzdem Wechselmedien.
        return (node.get("tran") or "").lower() == "usb"

    def walk(node: dict, parent_removable: bool) -> None:
        removable = parent_removable or is_removable(node)
        node_type = (node.get("type") or "").lower()
        path = node.get("path") or ""

        # Sicherheitsnetz: die SD-Karte des Pi selbst ist tabu, egal was
        # lsblk ueber "removable" meldet. Das ist KEINE theoretische
        # Vorsichtsmassnahme - auf diesem Pi meldet mmcblk0 tatsaechlich
        # hotplug=true (bei rm=false). Ohne diesen Filter wuerden
        # /boot/firmware und / als Exportziel angeboten.
        if path.startswith("/dev/mmcblk"):
            return

        if node_type == "part" and removable:
            fstype = (node.get("fstype") or "").lower()
            # Partitionen ohne erkanntes Dateisystem (z.B. Swap-Reste oder
            # unformatierte Bereiche) sind nicht einbindbar.
            if fstype:
                try:
                    size = int(node.get("size") or 0)
                except (TypeError, ValueError):
                    size = 0
                found.append(UsbPartition(
                    device=path,
                    label=(node.get("label") or "").strip(),
                    fstype=fstype,
                    size_bytes=size,
                    mountpoint=node.get("mountpoint") or None,
                ))

        for child in node.get("children") or ():
            walk(child, removable)

    for device in data.get("blockdevices") or ():
        walk(device, False)
    return found


# Dateisysteme, auf die sich grundsaetzlich nicht schreiben laesst.
# Praxisfall: ein bootfaehiger Linux-Installationsstick wird als
# iso9660 eingebunden (read-only) und haette bei naiver Auswahl das
# eigentliche Ziel verdeckt.
_UNWRITABLE_FSTYPES = {"iso9660", "udf", "squashfs"}


def pick_best_partition(
    partitions: list[UsbPartition],
    required_bytes: int = 0,
) -> UsbPartition | None:
    """Waehlt aus mehreren Partitionen die sinnvollste zum Beschreiben aus.

    Warum nicht einfach die erste: ein bootfaehiger Stick bringt typisch
    eine grosse read-only-ISO-Partition UND eine winzige EFI-Partition
    mit. Die erste waere schreibgeschuetzt, die zweite viel zu klein.

    Reihenfolge: beschreibbares Dateisystem -> passt der Platzbedarf ->
    groesste. Passt keine, wird trotzdem die groesste zurueckgegeben,
    damit die Pruefung eine aussagekraeftige "zu klein"-Meldung erzeugen
    kann statt wortlos nichts zu tun.

    Gibt None zurueck, wenn ausschliesslich read-only-Datentraeger
    angeschlossen sind.
    """
    candidates = [p for p in partitions if p.fstype not in _UNWRITABLE_FSTYPES]
    if not candidates:
        return None
    fitting = [p for p in candidates if p.size_bytes >= required_bytes]
    pool = fitting or candidates
    return max(pool, key=lambda p: p.size_bytes)


def find_usb_partitions() -> list[UsbPartition]:
    """Alle aktuell angeschlossenen, einbindbaren Wechseldatentraeger.

    Gibt im Fehlerfall eine leere Liste zurueck statt zu werfen - ein
    fehlendes lsblk soll den Bildschirm nicht abstuerzen lassen, sondern
    schlicht wie "kein Stick gefunden" wirken.
    """
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-b", "-o",
             "NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,RM,HOTPLUG,MOUNTPOINT,TRAN"],
            capture_output=True, text=True, timeout=_CMD_TIMEOUT_SEC,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return _parse_lsblk(data)


# ------------------------------------------------------------------------------
# Einbinden / Aushaengen
# ------------------------------------------------------------------------------

def mount_partition(
    partition: UsbPartition,
    mountpoint: Path = DEFAULT_MOUNTPOINT,
) -> tuple[MountedStick | None, str]:
    """Bindet die Partition ein. Gibt (MountedStick|None, Meldung) zurueck.

    War der Stick bereits vom Desktop automatisch eingebunden, wird dieser
    Einhaengepunkt uebernommen statt ein zweites Mal zu mounten.
    """
    if partition.mountpoint:
        existing = Path(partition.mountpoint)
        if existing.is_dir():
            return (
                MountedStick(partition=partition, mountpoint=existing, mounted_by_us=False),
                f"Stick war bereits eingebunden ({existing}).",
            )

    try:
        mountpoint.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return None, f"Einhängepunkt konnte nicht angelegt werden: {exc}"

    # Reste eines frueheren Laufs entfernen, sonst schlaegt mount fehl.
    if is_mounted(mountpoint):
        unmount(mountpoint)

    # Erst mit Zeichensatz-Optionen versuchen, bei Fehlschlag ohne. So
    # profitieren FAT-Sticks von korrekten Umlauten, ohne dass ein
    # Dateisystem, das die Optionen nicht kennt, gar nicht mehr einbindbar
    # waere.
    charset = _CHARSET_OPTIONS.get(partition.fstype)
    attempts = []
    if charset:
        attempts.append(f"{_MOUNT_OPTIONS},{charset}")
    attempts.append(_MOUNT_OPTIONS)

    result = None
    for options in attempts:
        try:
            result = subprocess.run(
                ["mount", "-o", options, partition.device, str(mountpoint)],
                capture_output=True, text=True, timeout=_CMD_TIMEOUT_SEC,
            )
        except FileNotFoundError:
            return None, "mount-Befehl nicht gefunden."
        except subprocess.TimeoutExpired:
            return None, "Zeitüberschreitung beim Einbinden."
        if result.returncode == 0:
            break

    if result is not None and result.returncode != 0:
        message = (result.stderr or result.stdout).strip().splitlines()
        detail = message[0] if message else "unbekannter Fehler"
        hint = ""
        if partition.fstype in {"exfat", "ntfs"}:
            # Der mit Abstand haeufigste Grund bei modernen Sticks.
            hint = " (fehlt exfatprogs / ntfs-3g?)"
        return None, f"Einbinden fehlgeschlagen: {detail[:80]}{hint}"

    return (
        MountedStick(partition=partition, mountpoint=mountpoint, mounted_by_us=True),
        "Stick eingebunden.",
    )


def is_mounted(mountpoint: Path) -> bool:
    try:
        result = subprocess.run(
            ["mountpoint", "-q", str(mountpoint)],
            capture_output=True, timeout=_CMD_TIMEOUT_SEC,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def unmount(mountpoint: Path) -> tuple[bool, str]:
    """Puffer schreiben und aushaengen.

    Das vorgeschaltete sync ist der wichtigste Schritt ueberhaupt: ohne
    ihn koennen Daten noch im Schreibpuffer stehen, waehrend der Nutzer
    den Stick bereits abzieht - genau so gehen Dateien verloren.
    """
    try:
        subprocess.run(["sync"], capture_output=True, timeout=_CMD_TIMEOUT_SEC)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    try:
        result = subprocess.run(
            ["umount", str(mountpoint)],
            capture_output=True, text=True, timeout=_CMD_TIMEOUT_SEC,
        )
    except FileNotFoundError:
        return False, "umount-Befehl nicht gefunden."
    except subprocess.TimeoutExpired:
        return False, "Zeitüberschreitung beim Aushängen."

    if result.returncode == 0:
        return True, "Stick ausgehängt."
    message = (result.stderr or result.stdout).strip().splitlines()
    detail = message[0] if message else "unbekannter Fehler"
    if "not mounted" in detail.lower():
        return True, "Stick war nicht mehr eingebunden."
    return False, f"Aushängen fehlgeschlagen: {detail[:80]}"


def free_bytes(mountpoint: Path) -> int:
    try:
        return shutil.disk_usage(mountpoint).free
    except OSError:
        return 0


def is_writable(mountpoint: Path) -> bool:
    """Prueft schreibend statt nur die Mount-Optionen zu lesen - ein
    schreibgeschuetzter Stick faellt sonst erst beim ersten Kopieren auf."""
    probe = mountpoint / ".fotobox_schreibtest"
    try:
        probe.write_bytes(b"x")
        probe.unlink()
        return True
    except OSError:
        return False


# ------------------------------------------------------------------------------
# Gesamtpruefung
# ------------------------------------------------------------------------------

@dataclass
class UsbCheckResult:
    ok: bool = False
    stick: MountedStick | None = None
    lines: tuple[str, ...] = ()
    # Unterscheidet die beiden Fehlerarten, weil sie unterschiedlich
    # behandelt werden: eine zu kleine Kapazitaet ist endgueltig (anderer
    # Stick noetig), zu wenig FREIER Platz laesst sich durch Aufraeumen
    # beheben (Etappe 4b bietet dafuer das Leeren des Sticks an).
    too_small: bool = False
    not_enough_free: bool = False


def check_stick_for_export(
    partition: UsbPartition,
    required_bytes: int,
    mountpoint: Path = DEFAULT_MOUNTPOINT,
) -> UsbCheckResult:
    """Bindet ein und prueft Kapazitaet, freien Platz und Schreibbarkeit.

    Bei einem Fehlschlag bleibt der Stick eingehaengt zurueck, damit der
    aufrufende Ablauf ihn kontrolliert wieder aushaengen kann (der Nutzer
    soll ihn nie unangekuendigt abziehen muessen).
    """
    result = UsbCheckResult()
    stick, message = mount_partition(partition, mountpoint)
    if stick is None:
        result.lines = (
            "Der USB-Stick konnte nicht eingebunden werden.",
            message,
        )
        return result

    result.stick = stick
    total = partition.size_bytes
    free = free_bytes(stick.mountpoint)

    if total and total < required_bytes:
        result.too_small = True
        result.lines = (
            "Der USB-Stick ist zu klein.",
            f"Benötigt: {format_bytes(required_bytes)}",
            f"Kapazität des Sticks: {format_bytes(total)}",
            "Bitte einen größeren Stick verwenden.",
        )
        return result

    if not is_writable(stick.mountpoint):
        result.lines = (
            "Auf den USB-Stick kann nicht geschrieben werden.",
            "Möglicherweise ist er schreibgeschützt.",
        )
        return result

    if free < required_bytes:
        result.not_enough_free = True
        result.lines = (
            "Auf dem USB-Stick ist nicht genügend freier Speicher.",
            f"Benötigt: {format_bytes(required_bytes)}",
            f"Frei: {format_bytes(free)} von {format_bytes(total)}",
        )
        return result

    result.ok = True
    result.lines = (
        f"USB-Stick bereit: {partition.display_name()}",
        f"Benötigt: {format_bytes(required_bytes)}",
        f"Frei: {format_bytes(free)} von {format_bytes(total)}",
    )
    return result
