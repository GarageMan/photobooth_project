"""Sprint 16: Veranstaltungs-Schrift fuer Gaeste-Buttons (z. B. EIN MOMENT ADVENT)."""
import unittest
from dataclasses import replace

import pygame

from config import DEFAULT_CONFIG
from events import AppEvent, EventType
from layout import build_layout, button_rects_for_state
from models import AppModel, UiState
from renderer import GUEST_FONT_STATES, Renderer
from state_machine import StateMachine
from states import AppState


class EventFontSettingsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = StateMachine(DEFAULT_CONFIG)
        self.now = 1000.0
        model = self.machine.initial_model(self.now)
        model = self.machine._go_admin_event_settings(model, self.now).model
        self.model = self.send(EventType.ADMIN_EVENT_SETTINGS_READY, model, {"event_font_enabled": True}).model

    def send(self, event_type, model=None, payload=None):
        self.now += 0.1
        return self.machine.transition(
            model if model is not None else self.model, AppEvent(event_type, payload=payload or {}, source="t"), self.now,
        )

    def test_config_default_off(self) -> None:
        self.assertFalse(DEFAULT_CONFIG.event_font_enabled)
        self.assertTrue((DEFAULT_CONFIG.assets_dir / DEFAULT_CONFIG.event_font_file).is_file())

    def test_ready_and_toggle(self) -> None:
        self.assertTrue(self.model.ui.admin_event_font_enabled)
        self.model = self.send(EventType.TAP_ADMIN_EVENT_TOGGLE, payload={"field": "font"}).model
        self.assertFalse(self.model.ui.admin_event_font_enabled)

    def test_cancel_restores(self) -> None:
        self.model = self.send(EventType.TAP_ADMIN_EVENT_TOGGLE, payload={"field": "font"}).model
        self.model = self.send(EventType.TAP_BACK).model
        self.assertTrue(self.model.ui.admin_event_font_enabled)

    def test_defaults_switch_font_off(self) -> None:
        self.model = self.send(EventType.TAP_ADMIN_EVENT_DEFAULTS).model
        self.assertFalse(self.model.ui.admin_event_font_enabled)

    def test_tile_uses_free_slot(self) -> None:
        r = build_layout(1280, 720)
        self.assertEqual(r.admin_event_font_toggle.size, r.admin_event_defaults_button.size)
        self.assertEqual(r.admin_event_font_toggle.top, r.admin_event_defaults_button.top)
        self.assertEqual(r.admin_event_font_toggle.left, r.admin_event_color_style_button.left)
        for other in (r.admin_event_defaults_button, r.admin_event_color_style_button, r.left, r.right,
                      r.admin_event_page_button):
            self.assertFalse(r.admin_event_font_toggle.colliderect(other))
        self.assertIn("admin_event_toggle_font", button_rects_for_state(AppState.ADMIN_EVENT_SETTINGS, r))


class EventFontRendererTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.init()
        cls.screen = pygame.display.set_mode((1280, 720))

    def renderer(self, enabled: bool, state: AppState) -> Renderer:
        r = Renderer(config=replace(DEFAULT_CONFIG, event_font_enabled=enabled), screen=self.screen)
        r.render(AppModel(state=state, ui=UiState()), fps=30.0)
        return r

    def test_guest_screen_uses_event_font(self) -> None:
        r = self.renderer(True, AppState.MAIN_MENU)
        self.assertIsNotNone(r._event_button_font(50, "Fotografieren"))

    def test_admin_screens_keep_default_font(self) -> None:
        for state in (AppState.ADMIN_MENU, AppState.ADMIN_EVENT_SETTINGS, AppState.PIN_ENTRY, AppState.MAINTENANCE):
            self.assertNotIn(state, GUEST_FONT_STATES)
            r = self.renderer(True, state)
            self.assertIsNone(r._event_button_font(50, "Speichern"), state)

    def test_switch_off_keeps_default_font(self) -> None:
        r = self.renderer(False, AppState.MAIN_MENU)
        self.assertIsNone(r._event_button_font(50, "Fotografieren"))

    def test_missing_glyph_falls_back(self) -> None:
        r = self.renderer(True, AppState.MAIN_MENU)
        self.assertIsNone(r._event_button_font(50, "✓ OK"))

    def test_missing_font_file_falls_back(self) -> None:
        r = Renderer(config=replace(DEFAULT_CONFIG, event_font_enabled=True, event_font_file="fehlt.ttf"),
                     screen=self.screen)
        r.render(AppModel(state=AppState.MAIN_MENU, ui=UiState()), fps=30.0)
        self.assertIsNone(r._event_button_font(50, "Fotografieren"))


if __name__ == "__main__":
    unittest.main()
