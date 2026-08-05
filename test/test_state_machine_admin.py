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

from admin_usb_export import ExportConflict
from config import DEFAULT_CONFIG
from events import AppEvent, EventType
from admin_service import PinResult
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
        self._go_to_admin_menu()
        self.transition(EventType.BUTTON_PRESS, now_offset=10.0)
        self.assertEqual(self.model.state, AppState.ADMIN_MENU)

    def test_every_menu_item_leads_somewhere(self) -> None:
        from admin_menu import ADMIN_MENU_ITEMS

        for item in ADMIN_MENU_ITEMS:
            if not item.enabled:
                continue
            self.setUp()
            self._go_to_admin_menu(now_offset=5.2)
            result = self.transition(item.event_type, now_offset=20.0)
            self.assertNotEqual(
                result.model.state, AppState.ADMIN_MENU,
                f"Menuepunkt '{item.key}' ist aktiv, fuehrt aber nirgendwohin.",
            )


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


class AdminStatusTestCase(unittest.TestCase):
    """Tests fuer 'Status / Diagnose' (Etappe 4.3): Betreten sammelt
    Diagnosezeilen, ADMIN_STATUS_READY befuellt sie, Zurueck/Idle fuehrt
    zum Menue (nicht ins Hauptmenue) zurueck."""

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

    def _go_to_admin_status(self, now_offset: float = 5.2) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.SHUTDOWN_GESTURE_DETECTED, now_offset=now_offset)
        self.transition(
            EventType.PIN_SUBMIT,
            now_offset=now_offset + 1.0,
            payload={"pin_result": PinResult.ACCEPTED},
        )
        self.assertEqual(self.model.state, AppState.ADMIN_MENU)
        result = self.transition(EventType.TAP_ADMIN_STATUS, now_offset=now_offset + 2.0)
        self.assertEqual(result.model.state, AppState.ADMIN_STATUS)
        self.assertIn("collect_admin_status", result.actions)

    def test_tap_status_enters_admin_status_and_requests_collection(self) -> None:
        self._go_to_admin_status()
        self.assertEqual(self.model.ui.admin_status_lines, ())

    def test_status_ready_fills_lines(self) -> None:
        self._go_to_admin_status()
        lines = ("Speicherplatz: 10 GB frei", "Fotos in der Galerie: 3 Fotos")
        result = self.transition(EventType.ADMIN_STATUS_READY, now_offset=10.0, payload={"lines": lines})
        self.assertEqual(result.model.state, AppState.ADMIN_STATUS)
        self.assertEqual(result.model.ui.admin_status_lines, lines)

    def test_back_returns_to_admin_menu_not_main_menu(self) -> None:
        self._go_to_admin_status()
        result = self.transition(EventType.TAP_BACK, now_offset=10.0)
        self.assertEqual(result.model.state, AppState.ADMIN_MENU)

    def test_idle_timeout_returns_to_admin_menu(self) -> None:
        self._go_to_admin_status()
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=40.0)
        self.assertEqual(result.model.state, AppState.ADMIN_MENU)


