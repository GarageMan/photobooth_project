"""
admin_diagnostics.py
=====================
Reine Diagnosefunktionen fuer den "Status / Diagnose"-Punkt im
Service-Menue (Etappe 4.3). Jede Funktion liefert im Fehlerfall eine
sprechende Fehlerzeile statt eine Ausnahme zu werfen - ein einzelner
nicht verfuegbarer Wert (z.B. Kamera nicht angeschlossen) soll nicht
die gesamte Diagnoseseite zum Absturz bringen.

Bewusst UNABHAENGIG von app importierbar (nur Path/Zahlen als
Parameter), damit die Funktionen isoliert und ohne Pygame-Fenster
getestet werden koennen (siehe test_admin_diagnostics.py).
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


def format_bytes(num_bytes: float) -> str:
    """z.B. 1234567890 -> '1.15 GB'."""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0:
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} PB"


def disk_usage_line(path: Path) -> str:
    try:
        usage = shutil.disk_usage(path)
        free = format_bytes(usage.free)
        total = format_bytes(usage.total)
        percent_used = 100.0 * (usage.total - usage.free) / usage.total if usage.total else 0.0
        return f"Speicherplatz: {free} frei von {total} ({percent_used:.0f}% belegt)"
    except OSError as exc:
        return f"Speicherplatz: konnte nicht ermittelt werden ({exc})"


def photo_count_line(photo_count: int) -> str:
    plural = "Foto" if photo_count == 1 else "Fotos"
    return f"Fotos in der Galerie: {photo_count} {plural}"


def camera_status_line(timeout_seconds: float = 3.0) -> str:
    """Best-effort-Pruefung per 'gphoto2 --auto-detect'. Bewusst NICHT ueber
    den bestehenden camera_lock/capture_service - --auto-detect oeffnet
    keine Aufnahme-Session und blockiert daher keine laufende Vorschau
    (falls doch einmal parallel aktiv)."""
    try:
        result = subprocess.run(
            ["gphoto2", "--auto-detect"],
            capture_output=True, text=True, timeout=timeout_seconds,
        )
    except FileNotFoundError:
        return "Kamera: gphoto2 ist auf diesem System nicht installiert."
    except subprocess.TimeoutExpired:
        return "Kamera: Pruefung hat zu lange gedauert (Zeitueberschreitung)."

    output = result.stdout.lower()
    # --auto-detect gibt bei keiner gefundenen Kamera eine Kopfzeile ohne
    # weitere Modellzeile aus - "usb:" taucht nur bei tatsaechlich
    # gefundenen Geraeten in der Ausgabe auf.
    if "usb:" in output:
        return "Kamera: verbunden"
    return "Kamera: NICHT verbunden"


def download_path_line(photo_url_prefix: str, timeout_seconds: float = 3.0) -> str:
    """Prueft, ob der Foto-Download-Pfad tatsaechlich erreichbar ist -
    exakt der Pfad, ueber den Gaeste per QR-Code ihr Foto abrufen (siehe
    config.network.photo_url_prefix). Bewusst NICHT ueber "localhost"
    geprueft, sondern ueber dieselbe Adresse, die auch die Gaeste
    benutzen wuerden - deckt damit z.B. auch ab, falls nginx aus
    irgendeinem Grund nur auf einer bestimmten Netzwerkschnittstelle
    lauscht statt auf allen.

    Nutzt testbild.png, das bewusst dauerhaft im Fotoverzeichnis liegt und
    nirgends in der App angezeigt wird (siehe gallery_service.
    DEFAULT_EXCLUDED_FILENAMES) - ein fester, garantiert vorhandener
    Testkoerper fuer genau diesen Zweck. HEAD-Anfrage statt GET, um nicht
    unnoetig die Bilddaten selbst zu uebertragen."""
    url = f"{photo_url_prefix}/testbild.png"
    try:
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        return f"Foto-Download-Pfad: NICHT erreichbar (HTTP {exc.code})"
    except urllib.error.URLError as exc:
        return f"Foto-Download-Pfad: NICHT erreichbar ({exc.reason})"
    except OSError as exc:
        return f"Foto-Download-Pfad: NICHT erreichbar ({exc})"

    if status == 200:
        return "Foto-Download-Pfad: erreichbar (testbild.png)"
    return f"Foto-Download-Pfad: unerwarteter Status {status}"


def protected_files_line(
    photo_dir: Path,
    web_dir: Path,
    protected_photo_filenames: tuple[str, ...],
    protected_web_filenames: tuple[str, ...],
) -> str:
    """Prueft, ob alle geschuetzten Dateien (Beispielbilder, Testbild) noch
    an ihrem festen Platz liegen - diese werden zwar vor Loeschen/Export
    geschuetzt (siehe config.protected_filenames), koennten aber trotzdem
    von Hand versehentlich entfernt oder verschoben werden. Meldet fehlende
    Dateien NAMENTLICH, statt nur "irgendetwas fehlt" zu sagen."""
    missing: list[str] = []
    for name in protected_photo_filenames:
        if not (photo_dir / name).exists():
            missing.append(name)
    for name in protected_web_filenames:
        if not (web_dir / name).exists():
            missing.append(name)
    if not missing:
        return "Geschützte Dateien (Beispielbilder/Testbild): alle vorhanden"
    return f"ACHTUNG - fehlende geschützte Dateien: {', '.join(missing)}"


def ip_address_line() -> str:
    """Best-effort lokale IP-Adresse (Fotobox-Netz, ueber Ethernet zum
    TP-Link). Nutzt einen UDP-'Verbindungsversuch' ohne tatsaechlichen
    Traffic - Standardtrick, um die ausgehende Interface-Adresse zu
    ermitteln, ohne Netzwerktools zu parsen."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(1.0)
            sock.connect(("192.168.0.1", 80))
            ip = sock.getsockname()[0]
        return f"IP-Adresse: {ip}"
    except OSError as exc:
        return f"IP-Adresse: konnte nicht ermittelt werden ({exc})"


def uptime_line(app_start_monotonic: float, now_monotonic: float) -> str:
    seconds = max(0.0, now_monotonic - app_start_monotonic)
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"App läuft seit: {hours}h {minutes:02d}min"
    if minutes:
        return f"App läuft seit: {minutes}min {secs:02d}s"
    return f"App läuft seit: {secs}s"


def collect_status_lines(
    photo_dir: Path,
    web_dir: Path,
    photo_count: int,
    app_start_monotonic: float,
    photo_url_prefix: str,
    protected_photo_filenames: tuple[str, ...],
    protected_web_filenames: tuple[str, ...],
) -> tuple[str, ...]:
    """Buendelt alle Diagnosezeilen fuer den Status-Screen. Jede Zeile wird
    unabhaengig ermittelt - ein Fehler bei einer Quelle (z.B. Kamera nicht
    gefunden) unterdrueckt nicht die uebrigen Zeilen."""
    return (
        disk_usage_line(photo_dir),
        photo_count_line(photo_count),
        camera_status_line(),
        download_path_line(photo_url_prefix),
        protected_files_line(photo_dir, web_dir, protected_photo_filenames, protected_web_filenames),
        ip_address_line(),
        uptime_line(app_start_monotonic, time.monotonic()),
    )
