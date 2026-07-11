from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage_service import StorageService


class StorageServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.photo_dir = base / 'photos'
        self.web_dir = base / 'web'
        self.service = StorageService(self.photo_dir, self.web_dir)
        self.service.ensure_directories()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_build_photo_filename_creates_jpg_name(self) -> None:
        name = self.service.build_photo_filename()
        self.assertTrue(name.startswith('photo_'))
        self.assertTrue(name.endswith('.jpg'))

    def test_export_to_web_copies_file(self) -> None:
        source = self.photo_dir / 'test.jpg'
        source.write_bytes(b'img')
        exported = self.service.export_to_web(source)
        self.assertTrue(exported.exists())
        self.assertEqual(exported.read_bytes(), b'img')

    def test_delete_local_photo_removes_file(self) -> None:
        source = self.photo_dir / 'local.jpg'
        source.write_bytes(b'img')
        deleted = self.service.delete_local_photo(source)
        self.assertTrue(deleted)
        self.assertFalse(source.exists())

    def test_delete_web_photo_removes_file(self) -> None:
        target = self.web_dir / 'web.jpg'
        target.write_bytes(b'img')
        deleted = self.service.delete_web_photo('web.jpg')
        self.assertTrue(deleted)
        self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
