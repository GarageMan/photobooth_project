from __future__ import annotations

import os
import sys
import time
from dataclasses import replace

import pygame

from config import DEFAULT_CONFIG
from events import AppEvent, EventType
from gallery_service import GalleryService
from state_machine import StateMachine
from states import AppState


# Prozentuale Button-Layouts (relativ zu screen.width / screen.height), abgeleitet
# aus dem ursprünglichen Portrait-Design (1080x1920). Dadurch passen sich die
# Buttons automatisch an, egal ob Portrait (Pi) oder Landscape (PC-Testmodus).
BUTTON_LAYOUT_PCT = {
    "photo": (120 / 1080, 520 / 1920, 360 / 1080, 100 / 1920),
    "gallery": (600 / 1080, 520 / 1920, 360 / 1080, 100 / 1920),
    "back": (120 / 1080, 1680 / 1920, 260 / 1080, 90 / 1920),
    "cancel": (700 / 1080, 1680 / 1920, 260 / 1080, 90 / 1920),
    "save": (120 / 1080, 1680 / 1920, 320 / 1080, 90 / 1920),
    "delete": (640 / 1080, 1680 / 1920, 320 / 1080, 90 / 1920),
    "confirm_delete": (120 / 1080, 1680 / 1920, 320 / 1080, 90 / 1920),
    "abort_delete": (640 / 1080, 1680 / 1920, 320 / 1080, 90 / 1920),
}


def build_button_rects(screen_width: int, screen_height: int) -> dict[str, pygame.Rect]:
    """Berechnet die Button-Rects passend zur aktuellen Fenstergröße."""
    rects: dict[str, pygame.Rect] = {}
    for name, (x_pct, y_pct, w_pct, h_pct) in BUTTON_LAYOUT_PCT.items():
        rects[name] = pygame.Rect(
            round(x_pct * screen_width),
            round(y_pct * screen_height),
            round(w_pct * screen_width),
            round(h_pct * screen_height),
        )
    return rects


