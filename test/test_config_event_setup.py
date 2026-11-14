"""
test_config_event_setup.py
==========================
BUGFIX (Sprint 14, Nutzer-Feedback 2026-11-14): Der Hinweis
"Standardkonfiguration aktiv" im Hauptmenue blieb trotz gepflegter
Veranstaltungsdaten stehen, sobald der Titel bewusst leer war
("event_title": ""). Geprueft wird config.needs_event_setup().

    python3 -m pytest test/test_config_event_setup.py -v
"""

from __future__ import annotations

import unittest

from config import needs_event_setup

CONFIGURED = {
    "event_title": "Weihnachtsbasar",
    "photo_prefix": "basar_",
    "guest_wifi_password": "geheim123",
    "qr_codes_enabled": True,
}


class NeedsEventSetupTestCase(unittest.TestCase):
    def test_missing_file_needs_setup(self) -> None:
        self.assertTrue(needs_event_setup({}))

    def test_unchanged_example_file_needs_setup(self) -> None:
        example = {
            "event_title": "Fotobox",
            "guest_wifi_password": "BITTE_ANPASSEN",
            "qr_codes_enabled": True,
        }
        self.assertTrue(needs_event_setup(example))

    def test_fully_configured_needs_no_setup(self) -> None:
        self.assertFalse(needs_event_setup(CONFIGURED))

    def test_intentionally_empty_title_counts_as_configured(self) -> None:
        # Der eigentliche Fehler: leerer Titel (Wallpaper traegt Logo/
        # Schriftzug selbst) darf KEINEN Hinweis ausloesen.
        self.assertFalse(needs_event_setup({**CONFIGURED, "event_title": ""}))
        self.assertFalse(needs_event_setup({**CONFIGURED, "event_title": "   "}))

    def test_example_title_still_needs_setup(self) -> None:
        self.assertTrue(needs_event_setup({**CONFIGURED, "event_title": "Fotobox"}))

    def test_placeholder_password_needs_setup_when_qr_enabled(self) -> None:
        self.assertTrue(needs_event_setup({**CONFIGURED, "guest_wifi_password": "BITTE_ANPASSEN"}))
        self.assertTrue(needs_event_setup({**CONFIGURED, "guest_wifi_password": ""}))

    def test_password_irrelevant_without_qr_codes(self) -> None:
        # Ohne QR-Download wird das Gaeste-WLAN nirgends angezeigt.
        config = {**CONFIGURED, "guest_wifi_password": "BITTE_ANPASSEN", "qr_codes_enabled": False}
        self.assertFalse(needs_event_setup(config))


if __name__ == "__main__":
    unittest.main()
