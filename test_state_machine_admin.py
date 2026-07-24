"""
test_state_machine_admin.py
===========================
Tests fuer das Service-/Admin-Menue (Etappe 4.1):
PIN akzeptiert -> ADMIN_MENU, Herunterfahren aus dem Menue, Zurueck,
Idle-Timeout, und dass der Hardware-Taster im Menue nichts ausloest.

Reine Logik, keine Hardware. Alle Zeitpunkte werden injiziert.

    python3 -m pytest test_state_machine_admin.py -v
"""

from __future__ import annotations

import unittest

from config import DEFAULT_CONFIG
from events import AppEvent, EventType
from shutdown_service import PinResult
from state_machine import StateMachine
from states import AppState


class AdminMenuTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.config = DEFAULT_CONFIG
        self.machine = StateMachine(self.config)
        self.now = 1000.0
        self.model = self.machine.initial_model(self.now)

    # -- Hilfsmittel -------------------------------------------------------

    def transition(self, event_type: EventType, now_offset: float = 0.0, payload: dict | None = None):
        event = AppEvent(event_type, payload=payload or {}, source="test")
        result = self.machine.transition(self.model, event, self.now + now_offset)
        self.model = result.model
        return result

    def _go_to_admin_menu(self, now_offset: float = 5.2) -> None:
        # BOOT -> MAIN_MENU -> (Geste) PIN_ENTRY -> (PIN ok) ADMIN_MENU
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.assertEqual(self.model.state, AppState.MAIN_MENU)
        self.transition(EventType.SHUTDOWN_GESTURE_DETECTED, now_offset=now_offset)
        self.assertEqual(self.model.state, AppState.PIN_ENTRY)
        self.transition(
            EventType.PIN_SUBMIT,
            now_offset=now_offset + 1.0,
            payload={"pin_result": PinResult.ACCEPTED},
        )

    # -- PIN -> Menue ------------------------------------------------------

    def test_accepted_pin_opens_admin_menu_not_shutdown(self) -> None:
        # Kernaenderung von Etappe 4.1: die PIN fuehrt NICHT mehr direkt
        # in die Abschieds-Animation.
        self._go_to_admin_menu()
        self.assertEqual(self.model.state, AppState.ADMIN_MENU)
        self.assertNotEqual(self.model.state, AppState.SHUTDOWN_GOODBYE)

    def test_pin_buffer_is_cleared_on_entering_menu(self) -> None:
        self._go_to_admin_menu()
        self.assertEqual(self.model.ui.pin_entry, "")
        self.assertIsNone(self.model.ui.error_text)

    def test_idle_deadline_is_set_on_entering_menu(self) -> None:
        self._go_to_admin_menu(now_offset=5.2)
        expected = self.now + 6.2 + self.config.timeouts.admin_menu_idle_seconds
        self.assertAlmostEqual(self.model.timers.idle_deadline, expected)

    # -- Menuepunkte -------------------------------------------------------

    def test_shutdown_item_starts_goodbye(self) -> None:
        self._go_to_admin_menu()
        self.transition(EventType.TAP_ADMIN_SHUTDOWN, now_offset=10.0)
        self.assertEqual(self.model.state, AppState.SHUTDOWN_GOODBYE)

    def test_back_returns_to_main_menu(self) -> None:
        self._go_to_admin_menu()
        self.transition(EventType.TAP_BACK, now_offset=10.0)
        self.assertEqual(self.model.state, AppState.MAIN_MENU)

    def test_idle_timeout_returns_to_main_menu(self) -> None:
        self._go_to_admin_menu()
        self.transition(EventType.IDLE_TIMEOUT, now_offset=40.0)
        self.assertEqual(self.model.state, AppState.MAIN_MENU)

    def test_hardware_button_does_nothing_in_admin_menu(self) -> None:
        # Der Taster darf im Service-Menue keine Aufnahme starten.
        self._go_to_admin_menu()
        self.transition(EventType.BUTTON_PRESS, now_offset=10.0)
        self.assertEqual(self.model.state, AppState.ADMIN_MENU)

    def test_unimplemented_items_refresh_idle_timer(self) -> None:
        # Etappe 2-4 noch nicht implementiert: Zustand bleibt, aber der
        # Idle-Timer wird neu aufgezogen (Fehlgriff schliesst das Menue nicht).
        self._go_to_admin_menu(now_offset=5.2)
        before = self.model.timers.idle_deadline
        self.transition(EventType.TAP_ADMIN_STATUS, now_offset=20.0)
        self.assertEqual(self.model.state, AppState.ADMIN_MENU)
        self.assertGreater(self.model.timers.idle_deadline, before)


class RendererStateCoverageTestCase(unittest.TestCase):
    """Reiner Abdeckungstest: jeder AppState-Wert muss in renderer.py an
    allen Stellen behandelt sein, die als Dictionary/Mapping ueber ALLE
    Zustaende implementiert sind (aktuell: _background_color). Waere
    dieser Test schon bei Etappe 4.1 dabei gewesen, haette er den
    KeyError fuer ADMIN_MENU vor dem Livetest auf dem Pi gefangen, statt
    erst danach - Renderer-Zustandslisten dieser Art laufen leicht
    auseinander (siehe README, Abschnitt "Enum-getriebene Pipelines
    konsequent pflegen").

    Bewusst kein pygame.display noetig - _background_color ist eine
    reine @staticmethod ohne Bildschirmzugriff.
    """

    def test_background_color_covers_every_app_state(self) -> None:
        from renderer import Renderer
        from states import AppState

        missing = []
        for state in AppState:
            try:
                Renderer._background_color(state)
            except KeyError:
                missing.append(state.name)
        self.assertEqual(
            missing, [],
            f"AppState(s) ohne Eintrag in Renderer._background_color: {missing}",
        )


class AdminMenuItemsTestCase(unittest.TestCase):
    """Prueft die Menuedefinition selbst - vor allem, dass Zeichnung und
    Treffererkennung dieselben Schluessel benutzen und sich die Buttons
    nicht ueberlappen."""

    def test_rect_keys_match_items(self) -> None:
        from admin_menu import ADMIN_MENU_ITEMS, build_admin_rects

        rects = build_admin_rects(1280, 720)
        self.assertEqual(set(rects), {item.key for item in ADMIN_MENU_ITEMS})

    def test_rects_do_not_overlap_and_fit_on_screen(self) -> None:
        from admin_menu import build_admin_rects

        width, height = 1280, 720
        rects = list(build_admin_rects(width, height).values())
        for index, rect in enumerate(rects):
            self.assertGreaterEqual(rect.left, 0)
            self.assertGreaterEqual(rect.top, 0)
            self.assertLessEqual(rect.right, width)
            self.assertLessEqual(rect.bottom, height)
            for other in rects[index + 1:]:
                self.assertFalse(rect.colliderect(other))


if __name__ == "__main__":
    unittest.main()
