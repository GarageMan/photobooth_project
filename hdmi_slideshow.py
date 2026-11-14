"""
hdmi_slideshow.py
==================
Sprint 14: eigenstaendiger Subprozess fuer die Galerie-Wiedergabe auf dem
per HDMI angeschlossenen Beamer (siehe hdmi_output_service.py fuer die
Begruendung, warum ein eigener Prozess statt eines zweiten pygame-
Fensters im Hauptprozess der Fotobox).

Wird von hdmi_output_service.HdmiOutputService.start_slideshow() gestartet
(nicht direkt vom Nutzer/Admin aufgerufen). Kann aber auch von Hand zum
Testen gestartet werden:

    python3 hdmi_slideshow.py --connector HDMI-1-1 --geometry 1920x1080+1280+0 --photo-dir data/photos

Darstellung: weiches Ueberblenden (1 s) statt Flug-Animation - die Flug-
Animation ruckelte auf dem Pi (Nutzerrueckmeldung Live-Test). Die
Bildauswahl pro Slot bleibt identisch zur Standby-Galerie.

Zufaelligkeit: nutzt exakt dieselbe Funktion (attract_slideshow.
compute_attract_slot) wie renderer.Renderer._draw_attract_gallery auf dem
Touch-Display - beide Wiedergaben zeigen dadurch nachweisbar dasselbe
Muster (Auswahl, Flugrichtung, Zeitpunkt), wie vom Nutzer gefordert
("genau so zufaellig verlaufen wie die aktuell existierende Galerie-
Wiedergabe im Standby der Fotobox").

Live-Fotos: die Fotoliste wird bei JEDEM Slot-Wechsel frisch von der
Festplatte gelesen (GalleryService.list_photos(), gleiche Quelle wie die
Haupt-App) - ein waehrend der Beamer-Wiedergabe an der Fotobox
aufgenommenes Foto erscheint dadurch spaetestens im naechsten Slot
(Standard-Slot-Dauer: 5 Sekunden, siehe attract_slideshow.
DEFAULT_SLOT_SECONDS), ohne dass dieser Prozess neu gestartet werden
muss.

Fensterplatzierung (X11, Pi mit lightdm/Xorg/openbox): hdmi_output_service
uebergibt per --geometry die Position/Groesse des HDMI-Bildschirms im
erweiterten Desktop (z.B. 1920x1080+1280+0); dieses Skript setzt
SDL_VIDEO_WINDOW_POS und oeffnet ein rahmenloses Fenster genau dort. Ohne
--geometry (z.B. Handstart) faellt es auf ein normales Vollbild zurueck.
Ob openbox das Fenster exakt dort belaesst, ist im Sandkasten ohne echte
zwei Bildschirme NICHT pruefbar - Sichtpruefung auf echter Hardware
erforderlich (siehe Sprint-14-Doku-Notiz).

Musik-Uebertragung: bewusst NICHT implementiert - der Nutzer war sich in
der Anforderung selbst unsicher, ob das ueberhaupt sinnvoll ist ("dazu
wuerde ich entsprechende MP3-Dateien in einen neuen Ordner ablegen - das
ergibt aber vielleicht keinen Sinn, da es im Regelfall auf jeder
Veranstaltung eher laut als leise sein wird"). config.HdmiConfig.music_dir
ist als Platzhalter fuer eine spaetere Etappe bereits angelegt, wird hier
aber noch nicht gelesen.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

import pygame

from attract_slideshow import compute_attract_slot
from gallery_service import GalleryService

# Fenstertitel - Ansatzpunkt fuer eine ggf. noetige Fensterregel des
# Fenstermanagers (openbox rc.xml <applications>), falls das Fenster nicht
# von selbst an der per --geometry gesetzten Position bleibt.
WINDOW_TITLE = "Fotobox-Beamer"

# Wie oft die Fotoliste frisch von der Platte gelesen wird (Sekunden) -
# an DEFAULT_SLOT_SECONDS gekoppelt: neue Fotos sollen spaetestens im
# naechsten Slot auftauchen, ein haeufigeres Nachlesen brächte keinen
# sichtbaren Vorteil, nur zusaetzliche Festplattenzugriffe.
_RESCAN_INTERVAL_SECONDS = 5.0

# Dauer der Ueberblendung und Anzahl zwischengespeicherter, bereits auf
# Bildschirmgroesse skalierter Bilder (je ~8 MB bei 1920x1080).
_FADE_SECONDS = 1.0
_CACHE_SIZE = 4


def _load_cover_surface(
    path: str, size: tuple[int, int], *, edge_colored_bars: bool = False,
) -> pygame.Surface | None:
    """"Contain"-Skalierung (ganzes Bild sichtbar, Balken statt Beschnitt) -
    gleiches Prinzip wie renderer.Renderer._get_thumbnail_surface, hier
    aber mit pygame-Bordmitteln statt Pillow (dieses Skript hat keine
    Renderer-Instanz und damit keinen Zugriff auf deren Bildcache).

    NEU (Sprint 14, Standbild): edge_colored_bars=True faerbt die Balken in
    der Randfarbe des Bildes statt dunkelgrau - das Standbild ist 16:9
    (1920x1080), ein Beamer mit 16:10 (1280x800) oder 4:3 haette sonst
    dunkle Balken oben/unten auf einem hellen Bild. Fuer Galerie-Fotos
    bleibt es bei den neutralen dunklen Balken."""
    target_w, target_h = size
    try:
        raw = pygame.image.load(path).convert()
    except (pygame.error, FileNotFoundError):
        return None
    img_w, img_h = raw.get_size()
    if img_w <= 0 or img_h <= 0:
        return None
    scale = min(target_w / img_w, target_h / img_h)
    scaled_w, scaled_h = max(1, round(img_w * scale)), max(1, round(img_h * scale))
    scaled = pygame.transform.smoothscale(raw, (scaled_w, scaled_h))
    canvas = pygame.Surface(size)
    canvas.fill((25, 25, 30))
    offset_x, offset_y = (target_w - scaled_w) // 2, (target_h - scaled_h) // 2
    if edge_colored_bars:
        # Balken oben/unten bzw. links/rechts jeweils in der Farbe des
        # angrenzenden Bildrands (Mitte der Kante) - passt bei einem
        # Verlauf (hell oben, creme unten) zu beiden Seiten.
        if offset_y > 0:
            canvas.fill(raw.get_at((img_w // 2, 0)), pygame.Rect(0, 0, target_w, offset_y))
            canvas.fill(raw.get_at((img_w // 2, img_h - 1)),
                        pygame.Rect(0, offset_y + scaled_h, target_w, target_h - offset_y - scaled_h))
        if offset_x > 0:
            canvas.fill(raw.get_at((0, img_h // 2)), pygame.Rect(0, 0, offset_x, target_h))
            canvas.fill(raw.get_at((img_w - 1, img_h // 2)),
                        pygame.Rect(offset_x + scaled_w, 0, target_w - offset_x - scaled_w, target_h))
    canvas.blit(scaled, (offset_x, offset_y))
    return canvas


def _draw_test_card(screen: pygame.Surface, message: str) -> None:
    """Einfaches Testbild fuer `--test-seconds` - grosser Text zentriert
    auf farbigem Grund, keine Fotos noetig (dient dem "ist ueberhaupt ein
    Bild auf dem Beamer zu sehen"-Check)."""
    width, height = screen.get_size()
    screen.fill((0, 90, 70))
    pygame.draw.rect(screen, (255, 255, 255), screen.get_rect(), width=6)
    font = pygame.font.Font(None, 64)
    text_surface = font.render(message, True, (255, 255, 255))
    rect = text_surface.get_rect(center=(width // 2, height // 2))
    screen.blit(text_surface, rect)


def _parse_geometry(text: str | None) -> tuple[int, int, int, int] | None:
    """"1920x1080+1280+0" -> (1920, 1080, 1280, 0); None bei fehlender/
    ungueltiger Angabe (dann Vollbild-Rueckfall wie bisher)."""
    if not text:
        return None
    match = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", text.strip())
    if not match:
        return None
    w, h, x, y = (int(g) for g in match.groups())
    return w, h, x, y


def run(
    connector: str, photo_dir: Path, test_seconds: float | None,
    geometry: tuple[int, int, int, int] | None = None,
    wallpaper: Path | None = None,
) -> None:
    del connector  # aktuell nur informativ
    if geometry is not None:
        # Unter X11 genau auf den HDMI-Bildschirm setzen: rahmenloses Fenster
        # in Bildschirmgroesse an dessen Position im erweiterten Desktop.
        # Muss VOR pygame.init()/set_mode() gesetzt sein.
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{geometry[2]},{geometry[3]}"
    pygame.init()
    pygame.display.set_caption(WINDOW_TITLE)
    if geometry is not None:
        screen = pygame.display.set_mode((geometry[0], geometry[1]), pygame.NOFRAME)
    else:
        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    pygame.mouse.set_visible(False)
    clock = pygame.time.Clock()

    gallery = GalleryService(photo_dir)
    photos: list[str] = []
    last_scan = 0.0
    start = time.monotonic()

    # Ueberblenden statt Flug-Animation (Nutzerwunsch: Flug ruckelte auf dem
    # Pi). Jedes Bild wird nur EINMAL geladen/skaliert (Cache); waehrend
    # der Blende nur zwei Blits pro Frame, danach Ruhe ohne Neuzeichnen.
    cache: dict[str, pygame.Surface] = {}
    current: pygame.Surface | None = None
    current_slot = -1
    fade_from: pygame.Surface | None = None
    fade_to: pygame.Surface | None = None
    fade_start = 0.0

    def get_surface(path: str, size: tuple[int, int]) -> pygame.Surface | None:
        key = f"{path}|{size[0]}x{size[1]}"
        if key not in cache:
            surf = _load_cover_surface(path, size)
            if surf is None:
                return None
            if len(cache) >= _CACHE_SIZE:
                cache.pop(next(iter(cache)))
            cache[key] = surf
        return cache[key]

    running = True
    while running:
        now = time.monotonic()
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False

        if wallpaper is not None:
            # Standbild-Modus (Galerie gestoppt): Bild einmal zeichnen,
            # danach nur noch auf Ende warten. Bei Fenster-Neuaufbau
            # (z.B. Expose-Ereignis) sicherheitshalber alle 2 s erneut.
            if now - last_scan >= 2.0 or last_scan == 0.0:
                surf = _load_cover_surface(str(wallpaper), screen.get_size(), edge_colored_bars=True)
                if surf is not None:
                    screen.blit(surf, (0, 0))
                else:
                    screen.fill((15, 15, 20))
                pygame.display.flip()
                last_scan = now
            clock.tick(5)
            continue

        if test_seconds is not None:
            _draw_test_card(screen, "HDMI-Test - Beamer verbunden")
            pygame.display.flip()
            if now - start >= test_seconds:
                running = False
            clock.tick(30)
            continue

        if now - last_scan >= _RESCAN_INTERVAL_SECONDS:
            photos = gallery.list_photos()
            last_scan = now

        width, height = screen.get_size()
        if not photos:
            screen.fill((15, 15, 20))
            pygame.display.flip()
            clock.tick(10)
            continue

        slot = compute_attract_slot(now, len(photos))
        if slot.slot != current_slot and fade_to is None:
            target = get_surface(photos[slot.photo_index % len(photos)], (width, height))
            current_slot = slot.slot
            if target is not None:
                fade_from = current
                fade_to = target
                fade_start = time.monotonic()

        if fade_to is not None:
            progress = min(1.0, (time.monotonic() - fade_start) / _FADE_SECONDS)
            if fade_from is None:
                screen.fill((0, 0, 0))
            else:
                screen.blit(fade_from, (0, 0))
            fade_to.set_alpha(round(255 * progress))
            screen.blit(fade_to, (0, 0))
            pygame.display.flip()
            if progress >= 1.0:
                fade_to.set_alpha(255)
                current = fade_to
                fade_from = fade_to = None
                # Naechstes Bild vorladen, solange das Bild ohnehin still steht.
                get_surface(photos[(slot.slot + 1) % len(photos)], (width, height))
            clock.tick(30)
        else:
            clock.tick(10)

    pygame.quit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fotobox-Galerie-Wiedergabe auf dem HDMI-Beamer.")
    parser.add_argument("--connector", required=True, help="Ausgabename laut xrandr, z.B. HDMI-1-1")
    parser.add_argument(
        "--geometry", default=None,
        help="Zielflaeche im erweiterten Desktop als BxH+X+Y (z.B. 1920x1080+1280+0)",
    )
    parser.add_argument("--photo-dir", required=True, type=Path, help="Foto-Verzeichnis (wie config.photo_dir)")
    parser.add_argument(
        "--test-seconds", type=float, default=None,
        help="Statt der Galerie ein einfaches Testbild fuer N Sekunden zeigen, dann beenden.",
    )
    parser.add_argument(
        "--wallpaper", type=Path, default=None,
        help="Statt der Galerie ein Standbild (z.B. hdmi_monitor_wallpaper.png) zeigen.",
    )
    args = parser.parse_args(argv)
    run(args.connector, args.photo_dir, args.test_seconds, _parse_geometry(args.geometry), args.wallpaper)
    return 0


if __name__ == "__main__":
    sys.exit(main())
