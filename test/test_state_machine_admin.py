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

    def test_shutdown_item_starts_confirm(self) -> None:
        # GEAENDERT (Sprint-11-Nachbesserung): fuehrt nicht mehr direkt zu
        # SHUTDOWN_GOODBYE, sondern zur Sicherheitsabfrage - siehe
        # test_state_machine_shutdown.AdminShutdownConfirmTestCase fuer deren
        # Verhalten (Ja/Nein/Zurueck/Idle).
        self._go_to_admin_menu()
        self.transition(EventType.TAP_ADMIN_SHUTDOWN, now_offset=10.0)
        self.assertEqual(self.model.state, AppState.ADMIN_SHUTDOWN_CONFIRM)

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

    def _fill_ready(self, now_offset: float = 10.0, **overrides):
        # NEU (Kamera-Menue 2.0): vollstaendige Werte fuer beide Seiten
        # (Belichtung + Sonstiges), damit Save/Cancel/Entry-Snapshot-Tests
        # realistische Payloads verwenden - einzelne Felder koennen ueber
        # **overrides pro Test angepasst werden.
        payload = {
            "available": True,
            "error": None,
            "iso": "400",
            "iso_choices": ("100", "200", "400", "800", "1600"),
            "aperture": "5.6",
            "aperture_choices": ("2.8", "4", "5.6", "8", "11"),
            "shutter": "1/125",
            "expcomp": "0.0",
            "expcomp_choices": ("-1.0", "-0.5", "0.0", "0.5", "1.0"),
            "metering": "Matrix",
            "metering_choices": ("Matrix", "Mittelbetont", "Spot"),
            "white_balance": "Auto",
            "white_balance_choices": ("Auto", "Sonne", "Wolken"),
            "quality": "Fine",
            "quality_choices": ("Fine", "Normal", "Basic"),
            "image_size": "Large",
            "image_size_choices": ("Large", "Medium", "Small"),
            "drive_mode": "Single",
            "drive_mode_choices": ("Single", "Continuous"),
        }
        payload.update(overrides)
        return self.transition(
            EventType.ADMIN_CAMERA_SETTINGS_READY,
            now_offset=now_offset,
            payload=payload,
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

    # BUGFIX (Nutzer-Feedback nach Live-Test): "+" muss die Bildgroesse
    # VERGROESSERN, "-" verkleinern - die Kamera liefert die Auswahlliste
    # aber absteigend (gross...klein), weshalb die Richtung hier (anders als
    # bei ISO/Blende) umgekehrt sein muss.
    def test_imagesize_plus_makes_it_bigger(self) -> None:
        self._go_to_admin_camera_settings()
        self._fill_ready(image_size="Medium", image_size_choices=("Large", "Medium", "Small"))
        result = self.transition(EventType.TAP_ADMIN_CAMERA_IMAGESIZE_UP, now_offset=11.0)
        self.assertEqual(result.model.ui.admin_camera_imagesize, "Large")
        self.assertIn("set_admin_camera_imagesize", result.actions)

    def test_imagesize_minus_makes_it_smaller(self) -> None:
        self._go_to_admin_camera_settings()
        self._fill_ready(image_size="Medium", image_size_choices=("Large", "Medium", "Small"))
        result = self.transition(EventType.TAP_ADMIN_CAMERA_IMAGESIZE_DOWN, now_offset=11.0)
        self.assertEqual(result.model.ui.admin_camera_imagesize, "Small")

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

    # BUGFIX (Nutzer-Feedback nach Live-Test): Betreten nutzt den eigenen,
    # laengeren admin_camera_settings_idle_seconds (60s) statt des kuerzeren
    # admin_menu_idle_seconds (30s) - und jede Bedienung (+/-/</>, Seiten-
    # wechsel) haengt die Deadline neu ein, statt sie ab Betreten einfach
    # weiterlaufen zu lassen (das fuehrte dazu, dass man auch waehrend
    # aktiver Bedienung unerwartet ins Service-Menue zurueckgereicht wurde).

    def test_entering_uses_camera_settings_idle_seconds_not_admin_menu(self) -> None:
        self._go_to_admin_camera_settings(now_offset=5.2)
        expected = self.now + 5.2 + 2.0 + self.config.timeouts.admin_camera_settings_idle_seconds
        self.assertAlmostEqual(self.model.timers.idle_deadline, expected)
        self.assertNotAlmostEqual(self.model.timers.idle_deadline, self.now + 5.2 + 2.0 + self.config.timeouts.admin_menu_idle_seconds)

    def test_stepping_a_value_refreshes_idle_deadline(self) -> None:
        self._go_to_admin_camera_settings()
        self._fill_ready(now_offset=10.0)
        result = self.transition(EventType.TAP_ADMIN_CAMERA_ISO_UP, now_offset=50.0)
        expected = self.now + 50.0 + self.config.timeouts.admin_camera_settings_idle_seconds
        self.assertAlmostEqual(result.model.timers.idle_deadline, expected)

    def test_page_navigation_refreshes_idle_deadline(self) -> None:
        self._go_to_admin_camera_settings()
        self._fill_ready(now_offset=10.0)
        result = self.transition(EventType.TAP_ADMIN_CAMERA_PAGE_NEXT, now_offset=50.0)
        expected = self.now + 50.0 + self.config.timeouts.admin_camera_settings_idle_seconds
        self.assertAlmostEqual(result.model.timers.idle_deadline, expected)

    # NEU (Kamera-Menue 2.0): Seiten-Navigation ("Weiter"/"Zurueck" zwischen
    # "Belichtung" und "Sonstiges").

    def test_page_next_switches_to_page_1(self) -> None:
        self._go_to_admin_camera_settings()
        self._fill_ready()
        result = self.transition(EventType.TAP_ADMIN_CAMERA_PAGE_NEXT, now_offset=11.0)
        self.assertEqual(result.model.state, AppState.ADMIN_CAMERA_SETTINGS)
        self.assertEqual(result.model.ui.admin_camera_page, 1)

    def test_page_prev_switches_back_to_page_0(self) -> None:
        self._go_to_admin_camera_settings()
        self._fill_ready()
        self.transition(EventType.TAP_ADMIN_CAMERA_PAGE_NEXT, now_offset=11.0)
        result = self.transition(EventType.TAP_ADMIN_CAMERA_PAGE_PREV, now_offset=12.0)
        self.assertEqual(result.model.ui.admin_camera_page, 0)

    # NEU (Kamera-Menue 2.0): Speichern/Abbrechen statt "Zurueck".

    def test_save_returns_to_admin_menu_without_revert_action(self) -> None:
        self._go_to_admin_camera_settings()
        self._fill_ready()
        self.transition(EventType.TAP_ADMIN_CAMERA_ISO_UP, now_offset=11.0)
        result = self.transition(EventType.TAP_ADMIN_CAMERA_SAVE, now_offset=12.0)
        self.assertEqual(result.model.state, AppState.ADMIN_MENU)
        self.assertNotIn("revert_admin_camera_settings", result.actions)
        # "stop_preview" kommt ueber _go_admin_menu automatisch mit (siehe
        # dessen Docstring/Kommentar) - hier nur als Regressionsschutz.
        self.assertIn("stop_preview", result.actions)

    def test_cancel_returns_to_admin_menu_with_revert_action(self) -> None:
        self._go_to_admin_camera_settings()
        self._fill_ready()
        self.transition(EventType.TAP_ADMIN_CAMERA_ISO_UP, now_offset=11.0)
        result = self.transition(EventType.TAP_ADMIN_CAMERA_CANCEL, now_offset=12.0)
        self.assertEqual(result.model.state, AppState.ADMIN_MENU)
        self.assertIn("revert_admin_camera_settings", result.actions)

    def test_back_emits_revert_action_like_cancel(self) -> None:
        self._go_to_admin_camera_settings()
        self._fill_ready()
        self.transition(EventType.TAP_ADMIN_CAMERA_ISO_UP, now_offset=11.0)
        result = self.transition(EventType.TAP_BACK, now_offset=12.0)
        self.assertIn("revert_admin_camera_settings", result.actions)

    def test_idle_timeout_emits_revert_action_like_cancel(self) -> None:
        self._go_to_admin_camera_settings()
        self._fill_ready()
        self.transition(EventType.TAP_ADMIN_CAMERA_ISO_UP, now_offset=11.0)
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=40.0)
        self.assertIn("revert_admin_camera_settings", result.actions)

    # NEU (Kamera-Menue 2.0): Einstiegs-Momentaufnahme fuer "Abbrechen" -
    # wird NUR beim ersten READY nach dem Betreten des Screens gesetzt.

    def test_entry_snapshot_captured_on_first_ready(self) -> None:
        self._go_to_admin_camera_settings()
        result = self._fill_ready(iso="400", aperture="5.6")
        self.assertTrue(result.model.ui.admin_camera_entry_captured)
        self.assertEqual(result.model.ui.admin_camera_entry_iso, "400")
        self.assertEqual(result.model.ui.admin_camera_entry_aperture, "5.6")

    def test_entry_snapshot_not_overwritten_by_later_ready(self) -> None:
        self._go_to_admin_camera_settings()
        self._fill_ready(iso="400", aperture="5.6")
        # +/--Tastendruck loest hardwareseitig ein weiteres READY aus (zur
        # Aktualisierung der Anzeige) - die urspruengliche Momentaufnahme
        # (400/5.6) darf dadurch NICHT auf den neuen Wert (800) ueberschrieben
        # werden, sonst waere "Abbrechen" wirkungslos.
        self.transition(EventType.TAP_ADMIN_CAMERA_ISO_UP, now_offset=11.0)
        result = self._fill_ready(now_offset=11.5, iso="800", aperture="5.6")
        self.assertEqual(result.model.ui.admin_camera_iso, "800")
        self.assertEqual(result.model.ui.admin_camera_entry_iso, "400")

    def test_cancel_reverts_ui_values_are_irrelevant_only_action_matters(self) -> None:
        # Das eigentliche Zuruecksenden an die Kamera passiert ausserhalb der
        # State-Machine (siehe app._revert_admin_camera_settings,
        # liest model.ui.admin_camera_entry_* aus) - hier wird nur
        # sichergestellt, dass die Entry-Werte zum Zeitpunkt des Abbrechens
        # noch unveraendert im Modell stehen, damit dieser Aufrufer sie lesen
        # kann.
        self._go_to_admin_camera_settings()
        self._fill_ready(iso="400", aperture="5.6")
        self.transition(EventType.TAP_ADMIN_CAMERA_ISO_UP, now_offset=11.0)
        result = self.transition(EventType.TAP_ADMIN_CAMERA_CANCEL, now_offset=12.0)
        self.assertIn("revert_admin_camera_settings", result.actions)
        # model bleibt (bis auf state/menu-relevante Felder) unveraendert -
        # die Entry-Werte sind im Modell weiterhin vorhanden.
        self.assertEqual(self.model.ui.admin_camera_entry_iso, "400")


