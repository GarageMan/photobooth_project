from __future__ import annotations

import unittest

from button_service import ButtonService


class ButtonServiceTestCase(unittest.TestCase):
    def test_first_press_is_accepted(self) -> None:
        service = ButtonService(debounce_seconds=0.3)
        self.assertTrue(service.register_press(1.0))

    def test_press_inside_debounce_is_rejected(self) -> None:
        service = ButtonService(debounce_seconds=0.3)
        self.assertTrue(service.register_press(1.0))
        self.assertFalse(service.register_press(1.1))

    def test_press_after_debounce_is_accepted(self) -> None:
        service = ButtonService(debounce_seconds=0.3)
        self.assertTrue(service.register_press(1.0))
        self.assertTrue(service.register_press(1.4))


if __name__ == '__main__':
    unittest.main()
