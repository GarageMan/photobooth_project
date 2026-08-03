"""
test_admin_diagnostics.py
==========================
Reine Logik-Tests fuer admin_diagnostics.py (Etappe 4.3). Kamera- und
Netzwerkfunktionen (camera_status_line, ip_address_line) werden hier
bewusst nicht getestet - die haengen von echter Hardware/Umgebung ab und
sind so geschrieben, dass sie im Fehlerfall eine Textzeile statt einer
Ausnahme liefern (siehe Docstrings in admin_diagnostics.py).

    python3 -m pytest test_admin_diagnostics.py -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from admin_diagnostics import (
    collect_status_lines,
    disk_usage_line,
    format_bytes,
    photo_count_line,
    uptime_line,
)


class FormatBytesTestCase(unittest.TestCase):
    def test_bytes(self) -> None:
        self.assertEqual(format_bytes(512), "512.00 B")

    def test_gigabytes(self) -> None:
        self.assertIn("GB", format_bytes(1234567890))

    def test_zero(self) -> None:
        self.assertEqual(format_bytes(0), "0.00 B")


class PhotoCountLineTestCase(unittest.TestCase):
    def test_singular(self) -> None:
        self.assertIn("1 Foto", photo_count_line(1))
        self.assertNotIn("1 Fotos", photo_count_line(1))

    def test_plural(self) -> None:
        self.assertIn("42 Fotos", photo_count_line(42))

    def test_zero_is_plural(self) -> None:
        self.assertIn("0 Fotos", photo_count_line(0))


class UptimeLineTestCase(unittest.TestCase):
    def test_seconds_only(self) -> None:
        self.assertEqual(uptime_line(1000.0, 1005.0), "App läuft seit: 5s")

    def test_minutes(self) -> None:
        self.assertEqual(uptime_line(1000.0, 1065.0), "App läuft seit: 1min 05s")

    def test_hours(self) -> None:
        self.assertEqual(uptime_line(0.0, 3725.0), "App läuft seit: 1h 02min")

    def test_never_negative(self) -> None:
        # Absicherung gegen Uhrensprünge/Aufrufreihenfolge - darf nicht
        # in negative Zahlen rutschen.
        self.assertEqual(uptime_line(1000.0, 999.0), "App läuft seit: 0s")


class DiskUsageLineTestCase(unittest.TestCase):
    def test_existing_path_returns_readable_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            line = disk_usage_line(Path(tmp))
            self.assertIn("Speicherplatz", line)
            self.assertIn("frei von", line)

    def test_nonexistent_path_does_not_raise(self) -> None:
        line = disk_usage_line(Path("/pfad/der/ganz/sicher/nicht/existiert"))
        self.assertIn("Speicherplatz", line)


class CollectStatusLinesTestCase(unittest.TestCase):
    # GEFIXT (Test-Altlast, aufgefallen beim ersten echten Pi-Testlauf nach
    # Sprint 11): collect_status_lines() wurde in einem frueheren Sprint um
    # vier Parameter (web_dir, photo_url_prefix, protected_photo_filenames,
    # protected_web_filenames, siehe admin_diagnostics.py) sowie zwei
    # weitere Zeilen (Foto-Download-Pfad, geschuetzte Dateien) erweitert -
    # dieser Test wurde dabei nie nachgezogen und rief die Funktion seither
    # mit der alten, unvollstaendigen Signatur auf (TypeError). Die
    # Produktivnutzung in app_with_hw.py war davon nicht betroffen (ruft
    # bereits korrekt mit allen sieben Parametern auf).
    def test_returns_seven_lines(self) -> None:
        with tempfile.TemporaryDirectory() as photo_tmp, tempfile.TemporaryDirectory() as web_tmp:
            lines = collect_status_lines(
                photo_dir=Path(photo_tmp),
                web_dir=Path(web_tmp),
                photo_count=7,
                app_start_monotonic=0.0,
                photo_url_prefix="http://192.168.0.10",
                protected_photo_filenames=("beispiel_1.jpg",),
                protected_web_filenames=("testbild.png",),
            )
        self.assertEqual(len(lines), 7)
        self.assertTrue(all(isinstance(line, str) and line for line in lines))


if __name__ == "__main__":
    unittest.main()
