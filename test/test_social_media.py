"""
test_social_media.py
====================
NEU (Sprint 15): Social-Media-Freigabe nach "Speichern".

Geprueft werden:
- State Machine: Ablauf REVIEW -> SOCIAL_MEDIA_CONSENT -> QR_DISPLAY,
  "Ja"/"Nein"/Untaetigkeit, Web-Export unabhaengig von der Antwort,
  ausgeschaltet = Verhalten wie bisher
- social_media_service: Kopie mit Praefix, atomar, Mitloeschen
- admin_delete_service: "Alle Bilder loeschen" leert auch social_media
- admin_usb_export: Unterordner social_media auf dem Stick
- config.needs_event_setup: Firmenname Pflicht bei eingeschalteter Freigabe
- layout: Ja/Nein-Tasten vorhanden

    python3 -m pytest test/test_social_media.py -v
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import social_media_service as sms
from admin_delete_service import delete_all_photos
from admin_usb_export import ExportResult, SOCIAL_MEDIA_SUBFOLDER, export_social_media
from config import DEFAULT_CONFIG, needs_event_setup
from events import AppEvent, EventType
from state_machine import StateMachine, social_media_question
from states import AppState


def _config(enabled: bool = True, qr: bool = True):
    return replace(
        DEFAULT_CONFIG,
        social_media_enabled=enabled,
        social_media_company="Holler Hof",
        social_media_platforms="Instagram",
        qr_codes_enabled=qr,
    )


class SocialMediaFlowTestCase(unittest.TestCase):
    def _machine(self, **kwargs):
        config = _config(**kwargs)
        machine = StateMachine(config)
        model = machine.initial_model(1000.0)
        session = replace(model.session, current_photo_path="/tmp/fotobox_20261205_153012.jpg")
        return machine, model.evolve(state=AppState.REVIEW, session=session), config

    def _send(self, machine, model, event_type, now=1001.0, payload=None):
        return machine.transition(model, AppEvent(event_type, payload=payload or {}, source="test"), now)

    def test_save_leads_to_consent_question(self) -> None:
        machine, model, _ = self._machine()
        result = self._send(machine, model, EventType.TAP_SAVE)
        self.assertEqual(result.model.state, AppState.SOCIAL_MEDIA_CONSENT)
        self.assertEqual(result.model.ui.status_text, "Darf Holler Hof dieses Foto auf Instagram veröffentlichen?")
        self.assertEqual(result.model.session.last_saved_photo_path, "/tmp/fotobox_20261205_153012.jpg")

    def test_web_export_happens_before_the_answer(self) -> None:
        # Vorgabe: Foto landet in jedem Fall im Web-Verzeichnis (QR-Download).
        machine, model, _ = self._machine()
        result = self._send(machine, model, EventType.TAP_SAVE)
        self.assertIn("export_photo", result.actions)

    def test_no_web_export_without_qr_codes(self) -> None:
        # Unveraenderte Datenschutz-Regel: ohne QR-Funktion kein Web-Export.
        machine, model, _ = self._machine(qr=False)
        result = self._send(machine, model, EventType.TAP_SAVE)
        self.assertNotIn("export_photo", result.actions)

    def test_yes_releases_photo_and_confirms(self) -> None:
        machine, model, _ = self._machine()
        model = self._send(machine, model, EventType.TAP_SAVE).model
        result = self._send(machine, model, EventType.TAP_SOCIAL_MEDIA_YES, now=1002.0)
        self.assertEqual(result.model.state, AppState.QR_DISPLAY)
        self.assertIn("release_photo_social_media", result.actions)
        self.assertNotIn("export_photo", result.actions)  # schon beim Speichern passiert
        self.assertIsNotNone(result.model.timers.qr_deadline)

    def test_no_does_not_release(self) -> None:
        machine, model, _ = self._machine()
        model = self._send(machine, model, EventType.TAP_SAVE).model
        result = self._send(machine, model, EventType.TAP_SOCIAL_MEDIA_NO, now=1002.0)
        self.assertEqual(result.model.state, AppState.QR_DISPLAY)
        self.assertNotIn("release_photo_social_media", result.actions)

    def test_timeout_counts_as_no(self) -> None:
        machine, model, config = self._machine()
        model = self._send(machine, model, EventType.TAP_SAVE).model
        self.assertAlmostEqual(
            model.timers.idle_deadline, 1001.0 + config.timeouts.social_media_consent_seconds,
        )
        result = self._send(machine, model, EventType.IDLE_TIMEOUT, now=2000.0)
        self.assertEqual(result.model.state, AppState.QR_DISPLAY)
        self.assertNotIn("release_photo_social_media", result.actions)

    def test_physical_button_and_other_events_are_ignored(self) -> None:
        machine, model, _ = self._machine()
        model = self._send(machine, model, EventType.TAP_SAVE).model
        for event_type in (EventType.TAP_SAVE, EventType.TAP_DELETE, EventType.TAP_CANCEL):
            result = self._send(machine, model, event_type, now=1002.0)
            self.assertEqual(result.model.state, AppState.SOCIAL_MEDIA_CONSENT)
            self.assertEqual(result.actions, ())

    def test_disabled_keeps_previous_behaviour(self) -> None:
        machine, model, _ = self._machine(enabled=False)
        result = self._send(machine, model, EventType.TAP_SAVE)
        self.assertEqual(result.model.state, AppState.QR_DISPLAY)
        self.assertEqual(result.actions, ("export_photo", "stop_preview", "set_led_qr"))

    def test_delete_path_never_asks(self) -> None:
        # Die Freigabefrage kommt nur NACH "Speichern" - "Loeschen" fuehrt
        # wie bisher zur Loeschbestaetigung.
        machine, model, _ = self._machine()
        result = self._send(machine, model, EventType.TAP_DELETE)
        self.assertEqual(result.model.state, AppState.DELETE_CONFIRM)

    def test_question_fallbacks(self) -> None:
        config = replace(DEFAULT_CONFIG, social_media_company="", social_media_platforms="")
        self.assertEqual(
            social_media_question(config), "Darf der Veranstalter dieses Foto auf Social Media veröffentlichen?",
        )


class SocialMediaServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.photos = self.root / "photos"
        self.social = self.root / "social_media"
        self.photos.mkdir()
        self.photo = self.photos / "fotobox_20261205_153012.jpg"
        self.photo.write_bytes(b"JPEGDATA")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_release_creates_prefixed_copy(self) -> None:
        ok, message = sms.release_photo(self.photo, self.social)
        self.assertTrue(ok, message)
        copy = self.social / "freigegeben_fotobox_20261205_153012.jpg"
        self.assertEqual(copy.read_bytes(), b"JPEGDATA")
        self.assertTrue(self.photo.exists())  # Original bleibt
        self.assertEqual(list(self.social.glob("*.tmp")), [])

    def test_release_missing_photo_fails_cleanly(self) -> None:
        ok, message = sms.release_photo(self.photos / "fehlt.jpg", self.social)
        self.assertFalse(ok)
        self.assertIn("nicht gefunden", message)

    def test_release_twice_replaces(self) -> None:
        sms.release_photo(self.photo, self.social)
        self.photo.write_bytes(b"NEU")
        sms.release_photo(self.photo, self.social)
        self.assertEqual((self.social / sms.released_name(self.photo.name)).read_bytes(), b"NEU")

    def test_delete_released_copy(self) -> None:
        sms.release_photo(self.photo, self.social)
        self.assertTrue(sms.delete_released_copy(self.photo.name, self.social))
        self.assertFalse(sms.delete_released_copy(self.photo.name, self.social))
        self.assertEqual(sms.list_released(self.social), [])

    def test_list_released_only_prefixed_files(self) -> None:
        sms.release_photo(self.photo, self.social)
        (self.social / "fremd.jpg").write_bytes(b"x")
        names = [p.name for p in sms.list_released(self.social)]
        self.assertEqual(names, ["freigegeben_fotobox_20261205_153012.jpg"])


class SocialMediaDeleteAndExportTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.photos = self.root / "photos"
        self.web = self.root / "web"
        self.social = self.root / "social_media"
        self.logs = self.root / "logs"
        for d in (self.photos, self.web, self.social, self.logs):
            d.mkdir()
        (self.photos / "a.jpg").write_bytes(b"A")
        (self.photos / "b.jpg").write_bytes(b"B")
        (self.web / "a.jpg").write_bytes(b"A")
        (self.social / "freigegeben_a.jpg").write_bytes(b"A")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_delete_all_includes_social_media(self) -> None:
        result = delete_all_photos(
            self.photos, self.web, self.logs, delete_from_camera=False,
            overwrite_before_delete=False, social_media_dir=self.social,
        )
        self.assertEqual(result.deleted_social_media, 1)
        self.assertEqual(list(self.social.iterdir()), [])
        self.assertTrue(any("Social-Media" in line for line in result.summary_lines()))

    def test_delete_all_without_social_dir_unchanged(self) -> None:
        result = delete_all_photos(
            self.photos, self.web, self.logs, delete_from_camera=False, overwrite_before_delete=False,
        )
        self.assertEqual(result.deleted_social_media, 0)
        self.assertTrue((self.social / "freigegeben_a.jpg").exists())
        self.assertFalse(any("Social-Media" in line for line in result.summary_lines()))

    def test_usb_export_creates_subfolder(self) -> None:
        stick = self.root / "stick"
        stick.mkdir()
        result = ExportResult()
        export_social_media(self.social, stick, result, folder_name="Weihnachtsbasar")
        copied = stick / "Weihnachtsbasar" / SOCIAL_MEDIA_SUBFOLDER / "freigegeben_a.jpg"
        self.assertEqual(copied.read_bytes(), b"A")
        self.assertEqual(result.social_copied, 1)
        self.assertTrue(any("social_media" in line for line in result.summary_lines()))

    def test_usb_export_skips_identical_copy(self) -> None:
        stick = self.root / "stick"
        stick.mkdir()
        export_social_media(self.social, stick, ExportResult(), folder_name="Weihnachtsbasar")
        result = ExportResult()
        export_social_media(self.social, stick, result, folder_name="Weihnachtsbasar")
        self.assertEqual(result.social_copied, 0)
        self.assertEqual(result.social_skipped, 1)

    def test_usb_export_without_releases_creates_nothing(self) -> None:
        empty = self.root / "leer"
        empty.mkdir()
        stick = self.root / "stick"
        stick.mkdir()
        result = ExportResult()
        export_social_media(empty, stick, result, folder_name="Weihnachtsbasar")
        self.assertFalse((stick / "Weihnachtsbasar").exists())
        self.assertFalse(any("social_media" in line for line in result.summary_lines()))


class SocialMediaConfigAndLayoutTestCase(unittest.TestCase):
    BASE = {"event_title": "Basar", "guest_wifi_password": "geheim123", "qr_codes_enabled": True}

    def test_enabled_without_company_needs_setup(self) -> None:
        self.assertTrue(needs_event_setup({**self.BASE, "social_media_enabled": True}))

    def test_enabled_with_company_is_configured(self) -> None:
        self.assertFalse(needs_event_setup(
            {**self.BASE, "social_media_enabled": True, "social_media_company": "Holler Hof"},
        ))

    def test_disabled_ignores_missing_company(self) -> None:
        self.assertFalse(needs_event_setup({**self.BASE, "social_media_enabled": False}))

    def test_default_is_disabled(self) -> None:
        self.assertFalse(DEFAULT_CONFIG.social_media_enabled)

    def test_layout_has_yes_and_no(self) -> None:
        from layout import build_layout, button_rects_for_state
        rects = build_layout(DEFAULT_CONFIG.screen.width, DEFAULT_CONFIG.screen.height)
        buttons = button_rects_for_state(AppState.SOCIAL_MEDIA_CONSENT, rects)
        self.assertEqual(set(buttons), {"social_yes", "social_no"})
        self.assertFalse(buttons["social_yes"].colliderect(buttons["social_no"]))


if __name__ == "__main__":
    unittest.main()


class PreservedSettingsTestCase(unittest.TestCase):
    """NEU (Sprint 15): Social-Media-Einstellungen stehen nur in
    event_config.json und duerfen beim Speichern von "Veranstaltungsdaten"
    nicht verloren gehen."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "event_config.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_only_social_media_keys_are_returned(self) -> None:
        import json
        from event_config_service import preserved_settings
        self.path.write_text(json.dumps({
            "event_title": "Basar",
            "social_media_enabled": True,
            "social_media_company": "Holler Hof",
            "social_media_platforms": "Instagram",
        }), encoding="utf-8")
        self.assertEqual(preserved_settings(self.path), {
            "social_media_enabled": True,
            "social_media_company": "Holler Hof",
            "social_media_platforms": "Instagram",
        })

    def test_missing_or_broken_file(self) -> None:
        from event_config_service import preserved_settings
        self.assertEqual(preserved_settings(self.path), {})
        self.path.write_text("{kaputt", encoding="utf-8")
        self.assertEqual(preserved_settings(self.path), {})
        self.path.write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(preserved_settings(self.path), {})
