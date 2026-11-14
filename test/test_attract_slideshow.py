"""
test_attract_slideshow.py
==========================
Reine Logik-Tests fuer attract_slideshow.py (Sprint 14 - extrahiert aus
renderer.Renderer._draw_attract_gallery). Wichtigstes Verhalten: dieselbe
(now, photo_count)-Kombination muss IMMER dasselbe Ergebnis liefern
(deterministisch pro Slot) - genau das garantiert, dass die Touch-Display-
Standby-Galerie und die separate HDMI-Beamer-Wiedergabe (hdmi_slideshow.py)
"genau so zufaellig" im selben Muster laufen, wie vom Nutzer gefordert.

    python3 -m pytest test_attract_slideshow.py -v
"""

from __future__ import annotations

import unittest

from attract_slideshow import (
    DEFAULT_FLY_SECONDS,
    DEFAULT_SLOT_SECONDS,
    compute_attract_slot,
    fly_in_offset,
)


class ComputeAttractSlotTestCase(unittest.TestCase):
    def test_same_input_gives_same_output(self) -> None:
        # Kernanforderung: identischer Aufruf (z.B. aus zwei getrennten
        # Prozessen - Touch-Display und Beamer) liefert dasselbe Ergebnis.
        a = compute_attract_slot(123.4, photo_count=7)
        b = compute_attract_slot(123.4, photo_count=7)
        self.assertEqual(a, b)

    def test_slot_changes_every_slot_seconds(self) -> None:
        first = compute_attract_slot(0.0, photo_count=5)
        still_same_slot = compute_attract_slot(DEFAULT_SLOT_SECONDS - 0.01, photo_count=5)
        next_slot = compute_attract_slot(DEFAULT_SLOT_SECONDS, photo_count=5)
        self.assertEqual(first.slot, still_same_slot.slot)
        self.assertEqual(next_slot.slot, first.slot + 1)

    def test_photo_index_cycles_with_photo_count(self) -> None:
        for slot in range(10):
            now = slot * DEFAULT_SLOT_SECONDS
            result = compute_attract_slot(now, photo_count=3)
            self.assertEqual(result.photo_index, slot % 3)

    def test_photo_index_is_zero_when_no_photos(self) -> None:
        result = compute_attract_slot(999.0, photo_count=0)
        self.assertEqual(result.photo_index, 0)

    def test_direction_is_one_of_the_four_known_values(self) -> None:
        result = compute_attract_slot(42.0, photo_count=4)
        self.assertIn(result.direction, ("left", "right", "top", "bottom"))

    def test_eased_reaches_one_after_fly_seconds(self) -> None:
        slot_start = 4 * DEFAULT_SLOT_SECONDS
        during_flight = compute_attract_slot(slot_start + DEFAULT_FLY_SECONDS / 2, photo_count=4)
        after_flight = compute_attract_slot(slot_start + DEFAULT_FLY_SECONDS + 0.5, photo_count=4)
        self.assertLess(during_flight.eased, 1.0)
        self.assertEqual(after_flight.eased, 1.0)

    def test_custom_slot_seconds_and_probability(self) -> None:
        # Randfall ad_probability=1.0: show_ad muss dann immer True sein.
        result = compute_attract_slot(10.0, photo_count=2, slot_seconds=2.0, ad_probability=1.0)
        self.assertTrue(result.show_ad)
        result_never = compute_attract_slot(10.0, photo_count=2, slot_seconds=2.0, ad_probability=0.0)
        self.assertFalse(result_never.show_ad)


class FlyInOffsetTestCase(unittest.TestCase):
    def test_fully_eased_gives_zero_offset(self) -> None:
        for direction in ("left", "right", "top", "bottom"):
            self.assertEqual(fly_in_offset(direction, 1.0, 1280, 720), (0, 0))

    def test_left_starts_off_screen_to_the_left(self) -> None:
        x, y = fly_in_offset("left", 0.0, 1280, 720)
        self.assertEqual((x, y), (-1280, 0))

    def test_right_starts_off_screen_to_the_right(self) -> None:
        x, y = fly_in_offset("right", 0.0, 1280, 720)
        self.assertEqual((x, y), (1280, 0))

    def test_top_starts_off_screen_above(self) -> None:
        x, y = fly_in_offset("top", 0.0, 1280, 720)
        self.assertEqual((x, y), (0, -720))

    def test_bottom_starts_off_screen_below(self) -> None:
        x, y = fly_in_offset("bottom", 0.0, 1280, 720)
        self.assertEqual((x, y), (0, 720))


if __name__ == "__main__":
    unittest.main()
