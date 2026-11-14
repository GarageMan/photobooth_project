"""
test_state_machine_attract_hdmi.py
==================================
NEU (Sprint 14, Nutzer-Feedback): laeuft die Galerie-Wiedergabe bereits
auf dem Beamer (HDMI), darf das Hauptmenue bei Untaetigkeit KEINE
automatische Galerie (ATTRACT_GALLERY) auf dem Touch-Display starten.
Die App liefert den Status als Payload "hdmi_slideshow_running" mit dem
IDLE_TIMEOUT-Event (siehe app._emit_due_timers).

Reine Logik, keine Hardware.

    python3 -m pytest test/test_state_machine_attract_hdmi.py -v
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from config import DEFAULT_CONFIG
from events import AppEvent, EventType
from state_machine import StateMachine
from states import AppState


class AttractGalleryWithHdmiSlideshowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.config = DEFAULT_CONFIG
        self.machine = StateMachine(self.config)
        self.now = 1000.0
        self.model = self.machine.initial_model(self.now)
        # BOOT -> MAIN_MENU
        self._send(EventType.TICK, self.config.timeouts.boot_seconds + 0.1)
        self.assertEqual(self.model.state, AppState.MAIN_MENU)

    def _send(self, event_type: EventType, offset: float, payload: dict | None = None):
        event = AppEvent(event_type, payload=payload or {}, source="test")
        result = self.machine.transition(self.model, event, self.now + offset)
        self.model = result.model
        return result

    def test_idle_without_hdmi_slideshow_starts_attract_gallery(self) -> None:
        self._send(EventType.IDLE_TIMEOUT, 100.0, {"hdmi_slideshow_running": False})
        self.assertEqual(self.model.state, AppState.ATTRACT_GALLERY)

    def test_idle_without_payload_keeps_previous_behaviour(self) -> None:
        # Bestehende Aufrufer ohne Payload: Verhalten wie bisher.
        self._send(EventType.IDLE_TIMEOUT, 100.0)
        self.assertEqual(self.model.state, AppState.ATTRACT_GALLERY)

    def test_idle_with_hdmi_slideshow_stays_in_main_menu(self) -> None:
        self._send(EventType.IDLE_TIMEOUT, 100.0, {"hdmi_slideshow_running": True})
        self.assertEqual(self.model.state, AppState.MAIN_MENU)

    def test_idle_with_hdmi_slideshow_rearms_idle_deadline(self) -> None:
        # Hauptmenue muss die Idle-Deadline neu setzen, sonst feuert
        # IDLE_TIMEOUT in jedem Frame erneut.
        self._send(EventType.IDLE_TIMEOUT, 100.0, {"hdmi_slideshow_running": True})
        self.assertIsNotNone(self.model.timers.idle_deadline)
        self.assertGreater(self.model.timers.idle_deadline, self.now + 100.0)

    def test_manual_gallery_still_possible_with_hdmi_slideshow(self) -> None:
        # Nur die AUTOMATISCHE Galerie entfaellt - Gaeste koennen die
        # Galerie weiterhin selbst oeffnen.
        self._send(EventType.IDLE_TIMEOUT, 100.0, {"hdmi_slideshow_running": True})
        self.model = replace(self.model, session=replace(self.model.session, photos=[]))
        self._send(EventType.TAP_GALLERY, 101.0)
        self.assertIn(self.model.state, {AppState.GALLERY_GRID, AppState.GALLERY_EMPTY})


if __name__ == "__main__":
    unittest.main()
