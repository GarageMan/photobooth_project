"""
test_state_machine_shutdown.py
==============================
Tests fuer den versteckten Shutdown-Fluss in der State Machine (Schritt 3.2):
Geste -> PIN_ENTRY, Ziffern-/Backspace-Logik, Auswertung jedes PinResult,
Abbruch/Idle zurueck ins Menue und SHUTDOWN_GOODBYE -> power_off.

Reine Logik, keine Hardware. Alle Zeitpunkte werden injiziert.

    python3 -m pytest test_state_machine_shutdown.py -v
"""

from __future__ import annotations

import unittest

from config import DEFAULT_CONFIG
from events import AppEvent, EventType
from shutdown_service import PinResult
from state_machine import StateMachine, _MAX_PIN_LENGTH
from states import AppState


class ShutdownFlowTestCase(unittest.TestCase):
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

    def _go_to_main_menu(self) -> None:
        # BOOT -> MAIN_MENU (Boot-Deadline ueberschritten)
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.assertEqual(self.model.state, AppState.MAIN_MENU)

    def _enter_pin_entry(self, now_offset: float = 5.2) -> None:
        self._go_to_main_menu()
        self.transition(EventType.SHUTDOWN_GESTURE_DETECTED, now_offset=now_offset)
        self.assertEqual(self.model.state, AppState.PIN_ENTRY)

    def _type(self, digits: str, base_offset: float) -> None:
        for i, ch in enumerate(digits):
            self.transition(EventType.PIN_DIGIT, now_offset=base_offset + i * 0.01, payload={"digit": ch})

    # -- Geste -> PIN_ENTRY ------------------------------------------------

    def test_gesture_from_main_menu_enters_pin_entry(self) -> None:
        self._go_to_main_menu()
        result = self.transition(EventType.SHUTDOWN_GESTURE_DETECTED, now_offset=5.2)
        self.assertEqual(result.model.state, AppState.PIN_ENTRY)
        self.assertEqual(result.model.ui.pin_entry, "")
        # Idle-Timeout der PIN-Eingabe ist gesetzt.
        expected_idle = self.now + 5.2 + self.config.shutdown.pin_entry_idle_seconds
        self.assertAlmostEqual(result.model.timers.idle_deadline, expected_idle)

    def test_gesture_ignored_outside_main_menu(self) -> None:
        # In PHOTO_INTRO darf die Geste nichts ausloesen.
        self._go_to_main_menu()
        self.transition(EventType.TAP_PHOTO, now_offset=5.2)
        self.assertEqual(self.model.state, AppState.PHOTO_INTRO)
        result = self.transition(EventType.SHUTDOWN_GESTURE_DETECTED, now_offset=5.3)
        self.assertEqual(result.model.state, AppState.PHOTO_INTRO)

    # -- Ziffern-/Backspace-Logik ------------------------------------------

    def test_digits_accumulate(self) -> None:
        self._enter_pin_entry()
        self._type("123", base_offset=5.3)
        self.assertEqual(self.model.ui.pin_entry, "123")

    def test_backspace_removes_last_digit(self) -> None:
        self._enter_pin_entry()
        self._type("123", base_offset=5.3)
        self.transition(EventType.PIN_BACKSPACE, now_offset=5.4)
        self.assertEqual(self.model.ui.pin_entry, "12")

    def test_backspace_on_empty_is_harmless(self) -> None:
        self._enter_pin_entry()
        self.transition(EventType.PIN_BACKSPACE, now_offset=5.3)
        self.assertEqual(self.model.ui.pin_entry, "")

    def test_non_digit_is_ignored(self) -> None:
        self._enter_pin_entry()
        self.transition(EventType.PIN_DIGIT, now_offset=5.3, payload={"digit": "a"})
        self.transition(EventType.PIN_DIGIT, now_offset=5.4, payload={"digit": ""})
        self.assertEqual(self.model.ui.pin_entry, "")

    def test_length_is_capped(self) -> None:
        self._enter_pin_entry()
        self._type("1" * (_MAX_PIN_LENGTH + 5), base_offset=5.3)
        self.assertEqual(len(self.model.ui.pin_entry), _MAX_PIN_LENGTH)

    def test_digit_refreshes_idle_deadline(self) -> None:
        self._enter_pin_entry(now_offset=5.2)
        self.transition(EventType.PIN_DIGIT, now_offset=8.0, payload={"digit": "1"})
        expected_idle = self.now + 8.0 + self.config.shutdown.pin_entry_idle_seconds
        self.assertAlmostEqual(self.model.timers.idle_deadline, expected_idle)

    # -- PIN_SUBMIT: Auswertung des PinResult ------------------------------

    def test_accepted_goes_to_goodbye(self) -> None:
        self._enter_pin_entry()
        self._type("1234", base_offset=5.3)
        result = self.transition(
            EventType.PIN_SUBMIT, now_offset=6.0,
            payload={"pin_result": PinResult.ACCEPTED},
        )
        self.assertEqual(result.model.state, AppState.SHUTDOWN_GOODBYE)
        expected = self.now + 6.0 + self.config.shutdown.goodbye_seconds
        self.assertAlmostEqual(result.model.timers.shutdown_goodbye_deadline, expected)
        self.assertEqual(result.model.ui.pin_entry, "")

    def test_rejected_stays_clears_buffer_and_arms_error_flash(self) -> None:
        self._enter_pin_entry()
        self._type("99", base_offset=5.3)
        result = self.transition(
            EventType.PIN_SUBMIT, now_offset=6.0,
            payload={"pin_result": PinResult.REJECTED, "attempts_left": 2},
        )
        self.assertEqual(result.model.state, AppState.PIN_ENTRY)
        self.assertEqual(result.model.ui.pin_entry, "")
        self.assertIn("2", result.model.ui.error_text)
        expected = self.now + 6.0 + self.config.shutdown.error_flash_seconds
        self.assertAlmostEqual(result.model.timers.pin_error_deadline, expected)

    def test_rejected_now_locked_shows_wait_minutes(self) -> None:
        self._enter_pin_entry()
        result = self.transition(
            EventType.PIN_SUBMIT, now_offset=6.0,
            payload={"pin_result": PinResult.REJECTED_NOW_LOCKED, "remaining_seconds": 1800.0},
        )
        self.assertEqual(result.model.state, AppState.PIN_ENTRY)
        self.assertIn("30", result.model.ui.error_text)
        self.assertIsNotNone(result.model.timers.pin_error_deadline)

    def test_locked_shows_wait_minutes(self) -> None:
        self._enter_pin_entry()
        result = self.transition(
            EventType.PIN_SUBMIT, now_offset=6.0,
            payload={"pin_result": PinResult.LOCKED, "remaining_seconds": 600.0},
        )
        self.assertEqual(result.model.state, AppState.PIN_ENTRY)
        self.assertIn("10", result.model.ui.error_text)

    def test_not_configured_shows_hint(self) -> None:
        self._enter_pin_entry()
        self._type("1234", base_offset=5.3)
        result = self.transition(
            EventType.PIN_SUBMIT, now_offset=6.0,
            payload={"pin_result": PinResult.NOT_CONFIGURED},
        )
        self.assertEqual(result.model.state, AppState.PIN_ENTRY)
        self.assertIn("nicht eingerichtet", result.model.ui.error_text)
        self.assertEqual(result.model.ui.pin_entry, "")

    # -- Verlassen des PIN-Screens -----------------------------------------

    def test_cancel_returns_to_main_menu_and_clears_pin(self) -> None:
        self._enter_pin_entry()
        self._type("12", base_offset=5.3)
        result = self.transition(EventType.PIN_ENTRY_CANCEL, now_offset=6.0)
        self.assertEqual(result.model.state, AppState.MAIN_MENU)
        self.assertEqual(result.model.ui.pin_entry, "")

    def test_idle_timeout_returns_to_main_menu(self) -> None:
        self._enter_pin_entry()
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=40.0)
        self.assertEqual(result.model.state, AppState.MAIN_MENU)

    def test_main_menu_model_clears_shutdown_state(self) -> None:
        # Nach einem Fehlversuch (pin_error_deadline gesetzt) muss die
        # Rueckkehr ins Menue alle Shutdown-Spuren beseitigen.
        self._enter_pin_entry()
        self.transition(
            EventType.PIN_SUBMIT, now_offset=6.0,
            payload={"pin_result": PinResult.REJECTED, "attempts_left": 2},
        )
        self.assertIsNotNone(self.model.timers.pin_error_deadline)
        result = self.transition(EventType.PIN_ENTRY_CANCEL, now_offset=6.5)
        self.assertEqual(result.model.state, AppState.MAIN_MENU)
        self.assertIsNone(result.model.timers.pin_error_deadline)
        self.assertIsNone(result.model.timers.shutdown_goodbye_deadline)
        self.assertEqual(result.model.ui.pin_entry, "")

    # -- SHUTDOWN_GOODBYE ---------------------------------------------------

    def _reach_goodbye(self) -> None:
        self._enter_pin_entry()
        self.transition(
            EventType.PIN_SUBMIT, now_offset=6.0,
            payload={"pin_result": PinResult.ACCEPTED},
        )
        self.assertEqual(self.model.state, AppState.SHUTDOWN_GOODBYE)

    def test_goodbye_timeout_emits_power_off(self) -> None:
        self._reach_goodbye()
        result = self.transition(EventType.SHUTDOWN_TIMEOUT, now_offset=15.0)
        self.assertIn("power_off", result.actions)
        # Zustand bleibt SHUTDOWN_GOODBYE - das Poweroff macht die App.
        self.assertEqual(result.model.state, AppState.SHUTDOWN_GOODBYE)

    def test_goodbye_ignores_taps_and_button(self) -> None:
        self._reach_goodbye()
        for ev in (EventType.TAP_CANCEL, EventType.BUTTON_PRESS, EventType.TAP_BACK):
            result = self.transition(ev, now_offset=7.0)
            self.assertEqual(result.model.state, AppState.SHUTDOWN_GOODBYE)
            self.assertEqual(result.actions, ())


if __name__ == "__main__":
    unittest.main()