class AdminEventSettingsTestCase(unittest.TestCase):
    """Tests fuer 'Veranstaltungsdaten' (letzte Sprint-11-Aufgabe): Betreten
    fordert synchron die aktuellen Werte an, Textfelder werden ueber die
    Bildschirmtastatur bearbeitet, Speichern schreibt (Action), Abbrechen/
    Idle stellen den Snapshot wieder her, der Wallpaper-Import kehrt in die
    noch offene Bearbeitung zurueck statt ins Service-Menue."""

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

    def _go_to_admin_event_settings(self, now_offset: float = 5.2) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.SHUTDOWN_GESTURE_DETECTED, now_offset=now_offset)
        self.transition(
            EventType.PIN_SUBMIT,
            now_offset=now_offset + 1.0,
            payload={"pin_result": PinResult.ACCEPTED},
        )
        self.assertEqual(self.model.state, AppState.ADMIN_MENU)
        result = self.transition(EventType.TAP_ADMIN_EVENT_SETTINGS, now_offset=now_offset + 2.0)
        self.assertEqual(result.model.state, AppState.ADMIN_EVENT_SETTINGS)
        self.assertIn("collect_admin_event_settings", result.actions)

    def _fill_ready(self, now_offset: float = 10.0, **overrides):
        payload = {
            "title": "Testfest",
            "prefix": "test_",
            "wifi_ssid": "Fotobox_Gast",
            "wifi_password": "geheim123",
            "qr_enabled": True,
            "gallery_enabled": True,
        }
        payload.update(overrides)
        return self.transition(EventType.ADMIN_EVENT_SETTINGS_READY, now_offset=now_offset, payload=payload)

    # -- Betreten / Snapshot -------------------------------------------------

    def test_ready_fills_draft_and_snapshot(self) -> None:
        self._go_to_admin_event_settings()
        result = self._fill_ready()
        self.assertEqual(result.model.ui.admin_event_title, "Testfest")
        self.assertEqual(result.model.ui.admin_event_entry_title, "Testfest")
        self.assertEqual(result.model.ui.admin_event_wifi_password, "geheim123")
        self.assertEqual(result.model.ui.admin_event_entry_wifi_password, "geheim123")

    # -- Feld bearbeiten (Textfelder) ----------------------------------------

    def test_field_edit_opens_text_entry_with_current_value_preset(self) -> None:
        self._go_to_admin_event_settings()
        self._fill_ready()
        result = self.transition(
            EventType.TAP_ADMIN_EVENT_FIELD_EDIT, now_offset=11.0, payload={"field": "title"},
        )
        self.assertEqual(result.model.state, AppState.ADMIN_EVENT_TEXT_ENTRY)
        self.assertEqual(result.model.ui.admin_event_edit_field, "title")
        self.assertEqual(result.model.ui.admin_event_text_buffer, "Testfest")

    def test_text_entry_char_and_submit_writes_field(self) -> None:
        self._go_to_admin_event_settings()
        self._fill_ready()
        self.transition(EventType.TAP_ADMIN_EVENT_FIELD_EDIT, now_offset=11.0, payload={"field": "title"})
        self.transition(EventType.TEXT_ENTRY_BACKSPACE, now_offset=12.0)  # "Testfest" -> "Testfes"
        for char in "!!":
            self.transition(EventType.TEXT_ENTRY_CHAR, now_offset=12.1, payload={"char": char})
        result = self.transition(EventType.TEXT_ENTRY_SUBMIT, now_offset=13.0)
        self.assertEqual(result.model.state, AppState.ADMIN_EVENT_SETTINGS)
        self.assertEqual(result.model.ui.admin_event_title, "Testfes!!")

    def test_text_entry_cancel_discards_buffer(self) -> None:
        self._go_to_admin_event_settings()
        self._fill_ready()
        self.transition(EventType.TAP_ADMIN_EVENT_FIELD_EDIT, now_offset=11.0, payload={"field": "title"})
        self.transition(EventType.TEXT_ENTRY_CHAR, now_offset=12.0, payload={"char": "X"})
        result = self.transition(EventType.TEXT_ENTRY_CANCEL, now_offset=13.0)
        self.assertEqual(result.model.state, AppState.ADMIN_EVENT_SETTINGS)
        self.assertEqual(result.model.ui.admin_event_title, "Testfest")

    def test_empty_submit_keeps_previous_value(self) -> None:
        self._go_to_admin_event_settings()
        self._fill_ready()
        self.transition(EventType.TAP_ADMIN_EVENT_FIELD_EDIT, now_offset=11.0, payload={"field": "title"})
        for _ in range(20):
            self.transition(EventType.TEXT_ENTRY_BACKSPACE, now_offset=12.0)
        result = self.transition(EventType.TEXT_ENTRY_SUBMIT, now_offset=13.0)
        self.assertEqual(result.model.ui.admin_event_title, "Testfest")

    def test_prefix_rejects_disallowed_characters(self) -> None:
        self._go_to_admin_event_settings()
        self._fill_ready()
        self.transition(EventType.TAP_ADMIN_EVENT_FIELD_EDIT, now_offset=11.0, payload={"field": "prefix"})
        for _ in range(20):
            self.transition(EventType.TEXT_ENTRY_BACKSPACE, now_offset=11.5)
        self.transition(EventType.TEXT_ENTRY_CHAR, now_offset=12.0, payload={"char": " "})
        self.transition(EventType.TEXT_ENTRY_CHAR, now_offset=12.0, payload={"char": "ä"})
        self.transition(EventType.TEXT_ENTRY_CHAR, now_offset=12.0, payload={"char": "/"})
        self.assertEqual(self.model.ui.admin_event_text_buffer, "")
        self.transition(EventType.TEXT_ENTRY_CHAR, now_offset=12.1, payload={"char": "_"})
        self.transition(EventType.TEXT_ENTRY_CHAR, now_offset=12.1, payload={"char": "a"})
        self.assertEqual(self.model.ui.admin_event_text_buffer, "_a")

    def test_field_length_limit_is_enforced(self) -> None:
        self._go_to_admin_event_settings()
        self._fill_ready()
        self.transition(EventType.TAP_ADMIN_EVENT_FIELD_EDIT, now_offset=11.0, payload={"field": "prefix"})
        for _ in range(20):
            self.transition(EventType.TEXT_ENTRY_BACKSPACE, now_offset=11.5)
        for offset in range(30):
            self.transition(EventType.TEXT_ENTRY_CHAR, now_offset=12.0 + offset, payload={"char": "a"})
        self.assertEqual(len(self.model.ui.admin_event_text_buffer), 20)

    def test_text_entry_uses_dedicated_longer_idle_timeout(self) -> None:
        self._go_to_admin_event_settings()
        self._fill_ready()
        result = self.transition(
            EventType.TAP_ADMIN_EVENT_FIELD_EDIT, now_offset=11.0, payload={"field": "title"},
        )
        expected = self.now + 11.0 + self.config.timeouts.admin_event_text_entry_idle_seconds
        self.assertAlmostEqual(result.model.timers.idle_deadline, expected)

    # -- Schalter -------------------------------------------------------------

    def test_toggle_qr_flips_value(self) -> None:
        self._go_to_admin_event_settings()
        self._fill_ready(qr_enabled=True)
        result = self.transition(EventType.TAP_ADMIN_EVENT_TOGGLE, now_offset=11.0, payload={"field": "qr"})
        self.assertFalse(result.model.ui.admin_event_qr_enabled)

    def test_toggle_gallery_flips_value(self) -> None:
        self._go_to_admin_event_settings()
        self._fill_ready(gallery_enabled=True)
        result = self.transition(EventType.TAP_ADMIN_EVENT_TOGGLE, now_offset=11.0, payload={"field": "gallery"})
        self.assertFalse(result.model.ui.admin_event_gallery_enabled)

    # ENTFERNT (Nutzer-Feedback): test_toggle_password_visible_flips_flag -
    # das WLAN-Passwort wird nicht mehr maskiert, es gibt kein "Anzeigen"
    # mehr und damit auch kein TAP_ADMIN_EVENT_TOGGLE_PASSWORD_VISIBLE.

    # -- Standardwerte ----------------------------------------------------------

    def test_defaults_fills_draft_with_default_event_values(self) -> None:
        # NEU (Nutzer-Feedback): "Standardwerte"-Taste - befuellt NUR den
        # Entwurf (admin_event_*), siehe naechster Test fuer den Snapshot.
        from event_config_service import DEFAULT_EVENT_VALUES

        self._go_to_admin_event_settings()
        self._fill_ready(
            title="Altes Fest", prefix="alt_", wifi_ssid="Alt-WLAN", wifi_password="altespw",
            qr_enabled=False, gallery_enabled=False,
        )
        result = self.transition(EventType.TAP_ADMIN_EVENT_DEFAULTS, now_offset=11.0)
        self.assertEqual(result.model.ui.admin_event_title, DEFAULT_EVENT_VALUES["title"])
        self.assertEqual(result.model.ui.admin_event_prefix, DEFAULT_EVENT_VALUES["prefix"])
        self.assertEqual(result.model.ui.admin_event_wifi_ssid, DEFAULT_EVENT_VALUES["wifi_ssid"])
        self.assertEqual(result.model.ui.admin_event_wifi_password, DEFAULT_EVENT_VALUES["wifi_password"])
        self.assertEqual(result.model.ui.admin_event_qr_enabled, DEFAULT_EVENT_VALUES["qr_enabled"])
        self.assertEqual(result.model.ui.admin_event_gallery_enabled, DEFAULT_EVENT_VALUES["gallery_enabled"])

    def test_defaults_does_not_touch_snapshot_so_cancel_still_reverts(self) -> None:
        # WICHTIG: "Standardwerte" ist nur eine Entwurfsaenderung wie jede
        # andere - "Abbrechen" muss sie danach genau wie eine manuelle
        # Aenderung wieder verwerfen koennen.
        self._go_to_admin_event_settings()
        self._fill_ready(title="Altes Fest")
        self.transition(EventType.TAP_ADMIN_EVENT_DEFAULTS, now_offset=11.0)
        self.assertNotEqual(self.model.ui.admin_event_title, "Altes Fest")
        result = self.transition(EventType.TAP_BACK, now_offset=12.0)
        self.assertEqual(result.model.state, AppState.ADMIN_MENU)
        self.assertEqual(result.model.ui.admin_event_title, "Altes Fest")

    def test_defaults_does_not_trigger_save(self) -> None:
        self._go_to_admin_event_settings()
        self._fill_ready()
        result = self.transition(EventType.TAP_ADMIN_EVENT_DEFAULTS, now_offset=11.0)
        self.assertNotIn("save_event_config", result.actions)
        self.assertEqual(result.model.state, AppState.ADMIN_EVENT_SETTINGS)

    # -- Speichern / Abbrechen -------------------------------------------------

    def test_save_triggers_action_and_stays_in_state(self) -> None:
        self._go_to_admin_event_settings()
        self._fill_ready()
        result = self.transition(EventType.TAP_ADMIN_EVENT_SAVE, now_offset=11.0)
        self.assertIn("save_event_config", result.actions)
        self.assertEqual(result.model.state, AppState.ADMIN_EVENT_SETTINGS)

    def test_save_result_ok_transitions_to_saved(self) -> None:
        self._go_to_admin_event_settings()
        self._fill_ready()
        self.transition(EventType.TAP_ADMIN_EVENT_SAVE, now_offset=11.0)
        result = self.transition(
            EventType.ADMIN_EVENT_SAVE_RESULT, now_offset=11.5,
            payload={"ok": True, "message": "event_config.json gespeichert."},
        )
        self.assertEqual(result.model.state, AppState.ADMIN_EVENT_SAVED)
        self.assertTrue(result.model.ui.admin_event_save_ok)
        self.assertEqual(result.model.ui.admin_event_save_message, "event_config.json gespeichert.")

    def test_cancel_reverts_draft_and_does_not_save(self) -> None:
        self._go_to_admin_event_settings()
        self._fill_ready(title="Altes Fest")
        self.transition(EventType.TAP_ADMIN_EVENT_FIELD_EDIT, now_offset=11.0, payload={"field": "title"})
        for _ in range(20):
            self.transition(EventType.TEXT_ENTRY_BACKSPACE, now_offset=11.5)
        self.transition(EventType.TEXT_ENTRY_CHAR, now_offset=12.0, payload={"char": "X"})
        self.transition(EventType.TEXT_ENTRY_SUBMIT, now_offset=12.5)
        self.assertEqual(self.model.ui.admin_event_title, "X")
        result = self.transition(EventType.TAP_BACK, now_offset=13.0)
        self.assertEqual(result.model.state, AppState.ADMIN_MENU)
        self.assertNotIn("save_event_config", result.actions)
        self.assertEqual(result.model.ui.admin_event_title, "Altes Fest")
        # NEU (Nutzer-Feedback, Bugfix): "Abbrechen" muss ein evtl.
        # zwischengelagertes (aber noch nicht uebernommenes) Wallpaper immer
        # verwerfen - die Action ist ein No-Op, wenn nie eines ausgewaehlt
        # wurde, wird aber trotzdem immer angehaengt.
        self.assertIn("discard_pending_wallpaper", result.actions)

    def test_idle_timeout_reverts_like_cancel(self) -> None:
        self._go_to_admin_event_settings()
        self._fill_ready(title="Altes Fest")
        self.transition(EventType.TAP_ADMIN_EVENT_TOGGLE, now_offset=11.0, payload={"field": "qr"})
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=90.0)
        self.assertEqual(result.model.state, AppState.ADMIN_MENU)
        self.assertTrue(result.model.ui.admin_event_qr_enabled)

    # -- Wallpaper-Import -------------------------------------------------------

    def test_wallpaper_import_starts_list_action_and_is_not_idle_abortable(self) -> None:
        # GEAENDERT (Nutzer-Feedback): TAP_ADMIN_EVENT_WALLPAPER_IMPORT loest
        # jetzt nur noch das Suchen/Auflisten aus (Action "wallpaper_pick_list"),
        # nichts wird mehr automatisch kopiert.
        self._go_to_admin_event_settings()
        self._fill_ready()
        result = self.transition(EventType.TAP_ADMIN_EVENT_WALLPAPER_IMPORT, now_offset=11.0)
        self.assertEqual(result.model.state, AppState.ADMIN_EVENT_WALLPAPER_PICK_LOADING)
        self.assertIn("wallpaper_pick_list", result.actions)
        self.assertIsNone(result.model.timers.idle_deadline)

    def test_wallpaper_list_finished_with_candidates_shows_pick_screen(self) -> None:
        self._go_to_admin_event_settings()
        self._fill_ready()
        self.transition(EventType.TAP_ADMIN_EVENT_WALLPAPER_IMPORT, now_offset=11.0)
        result = self.transition(
            EventType.ADMIN_EVENT_WALLPAPER_LIST_FINISHED, now_offset=13.0,
            payload={"ok": True, "candidates": ("aaa.png", "bbb.jpg")},
        )
        self.assertEqual(result.model.state, AppState.ADMIN_EVENT_WALLPAPER_PICK)
        self.assertEqual(result.model.ui.admin_event_wallpaper_candidates, ("aaa.png", "bbb.jpg"))
        self.assertEqual(result.model.ui.admin_event_wallpaper_selected, "")

    def test_wallpaper_list_finished_without_candidates_shows_error_result(self) -> None:
        self._go_to_admin_event_settings()
        self._fill_ready()
        self.transition(EventType.TAP_ADMIN_EVENT_WALLPAPER_IMPORT, now_offset=11.0)
        result = self.transition(
            EventType.ADMIN_EVENT_WALLPAPER_LIST_FINISHED, now_offset=13.0,
            payload={"ok": False, "lines": ("Kein Bild (.png/.jpg) auf dem Stick gefunden.",)},
        )
        self.assertEqual(result.model.state, AppState.ADMIN_EVENT_WALLPAPER_RESULT)
        self.assertFalse(result.model.ui.admin_event_wallpaper_ok)
        self.assertEqual(
            result.model.ui.admin_event_wallpaper_lines,
            ("Kein Bild (.png/.jpg) auf dem Stick gefunden.",),
        )

    def test_back_from_wallpaper_result_returns_to_editing_not_admin_menu(self) -> None:
        # Regressionsschutz fuer die bewusste Unterscheidung zwischen "ganz
        # verlassen" (verwirft) und "aus Unter-Screen zurueckkehren" (behaelt
        # den Bearbeitungsstand) - siehe state_machine._return_to_admin_event_settings.
        # GEAENDERT: ADMIN_EVENT_WALLPAPER_RESULT ist inzwischen ein reiner
        # Fehlerbildschirm (Erfolg fuehrt direkt zurueck, siehe
        # test_wallpaper_pick_save_stages_and_returns_with_pending_flag), der
        # Fehlerfall wird hier ueber die leere Kandidatenliste erzeugt.
        self._go_to_admin_event_settings()
        self._fill_ready(title="Altes Fest")
        self.transition(EventType.TAP_ADMIN_EVENT_FIELD_EDIT, now_offset=11.0, payload={"field": "title"})
        for _ in range(20):
            self.transition(EventType.TEXT_ENTRY_BACKSPACE, now_offset=11.5)
        self.transition(EventType.TEXT_ENTRY_CHAR, now_offset=12.0, payload={"char": "X"})
        self.transition(EventType.TEXT_ENTRY_SUBMIT, now_offset=12.5)
        self.assertEqual(self.model.ui.admin_event_title, "X")
        self.transition(EventType.TAP_ADMIN_EVENT_WALLPAPER_IMPORT, now_offset=13.0)
        self.transition(
            EventType.ADMIN_EVENT_WALLPAPER_LIST_FINISHED, now_offset=14.0,
            payload={"ok": False, "lines": ("Kein Bild (.png/.jpg) auf dem Stick gefunden.",)},
        )
        result = self.transition(EventType.TAP_BACK, now_offset=15.0)
        self.assertEqual(result.model.state, AppState.ADMIN_EVENT_SETTINGS)
        self.assertEqual(result.model.ui.admin_event_title, "X")

    # -- Wallpaper-Auswahlliste (ADMIN_EVENT_WALLPAPER_PICK) --------------------

    def _go_to_wallpaper_pick(self, candidates: tuple[str, ...] = ("aaa.png", "bbb.jpg")) -> None:
        self._go_to_admin_event_settings()
        self._fill_ready()
        self.transition(EventType.TAP_ADMIN_EVENT_WALLPAPER_IMPORT, now_offset=11.0)
        self.transition(
            EventType.ADMIN_EVENT_WALLPAPER_LIST_FINISHED, now_offset=13.0,
            payload={"ok": True, "candidates": candidates},
        )

    def test_wallpaper_select_marks_it_without_leaving_pick_screen(self) -> None:
        self._go_to_wallpaper_pick()
        result = self.transition(
            EventType.TAP_ADMIN_EVENT_WALLPAPER_SELECT, now_offset=14.0, payload={"name": "bbb.jpg"},
        )
        self.assertEqual(result.model.state, AppState.ADMIN_EVENT_WALLPAPER_PICK)
        self.assertEqual(result.model.ui.admin_event_wallpaper_selected, "bbb.jpg")

    def test_wallpaper_pick_save_without_selection_is_a_no_op(self) -> None:
        self._go_to_wallpaper_pick()
        result = self.transition(EventType.TAP_ADMIN_EVENT_WALLPAPER_PICK_SAVE, now_offset=14.0)
        self.assertEqual(result.model.state, AppState.ADMIN_EVENT_WALLPAPER_PICK)
        self.assertNotIn("wallpaper_pick_stage", result.actions)

    def test_wallpaper_pick_save_with_selection_triggers_stage_action(self) -> None:
        self._go_to_wallpaper_pick()
        self.transition(
            EventType.TAP_ADMIN_EVENT_WALLPAPER_SELECT, now_offset=14.0, payload={"name": "bbb.jpg"},
        )
        result = self.transition(EventType.TAP_ADMIN_EVENT_WALLPAPER_PICK_SAVE, now_offset=14.5)
        self.assertIn("wallpaper_pick_stage", result.actions)

    def test_wallpaper_stage_result_ok_returns_to_settings_with_pending_flag(self) -> None:
        # WICHTIG (Bugfix): erst hier wird admin_event_wallpaper_pending
        # gesetzt - das Bild ist damit NOCH NICHT das echte Hauptmenue-
        # Wallpaper, das passiert erst beim AEUSSEREN "Speichern" (siehe
        # app._save_admin_event_settings/promote_pending_wallpaper).
        self._go_to_wallpaper_pick()
        self.transition(
            EventType.TAP_ADMIN_EVENT_WALLPAPER_SELECT, now_offset=14.0, payload={"name": "bbb.jpg"},
        )
        self.transition(EventType.TAP_ADMIN_EVENT_WALLPAPER_PICK_SAVE, now_offset=14.5)
        result = self.transition(
            EventType.ADMIN_EVENT_WALLPAPER_STAGE_RESULT, now_offset=15.0, payload={"ok": True},
        )
        self.assertEqual(result.model.state, AppState.ADMIN_EVENT_SETTINGS)
        self.assertTrue(result.model.ui.admin_event_wallpaper_pending)

    def test_wallpaper_stage_result_error_shows_result_screen(self) -> None:
        self._go_to_wallpaper_pick()
        self.transition(
            EventType.TAP_ADMIN_EVENT_WALLPAPER_SELECT, now_offset=14.0, payload={"name": "bbb.jpg"},
        )
        self.transition(EventType.TAP_ADMIN_EVENT_WALLPAPER_PICK_SAVE, now_offset=14.5)
        result = self.transition(
            EventType.ADMIN_EVENT_WALLPAPER_STAGE_RESULT, now_offset=15.0,
            payload={"ok": False, "message": "Konnte Wallpaper nicht uebernehmen."},
        )
        self.assertEqual(result.model.state, AppState.ADMIN_EVENT_WALLPAPER_RESULT)
        self.assertFalse(result.model.ui.admin_event_wallpaper_ok)

    def test_wallpaper_pick_cancel_returns_to_settings_and_discards_mount(self) -> None:
        self._go_to_wallpaper_pick()
        result = self.transition(EventType.TAP_ADMIN_EVENT_WALLPAPER_PICK_CANCEL, now_offset=14.0)
        self.assertEqual(result.model.state, AppState.ADMIN_EVENT_SETTINGS)
        self.assertIn("wallpaper_pick_discard", result.actions)
        self.assertFalse(result.model.ui.admin_event_wallpaper_pending)

    def test_wallpaper_pick_idle_timeout_behaves_like_cancel(self) -> None:
        self._go_to_wallpaper_pick()
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=90.0)
        self.assertEqual(result.model.state, AppState.ADMIN_EVENT_SETTINGS)
        self.assertIn("wallpaper_pick_discard", result.actions)

    # -- Gespeichert-Bestaetigung -----------------------------------------------

    def test_saved_restart_now_leads_to_restart_pending(self) -> None:
        self._go_to_admin_event_settings()
        self._fill_ready()
        self.transition(EventType.TAP_ADMIN_EVENT_SAVE, now_offset=11.0)
        self.transition(
            EventType.ADMIN_EVENT_SAVE_RESULT, now_offset=11.5, payload={"ok": True, "message": "ok"},
        )
        result = self.transition(EventType.TAP_ADMIN_EVENT_RESTART_NOW, now_offset=12.0)
        self.assertEqual(result.model.state, AppState.ADMIN_RESTART_PENDING)

    def test_saved_later_returns_to_admin_menu(self) -> None:
        self._go_to_admin_event_settings()
        self._fill_ready()
        self.transition(EventType.TAP_ADMIN_EVENT_SAVE, now_offset=11.0)
        self.transition(
            EventType.ADMIN_EVENT_SAVE_RESULT, now_offset=11.5, payload={"ok": True, "message": "ok"},
        )
        result = self.transition(EventType.TAP_BACK, now_offset=12.0)
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
        # GEAENDERT (Nutzer-Feedback): TAP_ADMIN_RESTART_APP fuehrt seit der
        # Sicherheitsabfrage nicht mehr direkt in ADMIN_RESTART_PENDING,
        # sondern zuerst in ADMIN_RESTART_CONFIRM - dieser Helfer bestaetigt
        # die Abfrage mit, damit alle bestehenden Tests unten unveraendert
        # denselben Endzustand (ADMIN_RESTART_PENDING) sehen wie vorher.
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.SHUTDOWN_GESTURE_DETECTED, now_offset=now_offset)
        self.transition(
            EventType.PIN_SUBMIT,
            now_offset=now_offset + 1.0,
            payload={"pin_result": PinResult.ACCEPTED},
        )
        confirm = self.transition(EventType.TAP_ADMIN_RESTART_APP, now_offset=now_offset + 2.0)
        self.assertEqual(confirm.model.state, AppState.ADMIN_RESTART_CONFIRM)
        result = self.transition(EventType.TAP_ADMIN_RESTART_CONFIRM, now_offset=now_offset + 2.5)
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
        # GEAENDERT (Nutzer-Feedback): der tatsaechliche Uebergang nach
        # ADMIN_RESTART_PENDING passiert jetzt bei now_offset+2.5 (Bestaetigen
        # der Sicherheitsabfrage), nicht mehr bei now_offset+2.0 (Tap auf
        # "App neu starten" selbst) - siehe _go_to_restart_pending.
        expected = self.now + 7.7 + self.config.timeouts.admin_restart_delay_seconds
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
    wenn app.py ihn fuer diesen Zustand ueberhaupt AUSLOEST. Beides
    steht an voellig verschiedenen Stellen (_go_*-Methode setzt die
    Deadline, _emit_due_timers entscheidet ueber das Feuern) und lief
    auseinander - die Loesch-Abfrage bekam eine Deadline, lief aber nie in
    den Timeout, weil sie im idle_states-Set fehlte.

    Die uebrigen Tests hier speisen IDLE_TIMEOUT direkt ein und pruefen
    damit nur die Reaktion, nicht die Ausloesung. Dieser Test schliesst
    die Luecke - bewusst auf Quelltextebene, weil app.py sich fuer
    einen Import Hardware-Provider und ein Pygame-Fenster erzeugen wuerde.
    """

    ADMIN_STATES_WITH_IDLE = (
        "ADMIN_MENU", "ADMIN_STATUS", "ADMIN_DELETE_CONFIRM",
        "ADMIN_DELETE_DONE",
        "ADMIN_USB_WAIT", "ADMIN_USB_READY", "ADMIN_USB_PROBLEM", "ADMIN_USB_REMOVE",
        "ADMIN_USB_EXPORT_DONE",
        "ADMIN_USB_CONFLICTS",
        # NEU (Sprint 11, Feature 2 / Kamera-Menue 2.0): eigene Zeile, nicht
        # vergessen worden - beide bekommen eine idle_deadline (siehe
        # state_machine._go_admin_camera_settings/_go_admin_shutdown_confirm).
        "ADMIN_CAMERA_SETTINGS",
        "ADMIN_SHUTDOWN_CONFIRM",
        # NEU (Nutzer-Feedback): gleiche Begruendung wie ADMIN_SHUTDOWN_CONFIRM.
        "ADMIN_RESTART_CONFIRM",
        # NEU (Veranstaltungsdaten): bewusst OHNE ADMIN_EVENT_WALLPAPER_PICK_LOADING
        # - der laeuft nicht abbrechbar (analog ADMIN_USB_CHECK), bekommt
        # deshalb keine idle_deadline. ADMIN_EVENT_WALLPAPER_PICK (die
        # eigentliche Auswahlliste) bekommt dagegen eine - der Admin kann
        # dort beliebig lange ueberlegen, soll aber nicht ewig haengen bleiben.
        "ADMIN_EVENT_SETTINGS",
        "ADMIN_EVENT_TEXT_ENTRY",
        "ADMIN_EVENT_WALLPAPER_PICK",
        "ADMIN_EVENT_WALLPAPER_RESULT",
        "ADMIN_EVENT_SAVED",
    )

    def test_states_with_idle_deadline_are_emitted_by_the_app(self) -> None:
        from pathlib import Path
        source = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
        anchor = source.find("AppState.TERMS,")
        self.assertNotEqual(anchor, -1, "Anker 'AppState.TERMS,' in app.py nicht gefunden.")
        block_end = source.find("}", anchor)
        self.assertNotEqual(block_end, -1, "Ende des idle_states-Sets nicht gefunden.")
        block = source[anchor:block_end]

        missing = [name for name in self.ADMIN_STATES_WITH_IDLE if name not in block]
        self.assertEqual(
            missing, [],
            f"Diese Zustaende bekommen eine idle_deadline, feuern aber nie einen "
            f"IDLE_TIMEOUT (fehlen im idle_states-Set in app.py): {missing}",
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
            (EventType.TAP_ADMIN_CAMERA_SETTINGS, AppState.ADMIN_CAMERA_SETTINGS),
            (EventType.TAP_ADMIN_SHUTDOWN, AppState.ADMIN_SHUTDOWN_CONFIRM),
            (EventType.TAP_ADMIN_EVENT_SETTINGS, AppState.ADMIN_EVENT_SETTINGS),
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
