from __future__ import annotations

import math
import random
import time
from collections import OrderedDict
from dataclasses import dataclass

import pygame
from PIL import Image as PILImage

from config import AppConfig
from layout import LayoutRects, build_layout
from models import AppModel
from states import AppState
from admin_menu import ADMIN_MENU_ITEMS, build_admin_rects  # NEU (4.1)


@dataclass
class Renderer:
    config: AppConfig
    screen: pygame.Surface

    def __post_init__(self) -> None:
        self.layout: LayoutRects = build_layout(self.config.screen.width, self.config.screen.height)
        self.font_title = pygame.font.Font(None, 82)
        self.font_body = pygame.font.Font(None, 42)
        # Gleiche Groesse wie font_body, nur fett - fuer Ueberschriften
        # innerhalb laengerer Textblöcke (aktuell: _draw_terms). Bewusst
        # dieselbe Punktgroesse, damit die feste Zeilenhoehe (line_height in
        # _draw_terms/_draw_instructions, aus font_body.get_linesize()
        # berechnet) fuer alle Zeilen gueltig bleibt, unabhaengig davon, ob
        # eine einzelne Zeile fett oder normal gerendert wird.
        self.font_body_bold = pygame.font.Font(None, 42)
        self.font_body_bold.set_bold(True)
        self.font_small = pygame.font.Font(None, 32)
        self.font_button = pygame.font.Font(None, 50)
        # Etwa doppelt so gross wie font_body (42) - ausschliesslich fuer den
        # Willkommenstext im Hauptmenue ("Willkommen an der Fotobox!"),
        # damit dieser auf den ersten Blick auffaellt. Andere Statustexte
        # (die denselben model.ui.status_text-Slot in anderen States nutzen)
        # bleiben bei font_body.
        self.font_status_main_menu = pygame.font.Font(None, 84)
        # Grosse Ziffer fuer den Cinema-Countdown - bewusst proportional zur
        # Bildschirmhoehe (nicht fix), damit sie auf jeder Aufloesung den
        # Kreis dominant ausfuellt statt "verloren" zu wirken.
        self.font_countdown_digit = pygame.font.Font(None, round(self.config.screen.height * 0.5))
        # Zwei getrennte LRU-Caches statt einem unbegrenzten dict: Thumbnails
        # (Galerie-Grid) und Vollbild-Ansichten (Review/Fullscreen/Attract)
        # haben unterschiedliche Groessen und damit unterschiedlichen
        # Speicherbedarf pro Eintrag - je eigenes Limit aus config.gallery,
        # sonst waechst der RAM-Verbrauch bei einem Event mit vielen Fotos
        # unbegrenzt weiter.
        self._thumbnail_cache: OrderedDict[str, pygame.Surface] = OrderedDict()
        self._fullscreen_cache: OrderedDict[str, pygame.Surface] = OrderedDict()
        # Pool aller "bitte_laecheln_01.png" .. "_15.png"-Varianten, einmalig
        # geladen und skaliert (None = noch nicht geladen). Aus diesem Pool
        # wird bei jedem neuen Countdown-Durchlauf zufaellig ein Bild fuer
        # _current_countdown_image gezogen (siehe render()).
        self._countdown_image_pool: list[pygame.Surface] | None = None
        self._current_countdown_image: pygame.Surface | None = None
        self._main_menu_background: pygame.Surface | bool | None = None
        self._boot_background: pygame.Surface | bool | None = None
        self._shutdown_background: pygame.Surface | bool | None = None  # NEU (3.3)
        self.gallery_thumbnail_hitboxes: list[tuple[pygame.Rect, int]] = []
        # Scroll-Position der Anleitung (in Pixeln). Lebt bewusst nur hier im
        # Renderer (reine Anzeige-Angelegenheit), nicht im AppModel/State
        # Machine - aehnlich wie gallery_thumbnail_hitboxes.
        self.instructions_scroll_offset: int = 0
        # Scroll-Position der Nutzungsbedingungen-Ansicht - analog zu
        # instructions_scroll_offset, aber bewusst ein eigenes Feld, damit
        # ein Wechsel zwischen "Anleitung" und "Nutzungsbedingungen" die
        # jeweils andere Scroll-Position nicht zuruecksetzt/vermischt.
        self.terms_scroll_offset: int = 0
        # NEU (6c): Scroll-Position der USB-Konfliktliste - gleiches Prinzip
        # wie instructions_scroll_offset/terms_scroll_offset (reine Anzeige-
        # Angelegenheit, lebt nur hier im Renderer).
        self.usb_conflicts_scroll_offset: int = 0
        # NEU (6c): Trefferflaechen der aktuell sichtbaren Konfliktzeilen -
        # (Rect, Dateiname, "overwrite"|"rename"). Wird bei jedem Aufruf von
        # _draw_admin_usb_conflicts() neu befuellt (Positionen haengen vom
        # Scroll-Offset ab, sind also erst nach dem Zeichnen bekannt) und in
        # app_with_hw._map_click_to_event gegen den Tap geprueft - gleiches
        # Muster wie gallery_thumbnail_hitboxes.
        self.usb_conflict_row_hitboxes: list[tuple[pygame.Rect, str, str]] = []
        self._last_rendered_state: AppState | None = None

    def render(
        self,
        model: AppModel,
        fps: float,
        preview_frame: pygame.Surface | None = None,
        qr_surface: pygame.Surface | None = None,
    ) -> None:
        # Scroll-Position der Anleitung zuruecksetzen, sobald man neu in
        # diesen State wechselt (nicht bei jedem Frame innerhalb des States).
        if model.state == AppState.INSTRUCTIONS and self._last_rendered_state != AppState.INSTRUCTIONS:
            self.instructions_scroll_offset = 0
        if model.state == AppState.TERMS and self._last_rendered_state != AppState.TERMS:
            self.terms_scroll_offset = 0
        # NEU (6c): gleiches Prinzip - jeder neue Konflikt-Screen (z.B. nach
        # einem erneuten Export) beginnt oben, nicht an einer alten Position.
        if model.state == AppState.ADMIN_USB_CONFLICTS and self._last_rendered_state != AppState.ADMIN_USB_CONFLICTS:
            self.usb_conflicts_scroll_offset = 0
        # Neuer Countdown-Durchlauf (State-Wechsel IN COUNTDOWN hinein) -
        # zufaellig ein neues "bitte laecheln"-Bild fuer diesen Durchlauf
        # ziehen, damit es bei jedem Foto wechselt statt immer gleich zu sein.
        if model.state == AppState.COUNTDOWN and self._last_rendered_state != AppState.COUNTDOWN:
            self._select_random_countdown_image()
        self._last_rendered_state = model.state

        self.screen.fill(self._background_color(model.state))

        if model.state == AppState.MAIN_MENU:
            self._draw_main_menu_background()

        if model.state == AppState.BOOT:
            self._draw_boot_background()

        if preview_frame is not None:
            self._draw_preview_frame(preview_frame)

        if model.state == AppState.COUNTDOWN:
            value = model.ui.countdown_value
            if value == 1:
                # Liveview aus, "bitte laecheln" einblenden, keine Text-Ausgabe mehr.
                self._draw_countdown_image()
            elif value in (5, 4, 3, 2):
                # Liveview bleibt sichtbar (s.o.), Cinema-Countdown-Grafik davor.
                self._draw_cinema_countdown(value)

        if model.state == AppState.ATTRACT_GALLERY:
            self._draw_attract_gallery(model)

        if model.state == AppState.REVIEW:
            self._draw_review_photo(model)

        if model.state == AppState.GALLERY_FULLSCREEN:
            self._draw_gallery_fullscreen(model)

        if model.state == AppState.QR_DISPLAY:
            self._draw_qr_code(qr_surface)

        if model.state == AppState.INSTRUCTIONS:
            self._draw_instructions()

        if model.state == AppState.TERMS:
            self._draw_terms()

        if model.state == AppState.PIN_ENTRY:            # NEU (3.3)
            self._draw_pin_entry(model)

        if model.state == AppState.SHUTDOWN_GOODBYE:      # NEU (3.3)
            self._draw_shutdown_goodbye(model)

        if model.state == AppState.ADMIN_STATUS:          # NEU (4.3)
            self._draw_admin_status(model)

        if model.state == AppState.ADMIN_RESTART_PENDING:  # NEU (4.3)
            self._draw_admin_restart_pending(model)

        if model.state == AppState.ADMIN_DELETE_CONFIRM:   # NEU (4.4)
            self._draw_admin_delete_confirm(model)

        if model.state == AppState.ADMIN_DELETE_RUNNING:   # NEU (4.4)
            self._draw_admin_delete_running(model)

        if model.state == AppState.ADMIN_DELETE_DONE:      # NEU (4.4)
            self._draw_admin_delete_done(model)

        if model.state in {                                # NEU (4.6)
            AppState.ADMIN_USB_WAIT, AppState.ADMIN_USB_READY,
            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_REMOVE,
        }:
            self._draw_admin_usb_lines(model)

        if model.state in {AppState.ADMIN_USB_CHECK, AppState.ADMIN_USB_EJECT}:  # NEU (4.6)
            self._draw_admin_usb_busy(model)

        if model.state == AppState.ADMIN_USB_COPY:           # NEU (4.7)
            self._draw_admin_usb_copy(model)

        # NEU (6b/6c): Aufloesungslauf (Phase 2) zeigt denselben Fortschritts-
        # balken wie der Kopierlauf - app_with_hw.py fuellt dieselben UI-
        # Felder (admin_usb_export_progress/admin_usb_progress_fraction),
        # daher genuegt die Wiederverwendung derselben Zeichenfunktion statt
        # einer Kopie.
        if model.state == AppState.ADMIN_USB_RESOLVE:
            self._draw_admin_usb_copy(model)

        if model.state == AppState.ADMIN_USB_CONFLICTS:       # NEU (6c)
            self._draw_admin_usb_conflicts(model)

        if model.state == AppState.ADMIN_USB_EXPORT_DONE:    # NEU (4.7)
            self._draw_admin_usb_lines(model)

        # Bei Ziffer 1 (Liveview aus, "bitte laecheln") soll GAR KEIN Text
        # mehr zu sehen sein - weder Titel noch Statuszeile.
        hide_all_text = model.state == AppState.COUNTDOWN and model.ui.countdown_value == 1

        # Titel wird in der Anleitung, den Nutzungsbedingungen und bei
        # Ziffer 1 bewusst weggelassen (eigene scrollbare Textansichten,
        # die den vollen Bildschirm brauchen).
        text_screens = {
            AppState.INSTRUCTIONS, AppState.TERMS, AppState.PIN_ENTRY, AppState.SHUTDOWN_GOODBYE,
            AppState.ADMIN_MENU, AppState.ADMIN_STATUS, AppState.ADMIN_RESTART_PENDING,  # NEU (4.3)
            AppState.ADMIN_DELETE_CONFIRM, AppState.ADMIN_DELETE_RUNNING,                # NEU (4.4)
            AppState.ADMIN_DELETE_DONE,                                                  # NEU (4.4)
            AppState.ADMIN_USB_WAIT, AppState.ADMIN_USB_CHECK, AppState.ADMIN_USB_READY, # NEU (4.6)
            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_EJECT, AppState.ADMIN_USB_REMOVE,
            AppState.ADMIN_USB_COPY, AppState.ADMIN_USB_EXPORT_DONE,   # NEU (4.7)
            AppState.ADMIN_USB_CONFLICTS, AppState.ADMIN_USB_RESOLVE,  # NEU (6c)
        }

        if model.state not in text_screens and not hide_all_text:
            self._draw_text(self.config.screen.title, self.font_title, (255, 255, 255), (60, 60))
        elif model.state == AppState.ADMIN_MENU:
            # NEU (4.2): statt des Fotobox-Titels an gleicher Position/
            # Schrift/Farbe der Menuename - der Titel ist hier nicht der
            # passende Kontext.
            self._draw_text("Service-Menü", self.font_title, (255, 255, 255), (60, 60))
        elif model.state == AppState.ADMIN_STATUS:
            # NEU (4.3): eigener Titel statt des Fotobox-Titels, wie ADMIN_MENU.
            self._draw_text("Status / Diagnose", self.font_title, (255, 255, 255), (60, 60))
        elif model.state == AppState.ADMIN_DELETE_DONE:
            # NEU (4.4): Ergebnis der Loeschung.
            self._draw_text("Löschen abgeschlossen", self.font_title, (255, 255, 255), (60, 60))
        elif model.state == AppState.ADMIN_USB_EXPORT_DONE:
            # NEU (4.7): eigener Titel statt des generischen status_text.
            self._draw_text("Export abgeschlossen", self.font_title, (255, 255, 255), (60, 60))
        elif model.state in {
            AppState.ADMIN_USB_WAIT, AppState.ADMIN_USB_READY,
            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_REMOVE,
            AppState.ADMIN_USB_CONFLICTS,   # NEU (6c): "Dateien mit abweichendem Inhalt gefunden"
        }:
            # NEU (4.6): der jeweilige Schrittname steht in ui.status_text -
            # eine Ueberschrift fuer alle Screens, kein Sonderfall je Zustand.
            self._draw_text(model.ui.status_text, self.font_title, (255, 255, 255), (60, 60))
        # ADMIN_RESTART_PENDING/ADMIN_USB_RESOLVE zeigen bewusst gar keinen
        # Titel - nur die grosse zentrierte Statuszeile.

        if self.config.features.debug_overlay:
            self._draw_text(f"Zustand: {model.state.name}", self.font_body, (220, 220, 220), (60, 180))

        if model.state not in text_screens and not hide_all_text:
            # Im Hauptmenue liegt der Text auf dem Hintergrundbild - Anthrazit statt
            # dem sonst ueblichen Amber, da Amber auf dem Bild schlecht lesbar war.
            status_color = (40, 40, 45) if model.state == AppState.MAIN_MENU else (255, 220, 120)
            status_font = self.font_status_main_menu if model.state == AppState.MAIN_MENU else self.font_body
            self._draw_text(model.ui.status_text, status_font, status_color, (60, 240))

        if model.ui.error_text and model.state not in text_screens:
            self._draw_text(model.ui.error_text, self.font_body, (255, 120, 120), (60, 320))

        # NEU (Speicherplatz-Alarm): laufender Y-Offset, damit sich der
        # Event-Konfig-Hinweis und die Speicherplatz-Warnung (koennen
        # gleichzeitig zutreffen) nicht gegenseitig ueberdecken, sondern
        # sauber untereinander stehen.
        banner_y = 0
        if model.state == AppState.MAIN_MENU and self.config.needs_event_setup:
            # NEU (Etappe 8, Feedback): dezenter Hinweisstreifen ganz oben -
            # eigene halbtransparente Leiste statt einer weiteren Textzeile
            # im ohnehin schon belegten status_text/error_text-Bereich, und
            # unabhaengig vom Hintergrundbild lesbar. Gedacht fuer
            # Entwickler/neue GitHub-Nutzer, die die Box frisch aufsetzen -
            # verschwindet automatisch, sobald data/event_config.json echte
            # Werte enthaelt. Bewusst kein Alarm-Look (kein Rot, kein
            # Blinken) und keine Bedienungssperre - die Box funktioniert mit
            # Standardwerten technisch einwandfrei, nur eben noch nicht
            # event-spezifisch konfiguriert.
            banner_height = 34
            banner = pygame.Surface((self.config.screen.width, banner_height), pygame.SRCALPHA)
            banner.fill((0, 0, 0, 165))
            self.screen.blit(banner, (0, banner_y))
            self._draw_text(
                "Hinweis: Standardkonfiguration aktiv - data/event_config.json anpassen",
                self.font_small, (255, 210, 120), (16, banner_y + 6),
            )
            banner_y += banner_height

        if model.state == AppState.MAIN_MENU and model.ui.storage_alarm_level == 1:
            # NEU (Speicherplatz-Alarm) Stufe 1: farbiger Hinweistext, wie
            # gewuenscht NUR im Hauptmenue (nicht in GALLERY_EMPTY - dort
            # ist noch kein Foto gemacht worden, das Problem ist dort noch
            # nicht dringend). Kein Blinken, keine Sperre - die greift erst
            # ab Stufe 2 (siehe state_machine.py).
            banner_height = 34
            banner = pygame.Surface((self.config.screen.width, banner_height), pygame.SRCALPHA)
            banner.fill((60, 30, 0, 180))
            self.screen.blit(banner, (0, banner_y))
            self._draw_text(
                f"Speicherplatz wird knapp: noch {model.ui.storage_free_percent:.0f}% frei",
                self.font_small, (255, 180, 80), (16, banner_y + 6),
            )
            banner_y += banner_height

        if model.state == AppState.GALLERY_GRID:
            self._draw_gallery_grid(model)

        if model.state == AppState.GALLERY_EMPTY:            # NEU (Etappe 7)
            self._draw_gallery_empty(model)

        self._draw_buttons(model.state)
        self._draw_footer(model, fps)

        if model.state in {AppState.MAIN_MENU, AppState.GALLERY_EMPTY} and model.ui.storage_alarm_level >= 2:
            # NEU (Speicherplatz-Alarm) Stufe 2: sehr auffaellig, wie
            # gewuenscht - blinkender roter Rahmen um den ganzen Bildschirm
            # plus deutlicher Text. Bewusst NUR auf diesen beiden Screens
            # (dort, wo die Aufnahme-Sperre in state_machine.py tatsaechlich
            # greift), nicht global. Bewusst als LETZTES gezeichnet (nach
            # Buttons/Footer), damit der Alarm garantiert ueber allem
            # anderen liegt und nie versehentlich verdeckt werden kann.
            self._draw_storage_critical_overlay()

        pygame.display.flip()

    def _draw_preview_frame(self, preview_frame: pygame.Surface) -> None:
        """Kamera-Livebild als Hintergrund einblenden, skaliert auf Bildschirmgröße."""
        target_size = (self.config.screen.width, self.config.screen.height)
        if preview_frame.get_size() != target_size:
            preview_frame = pygame.transform.smoothscale(preview_frame, target_size)
        self.screen.blit(preview_frame, (0, 0))

    def _draw_gallery_grid(self, model: AppModel) -> None:
        photos = model.session.photos
        width = self.config.screen.width
        height = self.config.screen.height
        self.gallery_thumbnail_hitboxes: list[tuple[pygame.Rect, int]] = []

        if not photos:
            hint = f"Keine Fotos gefunden in: {self.config.photo_dir}"
            self._draw_text(hint, self.font_body, (200, 200, 200), (60, round(0.30 * height)))
            return

        columns = max(1, self.config.gallery.grid_columns)
        margin = round(0.06 * width)
        gap = round(0.03 * width)
        top = round(0.30 * height)
        bottom = round(0.77 * height)

        available_w = width - 2 * margin - (columns - 1) * gap
        cell_w = max(20, available_w // columns)
        thumb_w, thumb_h = self.config.gallery.thumbnail_size
        cell_h = max(20, round(cell_w * (thumb_h / thumb_w)))

        total_rows = max(1, (len(photos) + columns - 1) // columns)
        scroll_row = min(model.ui.gallery_scroll_offset, max(0, total_rows - 1))
        start_index = scroll_row * columns
        visible_photos = photos[start_index:]

        x, y, col = margin, top, 0
        for offset, path in enumerate(visible_photos):
            if y + cell_h > bottom:
                break
            cell_rect = pygame.Rect(x, y, cell_w, cell_h)
            surface = self._get_thumbnail_surface(path, (cell_w, cell_h))
            if surface is not None:
                self.screen.blit(surface, (x, y))
            else:
                pygame.draw.rect(self.screen, (60, 60, 60), (x, y, cell_w, cell_h))
                pygame.draw.rect(self.screen, (150, 60, 60), (x, y, cell_w, cell_h), width=2)
            self.gallery_thumbnail_hitboxes.append((cell_rect, start_index + offset))
            col += 1
            x += cell_w + gap
            if col >= columns:
                col = 0
                x = margin
                y += cell_h + gap

    def _draw_gallery_empty(self, model: AppModel) -> None:
        """NEU (Etappe 7): GALLERY_GRID wurde angetippt, aber es gibt noch
        keine Fotos. Ersetzt den vorherigen technischen Pfad-Hinweis
        ("Keine Fotos gefunden in: /home/...") durch eine einladende,
        gastfreundliche Nachricht mit direktem Weg zum ersten Foto."""
        height = self.config.screen.height
        self._blit_center(
            "Noch keine Fotos vorhanden!", self.font_status_main_menu, (210, 235, 225),
            round(0.42 * height),
        )
        self._blit_center(
            "Sei die/der Erste - mach jetzt ein Foto!", self.font_body, (190, 190, 195),
            round(0.42 * height) + 70,
        )

    def _draw_storage_critical_overlay(self) -> None:
        """NEU (Speicherplatz-Alarm) Stufe 2: sehr auffaelliges Warnsignal -
        dicker, blinkender roter Rahmen um den gesamten Bildschirm plus
        deutlicher Text. Blinkt unabhaengig vom LED-Ring (eigener
        Zeittakt hier), aber in aehnlicher Frequenz wie LedEffect.ERROR
        (5 Hz) - Bildschirm und Ring wirken dadurch synchron, ohne dass
        beide Systeme sich denselben Takt teilen muessten."""
        width, height = self.config.screen.width, self.config.screen.height
        on = int(time.time() * 5) % 2 == 0
        if on:
            border_width = 14
            color = (220, 0, 0)
            pygame.draw.rect(self.screen, color, pygame.Rect(0, 0, width, border_width))
            pygame.draw.rect(self.screen, color, pygame.Rect(0, height - border_width, width, border_width))
            pygame.draw.rect(self.screen, color, pygame.Rect(0, 0, border_width, height))
            pygame.draw.rect(self.screen, color, pygame.Rect(width - border_width, 0, border_width, height))
        text_color = (255, 60, 60) if on else (120, 20, 20)
        self._blit_center(
            "ACHTUNG: Bitte Techniker rufen!", self.font_status_main_menu, text_color,
            round(0.70 * height),
        )

    def _get_thumbnail_surface(self, path: str, size: tuple[int, int]) -> pygame.Surface | None:
        target_w, target_h = size
        is_fullscreen = size == (self.config.screen.width, self.config.screen.height)
        cache = self._fullscreen_cache if is_fullscreen else self._thumbnail_cache
        max_items = (
            self.config.gallery.max_fullscreen_cache_items
            if is_fullscreen
            else self.config.gallery.max_thumbnail_cache_items
        )

        cache_key = f"{path}:{size[0]}x{size[1]}"
        cached = cache.get(cache_key)
        if cached is not None:
            cache.move_to_end(cache_key)  # als zuletzt benutzt markieren
            return cached
        try:
            with PILImage.open(path) as im:
                # draft() nutzt die in JPEG eingebaute Stufen-Skalierung von
                # libjpeg (1/2, 1/4, 1/8) und dekodiert direkt in niedriger
                # Aufloesung, statt erst das komplette ~24-Megapixel-Foto der
                # D3300 zu dekodieren und danach zu verkleinern. Das ist der
                # Grund, warum das erste Anzeigen jedes Fotos in der Galerie
                # spuerbar geruckelt hat.
                im.draft("RGB", (target_w * 2, target_h * 2))
                im = im.convert("RGB")
                img_w, img_h = im.size
                scale = min(target_w / img_w, target_h / img_h)
                scaled_w = max(1, round(img_w * scale))
                scaled_h = max(1, round(img_h * scale))
                im = im.resize((scaled_w, scaled_h), PILImage.BILINEAR)
                scaled_image = pygame.image.fromstring(im.tobytes(), im.size, "RGB")
        except (FileNotFoundError, OSError):
            return None
        canvas = pygame.Surface(size)
        canvas.fill((25, 25, 30))
        canvas.blit(scaled_image, ((target_w - scaled_w) // 2, (target_h - scaled_h) // 2))
        cache[cache_key] = canvas
        if len(cache) > max_items:
            cache.popitem(last=False)  # aeltesten (am laengsten ungenutzten) Eintrag verwerfen
        return canvas

    def _draw_cinema_countdown(self, value: int) -> None:
        """
        Vom klassischen Kino-Countdown-Vorspann inspirierte, eigenstaendig
        gezeichnete Grafik (Kreis, Fadenkreuz, rotierender Wisch-Zeiger,
        grosse zentrierte Ziffer) - kein Bild/Video-Asset, alles per pygame
        gezeichnet. Wird halbtransparent ueber dem weiterlaufenden Liveview
        eingeblendet (nur fuer die Ziffern 5, 4, 3, 2 - bei 1 uebernimmt
        _draw_countdown_image() mit einem zufaellig gewaehlten "bitte
        laecheln"-Bild aus dem Pool).

        Farbwahl bewusst schwarz auf hellgrau (nicht reinweiss): grenzt sich
        so vom reinweissen Blitzen bei Ziffer 1 ab und bleibt auch auf einem
        hellen Liveview-Hintergrund gut lesbar.
        """
        width, height = self.config.screen.width, self.config.screen.height
        cx, cy = width // 2, round(height * 0.44)
        radius = round(min(width, height) * 0.30)

        pad = 12
        size = radius * 2 + pad * 2
        overlay = pygame.Surface((size, size), pygame.SRCALPHA)
        ocx, ocy = size // 2, size // 2

        # Heller Kreisgrund - hellgrau statt reinweiss (siehe Docstring),
        # leicht transparent, damit das Liveview minimal durchscheint.
        pygame.draw.circle(overlay, (222, 222, 222, 235), (ocx, ocy), radius)
        pygame.draw.circle(overlay, (30, 30, 30, 255), (ocx, ocy), radius, width=5)

        # Rotierender Wisch-Zeiger (klassische Kino-Countdown-Optik) - eine
        # volle Umdrehung pro Sekunde (360 Grad/s), laeuft kontinuierlich
        # mit. War vorher 90 Grad/s (= 4s pro Umdrehung); 360 Grad/s ist
        # die 4-fache Geschwindigkeit, also 1s pro Umdrehung.
        angle = (time.monotonic() * 360.0) % 360.0
        wedge_points = [(ocx, ocy)]
        for step in range(0, 46, 5):
            rad = math.radians(angle + step)
            wedge_points.append((ocx + radius * math.sin(rad), ocy - radius * math.cos(rad)))
        pygame.draw.polygon(overlay, (255, 255, 255, 110), wedge_points)
        pygame.draw.circle(overlay, (30, 30, 30, 255), (ocx, ocy), radius, width=5)

        # Fadenkreuz durch die Kreismitte
        pygame.draw.line(overlay, (30, 30, 30, 255), (ocx - radius, ocy), (ocx + radius, ocy), 3)
        pygame.draw.line(overlay, (30, 30, 30, 255), (ocx, ocy - radius), (ocx, ocy + radius), 3)

        # Grosse, zentrierte Ziffer - schwarz auf hellgrauem Kreisgrund
        digit_surf = self.font_countdown_digit.render(str(value), True, (15, 15, 15))
        digit_rect = digit_surf.get_rect(center=(ocx, ocy))
        overlay.blit(digit_surf, digit_rect)

        self.screen.blit(overlay, (cx - ocx, cy - ocy))

        hint = "Bitte auf die Markierung stellen."
        hint_surf = self.font_body.render(hint, True, (255, 255, 255))
        hint_rect = hint_surf.get_rect(center=(width // 2, cy + radius + 50))
        self.screen.blit(hint_surf, hint_rect)

    def _draw_countdown_image(self) -> None:
        if self._current_countdown_image is not None:
            self.screen.blit(self._current_countdown_image, (0, 0))

    def _select_random_countdown_image(self) -> None:
        """Zieht zufaellig ein Bild aus dem Pool fuer den jetzt startenden
        Countdown-Durchlauf. Wird einmal pro Durchlauf aufgerufen (siehe
        render()), nicht bei jedem Frame - der Wechsel soll erst beim
        naechsten Foto wieder stattfinden, nicht waehrend Ziffer 5..1."""
        pool = self._load_countdown_image_pool()
        self._current_countdown_image = random.choice(pool) if pool else None

    def _load_countdown_image_pool(self) -> list[pygame.Surface]:
        """Laedt und skaliert alle 15 "bitte_laecheln_XX.png"-Varianten
        (bitte_laecheln_01.png .. bitte_laecheln_15.png) einmalig und
        haelt sie fertig skaliert im Speicher - danach nur noch Auswahl
        per random.choice(), kein wiederholtes Laden/Skalieren."""
        if self._countdown_image_pool is not None:
            return self._countdown_image_pool

        target_w, target_h = self.config.screen.width, self.config.screen.height
        bg_color = self._background_color(AppState.COUNTDOWN)
        pool: list[pygame.Surface] = []
        for i in range(1, 16):
            path = self.config.assets_dir / f"bitte_laecheln_{i:02d}.png"
            try:
                raw = pygame.image.load(str(path)).convert_alpha()
            except (pygame.error, FileNotFoundError):
                print(f"[Renderer] Countdown-Bild nicht gefunden: {path}")
                continue

            img_w, img_h = raw.get_size()
            scale = min(target_w / img_w, target_h / img_h)
            scaled = pygame.transform.smoothscale(
                raw, (max(1, round(img_w * scale)), max(1, round(img_h * scale)))
            )
            canvas = pygame.Surface((target_w, target_h))
            canvas.fill(bg_color)
            canvas.blit(scaled, ((target_w - scaled.get_width()) // 2, (target_h - scaled.get_height()) // 2))
            pool.append(canvas)

        if not pool:
            print("[Renderer] Kein einziges bitte_laecheln_XX.png gefunden - es wird kein Bild angezeigt.")

        self._countdown_image_pool = pool
        return pool

    def _draw_main_menu_background(self) -> None:
        """Jubilaeums-Wallpaper als Hintergrund des Hauptmenues.

        Bild wird im "cover"-Modus skaliert (Bildschirm wird komplett
        ausgefuellt, ueberstehender Rand wird beschnitten) statt wie beim
        Countdown-Bild eingepasst mit Rand - hier soll es als vollflaechiger
        Hintergrund wirken, nicht als einzelnes zentriertes Motiv.
        Darueber liegt ein leichter dunkler Verlauf oben, damit Titel und
        Statuszeile (weisser Text) auf dem hellen Motiv lesbar bleiben.
        """
        image = self._get_main_menu_background()
        if image is None:
            return
        self.screen.blit(image, (0, 0))

        width = self.config.screen.width
        fade_height = round(self.config.screen.height * 0.30)
        overlay = pygame.Surface((width, fade_height), pygame.SRCALPHA)
        max_alpha = 130
        for y in range(fade_height):
            alpha = round(max_alpha * (1 - y / fade_height))
            pygame.draw.line(overlay, (0, 0, 0, alpha), (0, y), (width, y))
        self.screen.blit(overlay, (0, 0))

    def _get_main_menu_background(self) -> pygame.Surface | None:
        if self._main_menu_background is False:
            return None
        if self._main_menu_background is not None:
            return self._main_menu_background  # type: ignore[return-value]

        path = self.config.assets_dir / "hauptmenu_wallpaper.png"
        try:
            raw = pygame.image.load(str(path)).convert()
        except (pygame.error, FileNotFoundError):
            print(f"[Renderer] Hauptmenue-Hintergrundbild nicht gefunden: {path}")
            self._main_menu_background = False
            return None

        target_w, target_h = self.config.screen.width, self.config.screen.height
        img_w, img_h = raw.get_size()
        # "Cover"-Skalierung: groesserer der beiden Skalierungsfaktoren, damit
        # keine Raender frei bleiben - ueberstehender Teil wird beschnitten.
        scale = max(target_w / img_w, target_h / img_h)
        scaled_w, scaled_h = max(1, round(img_w * scale)), max(1, round(img_h * scale))
        scaled = pygame.transform.smoothscale(raw, (scaled_w, scaled_h))

        canvas = pygame.Surface((target_w, target_h))
        offset_x = (target_w - scaled_w) // 2
        offset_y = (target_h - scaled_h) // 2
        canvas.blit(scaled, (offset_x, offset_y))
        self._main_menu_background = canvas
        return canvas

    def _draw_boot_background(self) -> None:
        """Wallpaper waehrend des Systemstarts (AppState.BOOT).

        Analog zum Hauptmenue-Hintergrund im "cover"-Modus skaliert
        (Bildschirm komplett ausgefuellt, ueberstehender Rand
        beschnitten), mit demselben leichten dunklen Verlauf oben, damit
        Titel und Statuszeile ("System startet...") lesbar bleiben. Der
        BOOT-State dauert mindestens config.timeouts.boot_seconds (siehe
        config.py) - so lange bleibt dieses Bild sichtbar.
        """
        image = self._get_boot_background()
        if image is None:
            return
        self.screen.blit(image, (0, 0))

        width = self.config.screen.width
        fade_height = round(self.config.screen.height * 0.30)
        overlay = pygame.Surface((width, fade_height), pygame.SRCALPHA)
        max_alpha = 130
        for y in range(fade_height):
            alpha = round(max_alpha * (1 - y / fade_height))
            pygame.draw.line(overlay, (0, 0, 0, alpha), (0, y), (width, y))
        self.screen.blit(overlay, (0, 0))

    def _get_boot_background(self) -> pygame.Surface | None:
        if self._boot_background is False:
            return None
        if self._boot_background is not None:
            return self._boot_background  # type: ignore[return-value]

        path = self.config.assets_dir / "systemstart_wallpaper.png"
        try:
            raw = pygame.image.load(str(path)).convert()
        except (pygame.error, FileNotFoundError):
            print(f"[Renderer] Systemstart-Hintergrundbild nicht gefunden: {path}")
            self._boot_background = False
            return None

        target_w, target_h = self.config.screen.width, self.config.screen.height
        img_w, img_h = raw.get_size()
        # "Cover"-Skalierung: groesserer der beiden Skalierungsfaktoren, damit
        # keine Raender frei bleiben - ueberstehender Teil wird beschnitten.
        scale = max(target_w / img_w, target_h / img_h)
        scaled_w, scaled_h = max(1, round(img_w * scale)), max(1, round(img_h * scale))
        scaled = pygame.transform.smoothscale(raw, (scaled_w, scaled_h))

        canvas = pygame.Surface((target_w, target_h))
        offset_x = (target_w - scaled_w) // 2
        offset_y = (target_h - scaled_h) // 2
        canvas.blit(scaled, (offset_x, offset_y))
        self._boot_background = canvas
        return canvas

    def _draw_review_photo(self, model: AppModel) -> None:
        path = model.session.current_photo_path
        if not path:
            return
        width, height = self.config.screen.width, self.config.screen.height
        image = self._get_thumbnail_surface(path, (width, height))
        if image is not None:
            self.screen.blit(image, (0, 0))

    def _draw_gallery_fullscreen(self, model: AppModel) -> None:
        index = model.ui.selected_gallery_index
        photos = model.session.photos
        if index is None or not (0 <= index < len(photos)):
            return
        width, height = self.config.screen.width, self.config.screen.height
        image = self._get_thumbnail_surface(photos[index], (width, height))
        if image is not None:
            self.screen.blit(image, (0, 0))

    def _draw_attract_gallery(self, model: AppModel) -> None:
        photos = model.session.photos
        width, height = self.config.screen.width, self.config.screen.height
        if not photos:
            self._draw_text("Noch keine Fotos vorhanden.", self.font_body, (200, 200, 200), (60, round(0.4 * height)))
            return

        slot_seconds = 5.0
        fly_seconds = 0.6
        # Wahrscheinlichkeit pro Slot, dass statt eines Kundenfotos das
        # Systemstart-Wallpaper als "Werbung" eingeflogen wird - sorgt fuer
        # ein gelegentliches, aber nicht zu haeufiges Einstreuen.
        ad_probability = 0.2
        now = time.monotonic()
        slot = int(now // slot_seconds)
        t = now % slot_seconds

        # Ein auf den Slot geseedeter RNG: sowohl die Werbung-ja/nein-
        # Entscheidung als auch die Flugrichtung bleiben so fuer den
        # gesamten Slot stabil (kein Neu-Wuerfeln/Flackern bei jedem
        # Frame), aendern sich aber deterministisch mit jedem neuen Slot -
        # exakt wie bisher schon bei der Flugrichtung gehandhabt.
        slot_rng = random.Random(slot)
        show_ad = slot_rng.random() < ad_probability
        direction = slot_rng.choice(("left", "right", "top", "bottom"))

        image = None
        if show_ad:
            image = self._get_boot_background()
            if image is None:
                show_ad = False  # Fallback: kein Wallpaper vorhanden - normales Foto zeigen
        if not show_ad:
            index = slot % len(photos)
            image = self._get_thumbnail_surface(photos[index], (width, height))
        if image is None:
            return

        if t < fly_seconds:
            progress = t / fly_seconds
            eased = 1 - (1 - progress) ** 3  # ease-out: schnell rein, sanft einrasten
        else:
            eased = 1.0

        x, y = 0, 0
        if direction == "left":
            x = round(-width * (1 - eased))
        elif direction == "right":
            x = round(width * (1 - eased))
        elif direction == "top":
            y = round(-height * (1 - eased))
        else:
            y = round(height * (1 - eased))

        self.screen.blit(image, (x, y))

    def _draw_qr_code(self, qr_surface: pygame.Surface | None) -> None:
        width, height = self.config.screen.width, self.config.screen.height
        if qr_surface is None:
            self._draw_text(
                "QR-Code konnte nicht erzeugt werden.", self.font_body, (200, 80, 80),
                (60, round(0.4 * height)),
            )
            return

        # Weiße Karte mit Rand hinter dem Code - deutlich bessere Scanbarkeit
        # auf dunklem Hintergrund und robuster gegen schräge Blickwinkel.
        target_size = round(min(width, height) * 0.55)
        scaled = pygame.transform.smoothscale(qr_surface, (target_size, target_size))
        padding = 24
        card = pygame.Surface((target_size + 2 * padding, target_size + 2 * padding))
        card.fill((255, 255, 255))
        card.blit(scaled, (padding, padding))
        card_rect = card.get_rect(center=(width // 2, round(height * 0.55)))
        self.screen.blit(card, card_rect)

        hint = "QR-Code scannen, um dein Foto herunterzuladen"
        hint_surf = self.font_body.render(hint, True, (230, 230, 230))
        hint_rect = hint_surf.get_rect(center=(width // 2, card_rect.top - 40))
        self.screen.blit(hint_surf, hint_rect)

    def _blit_center(self, text: str, font: pygame.font.Font, color: tuple[int, int, int], cy: int) -> None:
        # Einzeiligen Text horizontal zentriert auf Hoehe cy zeichnen
        # (das uebliche _draw_text ist linksbuendig ab (x, y)).
        surf = font.render(text, True, color)
        rect = surf.get_rect(center=(self.config.screen.width // 2, cy))
        self.screen.blit(surf, rect)

    def _draw_pin_entry(self, model: AppModel) -> None:
        """Verstecktes Ziffernfeld fuer die Wartungs-PIN (AppState.PIN_ENTRY).

        Erreichbar nur ueber die Geheim-Geste im Hauptmenue (siehe
        shutdown_service / app_with_hw). Die eingegebene PIN wird maskiert
        (ein Kreis je Ziffer), das Raster kommt aus layout.pin_keys.
        """
        width, height = self.config.screen.width, self.config.screen.height

        # Kopfzeile (vom State gesetzt: "Wartungs-PIN eingeben").
        header = model.ui.status_text or "Wartungs-PIN eingeben"
        self._blit_center(header, self.font_title, (255, 255, 255), round(0.07 * height))

        # Maskierte Anzeige: ein gefuellter Kreis pro eingegebener Ziffer.
        n = len(model.ui.pin_entry)
        if n:
            radius = 14
            spacing = 44
            total_w = (n - 1) * spacing
            cx0 = width // 2 - total_w // 2
            cy = round(0.17 * height)
            for i in range(n):
                pygame.draw.circle(self.screen, (240, 240, 240), (cx0 + i * spacing, cy), radius)

        # Fehlermeldung mittig unter der PIN-Anzeige.
        if model.ui.error_text:
            self._blit_center(model.ui.error_text, self.font_body, (255, 120, 120), round(0.235 * height))

        # Ziffernfeld aus layout.pin_keys. Ziffern-Schluessel sind bereits
        # "0".."9"; Sondertasten bekommen sprechende Beschriftungen/Farben.
        labels = {"backspace": "Löschen", "submit": "OK", "cancel": "Abbrechen"}
        colors = {"backspace": (120, 90, 0), "submit": (0, 130, 0), "cancel": (100, 100, 100)}
        for name, rect in self.layout.pin_keys.items():
            self._draw_button(labels.get(name, name), rect, colors.get(name, (55, 65, 85)))

        # Fehler-Optik: schnelles Rot/Gelb-Blinken am Bildschirmrand als
        # Bildschirm-Echo zur LED-/Taster-Fehleranzeige, solange
        # pin_error_deadline laeuft (gleiche Uhr wie die App: time.monotonic).
        deadline = model.timers.pin_error_deadline
        if deadline is not None:
            now = time.monotonic()
            if now < deadline:
                phase = int(now * self.config.shutdown.error_button_flash_hz) % 2
                border = (self.config.shutdown.error_ring_color_rgb if phase == 0
                          else self.config.shutdown.error_accent_color_rgb)
                pygame.draw.rect(self.screen, border, self.screen.get_rect(), width=12)

    def _draw_shutdown_goodbye(self, model: AppModel) -> None:
        """Abschieds-Screen (AppState.SHUTDOWN_GOODBYE): nur das Wallpaper
        (der Text steckt im Bild selbst). Danach faehrt die App den Pi herunter."""
        image = self._get_shutdown_background()
        if image is not None:
            self.screen.blit(image, (0, 0))

    def _get_shutdown_background(self) -> pygame.Surface | None:
        # Gleiche Cache-/Cover-Skalier-Logik wie _get_boot_background /
        # _get_main_menu_background.
        if self._shutdown_background is False:
            return None
        if self._shutdown_background is not None:
            return self._shutdown_background  # type: ignore[return-value]

        path = self.config.assets_dir / "shutdown_wallpaper.png"
        try:
            raw = pygame.image.load(str(path)).convert()
        except (pygame.error, FileNotFoundError):
            print(f"[Renderer] Shutdown-Hintergrundbild nicht gefunden: {path}")
            self._shutdown_background = False
            return None

        target_w, target_h = self.config.screen.width, self.config.screen.height
        img_w, img_h = raw.get_size()
        scale = max(target_w / img_w, target_h / img_h)
        scaled_w, scaled_h = max(1, round(img_w * scale)), max(1, round(img_h * scale))
        scaled = pygame.transform.smoothscale(raw, (scaled_w, scaled_h))

        canvas = pygame.Surface((target_w, target_h))
        offset_x = (target_w - scaled_w) // 2
        offset_y = (target_h - scaled_h) // 2
        canvas.blit(scaled, (offset_x, offset_y))
        self._shutdown_background = canvas
        return canvas


    def _draw_instructions(self) -> None:
        """Scrollbarer Anleitungstext, ohne Titel darueber (siehe render()).

        Die Liste `lines` darf beliebig erweitert werden - die Ansicht
        scrollt automatisch, sobald der Text nicht mehr komplett in den
        sichtbaren Bereich passt (Wischen hoch/runter, siehe app_with_hw.py).
        """
        width, height = self.config.screen.width, self.config.screen.height
        lines = [
            "Bitte nutze die Fotobox nur, wenn du den Nutzungsbedingungen zustimmst.",
            "",
            "1. \"Fotografieren\" drücken oder die Foto-Taste betätigen",
            "",
            "2. \"Countdown starten\" drücken, wenn du bereit für die Aufnahme bist (oder \"Abrechen\")",
            "   Der Countdown bis zur Auslösung der Aufnahme beträgt 5 Sekunden.",
            "",
            "3. Auf die Markierung stellen und lächeln!",
            "",
            "4. Nach der Aufnahme: Foto speichern oder löschen.",
            "",
            "5. Wurde das Foto gespeichert, so kannst du den QR-Code scannen,",
            "   um das Foto auf dein Mobiltelefon zu laden.",
            f"   Verbinde dich dazu mit dem Gäste-WLAN (Kennwort: {self.config.network.guest_wifi_password})",
            "",
            "In der \"Galerie\" siehst du alle bisherigen Fotos:",
            "Hoch/runter Wischen zum Blättern durch die Galerie,",
            "ein Foto antippen für die Vollansicht, dort links/rechts Wischen.",
            "",
            "Viel Spaß! Bei Fragen bitte an Lutz wenden."
        ]

        left = 60
        top = round(0.06 * height)
        # Statt einer eigenen, unabhaengigen Prozentzahl (frueher: fix
        # 0.78*height) direkt von der tatsaechlichen Button-Position
        # abgeleitet - 10px Sicherheitsabstand. So bleiben Textbereich und
        # Button immer synchron, auch wenn text_view_back in layout.py
        # kuenftig nochmal verschoben wird.
        bottom = self.layout.text_view_back.y - 10
        line_height = self.font_body.get_linesize()

        viewport = pygame.Rect(0, top, width, bottom - top)
        total_height = len(lines) * line_height
        max_scroll = max(0, total_height - viewport.height)
        self.instructions_scroll_offset = max(0, min(self.instructions_scroll_offset, max_scroll))

        previous_clip = self.screen.get_clip()
        self.screen.set_clip(viewport)
        y = top - self.instructions_scroll_offset
        for line in lines:
            if y + line_height >= top and y <= bottom:
                self._draw_text(line, self.font_body, (230, 230, 230), (left, y))
            y += line_height
        self.screen.set_clip(previous_clip)

    @staticmethod
    def _heading(text: str) -> tuple[str, bool]:
        """Markiert eine Zeile in der `lines`-Liste von _draw_terms als fett
        darzustellende Überschrift. Beispiel: self._heading("Deine Rechte:")
        statt einfach "Deine Rechte:". Normale Zeilen bleiben einfache
        Strings - kein "\\"-artiges Steuerzeichen mitten im Text noetig."""
        return (text, True)

    def _draw_terms(self) -> None:
        """Scrollbare Nutzungsbedingungen, analog zu _draw_instructions().

        Der Inhalt ist bewusst als einfache Zeilenliste hier im Code
        gepflegt (wie bei _draw_instructions) statt in einer externen
        Text-/HTML-Datei geladen zu werden:
        - Die Fotobox-App hat sonst nirgends einen Rich-Text-/HTML-Renderer;
          das haette einen zusaetzlichen Parser noetig gemacht, nur um am
          Ende wieder auf denselben pygame-Text-Zeilen zu landen.
        - Der Text soll beim Start zuverlaessig da sein, auch ohne
          Netzwerk/USB-Stick/Datei-Handling zur Laufzeit - eine fehlende
          oder beschaedigte externe Datei wuerde sonst zu einem leeren oder
          fehlerhaften Rechts-Hinweis fuehren, was bei einem Datenschutz-
          Text besonders unguenstig ist.
        Aenderungen an den Nutzungsbedingungen (z.B. nach einer neuen
        Veranstaltung) macht man hier direkt in der Liste `lines`.
        """
        width, height = self.config.screen.width, self.config.screen.height
        wifi = self.config.network.guest_wifi_password
        lines = [
            self._heading("Nutzungsbedingungen zur Fotobox"),
            "",
            "Mit der Nutzung dieser Fotobox (z. B. durch Betätigen des Auslösers) erklärst du",
            "dich damit einverstanden, dass Fotografien von dir angefertigt werden.",
            "Die Nutzung ist freiwillig.",
            "",
            self._heading("Verwendungszweck & Speicherung"),
            "",
            "Die Fotos dienen als Erinnerung für Familie, Freunde und Verwandte sowie den",
            "Gastgeber.",
            "Sie werden zunächst lokal auf der Fotobox gespeichert und anschließend vom Gastgeber",
            "in einem privaten Kreis weiterverarbeitet.",
            "Während der Veranstaltung sind deine Fotos auf dem Display von anderen Nutzern der",
            "Fotobox einsehbar. Eine Weitergabe an unbeteiligte Dritte, eine Veröffentlichung",
            "im Internet oder eine kommerzielle Nutzung findet nicht statt.",
            "",
            self._heading("Lokaler Download (WLAN)"),
            "",
            "Über das WLAN \"Fotobox_Gast\" kannst du dein Foto nach der Aufnahme per QR-Code",
            f"herunterladen (Kennwort: {wifi}).",
            "Da es sich um ein Veranstaltungsnetzwerk handelt, sind die Bilddateien dabei",
            "theoretisch für andere angemeldete Nutzer einsehbar. Lade keine Bilder herunter,",
            "wenn du damit nicht einverstanden bist.",
            "",
            self._heading("Deine Rechte"),
            "",
            "Du kannst der Speicherung deines Bildes direkt nach der Aufnahme über die \"Löschen\"",
            "-Taste widersprechen. Außerdem hast du jederzeit das Recht auf Auskunft, Berichtigung,",
            "Löschung, Einschränkung der Verarbeitung, Datenübertragbarkeit und Widerspruch.",
            "Wende dich dazu einfach an den unten genannten Verantwortlichen.",
            "Eine erteilte Einwilligung kannst du jederzeit mit Wirkung für die Zukunft widerrufen.",
            "",
            "Alle gespeicherten Fotos werden unwiderruflich innerhalb von zwei (2) Tagen nach der",
            "Veranstaltung von der Fotobox gelöscht.",
            "",
            "Kinder & Jugendliche nutzen die Fotobox bitte nur in Begleitung bzw. mit Zustimmun",
            "eines Erziehungsberechtigten.",
            "",
            self._heading("Verantwortlich für den Betrieb"),
            "Lutz Buchholz",
            "Dechant-Fein-Str. 24",
            "51375 Leverkusen",
            "lutz-peter@imail.de", 
            "0163 8506144",
        ]

        left = 60
        top = round(0.06 * height)
        # Siehe Kommentar in _draw_instructions() - dieselbe Ableitung aus
        # der tatsaechlichen Button-Position statt einer separaten,
        # unabhaengigen Prozentzahl.
        bottom = self.layout.text_view_back.y - 10
        line_height = self.font_body.get_linesize()

        viewport = pygame.Rect(0, top, width, bottom - top)
        total_height = len(lines) * line_height
        max_scroll = max(0, total_height - viewport.height)
        self.terms_scroll_offset = max(0, min(self.terms_scroll_offset, max_scroll))

        previous_clip = self.screen.get_clip()
        self.screen.set_clip(viewport)
        y = top - self.terms_scroll_offset
        for line in lines:
            if isinstance(line, tuple):
                text, is_bold = line
                font = self.font_body_bold if is_bold else self.font_body
                # Ueberschriften zusaetzlich farblich abgesetzt (warmes
                # Amber statt normalem Hellgrau) - Kontrast gegen den TERMS-
                # Hintergrund (20,20,35) liegt bei 13.66:1 und erfuellt damit
                # deutlich auch WCAG AAA (>= 7:1), wichtig fuer aeltere
                # Gaeste mit schwaecherer Sehkraft. Dieselbe Farbe wird auch
                # fuer status_text verwendet - schafft Wiedererkennung als
                # "hervorgehoben".
                color = (255, 220, 120) if is_bold else (230, 230, 230)
            else:
                text, font, color = line, self.font_body, (230, 230, 230)
            if y + line_height >= top and y <= bottom:
                self._draw_text(text, font, color, (left, y))
            y += line_height
        self.screen.set_clip(previous_clip)

    def _draw_buttons(self, state: AppState) -> None:
        if state == AppState.MAIN_MENU:
            self._draw_button("Fotografieren", self.layout.main_photo, (0, 150, 0))
            self._draw_button("Galerie", self.layout.main_gallery, (0, 100, 150))
            self._draw_button("Anleitung", self.layout.main_instructions, (120, 90, 0))
            self._draw_button("Bedingungen", self.layout.main_terms, (120, 30, 90))
        elif state == AppState.ATTRACT_GALLERY:
            pass  # bewusst kein Button - Tippen/Taster fuehrt zurueck
        elif state == AppState.INSTRUCTIONS:
            self._draw_button("Zurück", self.layout.text_view_back, (100, 100, 100))
        elif state == AppState.TERMS:
            self._draw_button("Verstanden", self.layout.text_view_back, (0, 130, 110))
        elif state == AppState.PHOTO_INTRO:
            self._draw_button("Countdown starten", self.layout.left, (0, 150, 0))
            self._draw_button("Zurück", self.layout.right, (100, 100, 100))
        elif state == AppState.PHOTO_PREVIEW:
            # Kein "Countdown starten"-Button mehr - der Countdown startet
            # automatisch (siehe state_machine.py::_go_preview).
            self._draw_button("Abbrechen", self.layout.right, (100, 100, 100))
        elif state == AppState.COUNTDOWN:
            self._draw_button("Abbrechen", self.layout.right, (100, 100, 100))
        elif state == AppState.GALLERY_GRID:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
        elif state == AppState.GALLERY_EMPTY:              # NEU (Etappe 7)
            self._draw_button("Jetzt fotografieren", self.layout.left, (0, 150, 0))
            self._draw_button("Zurück", self.layout.right, (100, 100, 100))
        elif state == AppState.GALLERY_FULLSCREEN:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
        elif state == AppState.REVIEW:
            self._draw_button("Speichern", self.layout.left, (0, 150, 0))
            self._draw_button("Löschen", self.layout.right, (150, 0, 0))
        elif state == AppState.DELETE_CONFIRM:
            self._draw_button("Wirklich löschen", self.layout.left, (150, 0, 0))
            self._draw_button("Abbrechen", self.layout.right, (100, 100, 100))
        elif state == AppState.QR_DISPLAY:
            self._draw_button("Zurück", self.layout.right, (100, 100, 100))
        elif state == AppState.ERROR_SCREEN:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
        elif state == AppState.ADMIN_MENU:
            self._draw_admin_menu_buttons()
        elif state == AppState.ADMIN_STATUS:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
        elif state == AppState.ADMIN_DELETE_CONFIRM:
            # NEU (4.4): "Nein" links neutral-grau, "Ja" rechts deutlich rot -
            # die gefaehrliche Wahl soll nicht wie die naheliegende aussehen.
            self._draw_button("Nein, abbrechen", self.layout.left, (70, 70, 75))
            self._draw_button("Ja, alles löschen", self.layout.right, (160, 0, 0))
        elif state == AppState.ADMIN_DELETE_DONE:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
        elif state == AppState.ADMIN_USB_WAIT:
            # NEU (4.6): "Weiter" bleibt ausgegraut, solange kein Stick
            # erkannt wurde - dieselbe Bedingung, die auch die State
            # Machine prueft (_handle_admin_usb_wait).
            ready = self._usb_continue_enabled
            self._draw_button("Abbrechen", self.layout.left, (100, 100, 100))
            self._draw_button("Weiter", self.layout.right, (0, 130, 110) if ready else (55, 55, 60))
        elif state == AppState.ADMIN_USB_READY:
            self._draw_button("Abbrechen", self.layout.left, (100, 100, 100))
            self._draw_button("Export starten", self.layout.right, (0, 130, 110))
        elif state == AppState.ADMIN_USB_PROBLEM:
            # NEU (4.7): bei not_enough_free wird "Stick leeren" angeboten.
            # Bei too_small hilft Aufraeumen nicht - dort ersetzt der
            # rechte Button die Wirkung von "Weiter".
            if self._usb_not_enough_free:
                self._draw_button("Abbrechen", self.layout.left, (100, 100, 100))
                self._draw_button("Stick leeren", self.layout.right, (180, 80, 0))
            else:
                self._draw_button("Weiter", self.layout.right, (120, 90, 0))
        elif state == AppState.ADMIN_USB_EXPORT_DONE:
            self._draw_button("Weiter", self.layout.right, (0, 130, 110))
        elif state == AppState.ADMIN_USB_REMOVE:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
        elif state == AppState.ADMIN_USB_CONFLICTS:            # NEU (6c)
            # Sammelaktionen ("Alle auswaehlen"-Zeile) und Dateizeilen samt
            # Kontrollkaestchen zeichnet _draw_admin_usb_conflicts() bereits
            # vollstaendig mit - hier nur die eine echte Haupt-Aktion.
            self._draw_button("Ausführen", self.layout.right, (0, 130, 110))
        # ADMIN_RESTART_PENDING / ADMIN_DELETE_RUNNING / ADMIN_USB_RESOLVE:
        # bewusst kein Button - nicht abbrechbar.

    def _draw_admin_menu_buttons(self) -> None:
        # Beschriftung, Farbe und Position kommen vollstaendig aus
        # admin_menu.ADMIN_MENU_ITEMS - hier wird nichts dupliziert, damit
        # Zeichnung und Treffererkennung (app_with_hw._map_admin_menu_click)
        # nicht auseinanderlaufen koennen.
        # Noch nicht implementierte Punkte (enabled=False) werden dunkelgrau
        # gezeichnet, damit sichtbar ist, dass sie noch nichts tun.
        rects = build_admin_rects(self.config.screen.width, self.config.screen.height)
        for item in ADMIN_MENU_ITEMS:
            color = item.color if item.enabled else (55, 55, 60)
            self._draw_button(item.label, rects[item.key], color)

    def _draw_admin_status(self, model: AppModel) -> None:
        # NEU (4.3): einfache Zeilenliste, kein Scrollen noetig - fuenf
        # kurze Zeilen passen bequem zwischen Titel und "Zurueck"-Button.
        width, height = self.config.screen.width, self.config.screen.height
        y = round(0.22 * height)
        line_height = self.font_body.get_linesize() + 14
        if not model.ui.admin_status_lines:
            self._draw_text("Ermittle Status ...", self.font_body, (200, 200, 200), (60, y))
            return
        for line in model.ui.admin_status_lines:
            self._draw_text(line, self.font_body, (230, 230, 230), (60, y))
            y += line_height

    # NEU (4.6): merkt sich fuer _draw_buttons, ob "Weiter" aktiv sein
    # darf. Wird in render() aus dem Modell gesetzt - _draw_buttons
    # bekommt nur den Zustand uebergeben, nicht das Modell.
    _usb_continue_enabled: bool = False

    # NEU (4.7): merkt sich, ob "Stick leeren" angeboten werden darf.
    _usb_not_enough_free: bool = False

    def _draw_admin_usb_copy(self, model: AppModel) -> None:
        # GEAENDERT (4.8): Fortschrittsbalken statt durchlaufender
        # Dateinamen. Die Namen wechselten zu schnell zum Mitlesen; ein
        # Balken beantwortet die eigentliche Frage ("wie lange noch?")
        # deutlich besser.
        height = self.config.screen.height
        text = model.ui.admin_usb_export_progress or "Export wird vorbereitet ..."
        self._blit_center(text, self.font_status_main_menu, (200, 235, 225), round(0.32 * height))
        self._draw_progress_bar(model.ui.admin_usb_progress_fraction, round(0.48 * height))

    def _draw_progress_bar(
        self,
        fraction: float,
        y: int,
        color: tuple[int, int, int] = (0, 185, 110),
        track: tuple[int, int, int] = (22, 52, 48),
        border: tuple[int, int, int] = (90, 145, 135),
    ) -> None:
        """NEU (4.8): waagerechter Fortschrittsbalken, mittig, mit
        Prozentangabe darunter.

        GEAENDERT (4.9): Farben sind jetzt Parameter - der Loeschlauf nutzt
        denselben Balken in Rot. Ein gruener Balken waehrend einer
        unwiderruflichen Loeschung waere das falsche Signal.
        """
        width, height = self.config.screen.width, self.config.screen.height
        fraction = max(0.0, min(1.0, float(fraction)))

        bar_w = round(0.70 * width)
        bar_h = round(0.070 * height)
        bar_x = (width - bar_w) // 2
        radius = bar_h // 2

        outer = pygame.Rect(bar_x, y, bar_w, bar_h)
        # Hintergrund (leerer Teil)
        pygame.draw.rect(self.screen, track, outer, border_radius=radius)
        # Gefuellter Teil - Mindestbreite, damit bei 1 % nicht nichts zu sehen ist
        if fraction > 0.0:
            fill_w = max(bar_h, round(bar_w * fraction))
            inner = pygame.Rect(bar_x, y, fill_w, bar_h)
            pygame.draw.rect(self.screen, color, inner, border_radius=radius)
        # Rahmen
        pygame.draw.rect(self.screen, border, outer, width=3, border_radius=radius)

        self._blit_center(
            f"{round(fraction * 100)} %", self.font_body, (220, 240, 235), y + bar_h + 20,
        )

    def _draw_admin_usb_lines(self, model: AppModel) -> None:
        # NEU (4.6): Zeilenliste wie bei Diagnose und Loesch-Ergebnis.
        self._usb_continue_enabled = model.ui.admin_usb_device_ready
        self._usb_not_enough_free = model.ui.admin_usb_not_enough_free
        height = self.config.screen.height
        y = round(0.22 * height)
        line_height = self.font_body.get_linesize() + 14
        for line in model.ui.admin_usb_lines:
            self._draw_text(line, self.font_body, (230, 230, 230), (60, y))
            y += line_height

    def _draw_admin_usb_busy(self, model: AppModel) -> None:
        # NEU (4.6): laufender Vorgang - zentrierter Hinweis, kein Button.
        self._blit_center(
            model.ui.status_text or "Bitte warten ...",
            self.font_status_main_menu, (200, 235, 225),
            round(0.45 * self.config.screen.height),
        )

    def _truncate_text(self, text: str, font: pygame.font.Font, max_width: int) -> str:
        """Kuerzt text mit angehaengtem '...', falls er in max_width (Pixel)
        nicht hineinpasst. Lineares Abschneiden reicht hier - Dateinamen
        sind kurz genug, dass die Performance keine Rolle spielt."""
        if font.size(text)[0] <= max_width:
            return text
        ellipsis = "..."
        truncated = text
        while truncated and font.size(truncated + ellipsis)[0] > max_width:
            truncated = truncated[:-1]
        return (truncated + ellipsis) if truncated else ellipsis

    def _draw_conflict_checkbox(self, hitbox: pygame.Rect, checked: bool) -> None:
        """NEU (6c, ueberarbeitet nach Nutzer-Feedback): echte Kontrollkasten-
        Optik statt grosser Buttons - ein kleines Quadrat, zentriert im
        (fuer Touch bewusst groesseren) hitbox-Rect. Gefuellt und weiss
        umrandet, wenn ausgewaehlt, sonst nur ein duenner, gedaempfter
        Rahmen. Eine einzige Auswahlfarbe fuer beide Spalten (vorher zwei
        verschiedene Farben je Spalte) - liest sich naeher an einem
        echten Kontrollkaestchen."""
        size = round(min(hitbox.width, hitbox.height) * 0.55)
        box = pygame.Rect(0, 0, size, size)
        box.center = hitbox.center
        if checked:
            pygame.draw.rect(self.screen, (0, 130, 110), box, border_radius=6)
            pygame.draw.rect(self.screen, (255, 255, 255), box, width=2, border_radius=6)
        else:
            pygame.draw.rect(self.screen, (40, 40, 45), box, border_radius=6)
            pygame.draw.rect(self.screen, (120, 120, 125), box, width=2, border_radius=6)

    def _draw_admin_usb_conflicts(self, model: AppModel) -> None:
        """NEU (6c, ueberarbeitet nach Nutzer-Feedback): scrollbare Liste
        offener Namenskonflikte im Kontrollkasten-Look (zwei Spalten
        "Ueberschreiben"/"Umbenennen"), mit einer festen "Alle auswaehlen"-
        Zeile ganz oben fuer die Sammelaktion.

        GEAENDERT: die vorherige Fassung zeichnete einen Hinweistext
        ("N Dateien ...") auf derselben Bildschirmzeile wie die (damals
        noch grossen) Sammelaktions-Buttons - beide ueberlappten sich
        sichtbar. Der Hinweistext entfaellt jetzt ersatzlos (der Titel
        "Dateien mit abweichendem Inhalt gefunden" plus die sichtbaren
        Zeilen sagen bereits genug), stattdessen klare eigene Zeilen fuer
        Spaltenkoepfe und "Alle auswaehlen".

        Die X-Position beider Spalten kommt bewusst aus layout.py
        (usb_conflicts_overwrite_all/_rename_all .centerx) statt hier neu
        berechnet zu werden - dieselbe Spalte fuer Kopf, "Alle auswaehlen"
        und jede einzelne Dateizeile, ohne Duplizierung.

        Scrollen wie bei INSTRUCTIONS/TERMS ueber einen rein im Renderer
        gehaltenen Offset (reine Anzeigesache). Tipp-Erkennung je
        Dateizeile ueber usb_conflict_row_hitboxes (siehe
        app_with_hw._map_click_to_event) - die "Alle auswaehlen"-Zeile
        scrollt dagegen NICHT mit und nutzt daher die statischen Rects aus
        layout.py, genau wie jeder andere Button im Programm.
        """
        width, height = self.config.screen.width, self.config.screen.height
        conflicts = model.ui.admin_usb_conflicts
        self.usb_conflict_row_hitboxes = []

        overwrite_x = self.layout.usb_conflicts_overwrite_all.centerx
        rename_x = self.layout.usb_conflicts_rename_all.centerx

        # -- Spaltenkoepfe -------------------------------------------------
        # NEU (Feedback): urspruenglich 0.185 - sass fast auf dem Titel.
        # +16px Luft zwischen Titel-Unterkante und dieser Zeile.
        header_y = round(0.185 * height) + 16
        for text, cx in (("Überschreiben", overwrite_x), ("Umbenennen", rename_x)):
            surf = self.font_small.render(text, True, (200, 200, 205))
            rect = surf.get_rect(center=(cx, header_y))
            self.screen.blit(surf, rect)

        # -- "Alle auswaehlen"-Zeile (fest, scrollt nicht mit) --------------
        # NEU (Feedback): das Haekchen dieser Zeile ist kein eigener,
        # persistenter Zustand, sondern wird bei jedem Zeichnen live aus
        # den TATSAECHLICHEN Entscheidungen abgeleitet - sind gerade ALLE
        # offenen Konflikte auf "overwrite" gesetzt, ist das Haekchen dort
        # aktiv, analog fuer "rename". Bei gemischten Entscheidungen (z.B.
        # weil eine einzelne Zeile danach wieder umgestellt wurde) bleiben
        # beide leer - das ist korrekt und entspricht dem Verhalten einer
        # echten Checkbox-Gruppe (kein State-Machine-Feld noetig).
        all_row = self.layout.usb_conflicts_overwrite_all
        label_y = all_row.y + (all_row.height - self.font_body.get_linesize()) // 2
        self._draw_text("Alle auswählen", self.font_body, (210, 210, 215), (60, label_y))
        all_overwrite = bool(conflicts) and all(c.decision == "overwrite" for c in conflicts)
        all_rename = bool(conflicts) and all(c.decision == "rename" for c in conflicts)
        self._draw_conflict_checkbox(self.layout.usb_conflicts_overwrite_all, all_overwrite)
        self._draw_conflict_checkbox(self.layout.usb_conflicts_rename_all, all_rename)

        # -- Scrollbare Liste ------------------------------------------------
        top = round(0.335 * height)
        bottom = round(0.77 * height)
        row_h = round(0.075 * height)
        gap = round(0.012 * height)

        if not conflicts:
            # Sollte praktisch nie sichtbar werden (der Screen wird nur bei
            # mindestens einem Konflikt betreten), ist aber kein Fehlerfall.
            self._blit_center(
                "Keine offenen Konflikte mehr.", self.font_body, (200, 200, 200), (top + bottom) // 2,
            )
            return

        viewport = pygame.Rect(0, top, width, bottom - top)
        total_height = len(conflicts) * (row_h + gap)
        max_scroll = max(0, total_height - viewport.height)
        self.usb_conflicts_scroll_offset = max(0, min(self.usb_conflicts_scroll_offset, max_scroll))

        checkbox_hitbox_w = round(0.09 * width)
        name_max_w = overwrite_x - checkbox_hitbox_w // 2 - 60 - round(0.02 * width)

        previous_clip = self.screen.get_clip()
        self.screen.set_clip(viewport)
        y = top - self.usb_conflicts_scroll_offset

        for conflict in conflicts:
            if y + row_h >= top and y <= bottom:
                name_y = y + (row_h - self.font_body.get_linesize()) // 2
                label = self._truncate_text(conflict.name, self.font_body, name_max_w)
                self._draw_text(label, self.font_body, (230, 230, 230), (60, name_y))

                overwrite_rect = pygame.Rect(0, 0, checkbox_hitbox_w, row_h)
                overwrite_rect.center = (overwrite_x, y + row_h // 2)
                rename_rect = pygame.Rect(0, 0, checkbox_hitbox_w, row_h)
                rename_rect.center = (rename_x, y + row_h // 2)

                self._draw_conflict_checkbox(overwrite_rect, conflict.decision == "overwrite")
                self._draw_conflict_checkbox(rename_rect, conflict.decision == "rename")

                # Hitboxen in Bildschirm-Koordinaten (nicht relativ zum
                # Clip) - app_with_hw.py prueft sie gegen die tatsaechliche
                # Tap-Position.
                self.usb_conflict_row_hitboxes.append((overwrite_rect, conflict.name, "overwrite"))
                self.usb_conflict_row_hitboxes.append((rename_rect, conflict.name, "rename"))
            y += row_h + gap

        self.screen.set_clip(previous_clip)

    def _draw_admin_delete_confirm(self, model: AppModel) -> None:
        # NEU (4.4): Warntext gross und zentriert. status_text enthaelt
        # bereits Zeilenumbrueche (siehe state_machine._go_admin_delete_confirm),
        # daher zeilenweise zentriert setzen statt in einem Rutsch.
        height = self.config.screen.height
        lines = (model.ui.status_text or "").split("\n")
        y = round(0.30 * height)
        line_height = self.font_status_main_menu.get_linesize() + 10
        for line in lines:
            self._blit_center(line, self.font_status_main_menu, (255, 210, 210), y)
            y += line_height
        # Zusaetzlicher Hinweis, was "alles" konkret umfasst - beugt der
        # Fehlannahme vor, es gehe nur um die Bilder auf dem Bildschirm.
        self._blit_center(
            "Betrifft Fotobox, QR-Download und Kamera-Speicherkarte.",
            self.font_body, (230, 170, 170), y + 16,
        )

    def _draw_admin_delete_running(self, model: AppModel) -> None:
        # GEAENDERT (4.9): Fortschrittsbalken wie beim Export, aber in Rot.
        # Kein Button - die Loeschung ist nicht abbrechbar.
        height = self.config.screen.height
        self._blit_center(
            model.ui.admin_delete_progress or "Bilder werden gelöscht ...",
            self.font_status_main_menu, (255, 210, 210), round(0.32 * height),
        )
        self._draw_progress_bar(
            model.ui.admin_delete_fraction, round(0.48 * height),
            color=(200, 45, 45), track=(58, 20, 20), border=(150, 95, 95),
        )

    def _draw_admin_delete_done(self, model: AppModel) -> None:
        # NEU (4.4): Zusammenfassung als Zeilenliste, gleiche Optik wie
        # die Diagnoseseite (_draw_admin_status).
        height = self.config.screen.height
        y = round(0.22 * height)
        line_height = self.font_body.get_linesize() + 14
        for line in model.ui.admin_delete_lines:
            self._draw_text(line, self.font_body, (230, 230, 230), (60, y))
            y += line_height

    def _draw_admin_restart_pending(self, model: AppModel) -> None:
        # NEU (4.3): grosse, zentrierte Statuszeile - bewusst kein Titel,
        # kein Button (nicht abbrechbar, siehe state_machine.py).
        self._blit_center(
            model.ui.status_text or "App wird neu gestartet ...",
            self.font_status_main_menu,
            (255, 220, 120),
            round(0.45 * self.config.screen.height),
        )

    def _draw_button(self, label: str, rect: pygame.Rect, color: tuple[int, int, int]) -> None:
        # Leichter Schatten nach rechts unten fuer einen dezenten 3D-Effekt.
        # Braucht eine separate SRCALPHA-Zwischenflaeche, weil self.screen
        # selbst keinen Alphakanal hat - echte Transparenz beim Zeichnen
        # geht nur ueber eine solche Flaeche, die per blit() ueberblendet
        # wird (gleiche Technik wie beim halbtransparenten Kreis in
        # _draw_cinema_countdown).
        #
        # Farbwahl bewusst Richtung Anthrazit (60,63,68) statt reinem
        # Schwarz: die meisten Bildschirm-Hintergruende dieser App liegen
        # selbst schon im sehr dunklen Bereich (z.B. (20,20,30)) - ein
        # schwarzer Schatten waere darauf kaum zu erkennen. Anthrazit ist
        # dort tatsaechlich heller als der Hintergrund und bleibt dadurch
        # als Tiefenkontur sichtbar, auch fuer Nutzer mit schwaecherem
        # Kontrastsehen.
        shadow_offset = 6
        shadow_surface = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
        pygame.draw.rect(shadow_surface, (60, 63, 68, 140), shadow_surface.get_rect(), border_radius=14)
        self.screen.blit(shadow_surface, (rect.x + shadow_offset, rect.y + shadow_offset))

        pygame.draw.rect(self.screen, color, rect, border_radius=14)
        pygame.draw.rect(self.screen, (255, 255, 255), rect, width=2, border_radius=14)

        # Schrift so gross wie moeglich, aber automatisch verkleinert, falls
        # ein langes Label (z.B. "Wirklich löschen") sonst ueberlaufen wuerde.
        max_w = rect.width - 24
        size = 50
        font = self.font_button
        text_surface = font.render(label, True, (255, 255, 255))
        while text_surface.get_width() > max_w and size > 24:
            size -= 4
            font = pygame.font.Font(None, size)
            text_surface = font.render(label, True, (255, 255, 255))
        text_rect = text_surface.get_rect(center=rect.center)
        self.screen.blit(text_surface, text_rect)

    def _draw_text(self, text: str, font: pygame.font.Font, color: tuple[int, int, int], pos: tuple[int, int]) -> None:
        """Rendert Text; unterstuetzt mehrzeilige Strings ueber "\\n"-Trennung
        (fuer einzeilige Texte ohne "\\n" identisch zum bisherigen Verhalten)."""
        x, y = pos
        line_height = font.get_linesize()
        for line in text.split("\n"):
            surf = font.render(line, True, color)
            self.screen.blit(surf, (x, y))
            y += line_height

    def _draw_footer(self, model: AppModel, fps: float) -> None:
        if not self.config.features.debug_overlay:
            return
        lines = [
            f"Letztes Event: {model.last_event.type.name if model.last_event else '-'}",
            f"Fotos im Speicher: {len(model.session.photos)}",
            f"FPS: {fps:.1f}",
            "ESC beendet diese Test-App.",
        ]
        y = self.config.screen.height - 140
        for line in lines:
            self._draw_text(line, self.font_small, (220, 220, 220), (60, y))
            y += 30

    @staticmethod
    def _background_color(state: AppState) -> tuple[int, int, int]:
        return {
            AppState.BOOT: (10, 25, 47),
            AppState.MAIN_MENU: (20, 20, 30),
            AppState.PHOTO_INTRO: (30, 30, 40),
            AppState.ATTRACT_GALLERY: (25, 25, 45),
            AppState.GALLERY_GRID: (15, 15, 20),
            # NEU (Etappe 7): dasselbe ruhige Dunkel-Tuerkis wie
            # GALLERY_GRID_BREATHE (LED) andeutungsweise im Hintergrund -
            # sichtbar zur Galerie-Familie gehoerig, aber warm genug fuer
            # eine einladende Botschaft statt eines rein technischen Grids.
            AppState.GALLERY_EMPTY: (10, 22, 20),
            AppState.GALLERY_FULLSCREEN: (5, 5, 5),
            AppState.PHOTO_PREVIEW: (30, 30, 40),
            AppState.COUNTDOWN: (60, 30, 20),
            AppState.CAPTURE_PENDING: (20, 40, 20),
            AppState.REVIEW: (40, 50, 40),
            AppState.DELETE_CONFIRM: (50, 20, 20),
            AppState.QR_DISPLAY: (35, 20, 90),
            AppState.INSTRUCTIONS: (20, 20, 35),
            AppState.TERMS: (20, 20, 35),
            AppState.ERROR_SCREEN: (80, 10, 10),
            AppState.PIN_ENTRY: (18, 22, 30),          # NEU (3.3)
            AppState.SHUTDOWN_GOODBYE: (10, 10, 15),   # NEU (3.3)
            AppState.MAINTENANCE: (50, 50, 10),
            AppState.ADMIN_MENU: (18, 22, 30),         # NEU (4.1) - wie PIN_ENTRY
            AppState.ADMIN_STATUS: (18, 22, 30),       # NEU (4.3) - wie ADMIN_MENU
            AppState.ADMIN_RESTART_PENDING: (20, 40, 20),  # NEU (4.3) - wie CAPTURE_PENDING
            # NEU (4.4): kraeftiges Dunkelrot als unuebersehbares Warnsignal,
            # deutlich abgesetzt vom ruhigen Blaugrau der uebrigen Admin-Screens.
            AppState.ADMIN_DELETE_CONFIRM: (55, 8, 8),
            AppState.ADMIN_DELETE_RUNNING: (40, 8, 8),
            AppState.ADMIN_DELETE_DONE: (18, 22, 30),
            # NEU (4.6): gedecktes Blaugruen - klar unterscheidbar vom
            # Rot der Loeschwege, gleiche Ruhe wie die uebrigen Admin-Screens.
            AppState.ADMIN_USB_WAIT: (12, 28, 28),
            AppState.ADMIN_USB_CHECK: (12, 28, 28),
            AppState.ADMIN_USB_READY: (10, 32, 26),
            AppState.ADMIN_USB_PROBLEM: (45, 32, 8),
            AppState.ADMIN_USB_EJECT: (12, 28, 28),
            AppState.ADMIN_USB_REMOVE: (10, 32, 26),
            AppState.ADMIN_USB_COPY: (12, 28, 28),      # NEU (4.7)
            AppState.ADMIN_USB_EXPORT_DONE: (10, 32, 26),  # NEU (4.7)
            # NEU (6c): gelblich gedeckt - Aufmerksamkeit noetig (Entscheidung
            # gefragt), aber keine Stoerung; angelehnt an ADMIN_USB_PROBLEM,
            # nur weniger intensiv (kein echtes Problem, nur eine Nachfrage).
            AppState.ADMIN_USB_CONFLICTS: (38, 30, 10),
            # NEU (6c): gleiches Blaugruen wie der eigentliche Kopierlauf -
            # aus Sicht des Gastes/Betreibers "es wird weiter geschrieben".
            AppState.ADMIN_USB_RESOLVE: (12, 28, 28),
        }[state]
