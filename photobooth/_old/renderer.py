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


@dataclass
class Renderer:
    config: AppConfig
    screen: pygame.Surface

    def __post_init__(self) -> None:
        self.layout: LayoutRects = build_layout(self.config.screen.width, self.config.screen.height)
        self.font_title = pygame.font.Font(None, 82)
        self.font_body = pygame.font.Font(None, 42)
        self.font_small = pygame.font.Font(None, 32)
        self.font_button = pygame.font.Font(None, 50)
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
        self._countdown_image: pygame.Surface | bool | None = None
        self._main_menu_background: pygame.Surface | bool | None = None
        self.gallery_thumbnail_hitboxes: list[tuple[pygame.Rect, int]] = []
        # Scroll-Position der Anleitung (in Pixeln). Lebt bewusst nur hier im
        # Renderer (reine Anzeige-Angelegenheit), nicht im AppModel/State
        # Machine - aehnlich wie gallery_thumbnail_hitboxes.
        self.instructions_scroll_offset: int = 0
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
        self._last_rendered_state = model.state

        self.screen.fill(self._background_color(model.state))

        if model.state == AppState.MAIN_MENU:
            self._draw_main_menu_background()

        if preview_frame is not None:
            self._draw_preview_frame(preview_frame)

        if model.state == AppState.COUNTDOWN:
            value = model.ui.countdown_value
            if value == 1:
                # Liveview aus, "bitte laecheln" einblenden, keine Text-Ausgabe mehr.
                self._draw_countdown_image()
            elif value in (4, 3, 2):
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

        # Bei Ziffer 1 (Liveview aus, "bitte laecheln") soll GAR KEIN Text
        # mehr zu sehen sein - weder Titel noch Statuszeile.
        hide_all_text = model.state == AppState.COUNTDOWN and model.ui.countdown_value == 1

        # Titel wird in der Anleitung und bei Ziffer 1 bewusst weggelassen.
        if model.state != AppState.INSTRUCTIONS and not hide_all_text:
            self._draw_text(self.config.screen.title, self.font_title, (255, 255, 255), (60, 60))

        if self.config.features.debug_overlay:
            self._draw_text(f"Zustand: {model.state.name}", self.font_body, (220, 220, 220), (60, 180))

        if model.state != AppState.INSTRUCTIONS and not hide_all_text:
            self._draw_text(model.ui.status_text, self.font_body, (255, 220, 120), (60, 240))

        if model.ui.error_text:
            self._draw_text(model.ui.error_text, self.font_body, (255, 120, 120), (60, 320))

        if model.state == AppState.GALLERY_GRID:
            self._draw_gallery_grid(model)

        self._draw_buttons(model.state)
        self._draw_footer(model, fps)
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
        eingeblendet (nur fuer die Ziffern 4, 3, 2 - bei 1 uebernimmt
        _draw_countdown_image() mit dem "bitte laecheln"-Bild).

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
        # volle Umdrehung alle 4 Sekunden, laeuft kontinuierlich mit.
        angle = (time.monotonic() * 90.0) % 360.0
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
        image = self._get_countdown_image()
        if image is not None:
            self.screen.blit(image, (0, 0))

    def _get_countdown_image(self) -> pygame.Surface | None:
        if self._countdown_image is False:
            return None
        if self._countdown_image is not None:
            return self._countdown_image  # type: ignore[return-value]

        path = self.config.assets_dir / "bitte_laecheln.png"
        try:
            raw = pygame.image.load(str(path)).convert_alpha()
        except (pygame.error, FileNotFoundError):
            print(f"[Renderer] Countdown-Bild nicht gefunden: {path}")
            self._countdown_image = False
            return None

        target_w, target_h = self.config.screen.width, self.config.screen.height
        img_w, img_h = raw.get_size()
        scale = min(target_w / img_w, target_h / img_h)
        scaled = pygame.transform.smoothscale(
            raw, (max(1, round(img_w * scale)), max(1, round(img_h * scale)))
        )
        canvas = pygame.Surface((target_w, target_h))
        canvas.fill(self._background_color(AppState.COUNTDOWN))
        canvas.blit(scaled, ((target_w - scaled.get_width()) // 2, (target_h - scaled.get_height()) // 2))
        self._countdown_image = canvas
        return canvas

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
        now = time.monotonic()
        slot = int(now // slot_seconds)
        index = slot % len(photos)
        t = now % slot_seconds

        image = self._get_thumbnail_surface(photos[index], (width, height))
        if image is None:
            return

        if t < fly_seconds:
            progress = t / fly_seconds
            eased = 1 - (1 - progress) ** 3  # ease-out: schnell rein, sanft einrasten
        else:
            eased = 1.0

        direction = random.Random(slot).choice(("left", "right", "top", "bottom"))
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
            "   Der Countdown bis zur Auslösung der Aufnahme beträgt 4 Sekunden.",
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
        bottom = round(0.78 * height)  # laesst Platz fuer den Zurueck-Button unten
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

    def _draw_buttons(self, state: AppState) -> None:
        if state == AppState.MAIN_MENU:
            self._draw_button("Fotografieren", self.layout.main_photo, (0, 150, 0))
            self._draw_button("Galerie", self.layout.main_gallery, (0, 100, 150))
            self._draw_button("Anleitung", self.layout.main_instructions, (120, 90, 0))
        elif state == AppState.ATTRACT_GALLERY:
            pass  # bewusst kein Button - Tippen/Taster fuehrt zurueck
        elif state == AppState.INSTRUCTIONS:
            self._draw_button("Zurück", self.layout.right, (100, 100, 100))
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

    def _draw_button(self, label: str, rect: pygame.Rect, color: tuple[int, int, int]) -> None:
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
            AppState.GALLERY_FULLSCREEN: (5, 5, 5),
            AppState.PHOTO_PREVIEW: (30, 30, 40),
            AppState.COUNTDOWN: (60, 30, 20),
            AppState.CAPTURE_PENDING: (20, 40, 20),
            AppState.REVIEW: (40, 50, 40),
            AppState.DELETE_CONFIRM: (50, 20, 20),
            AppState.QR_DISPLAY: (35, 20, 90),
            AppState.INSTRUCTIONS: (20, 20, 35),
            AppState.ERROR_SCREEN: (80, 10, 10),
            AppState.MAINTENANCE: (50, 50, 10),
        }[state]