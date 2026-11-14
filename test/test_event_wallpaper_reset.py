"""
test_event_wallpaper_reset.py
=============================
NEU (Sprint 14, Nutzer-Feedback 2026-11-14): Reset der veranstaltungs-
spezifischen Wallpaper beim "Standardwerte"-Speichern (event_wallpaper_
reset.py) sowie die Ablauflogik in der State Machine.

    python3 -m pytest test/test_event_wallpaper_reset.py -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import event_wallpaper_reset as ewr


class ResetEventWallpapersTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.assets = Path(self._tmp.name)
        (self.assets / ewr.STARTUP_WALLPAPER).write_bytes(b"START")
        (self.assets / ewr.MAIN_MENU_WALLPAPER_ORIGINAL).write_bytes(b"ORIGINAL")
        (self.assets / ewr.HDMI_WALLPAPER).write_bytes(b"BASAR-HDMI")
        (self.assets / ewr.MAIN_MENU_WALLPAPER).write_bytes(b"BASAR-MENU")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _read(self, name: str) -> bytes:
        return (self.assets / name).read_bytes()

    def test_both_wallpapers_are_reset(self) -> None:
        result = ewr.reset_event_wallpapers(self.assets)
        self.assertTrue(result.ok)
        self.assertEqual(result.messages, ())
        self.assertEqual(self._read(ewr.HDMI_WALLPAPER), b"START")
        self.assertEqual(self._read(ewr.MAIN_MENU_WALLPAPER), b"ORIGINAL")

    def test_sources_stay_untouched(self) -> None:
        ewr.reset_event_wallpapers(self.assets)
        self.assertEqual(self._read(ewr.STARTUP_WALLPAPER), b"START")
        self.assertEqual(self._read(ewr.MAIN_MENU_WALLPAPER_ORIGINAL), b"ORIGINAL")

    def test_missing_hdmi_target_is_created(self) -> None:
        (self.assets / ewr.HDMI_WALLPAPER).unlink()
        result = ewr.reset_event_wallpapers(self.assets)
        self.assertTrue(result.hdmi_reset)
        self.assertEqual(self._read(ewr.HDMI_WALLPAPER), b"START")

    def test_missing_original_keeps_main_menu_wallpaper(self) -> None:
        (self.assets / ewr.MAIN_MENU_WALLPAPER_ORIGINAL).unlink()
        result = ewr.reset_event_wallpapers(self.assets)
        self.assertFalse(result.main_menu_reset)
        self.assertTrue(result.hdmi_reset)
        self.assertEqual(self._read(ewr.MAIN_MENU_WALLPAPER), b"BASAR-MENU")
        self.assertTrue(any("original" in m for m in result.messages))

    def test_failed_copy_keeps_old_file_and_leaves_no_tmp(self) -> None:
        with mock.patch("event_wallpaper_reset.shutil.copyfile", side_effect=OSError("Speicher voll")):
            result = ewr.reset_event_wallpapers(self.assets)
        self.assertFalse(result.ok)
        self.assertEqual(self._read(ewr.HDMI_WALLPAPER), b"BASAR-HDMI")
        self.assertEqual(self._read(ewr.MAIN_MENU_WALLPAPER), b"BASAR-MENU")
        self.assertEqual(list(self.assets.glob("*.tmp")), [])

    def test_hdmi_path_prefers_own_file(self) -> None:
        self.assertEqual(ewr.hdmi_wallpaper_path(self.assets), self.assets / ewr.HDMI_WALLPAPER)

    def test_hdmi_path_falls_back_to_startup(self) -> None:
        (self.assets / ewr.HDMI_WALLPAPER).unlink()
        self.assertEqual(ewr.hdmi_wallpaper_path(self.assets), self.assets / ewr.STARTUP_WALLPAPER)


class DefaultsActionTestCase(unittest.TestCase):
    """"Standardwerte" muss den Wallpaper-Reset anmelden (Action), ein
    wartendes Wallpaper verwerfen und darf noch NICHT speichern."""

    def setUp(self) -> None:
        from config import DEFAULT_CONFIG
        from state_machine import StateMachine
        from states import AppState
        self.AppState = AppState
        self.machine = StateMachine(DEFAULT_CONFIG)
        self.model = self.machine.initial_model(1000.0)
        # Direkt in den Screen Veranstaltungsdaten springen (reiner Logiktest).
        from dataclasses import replace
        self.model = self.model.evolve(
            state=AppState.ADMIN_EVENT_SETTINGS,
            ui=replace(self.model.ui, admin_event_wallpaper_pending=True),
        )

    def _send(self, event_type, payload=None):
        from events import AppEvent
        result = self.machine.transition(self.model, AppEvent(event_type, payload=payload or {}, source="test"), 1001.0)
        self.model = result.model
        return result

    def test_defaults_requests_wallpaper_reset(self) -> None:
        from events import EventType
        result = self._send(EventType.TAP_ADMIN_EVENT_DEFAULTS)
        self.assertIn("mark_event_wallpaper_reset", result.actions)
        self.assertNotIn("save_event_config", result.actions)
        self.assertEqual(self.model.state, self.AppState.ADMIN_EVENT_SETTINGS)

    def test_defaults_drops_pending_wallpaper(self) -> None:
        from events import EventType
        self._send(EventType.TAP_ADMIN_EVENT_DEFAULTS)
        self.assertFalse(self.model.ui.admin_event_wallpaper_pending)

    def test_cancel_after_defaults_emits_discard(self) -> None:
        # Die App setzt bei "discard_pending_wallpaper" auch den Reset-
        # Merker zurueck - Abbrechen darf also nichts zuruecksetzen.
        from events import EventType
        self._send(EventType.TAP_ADMIN_EVENT_DEFAULTS)
        result = self._send(EventType.TAP_BACK)
        self.assertIn("discard_pending_wallpaper", result.actions)


if __name__ == "__main__":
    unittest.main()
