"""
test_wallpaper_theme_service.py
=================================
Tests fuer wallpaper_theme_service.py (Admin-Screen "Farbstil vorschlagen",
Sprint 13): Farbpaletten-Berechnung aus einem Wallpaper-Bild, WCAG-Kontrast-
Textfarbwahl, Hex-Konvertierung. Reine Logik, keine Hardware/pygame -
Testbilder werden mit Pillow direkt im Speicher/in einem temporaeren
Verzeichnis erzeugt, analog test_event_config_service.py.

    python3 -m pytest test/test_wallpaper_theme_service.py -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from wallpaper_theme_service import (
    CANDIDATE_COUNT,
    best_text_color,
    color_to_hex,
    compute_theme_candidates,
    hex_to_color,
)


def _solid_stripes_image(path: Path, colors: tuple[tuple[int, int, int], ...]) -> None:
    """Erzeugt ein einfaches Testbild aus gleich breiten, senkrechten
    Farbstreifen - genug Groesse (300x200), damit die interne Downscale-
    Logik (_ANALYSIS_MAX_WIDTH) mit einem realistischen Bild getestet wird,
    statt eines trivialen 1x1-Pixels."""
    width, height = 300, 200
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    stripe_w = width // len(colors)
    for x in range(width):
        color = colors[min(x // stripe_w, len(colors) - 1)]
        for y in range(height):
            pixels[x, y] = color
    image.save(path)


class ComputeThemeCandidatesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_multi_color_wallpaper_yields_candidates(self) -> None:
        path = self.dir / "wallpaper.png"
        _solid_stripes_image(path, ((210, 60, 50), (40, 180, 90), (50, 70, 210)))
        result = compute_theme_candidates(path)
        self.assertTrue(result.ok, result.message)
        self.assertGreater(len(result.candidates), 0)
        self.assertLessEqual(len(result.candidates), CANDIDATE_COUNT)

    def test_candidates_have_wcag_safe_text_contrast(self) -> None:
        # WCAG AA fuer normalen Fliesstext verlangt mindestens 4.5:1 -
        # jeder Kandidat muss das fuer sein eigenes Button/Text-Paar
        # einhalten, unabhaengig davon, wie kraeftig/dunkel die
        # Wallpaper-Farbe selbst war.
        path = self.dir / "wallpaper.png"
        _solid_stripes_image(path, ((210, 60, 50), (40, 180, 90), (50, 70, 210)))
        result = compute_theme_candidates(path)
        self.assertTrue(result.ok, result.message)
        for candidate in result.candidates:
            r, g, b = candidate.button_color
            tr, tg, tb = candidate.text_color
            # Direkte WCAG-Kontrastformel nochmal unabhaengig nachgerechnet,
            # statt die interne _contrast_ratio()-Hilfsfunktion zu importieren -
            # so testet dieser Test auch bei einer internen Umbenennung noch
            # das tatsaechlich beobachtbare Verhalten.
            def _lum(c: tuple[int, int, int]) -> float:
                def ch(v: int) -> float:
                    v = v / 255.0
                    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
                return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])

            l1, l2 = _lum(candidate.button_color), _lum(candidate.text_color)
            lighter, darker = max(l1, l2), min(l1, l2)
            contrast = (lighter + 0.05) / (darker + 0.05)
            self.assertGreaterEqual(contrast, 4.5, f"Kandidat {candidate} unterschreitet WCAG AA")

    def test_missing_file_fails_gracefully(self) -> None:
        result = compute_theme_candidates(self.dir / "does_not_exist.png")
        self.assertFalse(result.ok)
        self.assertEqual(result.candidates, ())
        self.assertTrue(result.message)

    def test_grey_only_wallpaper_fails_gracefully(self) -> None:
        path = self.dir / "grey.png"
        Image.new("RGB", (50, 50), (120, 120, 120)).save(path)
        result = compute_theme_candidates(path)
        self.assertFalse(result.ok)
        self.assertTrue(result.message)

    def test_broken_image_file_fails_gracefully(self) -> None:
        path = self.dir / "broken.png"
        path.write_bytes(b"not actually a png")
        result = compute_theme_candidates(path)
        self.assertFalse(result.ok)
        self.assertTrue(result.message)

    def test_never_raises_on_directory_instead_of_file(self) -> None:
        # is_file() ist False fuer ein Verzeichnis - deckt denselben Zweig
        # wie "fehlende Datei" ab, hier aber mit einem tatsaechlich
        # existierenden Pfad anderer Art.
        result = compute_theme_candidates(self.dir)
        self.assertFalse(result.ok)


class BestTextColorTestCase(unittest.TestCase):
    def test_dark_background_gets_white_text(self) -> None:
        self.assertEqual(best_text_color((20, 20, 30)), (255, 255, 255))

    def test_light_background_gets_black_text(self) -> None:
        self.assertEqual(best_text_color((240, 240, 230)), (0, 0, 0))


class HexColorConversionTestCase(unittest.TestCase):
    def test_color_to_hex_format(self) -> None:
        self.assertEqual(color_to_hex((255, 10, 1)), "#ff0a01")
        self.assertEqual(color_to_hex((0, 0, 0)), "#000000")

    def test_hex_to_color_round_trip(self) -> None:
        self.assertEqual(hex_to_color("#ff0a01"), (255, 10, 1))
        # ohne fuehrendes "#" ebenfalls gueltig - robuster gegen manuell
        # editierte event_config.json.
        self.assertEqual(hex_to_color("ff0a01"), (255, 10, 1))

    def test_hex_to_color_rejects_invalid_input(self) -> None:
        for bad in ("", "bad", "#ff00", "#gggggg", "1234567"):
            self.assertIsNone(hex_to_color(bad))


if __name__ == "__main__":
    unittest.main()
