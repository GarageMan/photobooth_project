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

from hw_camera_settings_provider import _filter_drive_choices, format_zoom_label, SW_ZOOM_CHOICES


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


class FormatZoomLabelTestCase(unittest.TestCase):
    """NEU (Sprint 12): Anzeige-Beschriftung fuer den Vollbild-Zoom - siehe
    Modul-Docstring von hw_camera_settings_provider.py (Live-View-Zoom-Lupe,
    PTP 0xD1A3) fuer den Hintergrund. read_zoom()/set_zoom() selbst sprechen
    direkt mit gphoto2 und werden wie read_current()/set_iso() stattdessen
    indirekt ueber test_state_machine_admin.py (gefakte Payloads) abgedeckt."""

    def test_software_fallback_returns_value_unchanged(self) -> None:
        # Software-Zoom-Werte (z.B. "150%") sind bereits fertig lesbar.
        self.assertEqual(format_zoom_label(False, "150%", SW_ZOOM_CHOICES), "150%")

    def test_software_fallback_with_empty_value_uses_first_choice(self) -> None:
        self.assertEqual(format_zoom_label(False, "", ()), SW_ZOOM_CHOICES[0])

    def test_hardware_index_zero_is_kein_zoom(self) -> None:
        choices = ("0", "1", "2", "3", "4", "5")
        self.assertEqual(format_zoom_label(True, "0", choices), "Kein Zoom (Originalansicht)")

    def test_hardware_other_index_shows_stage_out_of_max(self) -> None:
        choices = ("0", "1", "2", "3", "4", "5")
        self.assertEqual(format_zoom_label(True, "3", choices), "Zoomstufe 3 von 5")

    def test_hardware_max_index(self) -> None:
        choices = ("0", "1", "2", "3", "4", "5")
        self.assertEqual(format_zoom_label(True, "5", choices), "Zoomstufe 5 von 5")

    def test_hardware_value_not_in_choices_returns_raw_value(self) -> None:
        # Verteidigung in der Tiefe - sollte die Kamera einen krummen
        # Zwischenwert liefern (nicht in choices enthalten), wird er
        # trotzdem angezeigt statt einen Fehler auszuloesen.
        self.assertEqual(format_zoom_label(True, "9", ("0", "1", "2")), "9")

    def test_hardware_with_empty_choices_returns_raw_value(self) -> None:
        self.assertEqual(format_zoom_label(True, "0", ()), "0")


if __name__ == "__main__":
    unittest.main()
