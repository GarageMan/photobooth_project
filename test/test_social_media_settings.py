"""Tests fuer Sprint 15, Fix 2: Social-Media-Daten im Service-Menue.

Abgedeckt: Plattform-Tabelle und Einlesen (inkl. alter Textform),
Freigabefrage (ab drei Kanaelen "in sozialen Medien"), Pflichtfelder,
Kontaktblock, Zustandsautomat (Seiten, Plattform-Auswahl, Speichern,
Abbrechen, Standardwerte, Export der Bedingungen), Layout (Kachelraster,
Tastatur) sowie Nutzungsbedingungen (Text + Word-Datei).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import social_media_settings as sms
import terms_docx
import terms_text
from admin_usb_export import ExportResult
from config import DEFAULT_CONFIG, needs_event_setup
from events import AppEvent, EventType
from layout import build_layout, button_rects_for_state
from state_machine import StateMachine, social_settings_from_ui
from states import AppState


def _full(**overrides) -> sms.SocialMediaSettings:
    values = dict(
        enabled=True, platforms=("instagram",), company="Holler Hof", contact_name="Ena",
        street="Hofweg 1", city="12345 Musterdorf", email="info@hollerhof.de", phone="",
    )
    values.update(overrides)
    return sms.make_settings(**values)


class PlatformParsingTestCase(unittest.TestCase):
    def test_legacy_text_form(self) -> None:
        self.assertEqual(sms.normalize_platforms("Instagram"), (("instagram",), ""))
        self.assertEqual(
            sms.normalize_platforms("Facebook, Instagram, Mastodon"), (("instagram", "facebook"), "Mastodon"),
        )

    def test_list_form_keeps_fixed_order(self) -> None:
        self.assertEqual(sms.normalize_platforms(["web", "xing", "instagram"]), (("instagram", "xing", "web"), ""))

    def test_twitter_alias(self) -> None:
        self.assertEqual(sms.normalize_platforms(["Twitter"])[0], ("x",))

    def test_event_config_roundtrip(self) -> None:
        settings = _full(platforms=("facebook", "web"), platforms_other="Mastodon", handle="@hof")
        again = sms.settings_from_event_config(sms.to_event_config(settings))
        self.assertEqual(again, settings)

    def test_legacy_contact_is_fallback(self) -> None:
        settings = sms.settings_from_event_config({
            "social_media_enabled": True, "social_media_company": "Holler Hof",
            "social_media_platforms": "Instagram", "social_media_contact": "Hofweg 1\\n12345 Ort",
        })
        self.assertEqual(sms.contact_lines(settings), ["Holler Hof", "Hofweg 1", "12345 Ort"])

    def test_structured_contact_beats_legacy(self) -> None:
        settings = _full(legacy_contact="ALT", phone="0123")
        lines = sms.contact_lines(settings)
        self.assertNotIn("ALT", lines)
        # Nutzer-Feedback: eine Zeile, Kommata, keine Vorsaetze; EN mit Land.
        self.assertEqual(
            sms.contact_line(settings),
            "Holler Hof, Ena, Hofweg 1, 12345 Musterdorf, info@hollerhof.de, 0123",
        )
        self.assertEqual(
            sms.contact_line(settings, "en"),
            "Holler Hof, Ena, Hofweg 1, 12345 Musterdorf, Germany, info@hollerhof.de, 0123",
        )

    def test_operator_table_complete(self) -> None:
        self.assertEqual(
            set(sms.PLATFORM_KEYS),
            {"instagram", "facebook", "tiktok", "twitch", "x", "linkedin", "xing", "reddit", "snapchat", "web"},
        )
        for platform in sms.PLATFORMS:
            if platform.key != "web":
                self.assertTrue(platform.operator_de and platform.operator_en, platform.key)


class ConsentQuestionTestCase(unittest.TestCase):
    def q(self, platforms, other="") -> str:
        return sms.consent_question(_full(platforms=platforms, platforms_other=other))

    def test_one_and_two_named(self) -> None:
        self.assertEqual(self.q(("instagram",)), "Darf Holler Hof dieses Foto auf Instagram veröffentlichen?")
        self.assertEqual(
            self.q(("instagram", "facebook")), "Darf Holler Hof dieses Foto auf Instagram und Facebook veröffentlichen?",
        )

    def test_three_or_more_generic(self) -> None:
        self.assertEqual(
            self.q(("instagram", "facebook", "tiktok")),
            "Darf Holler Hof dieses Foto in sozialen Medien veröffentlichen?",
        )
        self.assertEqual(
            self.q(("instagram", "facebook"), "Mastodon"),
            "Darf Holler Hof dieses Foto in sozialen Medien veröffentlichen?",
        )

    def test_website(self) -> None:
        self.assertEqual(self.q(("web",)), "Darf Holler Hof dieses Foto auf der Webseite veröffentlichen?")
        self.assertEqual(
            self.q(("instagram", "web")),
            "Darf Holler Hof dieses Foto auf Instagram und auf der Webseite veröffentlichen?",
        )
        self.assertEqual(
            self.q(("instagram", "facebook", "web")),
            "Darf Holler Hof dieses Foto in sozialen Medien und auf der Webseite veröffentlichen?",
        )

    def test_person_name_when_no_company(self) -> None:
        settings = _full(company="", contact_name="Ena Muster")
        self.assertTrue(sms.consent_question(settings).startswith("Darf Ena Muster "))


class ValidationTestCase(unittest.TestCase):
    def test_disabled_needs_nothing(self) -> None:
        self.assertEqual(sms.validate(sms.make_settings(enabled=False)), [])

    def test_complete_is_ok(self) -> None:
        self.assertEqual(sms.validate(_full()), [])
        self.assertEqual(sms.validate(_full(email="", phone="0123")), [])

    def test_missing_fields(self) -> None:
        problems = sms.validate(sms.make_settings(enabled=True))
        self.assertEqual(problems, ["mindestens eine Plattform", "Firmen- oder Personenname", "E-Mail oder Telefon"])

    def test_other_platform_counts(self) -> None:
        self.assertEqual(sms.validate(_full(platforms=(), platforms_other="Mastodon")), [])

    def test_invalid_email(self) -> None:
        self.assertEqual(sms.validate(_full(email="info-at-hof")), ["gültige E-Mail-Adresse"])

    def test_needs_event_setup_accepts_person_name(self) -> None:
        base = {"event_title": "Basar", "guest_wifi_password": "geheim123"}
        self.assertFalse(needs_event_setup({**base, "social_media_enabled": True, "social_media_contact_name": "Ena"}))
        self.assertTrue(needs_event_setup({**base, "social_media_enabled": True}))


class EventSettingsFlowTestCase(unittest.TestCase):
    """Zustandsautomat: Seite 2 der Veranstaltungsdaten."""

    def setUp(self) -> None:
        self.machine = StateMachine(DEFAULT_CONFIG)
        self.now = 1000.0
        model = self.machine.initial_model(self.now)
        result = self.machine._go_admin_event_settings(model, self.now)
        self.model = self.send(EventType.ADMIN_EVENT_SETTINGS_READY, model=result.model, payload={
            "title": "Basar", "social_enabled": False, "social_platforms": ("instagram",),
            "social_company": "Alt GmbH",
        }).model

    def send(self, event_type, model=None, payload=None):
        self.now += 0.1
        return self.machine.transition(
            model if model is not None else self.model, AppEvent(event_type, payload=payload or {}, source="test"),
            self.now,
        )

    def step(self, event_type, payload=None):
        result = self.send(event_type, payload=payload)
        self.model = result.model
        return result

    def type_field(self, field: str, text: str) -> None:
        self.step(EventType.TAP_ADMIN_EVENT_FIELD_EDIT, {"field": field})
        self.assertEqual(self.model.state, AppState.ADMIN_EVENT_TEXT_ENTRY)
        for _ in range(len(self.model.ui.admin_event_text_buffer)):
            self.step(EventType.TEXT_ENTRY_BACKSPACE)
        for char in text:
            self.step(EventType.TEXT_ENTRY_CHAR, {"char": char})
        self.step(EventType.TEXT_ENTRY_SUBMIT)

    def test_ready_fills_social_fields_and_snapshot(self) -> None:
        self.assertEqual(self.model.ui.admin_event_social_platforms, ("instagram",))
        self.assertEqual(self.model.ui.admin_event_social_company, "Alt GmbH")
        self.assertEqual(len(self.model.ui.admin_event_entry_social), 11)
        self.assertEqual(self.model.ui.admin_event_page, 0)

    def test_page_toggle_changes_title(self) -> None:
        self.step(EventType.TAP_ADMIN_EVENT_PAGE)
        self.assertEqual(self.model.ui.admin_event_page, 1)
        self.assertIn("Social Media", self.model.ui.status_text)
        self.step(EventType.TAP_ADMIN_EVENT_PAGE)
        self.assertEqual(self.model.ui.admin_event_page, 0)

    def test_platform_screen_requires_enabled(self) -> None:
        self.step(EventType.TAP_ADMIN_EVENT_SOCIAL_PLATFORMS)
        self.assertEqual(self.model.state, AppState.ADMIN_EVENT_SETTINGS)
        self.step(EventType.TAP_ADMIN_EVENT_TOGGLE, {"field": "social"})
        self.step(EventType.TAP_ADMIN_EVENT_SOCIAL_PLATFORMS)
        self.assertEqual(self.model.state, AppState.ADMIN_EVENT_SOCIAL_PLATFORMS)

    def test_platform_multi_select_apply_and_cancel(self) -> None:
        self.step(EventType.TAP_ADMIN_EVENT_TOGGLE, {"field": "social"})
        self.step(EventType.TAP_ADMIN_EVENT_SOCIAL_PLATFORMS)
        self.step(EventType.TAP_ADMIN_EVENT_SOCIAL_PLATFORM_TOGGLE, {"key": "web"})
        self.step(EventType.TAP_ADMIN_EVENT_SOCIAL_PLATFORM_TOGGLE, {"key": "facebook"})
        self.step(EventType.TAP_ADMIN_EVENT_SOCIAL_PLATFORM_TOGGLE, {"key": "instagram"})
        self.assertEqual(self.model.ui.admin_event_social_platforms, ("facebook", "web"))
        self.step(EventType.TAP_ADMIN_EVENT_SOCIAL_PLATFORMS_CANCEL)
        self.assertEqual(self.model.state, AppState.ADMIN_EVENT_SETTINGS)
        self.assertEqual(self.model.ui.admin_event_social_platforms, ("instagram",))
        self.step(EventType.TAP_ADMIN_EVENT_SOCIAL_PLATFORMS)
        self.step(EventType.TAP_ADMIN_EVENT_SOCIAL_PLATFORM_TOGGLE, {"key": "tiktok"})
        self.step(EventType.TAP_ADMIN_EVENT_SOCIAL_PLATFORMS_APPLY)
        self.assertEqual(self.model.ui.admin_event_social_platforms, ("instagram", "tiktok"))

    def test_other_opens_keyboard_and_returns_to_platforms(self) -> None:
        self.step(EventType.TAP_ADMIN_EVENT_TOGGLE, {"field": "social"})
        self.step(EventType.TAP_ADMIN_EVENT_SOCIAL_PLATFORMS)
        self.step(EventType.TAP_ADMIN_EVENT_SOCIAL_PLATFORM_TOGGLE, {"key": "other"})
        self.assertEqual(self.model.state, AppState.ADMIN_EVENT_TEXT_ENTRY)
        for char in "Mastodon":
            self.step(EventType.TEXT_ENTRY_CHAR, {"char": char})
        self.step(EventType.TEXT_ENTRY_SUBMIT)
        self.assertEqual(self.model.state, AppState.ADMIN_EVENT_SOCIAL_PLATFORMS)
        self.assertEqual(self.model.ui.admin_event_social_other, "Mastodon")
        # Leeren ist erlaubt (Freitext wieder abwaehlen).
        self.step(EventType.TAP_ADMIN_EVENT_SOCIAL_PLATFORM_TOGGLE, {"key": "other"})
        for _ in range(8):
            self.step(EventType.TEXT_ENTRY_BACKSPACE)
        self.step(EventType.TEXT_ENTRY_SUBMIT)
        self.assertEqual(self.model.ui.admin_event_social_other, "")

    def test_save_blocked_until_required_fields(self) -> None:
        self.step(EventType.TAP_ADMIN_EVENT_TOGGLE, {"field": "social"})
        result = self.step(EventType.TAP_ADMIN_EVENT_SAVE)
        self.assertEqual(result.actions, ())
        self.assertEqual(self.model.ui.admin_event_page, 1)
        self.assertIn("E-Mail oder Telefon", self.model.ui.error_text)
        self.type_field("social_email", "info@hollerhof.de")
        result = self.step(EventType.TAP_ADMIN_EVENT_SAVE)
        self.assertEqual(result.actions, ("save_event_config",))

    def test_save_without_social_needs_no_contact(self) -> None:
        result = self.step(EventType.TAP_ADMIN_EVENT_SAVE)
        self.assertEqual(result.actions, ("save_event_config",))

    def test_cancel_restores_social_snapshot(self) -> None:
        self.step(EventType.TAP_ADMIN_EVENT_TOGGLE, {"field": "social"})
        self.type_field("social_company", "Neu GmbH")
        self.step(EventType.TAP_BACK)
        self.assertEqual(self.model.state, AppState.ADMIN_MENU)
        self.assertFalse(self.model.ui.admin_event_social_enabled)
        self.assertEqual(self.model.ui.admin_event_social_company, "Alt GmbH")

    def test_defaults_reset_social(self) -> None:
        self.step(EventType.TAP_ADMIN_EVENT_TOGGLE, {"field": "social"})
        self.step(EventType.TAP_ADMIN_EVENT_DEFAULTS)
        self.assertFalse(self.model.ui.admin_event_social_enabled)
        self.assertEqual(self.model.ui.admin_event_social_platforms, ())
        self.assertEqual(self.model.ui.admin_event_social_company, "")

    def test_social_field_can_be_cleared(self) -> None:
        self.type_field("social_company", "")
        self.assertEqual(self.model.ui.admin_event_social_company, "")

    def test_text_entry_returns_to_same_page(self) -> None:
        self.step(EventType.TAP_ADMIN_EVENT_PAGE)
        self.step(EventType.TAP_ADMIN_EVENT_TOGGLE, {"field": "social"})
        self.type_field("social_phone", "+49 123")
        self.assertEqual(self.model.state, AppState.ADMIN_EVENT_SETTINGS)
        self.assertEqual(self.model.ui.admin_event_page, 1)
        self.assertEqual(self.model.ui.admin_event_social_phone, "+49 123")

    def test_settings_from_ui(self) -> None:
        self.step(EventType.TAP_ADMIN_EVENT_TOGGLE, {"field": "social"})
        self.type_field("social_email", "a@b.de")
        settings = social_settings_from_ui(self.model.ui)
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.platforms, ("instagram",))
        self.assertEqual(settings.email, "a@b.de")

    def _saved(self, social: bool):
        ui = replace(self.model.ui, admin_event_save_ok=True, admin_event_social_enabled=social)
        return self.model.evolve(state=AppState.ADMIN_EVENT_SAVED, ui=ui)

    def test_terms_export_flow(self) -> None:
        self.model = self._saved(True)
        result = self.step(EventType.TAP_ADMIN_EVENT_TERMS_EXPORT)
        self.assertEqual(self.model.state, AppState.ADMIN_EVENT_TERMS_EXPORT)
        self.assertEqual(result.actions, ("export_event_terms",))
        self.assertIsNone(self.model.timers.idle_deadline)
        self.step(EventType.ADMIN_EVENT_TERMS_EXPORT_FINISHED, {"ok": True, "lines": ("a/b.docx",)})
        self.assertEqual(self.model.state, AppState.ADMIN_EVENT_TERMS_EXPORT_RESULT)
        self.assertEqual(self.model.ui.admin_event_terms_export_lines, ("a/b.docx",))
        self.step(EventType.TAP_BACK)
        self.assertEqual(self.model.state, AppState.ADMIN_EVENT_SAVED)

    def test_terms_export_ignored_without_social(self) -> None:
        self.model = self._saved(False)
        result = self.step(EventType.TAP_ADMIN_EVENT_TERMS_EXPORT)
        self.assertEqual(self.model.state, AppState.ADMIN_EVENT_SAVED)
        self.assertEqual(result.actions, ())


class LayoutTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.rects = build_layout(1280, 720)
        self.screen = __import__("pygame").Rect(0, 0, 1280, 720)

    def _no_overlap(self, rects) -> None:
        for rect in rects:
            self.assertTrue(self.screen.contains(rect))
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                self.assertFalse(a.colliderect(b))

    def test_both_pages_equal_tiles_without_overlap(self) -> None:
        r = self.rects
        page1 = [
            r.admin_event_title_row, r.admin_event_welcome_text_row, r.admin_event_wifi_ssid_row,
            r.admin_event_wifi_password_row, r.admin_event_prefix_row, r.admin_event_wallpaper_button,
            r.admin_event_qr_toggle, r.admin_event_gallery_toggle, r.admin_event_color_style_button,
            r.admin_event_defaults_button,
        ]
        page2 = [
            r.admin_event_social_toggle, r.admin_event_social_platforms_button, r.admin_event_social_company_row,
            r.admin_event_social_name_row, r.admin_event_social_street_row, r.admin_event_social_city_row,
            r.admin_event_social_email_row, r.admin_event_social_phone_row, r.admin_event_social_handle_row,
            r.admin_event_social_privacy_url_row,
        ]
        for page in (page1, page2):
            self._no_overlap(page + [r.left, r.right, r.admin_event_page_button])
            self.assertEqual({rect.size for rect in page}, {page[0].size})

    def test_platform_tiles(self) -> None:
        tiles = self.rects.admin_event_social_platform_tiles
        self.assertEqual(len(tiles), 11)
        self._no_overlap(list(tiles.values()) + [self.rects.left, self.rects.right])
        mapping = button_rects_for_state(AppState.ADMIN_EVENT_SOCIAL_PLATFORMS, self.rects)
        self.assertIn("admin_event_platform_other", mapping)

    def test_keyboard_has_mail_and_phone_keys(self) -> None:
        keys = self.rects.keyboard_keys
        for char in ("@", "+", "#"):
            self.assertIn(char, keys)
        self._no_overlap(list(keys.values()))

    def test_saved_screen_has_terms_button(self) -> None:
        mapping = button_rects_for_state(AppState.ADMIN_EVENT_SAVED, self.rects)
        self._no_overlap(list(mapping.values()))
        self.assertIn("admin_event_terms_export", mapping)


class TermsTextTestCase(unittest.TestCase):
    def ctx(self, social: sms.SocialMediaSettings) -> terms_text.TermsContext:
        return terms_text.TermsContext(True, True, "Gast", "pw", social)

    @staticmethod
    def flat(paragraphs) -> str:
        return "\n".join(p if isinstance(p, str) else str(p[-1]) for p in paragraphs)

    def test_private_event_has_no_publication_section(self) -> None:
        text = self.flat(terms_text.build_terms_paragraphs(self.ctx(sms.make_settings()), "de"))
        self.assertNotIn("Veröffentlichung", text.split("Verwendungszweck")[0])
        self.assertIn("kommerzielle Nutzung findet nicht statt", text)
        self.assertNotIn("Meta Platforms", text)

    def test_operators_listed_per_platform(self) -> None:
        settings = _full(platforms=("instagram", "tiktok", "xing", "web"), platforms_other="Mastodon",
                         handle="@hof", privacy_url="https://hof.de/ds")
        for lang in ("de", "en"):
            text = self.flat(terms_text.build_terms_paragraphs(self.ctx(settings), lang))
            self.assertIn("Meta Platforms Ireland Limited", text)
            self.assertIn("TikTok Technology Limited", text)
            self.assertIn("New Work SE", text)
            self.assertIn("Mastodon", text)
            self.assertIn("@hof", text)
            self.assertIn("https://hof.de/ds", text)
            self.assertIn("info@hollerhof.de", text)
        bullets = [p for p in terms_text.build_terms_paragraphs(self.ctx(settings), "de")
                   if isinstance(p, tuple) and p[0] == "BUL"]
        self.assertEqual(len(bullets), 6)  # 4 Kanaele + weitere + Profil

    def test_screen_uses_same_text(self) -> None:
        config = replace(DEFAULT_CONFIG, social_media_enabled=True, social_media_company="Holler Hof",
                         social_media_platforms=("facebook",))
        text = self.flat(terms_text.build_terms_paragraphs(terms_text.context_from_config(config), "de"))
        self.assertIn("Facebook: Betreiber Meta Platforms Ireland Limited", text)


class TermsDocxTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ctx = terms_text.TermsContext(True, False, "Gast", "pw", _full(platforms=("instagram", "web")))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_docx_is_valid_zip_with_both_languages(self) -> None:
        target = self.root / "t.docx"
        terms_docx.write_terms_docx(target, self.ctx, "Weihnachtsbasar")
        with zipfile.ZipFile(target) as zf:
            self.assertIn("word/document.xml", zf.namelist())
            self.assertIn("word/numbering.xml", zf.namelist())
            document = zf.read("word/document.xml").decode("utf-8")
            styles = zf.read("word/styles.xml").decode("utf-8")
        self.assertIn("Nutzungsbedingungen zur Fotobox", document)
        self.assertIn("Terms of use for the photo booth", document)
        self.assertIn("Holler Hof", document)
        # Kein Untertitel (Platz), echte Aufzaehlung, Format der Vorlage.
        self.assertNotIn("Weihnachtsbasar", document)
        self.assertIn('<w:numId w:val="1"/>', document)
        self.assertIn('w:top="567"', document)
        self.assertIn('w:fill="DDDDDD"', styles)
        self.assertIn("Calibri", styles)
        self.assertIn("+49 163 8506144", document)
        self.assertFalse((self.root / "t.docx.tmp").exists())

    def test_xml_special_chars_are_escaped(self) -> None:
        ctx = replace(self.ctx, social=_full(company="Müller & Söhne <GmbH>"))
        target = self.root / "t.docx"
        terms_docx.write_terms_docx(target, ctx, "A & B")
        with zipfile.ZipFile(target) as zf:
            document = zf.read("word/document.xml").decode("utf-8")
        self.assertIn("Müller &amp; Söhne &lt;GmbH&gt;", document)

    def test_export_to_stick_uses_event_folder(self) -> None:
        stick = self.root / "stick"
        stick.mkdir()
        ok, lines = terms_docx.export_terms_to_stick(self.ctx, "Weihnachts/Basar", stick, self.root / "work")
        self.assertTrue(ok, lines)
        files = list(stick.rglob("*.docx"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].parent.name, "Weihnachts_Basar")
        self.assertTrue(files[0].name.startswith("Nutzungsbedingungen_"))
        # Zweiter Lauf ersetzt die Datei statt eine Kopie anzulegen.
        ok, _ = terms_docx.export_terms_to_stick(self.ctx, "Weihnachts/Basar", stick, self.root / "work")
        self.assertTrue(ok)
        self.assertEqual(len(list(stick.rglob("*.docx"))), 1)
        self.assertEqual(list(stick.rglob("*.tmp")), [])

    def test_export_never_calls_global_sync(self) -> None:
        # Bugfix 2026-11-15: os.sync() blieb auf dem Pi haengen (CIFS-Mount).
        from unittest import mock
        stick = self.root / "stick"
        stick.mkdir()
        with mock.patch("os.sync", side_effect=AssertionError("os.sync aufgerufen")):
            ok, lines = terms_docx.export_terms_to_stick(self.ctx, "Basar", stick, self.root / "work")
        self.assertTrue(ok, lines)

    def test_export_summary_line(self) -> None:
        result = ExportResult(terms_file="Nutzungsbedingungen_Basar.docx")
        self.assertIn("Nutzungsbedingungen: Nutzungsbedingungen_Basar.docx", result.summary_lines())


if __name__ == "__main__":
    unittest.main()
