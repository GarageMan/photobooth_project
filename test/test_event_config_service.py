"""
test_event_config_service.py
=============================
Tests fuer event_config_service.py (Admin-Screen "Veranstaltungsdaten"):
Speichern der Event-Konfiguration und Wallpaper-Import von einem
USB-Stick. Reine Logik, keine Hardware/pygame - Fixtures ueber tempfile,
analog test_admin_usb_service.py/test_admin_usb_export.py.

    python3 -m pytest test/test_event_config_service.py -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from event_config_service import (
    find_wallpaper_on_stick,
    import_wallpaper,
    save_event_config,
)


class SaveEventConfigTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "event_config.json"

    def test_writes_readable_json(self) -> None:
        ok, message = save_event_config(self.path, {"event_title": "Testfest"})
        self.assertTrue(ok)
        self.assertIn("gespeichert", message.lower())
        with open(self.path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["event_title"], "Testfest")

    def test_overwrites_existing_file(self) -> None:
        save_event_config(self.path, {"event_title": "Alt"})
        ok, _ = save_event_config(self.path, {"event_title": "Neu"})
        self.assertTrue(ok)
        with open(self.path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["event_title"], "Neu")

    def test_creates_missing_parent_directory(self) -> None:
        nested = Path(self._tmp.name) / "sub" / "event_config.json"
        ok, _ = save_event_config(nested, {"event_title": "Testfest"})
        self.assertTrue(ok)
        self.assertTrue(nested.exists())

    def test_no_temp_file_left_behind_on_success(self) -> None:
        save_event_config(self.path, {"event_title": "Testfest"})
        leftovers = list(Path(self._tmp.name).glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_unwritable_target_returns_error_without_raising(self) -> None:
        # Ein regulaeres File als Pfadkomponente verhindert mkdir(parents=True)
        # zuverlaessig - unabhaengig von Dateiberechtigungen (die in dieser
        # Umgebung als root ohnehin wirkungslos waeren).
        blocker = Path(self._tmp.name) / "blocker"
        blocker.write_text("x")
        bad_path = blocker / "sub" / "event_config.json"
        ok, message = save_event_config(bad_path, {"event_title": "Testfest"})
        self.assertFalse(ok)
        self.assertTrue(message)


class FindWallpaperOnStickTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.mountpoint = Path(self._tmp.name)

    def test_empty_directory_returns_none(self) -> None:
        self.assertIsNone(find_wallpaper_on_stick(self.mountpoint))

    def test_finds_single_image(self) -> None:
        (self.mountpoint / "wallpaper.png").write_bytes(b"x")
        found = find_wallpaper_on_stick(self.mountpoint)
        self.assertEqual(found, self.mountpoint / "wallpaper.png")

    def test_non_image_files_are_ignored(self) -> None:
        (self.mountpoint / "readme.txt").write_bytes(b"x")
        (self.mountpoint / "notes.pdf").write_bytes(b"x")
        self.assertIsNone(find_wallpaper_on_stick(self.mountpoint))

    def test_multiple_images_pick_alphabetically_first(self) -> None:
        (self.mountpoint / "zzz.jpg").write_bytes(b"x")
        (self.mountpoint / "aaa.png").write_bytes(b"x")
        found = find_wallpaper_on_stick(self.mountpoint)
        self.assertEqual(found, self.mountpoint / "aaa.png")

    def test_subdirectories_are_not_searched(self) -> None:
        sub = self.mountpoint / "Bilder"
        sub.mkdir()
        (sub / "wallpaper.png").write_bytes(b"x")
        self.assertIsNone(find_wallpaper_on_stick(self.mountpoint))

    def test_missing_mountpoint_returns_none(self) -> None:
        missing = self.mountpoint / "nicht_vorhanden"
        self.assertIsNone(find_wallpaper_on_stick(missing))


class ImportWallpaperTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.source_dir = Path(self._tmp.name) / "stick"
        self.source_dir.mkdir()
        self.target_dir = Path(self._tmp.name) / "assets"
        self.target_dir.mkdir()
        self.target = self.target_dir / "hauptmenu_wallpaper.png"

    def test_copies_file_content_verbatim(self) -> None:
        source = self.source_dir / "wallpaper.jpg"
        source.write_bytes(b"BILDDATEN" * 100)
        ok, message = import_wallpaper(source, self.target)
        self.assertTrue(ok)
        self.assertEqual(self.target.read_bytes(), source.read_bytes())

    def test_missing_source_returns_error(self) -> None:
        source = self.source_dir / "fehlt.png"
        ok, message = import_wallpaper(source, self.target)
        self.assertFalse(ok)
        self.assertTrue(message)

    def test_oversized_file_is_rejected(self) -> None:
        source = self.source_dir / "riesig.png"
        with open(source, "wb") as fh:
            fh.truncate(31 * 1024 * 1024)  # sparse Datei - kein echtes 31-MB-Schreiben
        ok, message = import_wallpaper(source, self.target)
        self.assertFalse(ok)
        self.assertIn("gross", message.lower())
        self.assertFalse(self.target.exists())

    def test_no_temp_file_left_behind_on_success(self) -> None:
        source = self.source_dir / "wallpaper.png"
        source.write_bytes(b"x")
        import_wallpaper(source, self.target)
        leftovers = list(self.target_dir.glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_unwritable_target_returns_error_without_raising(self) -> None:
        blocker = Path(self._tmp.name) / "blocker"
        blocker.write_text("x")
        bad_target = blocker / "sub" / "hauptmenu_wallpaper.png"
        source = self.source_dir / "wallpaper.png"
        source.write_bytes(b"x")
        ok, message = import_wallpaper(source, bad_target)
        self.assertFalse(ok)
        self.assertTrue(message)


if __name__ == "__main__":
    unittest.main()
