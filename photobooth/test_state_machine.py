from __future__ import annotations

import time
import unittest

from config import DEFAULT_CONFIG
from events import AppEvent, EventType
from state_machine import StateMachine
from states import AppState


class StateMachineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.config = DEFAULT_CONFIG
        self.machine = StateMachine(self.config)
        self.now = 1000.0
        self.model = self.machine.initial_model(self.now)

    def transition(self, event_type: EventType, now_offset: float = 0.0, payload: dict | None = None):
        event = AppEvent(event_type, payload=payload or {}, source="test")
        result = self.machine.transition(self.model, event, self.now + now_offset)
        self.model = result.model
        return result

    def boot_and_go_to_countdown_menu(self) -> None:
        """Hilfsmethode: durchlaeuft BOOT -> MAIN_MENU -> PHOTO_INTRO -> PHOTO_PREVIEW."""
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.TAP_PHOTO, now_offset=self.config.timeouts.boot_seconds + 0.2)
        self.transition(EventType.BUTTON_PRESS, now_offset=self.config.timeouts.boot_seconds + 0.3)

    def test_boot_goes_to_main_menu_after_deadline(self) -> None:
        result = self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.assertEqual(result.model.state, AppState.MAIN_MENU)
        self.assertIn("set_led_main_menu", result.actions)

    def test_main_menu_to_photo_intro_on_photo_tap(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        result = self.transition(EventType.TAP_PHOTO, now_offset=self.config.timeouts.boot_seconds + 0.2)
        self.assertEqual(result.model.state, AppState.PHOTO_INTRO)

    def test_main_menu_to_instructions_on_tap(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        result = self.transition(EventType.TAP_INSTRUCTIONS, now_offset=self.config.timeouts.boot_seconds + 0.2)
        self.assertEqual(result.model.state, AppState.INSTRUCTIONS)

    def test_instructions_back_to_main_menu(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.TAP_INSTRUCTIONS, now_offset=self.config.timeouts.boot_seconds + 0.2)
        result = self.transition(EventType.TAP_BACK, now_offset=self.config.timeouts.boot_seconds + 0.3)
        self.assertEqual(result.model.state, AppState.MAIN_MENU)

    def test_photo_intro_to_countdown_menu_on_trigger(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.TAP_PHOTO, now_offset=self.config.timeouts.boot_seconds + 0.2)
        result = self.transition(EventType.BUTTON_PRESS, now_offset=self.config.timeouts.boot_seconds + 0.3)
        self.assertEqual(result.model.state, AppState.PHOTO_PREVIEW)
        self.assertIn("start_preview", result.actions)

    def test_photo_intro_idle_timeout_goes_to_main_menu(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.TAP_PHOTO, now_offset=self.config.timeouts.boot_seconds + 0.2)
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=self.config.timeouts.preview_total_seconds + 1)
        self.assertEqual(result.model.state, AppState.MAIN_MENU)

    def test_countdown_menu_to_countdown_on_trigger(self) -> None:
        self.boot_and_go_to_countdown_menu()
        result = self.transition(EventType.BUTTON_PRESS, now_offset=self.config.timeouts.boot_seconds + 0.4)
        self.assertEqual(result.model.state, AppState.COUNTDOWN)
        self.assertEqual(result.model.ui.countdown_value, self.config.timeouts.countdown_seconds[0])

    def test_countdown_menu_idle_timeout_goes_to_main_menu(self) -> None:
        self.boot_and_go_to_countdown_menu()
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=self.config.timeouts.preview_total_seconds + 5)
        self.assertEqual(result.model.state, AppState.MAIN_MENU)

    def test_capture_success_leads_to_review(self) -> None:
        self.boot_and_go_to_countdown_menu()
        self.transition(EventType.BUTTON_PRESS, now_offset=self.config.timeouts.boot_seconds + 0.4)
        self.transition(EventType.COUNTDOWN_FINISHED, now_offset=self.config.timeouts.boot_seconds + 4.5)
        result = self.transition(EventType.CAPTURE_OK, now_offset=self.config.timeouts.boot_seconds + 4.6, payload={"photo_path": "/tmp/test.jpg"})
        self.assertEqual(result.model.state, AppState.REVIEW)
        self.assertEqual(result.model.session.current_photo_path, "/tmp/test.jpg")

    def test_review_idle_timeout_auto_deletes(self) -> None:
        self.boot_and_go_to_countdown_menu()
        self.transition(EventType.BUTTON_PRESS, now_offset=self.config.timeouts.boot_seconds + 0.4)
        self.transition(EventType.COUNTDOWN_FINISHED, now_offset=self.config.timeouts.boot_seconds + 4.5)
        self.transition(EventType.CAPTURE_OK, now_offset=self.config.timeouts.boot_seconds + 4.6, payload={"photo_path": "/tmp/test.jpg"})
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=self.config.timeouts.boot_seconds + 4.6 + self.config.timeouts.review_idle_seconds + 1)
        self.assertEqual(result.model.state, AppState.MAIN_MENU)
        self.assertIsNone(result.model.session.current_photo_path)
        self.assertIn("delete_photo", result.actions)

    def test_review_save_leads_to_qr_display(self) -> None:
        self.boot_and_go_to_countdown_menu()
        self.transition(EventType.BUTTON_PRESS, now_offset=self.config.timeouts.boot_seconds + 0.4)
        self.transition(EventType.COUNTDOWN_FINISHED, now_offset=self.config.timeouts.boot_seconds + 4.5)
        self.transition(EventType.CAPTURE_OK, now_offset=self.config.timeouts.boot_seconds + 4.6, payload={"photo_path": "/tmp/test.jpg"})
        result = self.transition(EventType.TAP_SAVE, now_offset=self.config.timeouts.boot_seconds + 4.7, payload={"filename": "test.jpg"})
        self.assertEqual(result.model.state, AppState.QR_DISPLAY)
        self.assertEqual(result.model.session.qr_filename, "test.jpg")

    def test_qr_display_back_goes_to_photo_intro(self) -> None:
        self.boot_and_go_to_countdown_menu()
        self.transition(EventType.BUTTON_PRESS, now_offset=self.config.timeouts.boot_seconds + 0.4)
        self.transition(EventType.COUNTDOWN_FINISHED, now_offset=self.config.timeouts.boot_seconds + 4.5)
        self.transition(EventType.CAPTURE_OK, now_offset=self.config.timeouts.boot_seconds + 4.6, payload={"photo_path": "/tmp/test.jpg"})
        self.transition(EventType.TAP_SAVE, now_offset=self.config.timeouts.boot_seconds + 4.7, payload={"filename": "test.jpg"})
        result = self.transition(EventType.TAP_CANCEL, now_offset=self.config.timeouts.boot_seconds + 4.8)
        self.assertEqual(result.model.state, AppState.PHOTO_INTRO)

    def test_review_delete_leads_to_confirm(self) -> None:
        self.boot_and_go_to_countdown_menu()
        self.transition(EventType.BUTTON_PRESS, now_offset=self.config.timeouts.boot_seconds + 0.4)
        self.transition(EventType.COUNTDOWN_FINISHED, now_offset=self.config.timeouts.boot_seconds + 4.5)
        self.transition(EventType.CAPTURE_OK, now_offset=self.config.timeouts.boot_seconds + 4.6, payload={"photo_path": "/tmp/test.jpg"})
        result = self.transition(EventType.TAP_DELETE, now_offset=self.config.timeouts.boot_seconds + 4.7)
        self.assertEqual(result.model.state, AppState.DELETE_CONFIRM)

    def test_delete_confirm_returns_to_main_menu(self) -> None:
        self.boot_and_go_to_countdown_menu()
        self.transition(EventType.BUTTON_PRESS, now_offset=self.config.timeouts.boot_seconds + 0.4)
        self.transition(EventType.COUNTDOWN_FINISHED, now_offset=self.config.timeouts.boot_seconds + 4.5)
        self.transition(EventType.CAPTURE_OK, now_offset=self.config.timeouts.boot_seconds + 4.6, payload={"photo_path": "/tmp/test.jpg"})
        self.transition(EventType.TAP_DELETE, now_offset=self.config.timeouts.boot_seconds + 4.7)
        result = self.transition(EventType.TAP_CONFIRM_DELETE, now_offset=self.config.timeouts.boot_seconds + 4.8)
        self.assertEqual(result.model.state, AppState.MAIN_MENU)

    def test_main_menu_idle_timeout_goes_to_attract_gallery(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=self.config.timeouts.main_menu_idle_seconds + 1)
        self.assertEqual(result.model.state, AppState.ATTRACT_GALLERY)

    def test_gallery_grid_idle_timeout_goes_to_main_menu(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.TAP_GALLERY, now_offset=self.config.timeouts.boot_seconds + 0.2)
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=self.config.timeouts.gallery_idle_seconds + 1)
        self.assertEqual(result.model.state, AppState.MAIN_MENU)

    def test_gallery_fullscreen_idle_timeout_goes_back_to_grid(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.TAP_GALLERY, now_offset=self.config.timeouts.boot_seconds + 0.2)
        self.transition(EventType.TAP_FULLSCREEN_PHOTO, now_offset=self.config.timeouts.boot_seconds + 0.3, payload={"index": 0})
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=self.config.timeouts.gallery_fullscreen_idle_seconds + 1)
        self.assertEqual(result.model.state, AppState.GALLERY_GRID)


if __name__ == "__main__":
    unittest.main()
