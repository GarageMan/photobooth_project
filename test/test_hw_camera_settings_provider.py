"""
test_hw_camera_settings_provider.py
=====================================
Offline-Tests (pytest) fuer die reinen Hilfsfunktionen in
hw_camera_settings_provider.py, die ohne echte Kamera/gphoto2-Hardware
pruefbar sind. Der Rest des Moduls (read_current/set_*) spricht direkt mit
gphoto2 und wird stattdessen indirekt ueber test_state_machine_admin.py
(mit gefakten Payloads) abgedeckt - siehe dortige Docstrings.

NEU (Sprint-11-Nachbesserung): deckt die auf Lutz' Wunsch eingefuehrte
Einschraenkung der "Aufnahmebetrieb"-Auswahl auf "Single Shot"/"Burst" ab
(die D3300 bietet laut `gphoto2 --get-config` zusaetzlich "Timer", "Quick
Response Remote", "Delayed Remote" und "Quiet Release" an, die im
Fotobox-Betrieb per GPIO-Ausloesung keinen Sinn ergeben)."""

from __future__ import annotations

import unittest

from hw_camera_settings_provider import _filter_drive_choices


class FilterDriveChoicesTestCase(unittest.TestCase):
    def test_filters_to_single_shot_and_burst(self) -> None:
        choices = (
            "Single Shot", "Burst", "Timer", "Quick Response Remote",
            "Delayed Remote", "Quiet Release",
        )
        self.assertEqual(_filter_drive_choices(choices), ("Single Shot", "Burst"))

    def test_preserves_order(self) -> None:
        choices = ("Burst", "Timer", "Single Shot")
        self.assertEqual(_filter_drive_choices(choices), ("Burst", "Single Shot"))

    def test_empty_input_stays_empty(self) -> None:
        self.assertEqual(_filter_drive_choices(()), ())

    def test_falls_back_to_full_list_if_nothing_matches(self) -> None:
        # Defensiv: unbekannte/abweichende Bezeichnungen (z.B. andere
        # Firmware-/libgphoto2-Version) sollen den Aufnahmebetrieb nicht
        # komplett unbedienbar machen.
        choices = ("Continuous Low", "Continuous High", "Self-Timer")
        self.assertEqual(_filter_drive_choices(choices), choices)


if __name__ == "__main__":
    unittest.main()