class DemoApp:
    def __init__(self) -> None:
        self.config = DEFAULT_CONFIG

        # Nur für lokales PC-Testen: vertauscht Breite/Höhe des Fensters,
        # damit es auf einem normalen Landscape-Monitor passt.
        # Der Pi behält immer die echte Portrait-Auflösung aus config.py.
        if os.environ.get("PHOTOBOOTH_DESKTOP_TEST") == "1":
            self.config = replace(
                self.config,
                screen=replace(
                    self.config.screen,
                    width=self.config.screen.height,
                    height=self.config.screen.width,
                ),
            )

        self.config.ensure_directories()
        self.gallery_service = GalleryService(
            photo_dir=self.config.photo_dir,
            max_thumbnail_cache_items=self.config.gallery.max_thumbnail_cache_items,
            max_fullscreen_cache_items=self.config.gallery.max_fullscreen_cache_items,
        )
        self.button_rects = build_button_rects(self.config.screen.width, self.config.screen.height)
        self.state_machine = StateMachine(self.config)
        self.model = self.state_machine.initial_model(time.monotonic())
        self.running = True
        self.touch_start_x: int | None = None

        pygame.init()
        flags = pygame.FULLSCREEN if self.config.screen.fullscreen else 0
        self.screen = pygame.display.set_mode((self.config.screen.width, self.config.screen.height), flags)
        pygame.display.set_caption(self.config.screen.title)
        pygame.mouse.set_visible(not self.config.screen.hide_mouse)
        self.clock = pygame.time.Clock()
        self.font_title = pygame.font.Font(None, 82)
        self.font_body = pygame.font.Font(None, 42)
        self.font_small = pygame.font.Font(None, 32)

    def run(self) -> None:
        self.dispatch(AppEvent(EventType.APP_STARTED, source="system"))
        while self.running:
            now = time.monotonic()
            for event in pygame.event.get():
                self.handle_pygame_event(event)
            self.emit_due_timers(now)
            self.render()
            self.clock.tick(self.config.screen.target_fps)
        pygame.quit()

    def handle_pygame_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            self.running = False
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.running = False
            return
        if event.type == pygame.MOUSEBUTTONDOWN:
            self.touch_start_x = event.pos[0]
            mapped = self.map_click_to_event(event.pos)
            if mapped is not None:
                self.dispatch(mapped)
            return
        if event.type == pygame.MOUSEBUTTONUP and self.touch_start_x is not None:
            dx = event.pos[0] - self.touch_start_x
            self.touch_start_x = None
            if self.model.state == AppState.GALLERY_FULLSCREEN:
                if dx < -100:
                    self.dispatch(AppEvent(EventType.SWIPE_LEFT, source="touch"))
                elif dx > 100:
                    self.dispatch(AppEvent(EventType.SWIPE_RIGHT, source="touch"))

    def map_click_to_event(self, pos: tuple[int, int]) -> AppEvent | None:
        state = self.model.state
        if state == AppState.MAIN_MENU:
            if self.button_rects["photo"].collidepoint(pos):
                return AppEvent(EventType.TAP_PHOTO, source="touch")
            if self.button_rects["gallery"].collidepoint(pos):
                return AppEvent(EventType.TAP_GALLERY, source="touch")
        elif state == AppState.PHOTO_PREVIEW:
            if self.button_rects["photo"].collidepoint(pos):
                return AppEvent(EventType.TAP_PHOTO, source="touch")
            if self.button_rects["cancel"].collidepoint(pos):
                return AppEvent(EventType.TAP_CANCEL, source="touch")
        elif state == AppState.GALLERY_GRID:
            if self.button_rects["back"].collidepoint(pos):
                return AppEvent(EventType.TAP_BACK, source="touch")
        elif state == AppState.GALLERY_FULLSCREEN:
            if self.button_rects["back"].collidepoint(pos):
                return AppEvent(EventType.TAP_BACK, source="touch")
            if self.button_rects["photo"].collidepoint(pos):
                return AppEvent(EventType.TAP_PHOTO, source="touch")
        elif state == AppState.REVIEW:
            if self.button_rects["save"].collidepoint(pos):
                return AppEvent(EventType.TAP_SAVE, payload={"filename": "demo_photo.jpg"}, source="touch")
            if self.button_rects["delete"].collidepoint(pos):
                return AppEvent(EventType.TAP_DELETE, source="touch")
        elif state == AppState.DELETE_CONFIRM:
            if self.button_rects["confirm_delete"].collidepoint(pos):
                return AppEvent(EventType.TAP_CONFIRM_DELETE, source="touch")
            if self.button_rects["abort_delete"].collidepoint(pos):
                return AppEvent(EventType.TAP_ABORT_DELETE, source="touch")
        elif state == AppState.QR_DISPLAY:
            if self.button_rects["cancel"].collidepoint(pos):
                return AppEvent(EventType.TAP_CANCEL, source="touch")
        elif state == AppState.ERROR_SCREEN:
            if self.button_rects["back"].collidepoint(pos):
                return AppEvent(EventType.ERROR_ACKNOWLEDGED, source="touch")
        return None

    def emit_due_timers(self, now: float) -> None:
        timers = self.model.timers
        if self.model.state == AppState.MAIN_MENU and self._due(timers.idle_deadline, now):
            self.dispatch(AppEvent(EventType.IDLE_TIMEOUT, source="timer"), now)
        elif self.model.state == AppState.PHOTO_PREVIEW:
            if self._due(timers.preview_warning_deadline, now):
                self.model = self.model.evolve(timers=replace(self.model.timers, preview_warning_deadline=None))
                self.dispatch(AppEvent(EventType.WARNING_TIMEOUT, source="timer"), now)
            elif self._due(timers.preview_total_deadline, now):
                self.dispatch(AppEvent(EventType.IDLE_TIMEOUT, source="timer"), now)
        elif self.model.state == AppState.BOOT and self._due(timers.boot_deadline, now):
            self.dispatch(AppEvent(EventType.TICK, source="timer"), now)
        elif self.model.state == AppState.COUNTDOWN and self._due(timers.countdown_deadline, now):
            self.advance_countdown(now)
        elif self.model.state == AppState.DELETE_CONFIRM and self._due(timers.delete_deadline, now):
            self.dispatch(AppEvent(EventType.DELETE_TIMEOUT, source="timer"), now)
        elif self.model.state == AppState.QR_DISPLAY and self._due(timers.qr_deadline, now):
            self.dispatch(AppEvent(EventType.QR_TIMEOUT, source="timer"), now)

    def advance_countdown(self, now: float) -> None:
        current = self.model.ui.countdown_value or 0
        if current > 1:
            self.model = self.model.evolve(
                ui=replace(self.model.ui, countdown_value=current - 1),
                timers=replace(self.model.timers, countdown_deadline=now + 1.0),
            )
        else:
            self.dispatch(AppEvent(EventType.COUNTDOWN_FINISHED, source="timer"), now)
            self.dispatch(
                AppEvent(EventType.CAPTURE_OK, payload={"photo_path": str(self.config.photo_dir / "demo_photo.jpg")}, source="fake_capture"),
                now,
            )

    def dispatch(self, event: AppEvent, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        result = self.state_machine.transition(self.model, event, now)
        self.model = result.model
        self.apply_actions(result.actions)

    def apply_actions(self, actions: tuple[str, ...]) -> None:
        if not actions:
            return
        if "export_photo" in actions and self.model.session.current_photo_path:
            photos = tuple(self.gallery_service.list_photos())
            self.model = self.model.evolve(
                session=replace(self.model.session, photos=photos, last_saved_photo_path=self.model.session.current_photo_path)
            )
        elif self.model.state in {AppState.GALLERY_GRID, AppState.GALLERY_FULLSCREEN, AppState.ATTRACT_GALLERY}:
            photos = tuple(self.gallery_service.list_photos())
            self.model = self.model.evolve(session=replace(self.model.session, photos=photos))

    def render(self) -> None:
        bg = {
            AppState.BOOT: (10, 25, 47),
            AppState.MAIN_MENU: (20, 20, 30),
            AppState.ATTRACT_GALLERY: (25, 25, 45),
            AppState.GALLERY_GRID: (15, 15, 20),
            AppState.GALLERY_FULLSCREEN: (5, 5, 5),
            AppState.PHOTO_PREVIEW: (30, 30, 40),
            AppState.COUNTDOWN: (60, 30, 20),
            AppState.CAPTURE_PENDING: (20, 40, 20),
            AppState.REVIEW: (40, 50, 40),
            AppState.DELETE_CONFIRM: (50, 20, 20),
            AppState.QR_DISPLAY: (245, 245, 245),
            AppState.ERROR_SCREEN: (80, 10, 10),
            AppState.MAINTENANCE: (50, 50, 10),
        }[self.model.state]
        self.screen.fill(bg)

        self.draw_text(self.config.screen.title, self.font_title, (255, 255, 255), (60, 60))
        self.draw_text(f"Zustand: {self.model.state.name}", self.font_body, (220, 220, 220), (60, 180))
        self.draw_text(self.model.ui.status_text, self.font_body, (255, 220, 120), (60, 240))

        if self.model.ui.countdown_value is not None:
            text = str(self.model.ui.countdown_value)
            surf = self.font_title.render(text, True, (255, 255, 0))
            rect = surf.get_rect(center=(self.config.screen.width // 2, self.config.screen.height // 2))
            self.screen.blit(surf, rect)

        if self.model.ui.error_text:
            self.draw_text(self.model.ui.error_text, self.font_body, (255, 120, 120), (60, 320))

        if self.model.state == AppState.GALLERY_GRID:
            self.draw_gallery_grid()

        self.draw_buttons_for_state()
        self.draw_debug_footer()
        pygame.display.flip()

    def draw_gallery_grid(self) -> None:
        photos = self.model.session.photos
        width = self.config.screen.width
        height = self.config.screen.height

        if not photos:
            hint = f"Keine Fotos gefunden in: {self.config.photo_dir}"
            self.draw_text(hint, self.font_body, (200, 200, 200), (60, round(0.30 * height)))
            return

        columns = max(1, self.config.gallery.grid_columns)
        margin = round(0.06 * width)
        gap = round(0.03 * width)
        top = round(0.30 * height)
        bottom = round(0.85 * height)

        available_w = width - 2 * margin - (columns - 1) * gap
        cell_w = max(20, available_w // columns)
        thumb_w, thumb_h = self.config.gallery.thumbnail_size
        cell_h = max(20, round(cell_w * (thumb_h / thumb_w)))

        x, y, col = margin, top, 0
        for path in photos:
            if y + cell_h > bottom:
                break  # einfache Variante ohne Scrollen; weitere Fotos werden abgeschnitten
            surface = self.get_thumbnail_surface(path, (cell_w, cell_h))
            if surface is not None:
                self.screen.blit(surface, (x, y))
            else:
                pygame.draw.rect(self.screen, (60, 60, 60), (x, y, cell_w, cell_h))
                pygame.draw.rect(self.screen, (150, 60, 60), (x, y, cell_w, cell_h), width=2)
            col += 1
            x += cell_w + gap
            if col >= columns:
                col = 0
                x = margin
                y += cell_h + gap

    def get_thumbnail_surface(self, path: str, size: tuple[int, int]) -> pygame.Surface | None:
        cached = self.gallery_service.get_thumbnail(path)
        if cached is not None and cached.get_size() == size:
            return cached
        try:
            image = pygame.image.load(path).convert()
        except (pygame.error, FileNotFoundError):
            return None
        scaled = pygame.transform.smoothscale(image, size)
        self.gallery_service.remember_thumbnail(path, scaled)
        return scaled

    def draw_buttons_for_state(self) -> None:
        state = self.model.state
        if state == AppState.MAIN_MENU:
            self.draw_button("Fotografieren", self.button_rects["photo"], (0, 150, 0))
            self.draw_button("Galerie", self.button_rects["gallery"], (0, 100, 150))
        elif state == AppState.PHOTO_PREVIEW:
            self.draw_button("Countdown", self.button_rects["photo"], (0, 150, 0))
            self.draw_button("Abbrechen", self.button_rects["cancel"], (100, 100, 100))
        elif state == AppState.GALLERY_GRID:
            self.draw_button("Zurück", self.button_rects["back"], (100, 100, 100))
        elif state == AppState.GALLERY_FULLSCREEN:
            self.draw_button("Zurück", self.button_rects["back"], (100, 100, 100))
            self.draw_button("Fotografieren", self.button_rects["photo"], (0, 150, 0))
        elif state == AppState.REVIEW:
            self.draw_button("Speichern", self.button_rects["save"], (0, 150, 0))
            self.draw_button("Löschen", self.button_rects["delete"], (150, 0, 0))
        elif state == AppState.DELETE_CONFIRM:
            self.draw_button("Wirklich löschen", self.button_rects["confirm_delete"], (150, 0, 0))
            self.draw_button("Abbrechen", self.button_rects["abort_delete"], (100, 100, 100))
        elif state == AppState.QR_DISPLAY:
            self.draw_button("Schließen", self.button_rects["cancel"], (100, 100, 100))
        elif state == AppState.ERROR_SCREEN:
            self.draw_button("Zurück", self.button_rects["back"], (100, 100, 100))

    def draw_button(self, label: str, rect: pygame.Rect, color: tuple[int, int, int]) -> None:
        pygame.draw.rect(self.screen, color, rect, border_radius=12)
        pygame.draw.rect(self.screen, (255, 255, 255), rect, width=2, border_radius=12)
        text_surface = self.font_body.render(label, True, (255, 255, 255))
        text_rect = text_surface.get_rect(center=rect.center)
        self.screen.blit(text_surface, text_rect)

    def draw_text(self, text: str, font: pygame.font.Font, color: tuple[int, int, int], pos: tuple[int, int]) -> None:
        surf = font.render(text, True, color)
        self.screen.blit(surf, pos)

    def draw_debug_footer(self) -> None:
        lines = [
            f"Letztes Event: {self.model.last_event.type.name if self.model.last_event else '-'}",
            f"Fotos im Speicher: {len(self.model.session.photos)}",
            f"FPS: {self.clock.get_fps():.1f}",
            "ESC beendet diese Test-App.",
        ]
        y = self.config.screen.height - 140
        for line in lines:
            self.draw_text(line, self.font_small, (220, 220, 220), (60, y))
            y += 30

    @staticmethod
    def _due(deadline: float | None, now: float) -> bool:
        return deadline is not None and now >= deadline


def main() -> int:
    app = DemoApp()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
