from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gallery_service import GalleryService


class GalleryServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.photo_dir = Path(self.temp_dir.name)
        self.service = GalleryService(self.photo_dir, max_thumbnail_cache_items=2, max_fullscreen_cache_items=2)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_list_photos_returns_supported_images(self) -> None:
        (self.photo_dir / 'a.jpg').write_bytes(b'a')
        (self.photo_dir / 'b.png').write_bytes(b'b')
        (self.photo_dir / 'ignore.txt').write_text('x', encoding='utf-8')
        photos = self.service.list_photos()
        self.assertEqual(len(photos), 2)
        self.assertTrue(any(path.endswith('a.jpg') for path in photos))
        self.assertTrue(any(path.endswith('b.png') for path in photos))

    def test_delete_photo_removes_file(self) -> None:
        path = self.photo_dir / 'delete_me.jpg'
        path.write_bytes(b'data')
        deleted = self.service.delete_photo(str(path))
        self.assertTrue(deleted)
        self.assertFalse(path.exists())

    def test_thumbnail_cache_is_bounded(self) -> None:
        self.service.remember_thumbnail('1.jpg', object())
        self.service.remember_thumbnail('2.jpg', object())
        self.service.remember_thumbnail('3.jpg', object())
        self.assertIsNone(self.service.get_thumbnail('1.jpg'))
        self.assertIsNotNone(self.service.get_thumbnail('2.jpg'))
        self.assertIsNotNone(self.service.get_thumbnail('3.jpg'))

    def test_fullscreen_cache_is_bounded(self) -> None:
        self.service.remember_fullscreen('1.jpg', object())
        self.service.remember_fullscreen('2.jpg', object())
        self.service.remember_fullscreen('3.jpg', object())
        self.assertIsNone(self.service.get_fullscreen('1.jpg'))
        self.assertIsNotNone(self.service.get_fullscreen('2.jpg'))
        self.assertIsNotNone(self.service.get_fullscreen('3.jpg'))


if __name__ == '__main__':
    unittest.main()
