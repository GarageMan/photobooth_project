"""
test_admin_delete_service.py
=============================
Tests fuer die Loeschung aller Bilder (Etappe 4.4).

Die Kamera-Loeschung wird hier NICHT angesprochen
(delete_from_camera=False) - sie haengt an echter Hardware und ist so
gebaut, dass sie im Fehlerfall einen Statustext statt einer Ausnahme
liefert. Getestet wird alles, was ohne Hardware pruefbar ist: dass die
richtigen Dateien verschwinden, die falschen NICHT verschwinden, und
dass das Protokoll vollstaendig und lesbar ist.

    python3 -m pytest test_admin_delete_service.py -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from admin_delete_service import DeleteResult, delete_all_photos


class DeleteAllPhotosTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.photo_dir = base / "photos"
        self.web_dir = base / "web"
        self.log_dir = base / "logs"
        self.photo_dir.mkdir()
        self.web_dir.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, **kwargs):
        params = dict(
            photo_dir=self.photo_dir,
            web_dir=self.web_dir,
            log_dir=self.log_dir,
            excluded_filenames=frozenset({"testbild.png"}),
            delete_from_camera=False,
        )
        params.update(kwargs)
        return delete_all_photos(**params)

    def _make(self, directory: Path, name: str, size: int = 1024) -> Path:
        path = directory / name
        path.write_bytes(b"X" * size)
        return path

    # -- Was geloescht wird ------------------------------------------------

    def test_deletes_photos_and_web_copies(self) -> None:
        self._make(self.photo_dir, "foto_a.jpg")
        self._make(self.photo_dir, "foto_b.jpg")
        self._make(self.web_dir, "foto_a.jpg")
        result = self._run()
        self.assertEqual(result.deleted_photos, 2)
        self.assertEqual(result.deleted_web_copies, 1)
        self.assertEqual(list(self.photo_dir.iterdir()), [])
        self.assertEqual(list(self.web_dir.iterdir()), [])

    def test_empty_directories_are_not_an_error(self) -> None:
        result = self._run()
        self.assertEqual(result.deleted_photos, 0)
        self.assertEqual(result.errors, [])

    def test_missing_web_dir_is_not_an_error(self) -> None:
        # data/web/ kann fehlen, wenn noch nie ein QR-Export lief.
        self.web_dir.rmdir()
        self._make(self.photo_dir, "foto_a.jpg")
        result = self._run()
        self.assertEqual(result.deleted_photos, 1)
        self.assertEqual(result.errors, [])

    # -- Was NICHT geloescht wird ------------------------------------------

    def test_excluded_file_survives_in_both_directories(self) -> None:
        self._make(self.photo_dir, "testbild.png")
        self._make(self.web_dir, "testbild.png")
        self._make(self.photo_dir, "foto_a.jpg")
        result = self._run()
        self.assertEqual(result.deleted_photos, 1)
        self.assertTrue((self.photo_dir / "testbild.png").exists())
        self.assertTrue((self.web_dir / "testbild.png").exists())

    def test_excluded_match_is_case_insensitive(self) -> None:
        self._make(self.photo_dir, "TestBild.PNG")
        self._run()
        self.assertTrue((self.photo_dir / "TestBild.PNG").exists())

    def test_non_image_files_are_untouched(self) -> None:
        # z.B. versehentlich abgelegte Notizen - nicht Aufgabe dieser Routine.
        self._make(self.photo_dir, "notizen.txt")
        result = self._run()
        self.assertEqual(result.deleted_photos, 0)
        self.assertTrue((self.photo_dir / "notizen.txt").exists())

    def test_subdirectories_are_untouched(self) -> None:
        (self.photo_dir / "unterordner").mkdir()
        result = self._run()
        self.assertEqual(result.deleted_photos, 0)
        self.assertTrue((self.photo_dir / "unterordner").is_dir())

    # -- Protokoll ---------------------------------------------------------

    def test_report_is_written_and_lists_every_file(self) -> None:
        self._make(self.photo_dir, "foto_a.jpg", size=2048)
        self._make(self.web_dir, "foto_a.jpg", size=512)
        result = self._run()
        self.assertIsNotNone(result.report_path)
        text = result.report_path.read_text(encoding="utf-8")
        self.assertIn("LOESCHPROTOKOLL FOTOBOX", text)
        self.assertIn("foto_a.jpg", text)
        self.assertIn("2048", text)
        self.assertIn("512", text)

    def test_report_mentions_flash_limitation(self) -> None:
        # Der Hinweis auf die Grenzen der Loeschung auf Flash-Speicher ist
        # bewusster Bestandteil des Protokolls und darf nicht wegfallen.
        result = self._run()
        text = result.report_path.read_text(encoding="utf-8")
        self.assertIn("Wear-Leveling", text)

    def test_report_mentions_backups(self) -> None:
        # Ebenso der Hinweis, dass raspiBackup-Sicherungen nicht betroffen sind.
        result = self._run()
        text = result.report_path.read_text(encoding="utf-8")
        self.assertIn("raspiBackup", text)

    def test_log_dir_is_created_if_missing(self) -> None:
        self.assertFalse(self.log_dir.exists())
        result = self._run()
        self.assertTrue(self.log_dir.is_dir())
        self.assertIsNotNone(result.report_path)

    def test_report_is_not_deleted_by_the_run(self) -> None:
        # Zur Sicherheit: das Protokoll liegt in data/logs/ und darf von
        # der Loeschung selbst nie erfasst werden.
        self._make(self.photo_dir, "foto_a.jpg")
        first = self._run()
        second = self._run()
        self.assertTrue(first.report_path.exists())
        self.assertTrue(second.report_path.exists())

    # -- Ueberschreiben ----------------------------------------------------

    def test_overwrite_can_be_disabled(self) -> None:
        self._make(self.photo_dir, "foto_a.jpg")
        result = self._run(overwrite_before_delete=False)
        self.assertEqual(result.deleted_photos, 1)
        self.assertEqual(result.errors, [])

    # -- Zusammenfassung fuer den Bildschirm -------------------------------

    def test_summary_lines_mention_report(self) -> None:
        result = self._run()
        joined = " ".join(result.summary_lines())
        self.assertIn("Löschprotokoll", joined)

    def test_summary_lines_report_camera_honestly(self) -> None:
        # Kamera uebersprungen -> muss auch so dastehen, nicht als Erfolg.
        result = self._run()
        joined = " ".join(result.summary_lines())
        self.assertIn("übersprungen", joined)

    def test_summary_lines_are_short_enough_for_the_screen(self) -> None:
        result = self._run()
        self.assertLessEqual(len(result.summary_lines()), 5)


class DeleteResultTestCase(unittest.TestCase):
    def test_errors_appear_in_summary(self) -> None:
        result = DeleteResult()
        result.errors.append("irgendein Fehler")
        joined = " ".join(result.summary_lines())
        self.assertIn("Fehler", joined)

    def test_clean_result_has_no_error_line(self) -> None:
        result = DeleteResult()
        joined = " ".join(result.summary_lines())
        self.assertNotIn("Fehler aufgetreten", joined)


if __name__ == "__main__":
    unittest.main()