class AdminCameraSettingsTestCase(unittest.TestCase):
    """Tests fuer 'Kamera-Einstellungen' (Sprint 11, Feature 2): Betreten
    fordert synchron ISO/Blende an, ADMIN_CAMERA_SETTINGS_READY befuellt sie,
    +/- wandert in den von der Kamera gelieferten Auswahllisten (kein
    Umlaufen an den Enden), Zurueck/Idle fuehrt zum Menue zurueck."""

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

    def _go_to_admin_camera_settings(self, now_offset: float = 5.2) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.SHUTDOWN_GESTURE_DETECTED, now_offset=now_offset)
        self.transition(
            EventType.PIN_SUBMIT,
            now_offset=now_offset + 1.0,
            payload={"pin_result": PinResult.ACCEPTED},
        )
        self.assertEqual(self.model.state, AppState.ADMIN_MENU)
        result = self.transition(EventType.TAP_ADMIN_CAMERA_SETTINGS, now_offset=now_offset + 2.0)
        self.assertEqual(result.model.state, AppState.ADMIN_CAMERA_SETTINGS)
        self.assertIn("read_admin_camera_settings", result.actions)

    def _fill_ready(self, now_offset: float = 10.0):
        return self.transition(
            EventType.ADMIN_CAMERA_SETTINGS_READY,
            now_offset=now_offset,
            payload={
                "available": True,
                "error": None,
                "iso": "400",
                "iso_choices": ("100", "200", "400", "800", "1600"),
                "aperture": "5.6",
                "aperture_choices": ("2.8", "4", "5.6", "8", "11"),
            },
        )

    def test_tap_camera_settings_enters_state_and_requests_read(self) -> None:
        self._go_to_admin_camera_settings()
        self.assertEqual(self.model.ui.admin_camera_iso_choices, ())

    def test_ready_fills_values_and_choices(self) -> None:
        self._go_to_admin_camera_settings()
        result = self._fill_ready()
        self.assertEqual(result.model.state, AppState.ADMIN_CAMERA_SETTINGS)
        self.assertTrue(result.model.ui.admin_camera_available)
        self.assertEqual(result.model.ui.admin_camera_iso, "400")
        self.assertEqual(result.model.ui.admin_camera_aperture, "5.6")

    def test_ready_with_unavailable_camera_sets_error(self) -> None:
        self._go_to_admin_camera_settings()
        result = self.transition(
            EventType.ADMIN_CAMERA_SETTINGS_READY,
            now_offset=10.0,
            payload={"available": False, "error": "Kamera nicht erreichbar: Timeout"},
        )
        self.assertFalse(result.model.ui.admin_camera_available)
        self.assertEqual(result.model.ui.admin_camera_error, "Kamera nicht erreichbar: Timeout")

    def test_iso_up_steps_to_next_choice_and_triggers_set_action(self) -> None:
        self._go_to_admin_camera_settings()
        self._fill_ready()
        result = self.transition(EventType.TAP_ADMIN_CAMERA_ISO_UP, now_offset=11.0)
        self.assertEqual(result.model.ui.admin_camera_iso, "800")
        self.assertIn("set_admin_camera_iso", result.actions)

    def test_iso_down_steps_to_previous_choice(self) -> None:
        self._go_to_admin_camera_settings()
        self._fill_ready()
        result = self.transition(EventType.TAP_ADMIN_CAMERA_ISO_DOWN, now_offset=11.0)
        self.assertEqual(result.model.ui.admin_camera_iso, "200")

    def test_iso_up_stops_at_highest_choice(self) -> None:
        self._go_to_admin_camera_settings()
        self._fill_ready()
        for offset in range(11, 20):
            self.transition(EventType.TAP_ADMIN_CAMERA_ISO_UP, now_offset=float(offset))
        self.assertEqual(self.model.ui.admin_camera_iso, "1600")

    def test_aperture_up_steps_to_next_choice_and_triggers_set_action(self) -> None:
        self._go_to_admin_camera_settings()
        self._fill_ready()
        result = self.transition(EventType.TAP_ADMIN_CAMERA_APERTURE_UP, now_offset=11.0)
        self.assertEqual(result.model.ui.admin_camera_aperture, "8")
        self.assertIn("set_admin_camera_aperture", result.actions)

    def test_aperture_down_stops_at_lowest_choice(self) -> None:
        self._go_to_admin_camera_settings()
        self._fill_ready()
        for offset in range(11, 20):
            self.transition(EventType.TAP_ADMIN_CAMERA_APERTURE_DOWN, now_offset=float(offset))
        self.assertEqual(self.model.ui.admin_camera_aperture, "2.8")

    def test_iso_up_without_choices_is_ignored(self) -> None:
        self._go_to_admin_camera_settings()
        result = self.transition(EventType.TAP_ADMIN_CAMERA_ISO_UP, now_offset=11.0)
        self.assertEqual(result.model.state, AppState.ADMIN_CAMERA_SETTINGS)
        self.assertNotIn("set_admin_camera_iso", result.actions)

    def test_back_returns_to_admin_menu_not_main_menu(self) -> None:
        self._go_to_admin_camera_settings()
        self._fill_ready()
        result = self.transition(EventType.TAP_BACK, now_offset=12.0)
        self.assertEqual(result.model.state, AppState.ADMIN_MENU)

    def test_idle_timeout_returns_to_admin_menu(self) -> None:
        self._go_to_admin_camera_settings()
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=40.0)
        self.assertEqual(result.model.state, AppState.ADMIN_MENU)


class AdminRestartPendingTestCase(unittest.TestCase):
    """Tests fuer 'App neu starten' (Etappe 4.3): nicht abbrechbarer
    Zwischenscreen, Timeout loest 'restart_app' aus, keine andere
    Eingabe hat vorher irgendeine Wirkung."""

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

    def _go_to_restart_pending(self, now_offset: float = 5.2) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.SHUTDOWN_GESTURE_DETECTED, now_offset=now_offset)
        self.transition(
            EventType.PIN_SUBMIT,
            now_offset=now_offset + 1.0,
            payload={"pin_result": PinResult.ACCEPTED},
        )
        result = self.transition(EventType.TAP_ADMIN_RESTART_APP, now_offset=now_offset + 2.0)
        self.assertEqual(result.model.state, AppState.ADMIN_RESTART_PENDING)

    def test_timeout_triggers_restart_action(self) -> None:
        self._go_to_restart_pending()
        result = self.transition(EventType.ADMIN_RESTART_TIMEOUT, now_offset=10.0)
        self.assertIn("restart_app", result.actions)
        self.assertEqual(result.model.state, AppState.ADMIN_RESTART_PENDING)

    def test_taps_do_not_abort(self) -> None:
        self._go_to_restart_pending()
        for ev in (EventType.TAP_BACK, EventType.TAP_CANCEL, EventType.BUTTON_PRESS, EventType.IDLE_TIMEOUT):
            result = self.transition(ev, now_offset=6.0)
            self.assertEqual(result.model.state, AppState.ADMIN_RESTART_PENDING)
            self.assertEqual(result.actions, ())

    def test_restart_deadline_is_set_not_idle_deadline(self) -> None:
        self._go_to_restart_pending(now_offset=5.2)
        expected = self.now + 7.2 + self.config.timeouts.admin_restart_delay_seconds
        self.assertAlmostEqual(self.model.timers.admin_restart_deadline, expected)
        self.assertIsNone(self.model.timers.idle_deadline)


