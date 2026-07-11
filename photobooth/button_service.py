from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ButtonService:
    debounce_seconds: float = 0.3
    _last_press_at: float = 0.0

    def register_press(self, now: float) -> bool:
        if now - self._last_press_at < self.debounce_seconds:
            return False
        self._last_press_at = now
        return True
