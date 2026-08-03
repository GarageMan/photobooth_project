from __future__ import annotations

import time
import unittest
from dataclasses import replace

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

    def test_review_save_no_longer_generates_qr(self) -> None:
        """NEU (Sprint 11, Feature 3): TAP_SAVE zeigt keinen QR-Code mehr
        direkt an - "generate_qr" darf nicht mehr unter den ausgeloesten
        Actions sein, "export_photo" bleibt (Feature 4 braucht die
        exportierte Datei spaeter fuer den Galerie-QR-Code)."""
        self.boot_and_go_to_countdown_menu()
        self.transition(EventType.BUTTON_PRESS, now_offset=self.config.timeouts.boot_seconds + 0.4)
        self.transition(EventType.COUNTDOWN_FINISHED, now_offset=self.config.timeouts.boot_seconds + 4.5)
        self.transition(EventType.CAPTURE_OK, now_offset=self.config.timeouts.boot_seconds + 4.6, payload={"photo_path": "/tmp/test.jpg"})
        result = self.transition(EventType.TAP_SAVE, now_offset=self.config.timeouts.boot_seconds + 4.7, payload={"filename": "test.jpg"})
        self.assertNotIn("generate_qr", result.actions)
        self.assertIn("export_photo", result.actions)
        self.assertIn("Galerie", result.model.ui.status_text)

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

    def test_delete_confirm_returns_to_countdown(self) -> None:
        # GEAENDERT (Sprint-11-Nachbesserung): frueher ging es zurueck ins
        # Hauptmenue, jetzt direkt in einen neuen Countdown, damit der Gast
        # ohne Umweg ueber PHOTO_INTRO/PHOTO_PREVIEW ein neues Foto aufnehmen
        # kann. Live-Vorschau muss dabei explizit neu gestartet werden, da
        # sie seit COUNTDOWN->CAPTURE_PENDING nicht mehr lief.
        self.boot_and_go_to_countdown_menu()
        self.transition(EventType.BUTTON_PRESS, now_offset=self.config.timeouts.boot_seconds + 0.4)
        self.transition(EventType.COUNTDOWN_FINISHED, now_offset=self.config.timeouts.boot_seconds + 4.5)
        self.transition(EventType.CAPTURE_OK, now_offset=self.config.timeouts.boot_seconds + 4.6, payload={"photo_path": "/tmp/test.jpg"})
        self.transition(EventType.TAP_DELETE, now_offset=self.config.timeouts.boot_seconds + 4.7)
        result = self.transition(EventType.TAP_CONFIRM_DELETE, now_offset=self.config.timeouts.boot_seconds + 4.8)
        self.assertEqual(result.model.state, AppState.COUNTDOWN)
        self.assertIsNone(result.model.session.current_photo_path)
        self.assertIn("delete_photo", result.actions)
        self.assertIn("start_preview", result.actions)
        self.assertIn("set_led_countdown", result.actions)

    def test_main_menu_idle_timeout_goes_to_attract_gallery(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=self.config.timeouts.main_menu_idle_seconds + 1)
        self.assertEqual(result.model.state, AppState.ATTRACT_GALLERY)

    def test_gallery_grid_idle_timeout_goes_to_main_menu(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        # NEU (Etappe 7): TAP_GALLERY fuehrt bei LEERER Fotoliste jetzt nach
        # GALLERY_EMPTY statt GALLERY_GRID (siehe test_gallery_empty_*
        # unten). Damit dieser Test wirklich GALLERY_GRID prueft (nicht nur
        # zufaellig ueber GALLERY_EMPTY denselben Zielzustand trifft), wird
        # hier ein Foto "eingespeist" - in der echten App erledigt das
        # app_with_hw.py beim Betreten von MAIN_MENU (gallery_service.
        # list_photos()), hier simulieren wir das direkt am Modell.
        self.model = self.model.evolve(session=replace(self.model.session, photos=("test.jpg",)))
        self.transition(EventType.TAP_GALLERY, now_offset=self.config.timeouts.boot_seconds + 0.2)
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=self.config.timeouts.gallery_idle_seconds + 1)
        self.assertEqual(result.model.state, AppState.MAIN_MENU)

    def test_gallery_fullscreen_idle_timeout_goes_back_to_grid(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        # NEU (Etappe 7): siehe Kommentar oben - ohne mindestens ein Foto
        # wuerde TAP_GALLERY nach GALLERY_EMPTY fuehren, das
        # TAP_FULLSCREEN_PHOTO gar nicht kennt (unveraendert liegen
        # bleiben wuerde der Zustand).
        self.model = self.model.evolve(session=replace(self.model.session, photos=("test.jpg",)))
        self.transition(EventType.TAP_GALLERY, now_offset=self.config.timeouts.boot_seconds + 0.2)
        self.transition(EventType.TAP_FULLSCREEN_PHOTO, now_offset=self.config.timeouts.boot_seconds + 0.3, payload={"index": 0})
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=self.config.timeouts.gallery_fullscreen_idle_seconds + 1)
        self.assertEqual(result.model.state, AppState.GALLERY_GRID)

    # -- Foto-QR in der Galerie-Vollansicht (Sprint 11, Feature 4) -----------

    def _go_to_gallery_fullscreen(self, index: int = 0) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.model = self.model.evolve(session=replace(self.model.session, photos=("a.jpg", "b.jpg")))
        self.transition(EventType.TAP_GALLERY, now_offset=self.config.timeouts.boot_seconds + 0.2)
        self.transition(
            EventType.TAP_FULLSCREEN_PHOTO, now_offset=self.config.timeouts.boot_seconds + 0.3,
            payload={"index": index},
        )

    def test_gallery_qr_tap_leads_to_photo_qr_state(self) -> None:
        self._go_to_gallery_fullscreen(index=1)
        result = self.transition(EventType.TAP_GALLERY_QR, now_offset=self.config.timeouts.boot_seconds + 0.4)
        self.assertEqual(result.model.state, AppState.GALLERY_PHOTO_QR)
        self.assertEqual(result.model.ui.selected_gallery_index, 1)
        self.assertIn("generate_gallery_qr", result.actions)
        self.assertIsNotNone(result.model.timers.gallery_qr_deadline)

    def test_gallery_qr_without_photos_is_ignored(self) -> None:
        # NEU (Sprint 11, Feature 4): TAP_GALLERY_QR ohne Fotos darf nicht
        # nach GALLERY_PHOTO_QR fuehren (keine Datei, fuer die ein QR-Code
        # erzeugt werden koennte) - regressionssicher analog zum bereits
        # bestehenden Swipe-Schutz in _handle_gallery_fullscreen.
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.model = self.model.evolve(state=AppState.GALLERY_FULLSCREEN)
        result = self.transition(EventType.TAP_GALLERY_QR, now_offset=self.config.timeouts.boot_seconds + 0.2)
        self.assertEqual(result.model.state, AppState.GALLERY_FULLSCREEN)

    def test_gallery_photo_qr_back_returns_to_fullscreen_with_index(self) -> None:
        self._go_to_gallery_fullscreen(index=1)
        self.transition(EventType.TAP_GALLERY_QR, now_offset=self.config.timeouts.boot_seconds + 0.4)
        result = self.transition(EventType.TAP_BACK, now_offset=self.config.timeouts.boot_seconds + 0.5)
        self.assertEqual(result.model.state, AppState.GALLERY_FULLSCREEN)
        self.assertEqual(result.model.ui.selected_gallery_index, 1)
        self.assertIsNone(result.model.timers.gallery_qr_deadline)
        self.assertIsNotNone(result.model.timers.idle_deadline)

    def test_gallery_photo_qr_auto_closes_after_timeout(self) -> None:
        self._go_to_gallery_fullscreen(index=0)
        self.transition(EventType.TAP_GALLERY_QR, now_offset=self.config.timeouts.boot_seconds + 0.4)
        result = self.transition(
            EventType.GALLERY_QR_TIMEOUT,
            now_offset=self.config.timeouts.boot_seconds + 0.4 + self.config.timeouts.gallery_qr_seconds + 1,
        )
        self.assertEqual(result.model.state, AppState.GALLERY_FULLSCREEN)
        self.assertEqual(result.model.ui.selected_gallery_index, 0)

    # -- Leere Galerie (Etappe 7) --------------------------------------------
    # GALLERY_GRID ohne Fotos zeigte frueher einen technischen Pfad-Hinweis
    # ("Keine Fotos gefunden in: /home/..."). TAP_GALLERY fuehrt bei leerer
    # session.photos jetzt stattdessen nach GALLERY_EMPTY - eigener Zustand
    # mit einladender Nachricht und direktem Weg zum ersten Foto.

    def test_tap_gallery_without_photos_goes_to_gallery_empty(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.assertEqual(self.model.session.photos, ())
        result = self.transition(EventType.TAP_GALLERY, now_offset=self.config.timeouts.boot_seconds + 0.2)
        self.assertEqual(result.model.state, AppState.GALLERY_EMPTY)

    def test_tap_gallery_with_photos_still_goes_to_gallery_grid(self) -> None:
        # Regressionsschutz: die eigentliche GALLERY_GRID-Anzeige darf sich
        # durch Etappe 7 nicht veraendern, sobald Fotos existieren.
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.model = self.model.evolve(session=replace(self.model.session, photos=("a.jpg", "b.jpg")))
        result = self.transition(EventType.TAP_GALLERY, now_offset=self.config.timeouts.boot_seconds + 0.2)
        self.assertEqual(result.model.state, AppState.GALLERY_GRID)

    def test_gallery_empty_tap_photo_leads_to_photo_intro(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.TAP_GALLERY, now_offset=self.config.timeouts.boot_seconds + 0.2)
        result = self.transition(EventType.TAP_PHOTO, now_offset=self.config.timeouts.boot_seconds + 0.3)
        self.assertEqual(result.model.state, AppState.PHOTO_INTRO)

    def test_gallery_empty_button_press_leads_to_photo_intro(self) -> None:
        # Der physische Ausloeser-Taster soll auf GALLERY_EMPTY dasselbe
        # bewirken wie der "Jetzt fotografieren"-Button.
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.TAP_GALLERY, now_offset=self.config.timeouts.boot_seconds + 0.2)
        result = self.transition(EventType.BUTTON_PRESS, now_offset=self.config.timeouts.boot_seconds + 0.3)
        self.assertEqual(result.model.state, AppState.PHOTO_INTRO)

    def test_gallery_empty_back_goes_to_main_menu(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.TAP_GALLERY, now_offset=self.config.timeouts.boot_seconds + 0.2)
        result = self.transition(EventType.TAP_BACK, now_offset=self.config.timeouts.boot_seconds + 0.3)
        self.assertEqual(result.model.state, AppState.MAIN_MENU)

    def test_gallery_empty_idle_timeout_goes_to_main_menu(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.TAP_GALLERY, now_offset=self.config.timeouts.boot_seconds + 0.2)
        result = self.transition(EventType.IDLE_TIMEOUT, now_offset=self.config.timeouts.gallery_idle_seconds + 1)
        self.assertEqual(result.model.state, AppState.MAIN_MENU)

    def test_gallery_empty_has_idle_deadline(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        result = self.transition(EventType.TAP_GALLERY, now_offset=self.config.timeouts.boot_seconds + 0.2)
        self.assertIsNotNone(result.model.timers.idle_deadline)


class StorageAlarmLockTestCase(unittest.TestCase):
    """Speicherplatz-Alarm Stufe 2: keine neue Aufnahme mehr moeglich, weder
    ueber MAIN_MENU noch ueber GALLERY_EMPTY. storage_alarm_level wird in
    der echten App periodisch von app_with_hw.py gesetzt (siehe
    storage_service.assess_storage()) - hier direkt am Modell simuliert."""

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

    def _set_alarm_level(self, level: int) -> None:
        self.model = self.model.evolve(ui=replace(self.model.ui, storage_alarm_level=level))

    def test_tap_photo_blocked_at_critical_level(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self._set_alarm_level(2)
        result = self.transition(EventType.TAP_PHOTO, now_offset=self.config.timeouts.boot_seconds + 0.2)
        self.assertEqual(result.model.state, AppState.MAIN_MENU)

    def test_button_press_blocked_at_critical_level(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self._set_alarm_level(2)
        result = self.transition(EventType.BUTTON_PRESS, now_offset=self.config.timeouts.boot_seconds + 0.2)
        self.assertEqual(result.model.state, AppState.MAIN_MENU)

    def test_tap_photo_still_works_at_warning_level(self) -> None:
        # Stufe 1 (Warnung) sperrt NICHT - nur Stufe 2 (kritisch).
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self._set_alarm_level(1)
        result = self.transition(EventType.TAP_PHOTO, now_offset=self.config.timeouts.boot_seconds + 0.2)
        self.assertEqual(result.model.state, AppState.PHOTO_INTRO)

    def test_gallery_empty_photo_blocked_at_critical_level(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.TAP_GALLERY, now_offset=self.config.timeouts.boot_seconds + 0.2)
        self.assertEqual(self.model.state, AppState.GALLERY_EMPTY)
        self._set_alarm_level(2)
        result = self.transition(EventType.TAP_PHOTO, now_offset=self.config.timeouts.boot_seconds + 0.3)
        self.assertEqual(result.model.state, AppState.GALLERY_EMPTY)

    def test_gallery_empty_button_press_blocked_at_critical_level(self) -> None:
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.TAP_GALLERY, now_offset=self.config.timeouts.boot_seconds + 0.2)
        self._set_alarm_level(2)
        result = self.transition(EventType.BUTTON_PRESS, now_offset=self.config.timeouts.boot_seconds + 0.3)
        self.assertEqual(result.model.state, AppState.GALLERY_EMPTY)

    def test_gallery_empty_back_still_works_at_critical_level(self) -> None:
        # Die Sperre betrifft nur das STARTEN einer neuen Aufnahme - "Zurueck"
        # muss trotzdem jederzeit funktionieren.
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.transition(EventType.TAP_GALLERY, now_offset=self.config.timeouts.boot_seconds + 0.2)
        self._set_alarm_level(2)
        result = self.transition(EventType.TAP_BACK, now_offset=self.config.timeouts.boot_seconds + 0.3)
        self.assertEqual(result.model.state, AppState.MAIN_MENU)

    def test_gallery_tap_still_works_at_critical_level(self) -> None:
        # Die Sperre betrifft nur NEUE Aufnahmen - vorhandene Fotos duerfen
        # weiterhin angesehen werden (loeschen schafft ja sogar Platz).
        self.transition(EventType.TICK, now_offset=self.config.timeouts.boot_seconds + 0.1)
        self.model = self.model.evolve(session=replace(self.model.session, photos=("a.jpg",)))
        self._set_alarm_level(2)
        result = self.transition(EventType.TAP_GALLERY, now_offset=self.config.timeouts.boot_seconds + 0.2)
        self.assertEqual(result.model.state, AppState.GALLERY_GRID)


if __name__ == "__main__":
    unittest.main()