class AdminDeleteAllTestCase(unittest.TestCase):
    """Tests fuer 'Alle Bilder loeschen' (Etappe 4.4). Der eigentliche
    Loeschvorgang liegt in admin_delete_service.py und wird dort getestet -
    hier geht es ausschliesslich um die Zustandslogik, insbesondere darum,
    dass ohne ausdrueckliche Bestaetigung NIE die Loesch-Aktion entsteht."""

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

    def _go_to_delete_confirm(self, now_offset: float = 5.2):
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.SHUTDOWN_GESTURE_DETECTED, now_offset=now_offset)
        self.transition(
            EventType.PIN_SUBMIT,
            now_offset=now_offset + 1.0,
            payload={"pin_result": PinResult.ACCEPTED},
        )
        self.assertEqual(self.model.state, AppState.ADMIN_MENU)
        return self.transition(EventType.TAP_ADMIN_DELETE_ALL, now_offset=now_offset + 2.0)

    # -- Sicherheitsabfrage ------------------------------------------------

    def test_tap_delete_all_opens_confirmation_without_deleting(self) -> None:
        result = self._go_to_delete_confirm()
        self.assertEqual(result.model.state, AppState.ADMIN_DELETE_CONFIRM)
        self.assertNotIn("start_delete_all", result.actions)

    def test_abort_returns_to_menu_without_deleting(self) -> None:
        self._go_to_delete_confirm()
        result = self.transition(EventType.TAP_ADMIN_DELETE_ABORT, now_offset=10.0)
        self.assertEqual(result.model.state, AppState.ADMIN_MENU)
        self.assertNotIn("start_delete_all", result.actions)

    def test_back_also_aborts(self) -> None:
        self._go_to_delete_confirm()
        result = self.transition(EventType.TAP_BACK, now_offset=10.0)
        self.assertEqual(result.model.state, AppState.ADMIN_MENU)
        self.assertNotIn("start_delete_all", result.actions)

    def test_idle_timeout_aborts_rather_than_deletes(self) -> None:
        self._go_to_delete_confirm()
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=60.0)
        self.assertEqual(result.model.state, AppState.ADMIN_MENU)
        self.assertNotIn("start_delete_all", result.actions)

    def test_hardware_button_does_not_confirm(self) -> None:
        self._go_to_delete_confirm()
        result = self.transition(EventType.BUTTON_PRESS, now_offset=10.0)
        self.assertEqual(result.model.state, AppState.ADMIN_DELETE_CONFIRM)
        self.assertEqual(result.actions, ())

    def test_single_photo_delete_event_does_not_confirm(self) -> None:
        self._go_to_delete_confirm()
        result = self.transition(EventType.TAP_CONFIRM_DELETE, now_offset=10.0)
        self.assertEqual(result.model.state, AppState.ADMIN_DELETE_CONFIRM)
        self.assertEqual(result.actions, ())

    # -- Loeschlauf --------------------------------------------------------

    def test_confirm_starts_deletion(self) -> None:
        self._go_to_delete_confirm()
        result = self.transition(EventType.TAP_ADMIN_DELETE_CONFIRM, now_offset=10.0)
        self.assertEqual(result.model.state, AppState.ADMIN_DELETE_RUNNING)
        self.assertIn("start_delete_all", result.actions)

    def test_running_has_no_idle_deadline(self) -> None:
        self._go_to_delete_confirm()
        self.transition(EventType.TAP_ADMIN_DELETE_CONFIRM, now_offset=10.0)
        self.assertIsNone(self.model.timers.idle_deadline)

    def test_running_ignores_taps(self) -> None:
        self._go_to_delete_confirm()
        self.transition(EventType.TAP_ADMIN_DELETE_CONFIRM, now_offset=10.0)
        for ev in (EventType.TAP_BACK, EventType.TAP_CANCEL, EventType.BUTTON_PRESS):
            result = self.transition(ev, now_offset=11.0)
            self.assertEqual(result.model.state, AppState.ADMIN_DELETE_RUNNING)

    # -- Ergebnis ----------------------------------------------------------

    def _go_to_delete_done(self, lines=("Fotos gelöscht: 3",)):
        self._go_to_delete_confirm()
        self.transition(EventType.TAP_ADMIN_DELETE_CONFIRM, now_offset=10.0)
        return self.transition(
            EventType.ADMIN_DELETE_FINISHED, now_offset=20.0, payload={"lines": lines},
        )

    def test_finished_shows_result_lines(self) -> None:
        result = self._go_to_delete_done(lines=("Fotos gelöscht: 3", "Kamera: geleert"))
        self.assertEqual(result.model.state, AppState.ADMIN_DELETE_DONE)
        self.assertEqual(result.model.ui.admin_delete_lines, ("Fotos gelöscht: 3", "Kamera: geleert"))

    def test_finished_clears_session_photos(self) -> None:
        result = self._go_to_delete_done()
        self.assertEqual(result.model.session.photos, ())
        self.assertIsNone(result.model.session.current_photo_path)
        self.assertIsNone(result.model.session.last_saved_photo_path)

    def test_done_screen_has_idle_deadline(self) -> None:
        result = self._go_to_delete_done()
        self.assertIsNotNone(result.model.timers.idle_deadline)

    def test_done_idle_timeout_returns_to_main_menu(self) -> None:
        self._go_to_delete_done()
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=60.0)
        self.assertEqual(result.model.state, AppState.MAIN_MENU)

    def test_done_back_returns_to_admin_menu(self) -> None:
        self._go_to_delete_done()
        result = self.transition(EventType.TAP_BACK, now_offset=30.0)
        self.assertEqual(result.model.state, AppState.ADMIN_MENU)


