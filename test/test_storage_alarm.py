"""
test_storage_alarm.py
========================
Tests fuer assess_storage() - reine Logik, komplett offline. disk_usage_fn
wird durch eine Fake-Funktion ersetzt, damit keine echte, kuenstlich
volllaufende Partition noetig ist.

    python3 -m pytest test_storage_alarm.py -v
"""

from __future__ import annotations

import tempfile
import unittest
from collections import namedtuple
from pathlib import Path

from storage_alarm import (
    DEFAULT_CRITICAL_THRESHOLD_PERCENT,
    DEFAULT_FALLBACK_AVG_PHOTO_SIZE_BYTES,
    DEFAULT_WARN_THRESHOLD_PERCENT,
    assess_storage,
)

_Usage = namedtuple("_Usage", ["total", "used", "free"])


def _fake_disk_usage(total_gb: float, free_gb: float):
    total = round(total_gb * 1024 ** 3)
    free = round(free_gb * 1024 ** 3)
    used = total - free

    def fn(path: str) -> _Usage:
        return _Usage(total=total, used=used, free=free)

    return fn


class AssessStorageTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.photo_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_photo(self, name: str, size_bytes: int) -> str:
        path = self.photo_dir / name
        path.write_bytes(b"X" * size_bytes)
        return str(path)

    # -- Alarmstufen ---------------------------------------------------------

    def test_plenty_of_space_is_level_0(self) -> None:
        status = assess_storage(
            self.photo_dir, [], disk_usage_fn=_fake_disk_usage(total_gb=20, free_gb=15),
        )
        self.assertEqual(status.alarm_level, 0)

    def test_exactly_at_warn_threshold_is_level_1(self) -> None:
        # Schwellwerte sind inklusiv: genau 10% frei zaehlt schon als Warnung.
        status = assess_storage(
            self.photo_dir, [], disk_usage_fn=_fake_disk_usage(total_gb=20, free_gb=2.0),
        )
        self.assertAlmostEqual(status.free_percent, 10.0, places=3)
        self.assertEqual(status.alarm_level, 1)

    def test_just_above_warn_threshold_is_level_0(self) -> None:
        status = assess_storage(
            self.photo_dir, [], disk_usage_fn=_fake_disk_usage(total_gb=20, free_gb=2.1),
        )
        self.assertEqual(status.alarm_level, 0)

    def test_exactly_at_critical_threshold_is_level_2(self) -> None:
        status = assess_storage(
            self.photo_dir, [], disk_usage_fn=_fake_disk_usage(total_gb=20, free_gb=1.0),
        )
        self.assertAlmostEqual(status.free_percent, 5.0, places=3)
        self.assertEqual(status.alarm_level, 2)

    def test_just_below_critical_threshold_is_level_2(self) -> None:
        status = assess_storage(
            self.photo_dir, [], disk_usage_fn=_fake_disk_usage(total_gb=20, free_gb=0.5),
        )
        self.assertEqual(status.alarm_level, 2)

    def test_between_thresholds_is_level_1(self) -> None:
        status = assess_storage(
            self.photo_dir, [], disk_usage_fn=_fake_disk_usage(total_gb=20, free_gb=1.4),
        )
        self.assertEqual(status.alarm_level, 1)

    # -- Durchschnittsgroesse --------------------------------------------------

    def test_no_photos_uses_fallback_average(self) -> None:
        status = assess_storage(
            self.photo_dir, [], disk_usage_fn=_fake_disk_usage(total_gb=20, free_gb=15),
        )
        self.assertTrue(status.average_is_fallback)
        self.assertEqual(status.average_photo_size_bytes, DEFAULT_FALLBACK_AVG_PHOTO_SIZE_BYTES)

    def test_real_photos_override_fallback_average(self) -> None:
        photos = [
            self._make_photo("a.jpg", 10_000_000),
            self._make_photo("b.jpg", 20_000_000),
        ]
        status = assess_storage(
            self.photo_dir, photos, disk_usage_fn=_fake_disk_usage(total_gb=20, free_gb=15),
        )
        self.assertFalse(status.average_is_fallback)
        self.assertEqual(status.average_photo_size_bytes, 15_000_000)

    def test_unreadable_photo_paths_fall_back_to_default(self) -> None:
        # Pfade, die (mehr) nicht existieren - z.B. zwischen list_photos()
        # und der Pruefung geloescht - duerfen nicht abstuerzen, sondern
        # sollen wie "keine Fotos" behandelt werden.
        status = assess_storage(
            self.photo_dir, [str(self.photo_dir / "geloescht.jpg")],
            disk_usage_fn=_fake_disk_usage(total_gb=20, free_gb=15),
        )
        self.assertTrue(status.average_is_fallback)

    def test_custom_fallback_average_is_used(self) -> None:
        status = assess_storage(
            self.photo_dir, [], fallback_avg_photo_size_bytes=25 * 1024 * 1024,
            disk_usage_fn=_fake_disk_usage(total_gb=20, free_gb=15),
        )
        self.assertEqual(status.average_photo_size_bytes, 25 * 1024 * 1024)

    # -- Geschaetzte Rest-Aufnahmen ---------------------------------------------

    def test_estimated_remaining_photos_uses_average(self) -> None:
        photos = [self._make_photo("a.jpg", 10 * 1024 * 1024)]
        # 100 MB frei / 10 MB Durchschnitt = 10 verbleibende Aufnahmen.
        status = assess_storage(
            self.photo_dir, photos,
            disk_usage_fn=_fake_disk_usage(total_gb=1, free_gb=100 / 1024),
        )
        self.assertEqual(status.estimated_remaining_photos, 10)

    def test_estimated_remaining_photos_floors_down(self) -> None:
        photos = [self._make_photo("a.jpg", 10 * 1024 * 1024)]
        free_bytes_gb = (25 * 1024 * 1024) / (1024 ** 3)  # 25 MB frei -> 2 Aufnahmen (nicht 2.5)
        status = assess_storage(
            self.photo_dir, photos, disk_usage_fn=_fake_disk_usage(total_gb=1, free_gb=free_bytes_gb),
        )
        self.assertEqual(status.estimated_remaining_photos, 2)

    # -- Rand- und Schwellwerte -------------------------------------------------

    def test_custom_thresholds_are_respected(self) -> None:
        status = assess_storage(
            self.photo_dir, [], warn_threshold_percent=50.0, critical_threshold_percent=25.0,
            disk_usage_fn=_fake_disk_usage(total_gb=20, free_gb=6),  # 30% frei
        )
        self.assertEqual(status.alarm_level, 1)

    def test_zero_total_does_not_crash(self) -> None:
        # Theoretischer Randfall (z.B. Pfad auf einem Netzlaufwerk mit
        # kaputt gemeldeter Groesse) - darf nicht durch Zero, sondern
        # muss defensiv 0% ergeben.
        status = assess_storage(
            self.photo_dir, [], disk_usage_fn=_fake_disk_usage(total_gb=0, free_gb=0),
        )
        self.assertEqual(status.free_percent, 0.0)
        self.assertEqual(status.alarm_level, 2)

    def test_default_thresholds_match_module_constants(self) -> None:
        self.assertEqual(DEFAULT_WARN_THRESHOLD_PERCENT, 10.0)
        self.assertEqual(DEFAULT_CRITICAL_THRESHOLD_PERCENT, 5.0)


if __name__ == "__main__":
    unittest.main()
