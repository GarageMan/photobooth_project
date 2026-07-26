"""
test_admin_usb_export.py
=========================
Tests fuer die Export-Logik (Etappe 4b). Alles ohne echten USB-Stick -
Quelle und Ziel sind temporaere Verzeichnisse.

    python3 -m pytest test_admin_usb_export.py -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from admin_usb_export import (
    ExportProgress,
    ExportResult,
    clear_stick,
    export_photos,
    sanitize_folder_name,
)


class ExportPhotosTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.photo_dir = base / "photos"
        self.stick = base / "stick"
        self.photo_dir.mkdir()
        self.stick.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make(self, name: str, size: int = 1024) -> Path:
        path = self.photo_dir / name
        path.write_bytes(b"X" * size)
        return path

    def _export(self, **kwargs):
        params = dict(
            photo_dir=self.photo_dir,
            mountpoint=self.stick,
            excluded_filenames=frozenset({"testbild.png"}),
            progress=ExportProgress(),
            folder_name="Minas Geburtstags-Fotobox",
            verify=True,
        )
        params.update(kwargs)
        return export_photos(**params)

    # -- Grundfunktion -----------------------------------------------------

    def test_copies_all_images(self) -> None:
        self._make("a.jpg")
        self._make("b.png")
        result = self._export()
        self.assertEqual(result.copied, 2)
        self.assertTrue(result.ok)

    def test_folder_is_named_after_the_event(self) -> None:
        self._make("a.jpg")
        result = self._export()
        self.assertEqual(result.target_dir.name, "Minas Geburtstags-Fotobox")
        self.assertTrue((result.target_dir / "a.jpg").exists())

    def test_repeated_export_uses_the_same_folder(self) -> None:
        # Ohne Zeitstempel im Namen landet ein zweiter Lauf im selben
        # Ordner - nur so kann die Uebersprung-Logik ueberhaupt greifen.
        self._make("a.jpg")
        first = self._export()
        second = self._export()
        self.assertEqual(first.target_dir, second.target_dir)
        self.assertEqual(second.skipped, 1)

    def test_excluded_file_is_not_exported(self) -> None:
        self._make("a.jpg")
        self._make("testbild.png")
        result = self._export()
        self.assertEqual(result.copied, 1)
        self.assertFalse((result.target_dir / "testbild.png").exists())

    def test_non_images_are_not_exported(self) -> None:
        self._make("a.jpg")
        self._make("notizen.txt")
        result = self._export()
        self.assertEqual(result.copied, 1)

    def test_empty_directory_exports_nothing(self) -> None:
        result = self._export()
        self.assertEqual(result.copied, 0)
        self.assertTrue(result.ok)

    # -- Verifikation ------------------------------------------------------

    def test_verification_passes_on_good_copy(self) -> None:
        self._make("a.jpg", 5000)
        result = self._export()
        self.assertEqual(result.verified, 1)
        self.assertEqual(result.failed_verify, [])
        self.assertTrue(result.ok)

    def test_verification_catches_corruption(self) -> None:
        self._make("a.jpg", 5000)
        result = self._export()
        # Korruption simulieren: Zieldatei nachtraeglich aendern.
        corrupted = result.target_dir / "a.jpg"
        data = corrupted.read_bytes()
        corrupted.write_bytes(data[:100] + b"\0" * 100 + data[200:])

        # Zweiter Export: Datei hat gleiche Groesse, wird also uebersprungen,
        # aber die Verifikation sieht den Unterschied.
        result2 = self._export()
        self.assertIn("a.jpg", result2.failed_verify)
        self.assertFalse(result2.ok)

    # -- Uebersprung-Logik -------------------------------------------------

    def test_existing_file_with_same_size_is_skipped(self) -> None:
        self._make("a.jpg", 1000)
        r1 = self._export()
        r2 = self._export()
        self.assertEqual(r1.copied, 1)
        self.assertEqual(r2.copied, 0)
        self.assertEqual(r2.skipped, 1)

    def test_partial_reexport_copies_only_missing(self) -> None:
        for i in range(5):
            self._make(f"foto_{i:03d}.jpg")
        r1 = self._export()
        # Zwei Dateien vom Stick loeschen.
        (r1.target_dir / "foto_001.jpg").unlink()
        (r1.target_dir / "foto_003.jpg").unlink()
        r2 = self._export()
        self.assertEqual(r2.copied, 2)
        self.assertEqual(r2.skipped, 3)

    # -- Zusammenfassung ---------------------------------------------------

    def test_summary_mentions_verification(self) -> None:
        self._make("a.jpg")
        result = self._export()
        joined = " ".join(result.summary_lines())
        self.assertIn("SHA256", joined)

    def test_failed_verify_blocks_delete_offer(self) -> None:
        result = ExportResult()
        result.failed_verify.append("bad.jpg")
        joined = " ".join(result.summary_lines())
        self.assertIn("PRÜFSUMMENFEHLER", joined)
        self.assertIn("NICHT", joined)
        self.assertFalse(result.ok)

    def test_summary_mentions_target_folder(self) -> None:
        self._make("a.jpg")
        result = self._export()
        joined = " ".join(result.summary_lines())
        self.assertIn("Minas Geburtstags-Fotobox", joined)

    # -- Fortschritt -------------------------------------------------------

    def test_progress_reaches_done(self) -> None:
        self._make("a.jpg")
        progress = ExportProgress()
        self._export(progress=progress)
        self.assertEqual(progress.phase, "done")
        self.assertEqual(progress.total_files, 1)


class SanitizeFolderNameTestCase(unittest.TestCase):
    """Der Event-Titel ist Freitext aus config.py und kann Zeichen
    enthalten, die FAT/exFAT nicht erlauben."""

    def test_plain_title_is_unchanged(self) -> None:
        self.assertEqual(sanitize_folder_name("Minas Geburtstags-Fotobox"),
                         "Minas Geburtstags-Fotobox")

    def test_umlauts_are_kept(self) -> None:
        self.assertEqual(sanitize_folder_name("Grillfeier Müller"), "Grillfeier Müller")

    def test_forbidden_characters_are_replaced(self) -> None:
        for ch in '<>:"/\\|?*':
            result = sanitize_folder_name(f"Fest{ch}2026")
            self.assertNotIn(ch, result)
            self.assertTrue(result.startswith("Fest"))

    def test_trailing_dot_and_space_are_stripped(self) -> None:
        # Windows kann Ordner mit Punkt/Leerzeichen am Ende nicht oeffnen.
        self.assertEqual(sanitize_folder_name("Sommerfest. "), "Sommerfest")

    def test_empty_title_falls_back(self) -> None:
        self.assertEqual(sanitize_folder_name("   "), "Fotobox")
        self.assertEqual(sanitize_folder_name(""), "Fotobox")

    def test_length_is_capped(self) -> None:
        self.assertLessEqual(len(sanitize_folder_name("x" * 300)), 80)

    def test_multiple_spaces_are_collapsed(self) -> None:
        self.assertEqual(sanitize_folder_name("Fest    2026"), "Fest 2026")


class ClearStickTestCase(unittest.TestCase):
    def test_clears_files_and_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stick = Path(tmp)
            (stick / "datei.txt").write_bytes(b"x")
            (stick / "ordner").mkdir()
            (stick / "ordner" / "inner.txt").write_bytes(b"y")
            deleted, errors = clear_stick(stick)
            self.assertEqual(deleted, 2)
            self.assertEqual(errors, [])
            self.assertEqual(list(stick.iterdir()), [])

    def test_empty_stick_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deleted, errors = clear_stick(Path(tmp))
            self.assertEqual(deleted, 0)
            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