class IdleTimeoutWiringTestCase(unittest.TestCase):
    """Quelltext-Test gegen eine Luecke, die in Etappe 4.4 tatsaechlich
    auftrat: die State Machine kann einen IDLE_TIMEOUT nur verarbeiten,
    wenn app_with_hw.py ihn fuer diesen Zustand ueberhaupt AUSLOEST. Beides
    steht an voellig verschiedenen Stellen (_go_*-Methode setzt die
    Deadline, _emit_due_timers entscheidet ueber das Feuern) und lief
    auseinander - die Loesch-Abfrage bekam eine Deadline, lief aber nie in
    den Timeout, weil sie im idle_states-Set fehlte.

    Die uebrigen Tests hier speisen IDLE_TIMEOUT direkt ein und pruefen
    damit nur die Reaktion, nicht die Ausloesung. Dieser Test schliesst
    die Luecke - bewusst auf Quelltextebene, weil app_with_hw.py sich fuer
    einen Import Hardware-Provider und ein Pygame-Fenster erzeugen wuerde.
    """

    ADMIN_STATES_WITH_IDLE = (
        "ADMIN_MENU", "ADMIN_STATUS", "ADMIN_DELETE_CONFIRM",
        "ADMIN_DELETE_DONE",
        "ADMIN_USB_WAIT", "ADMIN_USB_READY", "ADMIN_USB_PROBLEM", "ADMIN_USB_REMOVE",
        "ADMIN_USB_EXPORT_DONE",
        "ADMIN_USB_CONFLICTS",
    )

    def test_states_with_idle_deadline_are_emitted_by_the_app(self) -> None:
        from pathlib import Path
        source = (Path(__file__).resolve().parent.parent / "app_with_hw.py").read_text(encoding="utf-8")
        anchor = source.find("AppState.TERMS,")
        self.assertNotEqual(anchor, -1, "Anker 'AppState.TERMS,' in app_with_hw.py nicht gefunden.")
        block_end = source.find("}", anchor)
        self.assertNotEqual(block_end, -1, "Ende des idle_states-Sets nicht gefunden.")
        block = source[anchor:block_end]

        missing = [name for name in self.ADMIN_STATES_WITH_IDLE if name not in block]
        self.assertEqual(
            missing, [],
            f"Diese Zustaende bekommen eine idle_deadline, feuern aber nie einen "
            f"IDLE_TIMEOUT (fehlen im idle_states-Set in app_with_hw.py): {missing}",
        )

    def test_state_machine_really_sets_those_deadlines(self) -> None:
        machine = StateMachine(DEFAULT_CONFIG)
        now = 1000.0
        model = machine.initial_model(now)
        model = machine.transition(model, AppEvent(EventType.TICK), now + 10.0).model
        model = machine.transition(model, AppEvent(EventType.SHUTDOWN_GESTURE_DETECTED), now + 11.0).model
        model = machine.transition(
            model, AppEvent(EventType.PIN_SUBMIT, payload={"pin_result": PinResult.ACCEPTED}), now + 12.0,
        ).model
        self.assertEqual(model.state, AppState.ADMIN_MENU)
        self.assertIsNotNone(model.timers.idle_deadline)

        for event_type, expected in (
            (EventType.TAP_ADMIN_STATUS, AppState.ADMIN_STATUS),
            (EventType.TAP_ADMIN_DELETE_ALL, AppState.ADMIN_DELETE_CONFIRM),
        ):
            result = machine.transition(model, AppEvent(event_type), now + 13.0)
            self.assertEqual(result.model.state, expected)
            self.assertIsNotNone(
                result.model.timers.idle_deadline,
                f"{expected.name} bekommt keine idle_deadline - Liste oben anpassen.",
            )

        confirm = machine.transition(model, AppEvent(EventType.TAP_ADMIN_DELETE_ALL), now + 13.0).model
        running = machine.transition(confirm, AppEvent(EventType.TAP_ADMIN_DELETE_CONFIRM), now + 14.0).model
        done = machine.transition(
            running, AppEvent(EventType.ADMIN_DELETE_FINISHED, payload={"lines": ()}), now + 15.0,
        ).model
        self.assertEqual(done.state, AppState.ADMIN_DELETE_DONE)
        self.assertIsNotNone(
            done.timers.idle_deadline,
            "ADMIN_DELETE_DONE bekommt keine idle_deadline - Liste oben anpassen.",
        )

        wait = machine.transition(model, AppEvent(EventType.TAP_ADMIN_USB_EXPORT), now + 13.0).model
        self.assertEqual(wait.state, AppState.ADMIN_USB_WAIT)
        self.assertIsNotNone(wait.timers.idle_deadline, "ADMIN_USB_WAIT ohne idle_deadline.")

        detected = machine.transition(
            wait, AppEvent(EventType.ADMIN_USB_DETECTED, payload={"name": "Stick"}), now + 14.0,
        ).model
        check = machine.transition(detected, AppEvent(EventType.TAP_ADMIN_USB_CONTINUE), now + 15.0).model
        self.assertEqual(check.state, AppState.ADMIN_USB_CHECK)

        for ok, expected in ((True, AppState.ADMIN_USB_READY), (False, AppState.ADMIN_USB_PROBLEM)):
            branch = machine.transition(
                check, AppEvent(EventType.ADMIN_USB_CHECK_DONE, payload={"ok": ok, "lines": ()}), now + 16.0,
            ).model
            self.assertEqual(branch.state, expected)
            self.assertIsNotNone(branch.timers.idle_deadline, f"{expected.name} ohne idle_deadline.")

            eject = machine.transition(branch, AppEvent(EventType.TAP_CANCEL), now + 17.0).model
            self.assertEqual(eject.state, AppState.ADMIN_USB_EJECT)
            remove = machine.transition(
                eject, AppEvent(EventType.ADMIN_USB_EJECTED, payload={"lines": ()}), now + 18.0,
            ).model
            self.assertEqual(remove.state, AppState.ADMIN_USB_REMOVE)
            self.assertIsNotNone(remove.timers.idle_deadline, "ADMIN_USB_REMOVE ohne idle_deadline.")


