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

        # Bei Ziffer 1 (Liveview aus, "bitte laecheln") soll GAR KEIN Text
        # mehr zu sehen sein - weder Titel noch Statuszeile.
        hide_all_text = model.state == AppState.COUNTDOWN and model.ui.countdown_value == 1

        # Titel wird in der Anleitung, den Nutzungsbedingungen und bei
        # Ziffer 1 bewusst weggelassen (eigene scrollbare Textansichten,
        # die den vollen Bildschirm brauchen).
        text_screens = {AppState.INSTRUCTIONS, AppState.TERMS}

        if model.state not in text_screens and not hide_all_text:
            self._draw_text(self.config.screen.title, self.font_title, (255, 255, 255), (60, 60))

        if self.config.features.debug_overlay:
            self._draw_text(f"Zustand: {model.state.name}", self.font_body, (220, 220, 220), (60, 180))

        if model.state not in text_screens and not hide_all_text:
            # Im Hauptmenue liegt der Text auf dem Hintergrundbild - Anthrazit statt
            # dem sonst ueblichen Amber, da Amber auf dem Bild schlecht lesbar war.
            status_color = (40, 40, 45) if model.state == AppState.MAIN_MENU else (255, 220, 120)
            status_font = self.font_status_main_menu if model.state == AppState.MAIN_MENU else self.font_body
            self._draw_text(model.ui.status_text, status_font, status_color, (60, 240))

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
            AppState.MAINTENANCE: (50, 50, 10),
        }[state]