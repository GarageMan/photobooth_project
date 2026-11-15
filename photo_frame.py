"""Bilderrahmen fuer Fotos (Sprint 15, Weihnachtsbasar Holler Hof).

Pro Veranstaltung schaltbar (event_config.json "frame_enabled", Service-Menue
"Veranstaltungsdaten" -> Kachel "Bilderrahmen"). Ist der Rahmen an, werden
nach jeder Aufnahme ZWEI Versionen gespeichert:

- data/photos/<name>.jpg               mit Rahmen + Logo -> Galerie, HDMI,
                                        Attract-Modus, QR-Download, Social-
                                        Media-Freigabe (alles wie bisher)
- data/rahmenlos/rahmenlos_<name>.jpg  unbearbeitetes Original - liegt
                                        bewusst AUSSERHALB von data/photos,
                                        damit es in keiner Fotoliste
                                        auftaucht; wird beim USB-Export in
                                        den Unterordner "rahmenlos" kopiert
                                        und beim Loeschen mitgeloescht

Das Overlay (assets/rahmen_weihnachten_b1.png, 6000x4000 RGBA, Entwurf B1:
Goldrahmen + Schneeflocken + Holler-Hof-Logo unten rechts) wird nur dort
eingerechnet, wo es tatsaechlich etwas zeichnet (Kacheln mit Alpha > 0) -
spart auf dem Pi Speicher und Zeit gegenueber einem Komposit ueber das
ganze 24-MP-Bild. Passt die Fotogroesse nicht (Kamera-Bildgroesse M/S),
wird das Overlay einmalig auf die Fotogroesse skaliert und gecacht.

Pillow-only, kein pygame - offline testbar (test_photo_frame.py).
"""
from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

from PIL import Image

FRAME_FILENAME = "rahmen_weihnachten_b1.png"
UNFRAMED_PREFIX = "rahmenlos_"
UNFRAMED_SUBFOLDER = "rahmenlos"   # Unterordner im USB-Export

_TILE = 256


def unframed_name(photo_name: str) -> str:
    return f"{UNFRAMED_PREFIX}{photo_name}"


def delete_unframed_copy(photo_name: str, unframed_dir: Path) -> bool:
    """Loescht das zugehoerige rahmenlose Original (falls vorhanden)."""
    path = unframed_dir / unframed_name(photo_name)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        print(f"[Rahmen] Rahmenloses Original konnte nicht geloescht werden: {exc}")
        return False


def list_unframed(unframed_dir: Path) -> list[Path]:
    try:
        return sorted(
            p for p in unframed_dir.iterdir()
            if p.is_file() and p.name.startswith(UNFRAMED_PREFIX) and p.suffix.lower() in (".jpg", ".jpeg")
        )
    except OSError:
        return []


class FrameCompositor:
    """Rechnet das Rahmen-Overlay in Fotos ein (thread-sicher, gecacht)."""

    def __init__(self, overlay_path: Path) -> None:
        self.overlay_path = Path(overlay_path)
        self._lock = threading.Lock()
        self._tiles: dict[tuple[int, int], list[tuple[tuple[int, int], Image.Image]]] = {}

    def available(self) -> bool:
        return self.overlay_path.is_file()

    def _tiles_for(self, size: tuple[int, int]) -> list[tuple[tuple[int, int], Image.Image]]:
        with self._lock:
            cached = self._tiles.get(size)
            if cached is not None:
                return cached
            with Image.open(self.overlay_path) as raw:
                overlay = raw.convert("RGBA")
            if overlay.size != size:
                overlay = overlay.resize(size, Image.LANCZOS)
            alpha = overlay.getchannel("A")
            tiles: list[tuple[tuple[int, int], Image.Image]] = []
            width, height = size
            for y in range(0, height, _TILE):
                for x in range(0, width, _TILE):
                    box = (x, y, min(x + _TILE, width), min(y + _TILE, height))
                    bbox = alpha.crop(box).getbbox()
                    if bbox is None:
                        continue
                    sub = (box[0] + bbox[0], box[1] + bbox[1], box[0] + bbox[2], box[1] + bbox[3])
                    tiles.append(((sub[0], sub[1]), overlay.crop(sub)))
            self._tiles[size] = tiles
            return tiles

    def apply(self, photo_path: Path, unframed_dir: Path) -> tuple[bool, str]:
        """Sichert das Original als rahmenlos_<name> und ersetzt die Datei
        durch die Version mit Rahmen. Nie eine Exception nach aussen; bei
        einem Fehler bleibt das Foto unveraendert (ohne Rahmen) erhalten."""
        photo_path = Path(photo_path)
        if not self.available():
            return False, f"Rahmen-Datei fehlt: {self.overlay_path.name}"
        unframed = unframed_dir / unframed_name(photo_path.name)
        tmp_unframed = unframed.with_name(unframed.name + ".tmp")
        tmp_framed = photo_path.with_name(photo_path.name + ".rahmen.tmp")
        try:
            unframed_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(photo_path, tmp_unframed)
            os.replace(tmp_unframed, unframed)
            with Image.open(photo_path) as img:
                img.load()
                exif = img.info.get("exif")
                icc = img.info.get("icc_profile")
                is_jpeg = img.format == "JPEG"
                if img.mode != "RGB":
                    img = img.convert("RGB")
                for (x, y), tile in self._tiles_for(img.size):
                    img.paste(tile, (x, y), tile)
                params: dict = {}
                if exif:
                    params["exif"] = exif
                if icc:
                    params["icc_profile"] = icc
                try:
                    # Kamera-Quantisierung beibehalten (gleiche Qualitaet/
                    # Dateigroesse wie das Original).
                    img.save(tmp_framed, "JPEG", quality="keep" if is_jpeg else 95, **params)
                except (ValueError, OSError):
                    img.save(tmp_framed, "JPEG", quality=95, **params)
            os.replace(tmp_framed, photo_path)
            return True, f"Rahmen eingerechnet, Original: {unframed.name}"
        except Exception as exc:  # Sicherheitsnetz: Aufnahme darf nie verloren gehen
            for leftover in (tmp_unframed, tmp_framed):
                try:
                    leftover.unlink()
                except OSError:
                    pass
            return False, f"Rahmen konnte nicht eingerechnet werden: {exc}"