class AdminUsbFlowTestCase(unittest.TestCase):
    """Tests fuer den USB-Ablauf (Etappe 4a). Das echte Einbinden liegt in
    admin_usb_service.py; hier geht es nur um die Zustandslogik."""

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

    def _go_to_usb_wait(self, now_offset: float = 5.2):
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.SHUTDOWN_GESTURE_DETECTED, now_offset=now_offset)
        self.transition(
            EventType.PIN_SUBMIT, now_offset=now_offset + 1.0,
            payload={"pin_result": PinResult.ACCEPTED},
        )
        return self.transition(EventType.TAP_ADMIN_USB_EXPORT, now_offset=now_offset + 2.0)

    # -- Wartebildschirm ---------------------------------------------------

    def test_enters_wait_and_requests_space_calculation(self) -> None:
        result = self._go_to_usb_wait()
        self.assertEqual(result.model.state, AppState.ADMIN_USB_WAIT)
        self.assertIn("usb_prepare", result.actions)
        self.assertFalse(result.model.ui.admin_usb_device_ready)

    def test_continue_without_stick_does_nothing(self) -> None:
        self._go_to_usb_wait()
        result = self.transition(EventType.TAP_ADMIN_USB_CONTINUE, now_offset=10.0)
        self.assertEqual(result.model.state, AppState.ADMIN_USB_WAIT)
        self.assertEqual(result.actions, ())

    def test_detection_enables_continue(self) -> None:
        self._go_to_usb_wait()
        self.transition(
            EventType.ADMIN_USB_DETECTED, now_offset=10.0, payload={"name": "STICK (8 GB, vfat)"},
        )
        self.assertTrue(self.model.ui.admin_usb_device_ready)
        result = self.transition(EventType.TAP_ADMIN_USB_CONTINUE, now_offset=11.0)
        self.assertEqual(result.model.state, AppState.ADMIN_USB_CHECK)
        self.assertIn("usb_check", result.actions)

    def test_info_ready_fills_lines(self) -> None:
        self._go_to_usb_wait()
        lines = ("Zu exportieren: 12 Bilder", "Benötigt: 60 MB")
        self.transition(EventType.ADMIN_USB_INFO_READY, now_offset=10.0, payload={"lines": lines})
        self.assertEqual(self.model.ui.admin_usb_lines, lines)

    def test_cancel_returns_to_admin_menu(self) -> None:
        self._go_to_usb_wait()
        result = self.transition(EventType.TAP_CANCEL, now_offset=10.0)
        self.assertEqual(result.model.state, AppState.ADMIN_MENU)

    def test_wait_uses_the_longer_timeout(self) -> None:
        self._go_to_usb_wait(now_offset=5.2)
        expected = self.now + 7.2 + self.config.timeouts.admin_usb_wait_seconds
        self.assertAlmostEqual(self.model.timers.idle_deadline, expected)

    # -- Pruefung ----------------------------------------------------------

    def _go_to_check(self):
        self._go_to_usb_wait()
        self.transition(EventType.ADMIN_USB_DETECTED, now_offset=10.0, payload={"name": "S"})
        return self.transition(EventType.TAP_ADMIN_USB_CONTINUE, now_offset=11.0)

    def test_check_has_no_idle_deadline(self) -> None:
        self._go_to_check()
        self.assertIsNone(self.model.timers.idle_deadline)

    def test_check_ignores_taps(self) -> None:
        self._go_to_check()
        for ev in (EventType.TAP_BACK, EventType.TAP_CANCEL, EventType.TAP_ADMIN_USB_CONTINUE):
            result = self.transition(ev, now_offset=12.0)
            self.assertEqual(result.model.state, AppState.ADMIN_USB_CHECK)

    def test_check_ok_leads_to_ready(self) -> None:
        self._go_to_check()
        result = self.transition(
            EventType.ADMIN_USB_CHECK_DONE, now_offset=13.0,
            payload={"ok": True, "lines": ("Stick bereit",)},
        )
        self.assertEqual(result.model.state, AppState.ADMIN_USB_READY)
        self.assertEqual(result.model.ui.admin_usb_lines, ("Stick bereit",))

    def test_check_failure_leads_to_problem(self) -> None:
        self._go_to_check()
        result = self.transition(
            EventType.ADMIN_USB_CHECK_DONE, now_offset=13.0,
            payload={"ok": False, "too_small": True, "lines": ("Zu klein",)},
        )
        self.assertEqual(result.model.state, AppState.ADMIN_USB_PROBLEM)

    # -- Auswerfen und Entfernen -------------------------------------------

    def _go_to_remove(self, ok: bool):
        self._go_to_check()
        self.transition(
            EventType.ADMIN_USB_CHECK_DONE, now_offset=13.0, payload={"ok": ok, "lines": ()},
        )
        self.transition(EventType.TAP_CANCEL, now_offset=14.0)
        self.assertEqual(self.model.state, AppState.ADMIN_USB_EJECT)
        return self.transition(
            EventType.ADMIN_USB_EJECTED, now_offset=15.0,
            payload={"lines": ("Stick kann entfernt werden",)},
        )

    def test_continue_on_ready_starts_export(self) -> None:
        self._go_to_check()
        self.transition(EventType.ADMIN_USB_CHECK_DONE, now_offset=13.0, payload={"ok": True, "lines": ()})
        result = self.transition(EventType.TAP_ADMIN_USB_CONTINUE, now_offset=14.0)
        self.assertEqual(result.model.state, AppState.ADMIN_USB_COPY)
        self.assertIn("usb_start_export", result.actions)
        self.assertIsNone(result.model.timers.idle_deadline)

    def test_ready_timeout_ejects_without_export(self) -> None:
        self._go_to_check()
        self.transition(EventType.ADMIN_USB_CHECK_DONE, now_offset=13.0, payload={"ok": True, "lines": ()})
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=60.0)
        self.assertEqual(result.model.state, AppState.ADMIN_USB_EJECT)
        self.assertIn("usb_eject", result.actions)

    def test_after_success_without_export_returns_to_menu(self) -> None:
        self._go_to_remove(ok=True)
        self.assertEqual(self.model.state, AppState.ADMIN_USB_REMOVE)
        self.assertFalse(self.model.ui.admin_usb_can_retry)
        self.assertFalse(self.model.ui.admin_usb_offer_delete)
        result = self.transition(EventType.TAP_BACK, now_offset=20.0)
        self.assertEqual(result.model.state, AppState.ADMIN_MENU)

    def test_after_problem_remove_returns_to_wait_for_another_stick(self) -> None:
        self._go_to_remove(ok=False)
        self.assertTrue(self.model.ui.admin_usb_can_retry)
        result = self.transition(EventType.TAP_BACK, now_offset=20.0)
        self.assertEqual(result.model.state, AppState.ADMIN_USB_WAIT)
        self.assertIn("usb_prepare", result.actions)

    def test_retry_flag_is_reset_on_new_wait(self) -> None:
        self._go_to_remove(ok=False)
        self.transition(EventType.TAP_BACK, now_offset=20.0)
        self.assertFalse(self.model.ui.admin_usb_can_retry)
        self.assertFalse(self.model.ui.admin_usb_device_ready)


