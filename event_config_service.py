"""
event_config_service.py
========================
Reine Logik fuer den Admin-Screen "Veranstaltungsdaten": Speichern der
Event-Konfiguration (event_config.json) sowie Suche/Kopie eines
Wallpaper-Bilds von einem USB-Stick.

Bewusst OHNE Abhaengigkeit zu pygame, config oder app - genau wie
admin_usb_service.py/admin_usb_export.py, damit diese Logik offline und
ohne Hardware testbar bleibt (siehe test_event_config_service.py).

Kein Aufruf hier wirft jemals eine Exception nach aussen - jede Funktion
faengt alle erwartbaren Fehler (fehlende Datei, kein Schreibzugriff, volle
Platte, kaputter Stick waehrend des Kopierens) ab und liefert stattdessen
ein Ergebnis-Tupel mit einer fuer Lutz verstaendlichen Meldung zurueck.
Das ist wichtig, weil app.py diese Funktionen teils aus einem
Hintergrund-Thread heraus aufruft (siehe _wallpaper_start_list) - eine
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

# Zwischenablage-Dateiname fuer ein per Auswahlliste
# gepicktes, aber noch NICHT uebernommenes Wallpaper (siehe
# promote_pending_wallpaper/discard_pending_wallpaper) - liegt im selben
# Verzeichnis wie das echte "hauptmenu_wallpaper.png" (assets_dir).
WALLPAPER_PENDING_FILENAME = "hauptmenu_wallpaper.pending.png"

# NEU (Sprint 14, Nutzer-Feedback 2026-11-14): Gegenstueck fuer das Standbild
# des HDMI-Monitors/Beamers (echtes Ziel: hdmi_monitor_wallpaper.png, siehe
# event_wallpaper_reset.HDMI_WALLPAPER) - gleiches Zwischenablage-Prinzip.
HDMI_WALLPAPER_PENDING_FILENAME = "hdmi_monitor_wallpaper.pending.png"

# Groesste Ausgabegroesse fuer das HDMI-Standbild. Groessere Bilder (z.B.
# Handyfotos mit 4000x3000) werden proportional verkleinert - spart Speicher
# und Ladezeit; die Beamer-Wiedergabe skaliert ohnehin auf das jeweilige
# Geraet (hdmi_slideshow._load_cover_surface).
HDMI_WALLPAPER_MAX_SIZE = (1920, 1080)

# Werte fuer die "Standardwerte"-Taste auf dem
# Veranstaltungsdaten-Screen (fuellt den gerade in Bearbeitung befindlichen
# Entwurf, OHNE selbst zu speichern - "Speichern"/"Abbrechen" bleiben wie
# gewohnt zustaendig). Bewusst dieselben Werte wie in
# event_config_example.json (die Vorlage fuer eine frische Installation),
# NICHT die Fallback-Konstanten aus config.py - deren WLAN-Passwort-
# Platzhalter (_PLACEHOLDER = "BITTE_local_secrets.py_ANLEGEN") stammt noch
# aus der Zeit vor der Veranstaltungsdaten-Umstellung und waere hier
# irrefuehrend.
DEFAULT_EVENT_VALUES: dict = {
    "title": "Fotobox",
    "prefix": "foto_",
    "wifi_ssid": "Fotobox_Gast",
    "wifi_password": "BITTE_ANPASSEN",
    # editierbarer Hauptmenue-Willkommenstext -
    # derselbe Text, der bisher fest in state_machine._main_menu_model()
    # stand (siehe config.MAIN_MENU_WELCOME_TEXT fuer den Lade-Fallback).
    "welcome_text": "Lass dich zur Erinnerung an die Veranstaltung fotografieren!",
    "qr_enabled": True,
    "gallery_enabled": True,
}


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


def find_wallpaper_candidates(mountpoint: Path) -> list[Path]:
    """Sucht auf der obersten Ebene von mountpoint (KEINE Rekursion in
    Unterordner) nach Bilddateien und liefert ALLE Treffer, alphabetisch
    sortiert - deterministisch statt von der Dateisystem-Reihenfolge
    abhaengig.

    GEAENDERT (Nutzer-Feedback): ersetzt das fruehere find_wallpaper_on_stick
    (nur EIN, automatisch das alphabetisch erste Bild) - der Admin waehlt
    jetzt selbst aus einer Liste (siehe ADMIN_EVENT_WALLPAPER_PICK).

    Liefert eine leere Liste bei leerem/fehlendem/unlesbarem Verzeichnis -
    nie eine Exception.
    """
    try:
        return sorted(
            p for p in mountpoint.iterdir()
            if p.is_file() and p.suffix.lower() in _WALLPAPER_SUFFIXES
        )
    except OSError:
        return []


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


def import_hdmi_wallpaper(
    source: Path, target: Path, max_size: tuple[int, int] = HDMI_WALLPAPER_MAX_SIZE,
) -> tuple[bool, str]:
    """NEU (Sprint 14, Nutzer-Feedback 2026-11-14): bereitet ein Bild vom
    USB-Stick als Standbild fuer den HDMI-Monitor auf und legt es als ECHTES
    PNG unter target ab (Zwischenablage, siehe
    HDMI_WALLPAPER_PENDING_FILENAME).

    Anders als import_wallpaper() wird das Bild hier mit Pillow geoeffnet:
    - Handy-Fotos tragen ihre Drehung oft nur als EXIF-Angabe - ohne
      exif_transpose() stuende das Bild auf dem Beamer quer.
    - Groessere Bilder werden proportional auf hoechstens max_size
      verkleinert (nie vergroessert), Seitenverhaeltnis bleibt erhalten.
    - Ausgabe immer PNG (auch bei JPG-Quelle) - der Dateiname endet auf
      .png, der Inhalt soll dazu passen.
    Atomar wie import_wallpaper (Temp-Datei + os.replace). Nie eine
    Exception; ungueltige/kaputte Bilddateien liefern (False, Meldung).
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
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError:
        return False, "Bildbearbeitung (Pillow) ist nicht installiert."
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    try:
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGB")
            image.thumbnail(max_size, Image.LANCZOS)
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(tmp_path, format="PNG", optimize=True)
            final_size = image.size
        os.replace(tmp_path, target)
    except (UnidentifiedImageError, ValueError, Image.DecompressionBombError) as exc:
        _remove_quietly(tmp_path)
        return False, f"{source.name} ist kein lesbares Bild ({exc})."
    except OSError as exc:
        _remove_quietly(tmp_path)
        return False, f"Konnte HDMI-Wallpaper nicht uebernehmen: {exc}"
    return True, f"HDMI-Wallpaper übernommen ({source.name}, {final_size[0]}x{final_size[1]})."


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def promote_pending_wallpaper(pending: Path, target: Path) -> tuple[bool, str]:
    """Macht ein zuvor per import_wallpaper() in die Zwischenablage (pending)
    kopiertes Bild zum echten Hauptmenue-Wallpaper (target) - ein simples
    os.replace(), da pending bereits eine vollstaendige, ueberprueft kleine
    Datei im selben Verzeichnis ist (kein erneutes .tmp noetig).

    NEU (Nutzer-Feedback, Bugfix): wird ausschliesslich vom AEUSSEREN
    "Speichern" auf ADMIN_EVENT_SETTINGS aufgerufen (siehe
    app._save_admin_event_settings) - erst dadurch wird ein per
    Auswahlliste gepicktes Bild tatsaechlich zum Hauptmenue-Wallpaper, nicht
    schon beim "Speichern" innerhalb der Auswahlliste selbst.

    Faellt pending fehlt (kein Wallpaper zwischengelagert - der Normalfall,
    wenn der Admin die Veranstaltungsdaten ohne Wallpaper-Aenderung
    speichert), ist das kein Fehler: (True, ...). Nie eine Exception.
    """
    if not pending.exists():
        return True, "Kein wartendes Wallpaper vorhanden."
    try:
        os.replace(pending, target)
    except OSError as exc:
        return False, f"Konnte Wallpaper nicht uebernehmen: {exc}"
    return True, "Wallpaper übernommen."


def discard_pending_wallpaper(pending: Path) -> None:
    """Loescht ein zwischengelagertes, aber verworfenes Wallpaper (Admin hat
    "Abbrechen" auf ADMIN_EVENT_SETTINGS gedrueckt) - best effort, wirft nie
    (gleiche "nie eine Exception nach aussen"-Regel wie der Rest dieses
    Moduls). Sicher als No-Op aufrufbar, auch wenn nie etwas zwischengelagert
    wurde."""
    try:
        pending.unlink(missing_ok=True)
    except OSError:
        pass
