"""
test_layout.py
===============
Reine Geometrie-Tests fuer layout.py - kein pygame.display noetig
(pygame.Rect funktioniert auch ohne initialisiertes Fenster).

    python3 -m pytest test_layout.py -v
"""

from __future__ import annotations

import unittest

from layout import build_layout, button_rects_for_state
from states import AppState

WIDTH, HEIGHT = 1280, 720


class PinKeypadWidthTestCase(unittest.TestCase):
    """NEU (Nutzer-Feedback): Ziffern-/Aktionstasten auf 150% Breite,
    "Abbrechen" bewusst unveraendert."""

    def setUp(self) -> None:
        self.rects = build_layout(WIDTH, HEIGHT)

    def test_digit_keys_are_150_percent_of_original_square_width(self) -> None:
        # Urspruengliche (quadratische) Breite: key_h * height / width.
        key_h = 0.135
        original_w = round((key_h * HEIGHT) / WIDTH * WIDTH)
        expected_w = round(original_w * 1.5)
        actual_w = self.rects.pin_keys["1"].width
        # Rundungstoleranz (mehrfaches round() bei der Pixelumrechnung).
        self.assertAlmostEqual(actual_w, expected_w, delta=2)

    def test_all_digit_and_action_keys_share_the_same_wider_width(self) -> None:
        widths = {self.rects.pin_keys[key].width for key in ("1", "2", "3", "backspace", "0", "submit")}
        self.assertEqual(len(widths), 1, "Ziffern-/Aktionstasten sollten alle dieselbe Breite haben.")

    def test_cancel_button_width_is_unchanged(self) -> None:
        # "cancel" nutzt weiterhin button_w (0.28 * width) - unabhaengig von
        # der verbreiterten Ziffernbreite.
        expected_cancel_w = round(0.28 * WIDTH)
        self.assertEqual(self.rects.pin_keys["cancel"].width, expected_cancel_w)

    def test_digit_keys_stay_centered_on_screen(self) -> None:
        # Zeile 1 (Tasten "1","2","3") sollte weiterhin horizontal zentriert sein.
        row = [self.rects.pin_keys[k] for k in ("1", "2", "3")]
        left_edge = min(r.left for r in row)
        right_edge = max(r.right for r in row)
        margin_left = left_edge
        margin_right = WIDTH - right_edge
        self.assertAlmostEqual(margin_left, margin_right, delta=2)

    def test_digit_keys_fit_within_screen_width(self) -> None:
        row = [self.rects.pin_keys[k] for k in ("1", "2", "3")]
        self.assertGreaterEqual(min(r.left for r in row), 0)
        self.assertLessEqual(max(r.right for r in row), WIDTH)


class LanguageToggleTestCase(unittest.TestCase):
    """NEU (Nutzer-Feedback): DE/EN-Umschalter auf ANLEITUNG/BEDINGUNGEN,
    unten rechts (weiter aussen, siehe Nutzer-Feedback), ueberlappungsfrei
    zum jetzt zentrierten "Zurueck"/"Verstanden"-Button."""

    def setUp(self) -> None:
        self.rects = build_layout(WIDTH, HEIGHT)

    def test_language_toggle_does_not_overlap_text_view_back(self) -> None:
        self.assertFalse(self.rects.language_toggle.colliderect(self.rects.text_view_back))

    def test_language_toggle_is_bottom_right(self) -> None:
        # GEAENDERT (Nutzer-Feedback): jetzt rechts aussen statt links,
        # in derselben Zeile wie "Zurueck"/"Verstanden" (das seinerseits
        # jetzt zentriert ist statt rechts auszurichten).
        self.assertGreater(self.rects.language_toggle.left, WIDTH // 2)
        self.assertEqual(self.rects.language_toggle.top, self.rects.text_view_back.top)

    def test_language_toggle_fits_on_screen(self) -> None:
        screen_rect = __import__("pygame").Rect(0, 0, WIDTH, HEIGHT)
        self.assertTrue(screen_rect.contains(self.rects.language_toggle))

    def test_button_rects_for_instructions_includes_language_toggle(self) -> None:
        mapping = button_rects_for_state(AppState.INSTRUCTIONS, self.rects)
        self.assertIn("language_toggle", mapping)
        self.assertEqual(mapping["language_toggle"], self.rects.language_toggle)
        self.assertIn("back", mapping)

    def test_button_rects_for_terms_includes_language_toggle(self) -> None:
        mapping = button_rects_for_state(AppState.TERMS, self.rects)
        self.assertIn("language_toggle", mapping)
        self.assertEqual(mapping["language_toggle"], self.rects.language_toggle)
        self.assertIn("back", mapping)


class TextViewBackCenteredTestCase(unittest.TestCase):
    """NEU (Nutzer-Feedback): "Zurueck"/"Verstanden" auf ANLEITUNG/
    BEDINGUNGEN steht jetzt horizontal zentriert statt rechtsbuendig."""

    def setUp(self) -> None:
        self.rects = build_layout(WIDTH, HEIGHT)

    def test_text_view_back_is_horizontally_centered(self) -> None:
        screen_center_x = WIDTH // 2
        rect_center_x = self.rects.text_view_back.centerx
        self.assertAlmostEqual(rect_center_x, screen_center_x, delta=2)

    def test_text_view_back_fits_on_screen(self) -> None:
        screen_rect = __import__("pygame").Rect(0, 0, WIDTH, HEIGHT)
        self.assertTrue(screen_rect.contains(self.rects.text_view_back))


class AdminEventWelcomeTextRowTestCase(unittest.TestCase):
    """NEU (Nutzer-Feedback): 8. Zeile "Willkommenstext" auf dem
    Veranstaltungsdaten-Screen, zwischen WLAN-Passwort und den Schaltern."""

    def setUp(self) -> None:
        self.rects = build_layout(WIDTH, HEIGHT)

    def test_row_exists_and_is_below_wifi_password_row(self) -> None:
        self.assertGreater(
            self.rects.admin_event_welcome_text_row.top, self.rects.admin_event_wifi_password_row.top,
        )

    def test_row_is_above_qr_toggle_row(self) -> None:
        self.assertLess(
            self.rects.admin_event_welcome_text_row.top, self.rects.admin_event_qr_toggle.top,
        )

    def test_all_admin_event_rows_fit_on_screen_without_overlap(self) -> None:
        rows = [
            self.rects.admin_event_title_row,
            self.rects.admin_event_prefix_row,
            self.rects.admin_event_wifi_ssid_row,
            self.rects.admin_event_wifi_password_row,
            self.rects.admin_event_welcome_text_row,
            self.rects.admin_event_qr_toggle,
            self.rects.admin_event_gallery_toggle,
        ]
        screen_rect = __import__("pygame").Rect(0, 0, WIDTH, HEIGHT)
        for rect in rows:
            self.assertTrue(screen_rect.contains(rect))
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                self.assertFalse(a.colliderect(b))

    def test_button_rects_for_admin_event_settings_includes_welcome_text_row(self) -> None:
        mapping = button_rects_for_state(AppState.ADMIN_EVENT_SETTINGS, self.rects)
        self.assertIn("admin_event_edit_welcome_text", mapping)
        self.assertEqual(mapping["admin_event_edit_welcome_text"], self.rects.admin_event_welcome_text_row)


if __name__ == "__main__":
    unittest.main()