class AdminUsbExportFlowTestCase(unittest.TestCase):
    """Tests fuer den USB-Export-Ablauf (Etappe 4.7)."""

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

    def _go_to_copy(self):
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.SHUTDOWN_GESTURE_DETECTED, now_offset=5.0)
        self.transition(EventType.PIN_SUBMIT, now_offset=6.0, payload={"pin_result": PinResult.ACCEPTED})
        self.transition(EventType.TAP_ADMIN_USB_EXPORT, now_offset=7.0)
        self.transition(EventType.ADMIN_USB_DETECTED, now_offset=8.0, payload={"name": "S"})
        self.transition(EventType.TAP_ADMIN_USB_CONTINUE, now_offset=9.0)
        self.transition(EventType.ADMIN_USB_CHECK_DONE, now_offset=10.0, payload={"ok": True, "lines": ()})
        return self.transition(EventType.TAP_ADMIN_USB_CONTINUE, now_offset=11.0)

    # -- Export-Lauf -------------------------------------------------------

    def test_copy_state_has_no_idle_deadline(self) -> None:
        self._go_to_copy()
        self.assertEqual(self.model.state, AppState.ADMIN_USB_COPY)
        self.assertIsNone(self.model.timers.idle_deadline)

    def test_copy_ignores_all_taps(self) -> None:
        self._go_to_copy()
        for ev in (EventType.TAP_BACK, EventType.TAP_CANCEL, EventType.BUTTON_PRESS):
            result = self.transition(ev, now_offset=12.0)
            self.assertEqual(result.model.state, AppState.ADMIN_USB_COPY)

    def test_finished_ok_shows_export_done(self) -> None:
        self._go_to_copy()
        result = self.transition(
            EventType.ADMIN_USB_EXPORT_FINISHED, now_offset=15.0,
            payload={"ok": True, "lines": ("5 Bilder exportiert",)},
        )
        self.assertEqual(result.model.state, AppState.ADMIN_USB_EXPORT_DONE)
        self.assertTrue(result.model.ui.admin_usb_offer_delete)

    def test_finished_with_errors_blocks_delete_offer(self) -> None:
        self._go_to_copy()
        result = self.transition(
            EventType.ADMIN_USB_EXPORT_FINISHED, now_offset=15.0,
            payload={"ok": False, "lines": ("Pruefsummenfehler!",)},
        )
        self.assertEqual(result.model.state, AppState.ADMIN_USB_EXPORT_DONE)
        self.assertFalse(result.model.ui.admin_usb_offer_delete)

    # -- Uebergang zum Loeschen --------------------------------------------

    def test_successful_export_leads_to_delete_confirm(self) -> None:
        self._go_to_copy()
        self.transition(
            EventType.ADMIN_USB_EXPORT_FINISHED, now_offset=15.0,
            payload={"ok": True, "lines": ()},
        )
        self.transition(EventType.TAP_ADMIN_USB_CONTINUE, now_offset=16.0)
        self.assertEqual(self.model.state, AppState.ADMIN_USB_EJECT)
        self.transition(EventType.ADMIN_USB_EJECTED, now_offset=17.0, payload={"lines": ()})
        self.assertEqual(self.model.state, AppState.ADMIN_USB_REMOVE)
        self.assertTrue(self.model.ui.admin_usb_offer_delete)
        result = self.transition(EventType.TAP_BACK, now_offset=18.0)
        self.assertEqual(result.model.state, AppState.ADMIN_DELETE_CONFIRM)

    def test_failed_export_does_not_offer_delete(self) -> None:
        self._go_to_copy()
        self.transition(
            EventType.ADMIN_USB_EXPORT_FINISHED, now_offset=15.0,
            payload={"ok": False, "lines": ()},
        )
        self.transition(EventType.TAP_ADMIN_USB_CONTINUE, now_offset=16.0)
        self.transition(EventType.ADMIN_USB_EJECTED, now_offset=17.0, payload={"lines": ()})
        result = self.transition(EventType.TAP_BACK, now_offset=18.0)
        self.assertEqual(result.model.state, AppState.ADMIN_MENU)

    # -- Stick leeren (not_enough_free) ------------------------------------

    def test_clear_rechecks_stick(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.SHUTDOWN_GESTURE_DETECTED, now_offset=5.0)
        self.transition(EventType.PIN_SUBMIT, now_offset=6.0, payload={"pin_result": PinResult.ACCEPTED})
        self.transition(EventType.TAP_ADMIN_USB_EXPORT, now_offset=7.0)
        self.transition(EventType.ADMIN_USB_DETECTED, now_offset=8.0, payload={"name": "S"})
        self.transition(EventType.TAP_ADMIN_USB_CONTINUE, now_offset=9.0)
        self.transition(
            EventType.ADMIN_USB_CHECK_DONE, now_offset=10.0,
            payload={"ok": False, "not_enough_free": True, "lines": ("Nicht genug Platz",)},
        )
        self.assertEqual(self.model.state, AppState.ADMIN_USB_PROBLEM)
        self.assertTrue(self.model.ui.admin_usb_not_enough_free)
        result = self.transition(EventType.TAP_ADMIN_USB_CLEAR, now_offset=11.0)
        self.assertEqual(result.model.state, AppState.ADMIN_USB_CHECK)
        self.assertIn("usb_clear_and_check", result.actions)

    def test_clear_on_too_small_acts_as_eject(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.SHUTDOWN_GESTURE_DETECTED, now_offset=5.0)
        self.transition(EventType.PIN_SUBMIT, now_offset=6.0, payload={"pin_result": PinResult.ACCEPTED})
        self.transition(EventType.TAP_ADMIN_USB_EXPORT, now_offset=7.0)
        self.transition(EventType.ADMIN_USB_DETECTED, now_offset=8.0, payload={"name": "S"})
        self.transition(EventType.TAP_ADMIN_USB_CONTINUE, now_offset=9.0)
        self.transition(
            EventType.ADMIN_USB_CHECK_DONE, now_offset=10.0,
            payload={"ok": False, "too_small": True, "not_enough_free": False, "lines": ("Zu klein",)},
        )
        self.assertFalse(self.model.ui.admin_usb_not_enough_free)
        result = self.transition(EventType.TAP_ADMIN_USB_CLEAR, now_offset=11.0)
        self.assertEqual(result.model.state, AppState.ADMIN_USB_EJECT)


