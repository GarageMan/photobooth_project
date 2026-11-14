"""
test_renderer_admin_buttons.py
===============================
Sprint-14-Nachbesserung (Live-Test): auf "Galerie aufraeumen" fehlten die
Tasten "Loeschen"/"Zurueck" - layout.button_rects_for_state() und die
Klick-Erkennung kannten sie, renderer._draw_buttons() zeichnete sie aber
nicht (zwei getrennte Stellen, der Test pruefte nur die State Machine).

Dieser Test schliesst die Luecke fuer alle drei neuen Screens: er rendert
sie mit pygame (SDL-Dummy-Treiber) und prueft, dass an der Mitte JEDES in
button_rects_for_state() angegebenen Rechtecks tatsaechlich etwas anderes
als die Hintergrundfarbe liegt - ein Button, der nur klickbar, aber nicht
sichtbar ist, faellt damit sofort auf.

    SDL_VIDEODRIVER=dummy python3 -m pytest test_renderer_admin_buttons.py -v
"""

from __future__ import annotations

import os
import unittest
from dataclasses import replace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from config import DEFAULT_CONFIG
from layout import build_layout, button_rects_for_state
from models import AppModel, UiState
from renderer import Renderer
from states import AppState


class AdminButtonsAreDrawnTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.init()
        cls.screen = pygame.display.set_mode(
            (DEFAULT_CONFIG.screen.width, DEFAULT_CONFIG.screen.height)
        )
        cls.renderer = Renderer(config=DEFAULT_CONFIG, screen=cls.screen)

    @classmethod
    def tearDownClass(cls) -> None:
        pygame.quit()

    def _render(self, state: AppState, **ui_changes) -> None:
        ui = replace(UiState(), **ui_changes)
        photos = tuple(f"/nonexistent/foto_{i}.jpg" for i in range(3))
        model = AppModel(state=state, ui=ui)
        model = model.evolve(session=replace(model.session, photos=photos))
        self.renderer.render(model, 30.0)

    def _assert_all_buttons_visible(self, state: AppState) -> None:
        rects = build_layout(DEFAULT_CONFIG.screen.width, DEFAULT_CONFIG.screen.height)
        background = self.renderer._background_color(state)
        for name, rect in button_rects_for_state(state, rects).items():
            pixel = tuple(self.screen.get_at(rect.center))[:3]
            self.assertNotEqual(
                pixel, tuple(background),
                f"{state.name}: Taste '{name}' ist klickbar, aber nicht gezeichnet.",
            )

    def test_hdmi_menu_back_button_is_drawn(self) -> None:
        self._render(AppState.ADMIN_HDMI_MENU, admin_hdmi_available=True)
        self._assert_all_buttons_visible(AppState.ADMIN_HDMI_MENU)

    def test_gallery_cleanup_buttons_are_drawn_without_selection(self) -> None:
        self._render(AppState.ADMIN_GALLERY_CLEANUP)
        self._assert_all_buttons_visible(AppState.ADMIN_GALLERY_CLEANUP)

    def test_gallery_cleanup_buttons_are_drawn_with_selection(self) -> None:
        self._render(
            AppState.ADMIN_GALLERY_CLEANUP,
            admin_gallery_cleanup_selected=("/nonexistent/foto_0.jpg",),
        )
        self._assert_all_buttons_visible(AppState.ADMIN_GALLERY_CLEANUP)

    def test_gallery_cleanup_confirm_buttons_are_drawn(self) -> None:
        self._render(
            AppState.ADMIN_GALLERY_CLEANUP_CONFIRM,
            status_text="Wirklich 1 Bild löschen?",
            admin_gallery_cleanup_selected=("/nonexistent/foto_0.jpg",),
        )
        self._assert_all_buttons_visible(AppState.ADMIN_GALLERY_CLEANUP_CONFIRM)


if __name__ == "__main__":
    unittest.main()