class AdminUsbConflictFlowTestCase(unittest.TestCase):
    """Tests fuer die Konfliktbehebung nach dem USB-Export (Etappe 6b):
    ADMIN_USB_COPY -> (bei offenen Konflikten) ADMIN_USB_CONFLICTS ->
    ADMIN_USB_RESOLVE -> ADMIN_USB_EXPORT_DONE."""

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

    def _go_to_copy(self):
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.SHUTDOWN_GESTURE_DETECTED, now_offset=5.0)
        self.transition(EventType.PIN_SUBMIT, now_offset=6.0, payload={"pin_result": PinResult.ACCEPTED})
        self.transition(EventType.TAP_ADMIN_USB_EXPORT, now_offset=7.0)
        self.transition(EventType.ADMIN_USB_DETECTED, now_offset=8.0, payload={"name": "S"})
        self.transition(EventType.TAP_ADMIN_USB_CONTINUE, now_offset=9.0)
        self.transition(EventType.ADMIN_USB_CHECK_DONE, now_offset=10.0, payload={"ok": True, "lines": ()})
        return self.transition(EventType.TAP_ADMIN_USB_CONTINUE, now_offset=11.0)

    @staticmethod
    def _conflict(name: str, decision: str = "rename") -> ExportConflict:
        return ExportConflict(
            name=name, src_size=100, dst_size=90, src_mtime=1.0, dst_mtime=2.0, decision=decision,
        )

    def _go_to_conflicts(self, conflicts):
        self._go_to_copy()
        return self.transition(
            EventType.ADMIN_USB_EXPORT_FINISHED, now_offset=15.0,
            payload={"ok": False, "lines": ("Export nicht abgeschlossen",), "conflicts": conflicts},
        )

    # -- Ankunft auf dem Konflikt-Screen ------------------------------------

    def test_conflicts_in_payload_lead_to_conflicts_screen(self) -> None:
        result = self._go_to_conflicts((self._conflict("a.jpg"),))
        self.assertEqual(result.model.state, AppState.ADMIN_USB_CONFLICTS)
        self.assertEqual(len(result.model.ui.admin_usb_conflicts), 1)
        self.assertEqual(result.model.ui.admin_usb_conflicts[0].name, "a.jpg")

    def test_no_conflicts_keeps_old_behavior(self) -> None:
        self._go_to_copy()
        result = self.transition(
            EventType.ADMIN_USB_EXPORT_FINISHED, now_offset=15.0,
            payload={"ok": True, "lines": ("5 Bilder exportiert",)},
        )
        self.assertEqual(result.model.state, AppState.ADMIN_USB_EXPORT_DONE)

    def test_conflicts_screen_has_idle_deadline(self) -> None:
        result = self._go_to_conflicts((self._conflict("a.jpg"),))
        self.assertIsNotNone(result.model.timers.idle_deadline)

    # -- Einzelentscheidung und Sammelaktionen ------------------------------

    def test_toggle_single_decision(self) -> None:
        self._go_to_conflicts((self._conflict("a.jpg"), self._conflict("b.jpg")))
        result = self.transition(
            EventType.TAP_ADMIN_USB_CONFLICT_DECISION, now_offset=16.0,
            payload={"name": "a.jpg", "decision": "overwrite"},
        )
        by_name = {c.name: c.decision for c in result.model.ui.admin_usb_conflicts}
        self.assertEqual(by_name["a.jpg"], "overwrite")
        self.assertEqual(by_name["b.jpg"], "rename")

    def test_invalid_decision_is_ignored(self) -> None:
        self._go_to_conflicts((self._conflict("a.jpg"),))
        result = self.transition(
            EventType.TAP_ADMIN_USB_CONFLICT_DECISION, now_offset=16.0,
            payload={"name": "a.jpg", "decision": "loeschen"},
        )
        self.assertEqual(result.model.ui.admin_usb_conflicts[0].decision, "rename")

    def test_overwrite_all(self) -> None:
        self._go_to_conflicts((self._conflict("a.jpg"), self._conflict("b.jpg")))
        result = self.transition(EventType.TAP_ADMIN_USB_CONFLICTS_OVERWRITE_ALL, now_offset=16.0)
        decisions = {c.decision for c in result.model.ui.admin_usb_conflicts}
        self.assertEqual(decisions, {"overwrite"})

    def test_rename_all(self) -> None:
        self._go_to_conflicts((self._conflict("a.jpg", "overwrite"), self._conflict("b.jpg", "overwrite")))
        result = self.transition(EventType.TAP_ADMIN_USB_CONFLICTS_RENAME_ALL, now_offset=16.0)
        decisions = {c.decision for c in result.model.ui.admin_usb_conflicts}
        self.assertEqual(decisions, {"rename"})

    # -- Uebergang zur Aufloesung --------------------------------------------

    def test_apply_starts_resolve(self) -> None:
        self._go_to_conflicts((self._conflict("a.jpg"),))
        result = self.transition(EventType.TAP_ADMIN_USB_CONFLICTS_APPLY, now_offset=16.0)
        self.assertEqual(result.model.state, AppState.ADMIN_USB_RESOLVE)
        self.assertIn("usb_apply_resolutions", result.actions)
        self.assertIsNone(result.model.timers.idle_deadline)

    def test_back_also_starts_resolve(self) -> None:
        self._go_to_conflicts((self._conflict("a.jpg"),))
        result = self.transition(EventType.TAP_BACK, now_offset=16.0)
        self.assertEqual(result.model.state, AppState.ADMIN_USB_RESOLVE)

    def test_cancel_also_starts_resolve(self) -> None:
        self._go_to_conflicts((self._conflict("a.jpg"),))
        result = self.transition(EventType.TAP_CANCEL, now_offset=16.0)
        self.assertEqual(result.model.state, AppState.ADMIN_USB_RESOLVE)

    def test_idle_timeout_also_starts_resolve(self) -> None:
        self._go_to_conflicts((self._conflict("a.jpg"),))
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=16.0)
        self.assertEqual(result.model.state, AppState.ADMIN_USB_RESOLVE)

    # -- Aufloesungslauf (Hintergrund, nicht abbrechbar) --------------------

    def test_resolve_ignores_taps(self) -> None:
        self._go_to_conflicts((self._conflict("a.jpg"),))
        self.transition(EventType.TAP_ADMIN_USB_CONFLICTS_APPLY, now_offset=16.0)
        for ev in (EventType.TAP_BACK, EventType.TAP_CANCEL, EventType.BUTTON_PRESS):
            result = self.transition(ev, now_offset=17.0)
            self.assertEqual(result.model.state, AppState.ADMIN_USB_RESOLVE)

    def test_resolve_finished_leads_to_export_done(self) -> None:
        self._go_to_conflicts((self._conflict("a.jpg"),))
        self.transition(EventType.TAP_ADMIN_USB_CONFLICTS_APPLY, now_offset=16.0)
        result = self.transition(
            EventType.ADMIN_USB_RESOLVE_FINISHED, now_offset=17.0,
            payload={"ok": True, "lines": ("1 umbenannt",)},
        )
        self.assertEqual(result.model.state, AppState.ADMIN_USB_EXPORT_DONE)
        self.assertTrue(result.model.ui.admin_usb_offer_delete)

    def test_export_done_clears_conflict_list(self) -> None:
        self._go_to_conflicts((self._conflict("a.jpg"),))
        self.transition(EventType.TAP_ADMIN_USB_CONFLICTS_APPLY, now_offset=16.0)
        result = self.transition(
            EventType.ADMIN_USB_RESOLVE_FINISHED, now_offset=17.0,
            payload={"ok": True, "lines": ()},
        )
        self.assertEqual(result.model.ui.admin_usb_conflicts, ())

    def test_resolve_failed_does_not_offer_delete(self) -> None:
        self._go_to_conflicts((self._conflict("a.jpg"),))
        self.transition(EventType.TAP_ADMIN_USB_CONFLICTS_APPLY, now_offset=16.0)
        result = self.transition(
            EventType.ADMIN_USB_RESOLVE_FINISHED, now_offset=17.0,
            payload={"ok": False, "lines": ("Prüfsummenfehler!",)},
        )
        self.assertFalse(result.model.ui.admin_usb_offer_delete)


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
