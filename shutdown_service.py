"""
shutdown_service.py
===================
Reine Logik fuer das versteckte Herunterfahren der Fotobox (Schritt 2).
Kein pygame-, kein Hardware-Bezug - dadurch offline (WSL/PC) mit pytest
testbar, genau wie state_machine.py.

Zwei voneinander unabhaengige Bausteine:

  1. SecretGestureDetector
     Erkennt die Geheim-Geste "3x kurz, 1x lang, 2x kurz" in einer
     unsichtbaren Ecke des Hauptmenues. Der App-Layer meldet nur die
     Touch-Down-/Touch-Up-Zeitpunkte samt Position; der Detector meldet
     zurueck, wenn die vollstaendige Sequenz erkannt wurde.

  2. PinLockout
     Zaehlt Fehlversuche bei der PIN-Eingabe und verhaengt nach
     max_attempts Fehlversuchen eine Sperre von lockout_seconds. Zaehler
     UND Sperr-Ablauf werden persistent in einer JSON-Datei gehalten,
     damit ein Neustart der App oder des ganzen Pi weder die Sperre noch
     den Fehlversuchs-Zaehler zuruecksetzt (wichtig gegen gezielte
     Umgehungsversuche - Neustart darf kein Reset sein).

Die Fehler-Optik (LED-Ring rot/gelb, Taster-LED-Blitz) wird NICHT hier
ausgegeben, sondern vom App-/LED-Layer anhand des von check()
zurueckgegebenen PinResult ausgeloest (state-derived, Integrationsschritt).
Die konkreten Farb-/Blitz-Parameter stehen in config.ShutdownConfig.
"""

from __future__ import annotations

import hmac
import json
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path


# ---------------------------------------------------------------------------
# Geheim-Geste
# ---------------------------------------------------------------------------

class TapKind(Enum):
    SHORT = auto()
    LONG = auto()


def _normalize_pattern(raw: tuple[str, ...]) -> tuple[TapKind, ...]:
    # Config haelt das Muster menschenlesbar als ("kurz","lang",...), damit
    # config.py nichts aus diesem Service importieren muss. Alles ausser
    # "lang" (case-insensitiv) gilt als kurz.
    return tuple(
        TapKind.LONG if str(item).strip().lower() == "lang" else TapKind.SHORT
        for item in raw
    )


@dataclass
class SecretGestureDetector:
    # Rechteck der unsichtbaren Ecke in absoluten Pixeln (left, top, w, h).
    corner: tuple[int, int, int, int]
    # Ab dieser Haltedauer (Sekunden) gilt ein Tipp als "lang".
    long_press_seconds: float = 0.6
    # Groesste erlaubte Pause zwischen zwei Tipps; danach beginnt die
    # Sequenz von vorn.
    max_gap_seconds: float = 2.0
    # Muster als menschenlesbare Strings, siehe config.ShutdownConfig.
    pattern: tuple[str, ...] = ("kurz", "kurz", "kurz", "lang", "kurz", "kurz")

    def __post_init__(self) -> None:
        self._pattern: tuple[TapKind, ...] = _normalize_pattern(self.pattern)
        self._buffer: deque[TapKind] = deque(maxlen=len(self._pattern))
        self._down_at: float | None = None
        self._down_in_corner: bool = False
        self._last_tap_at: float | None = None

    def _in_corner(self, pos: tuple[int, int]) -> bool:
        x, y = pos
        left, top, w, h = self.corner
        return left <= x < left + w and top <= y < top + h

    def reset(self) -> None:
        self._buffer.clear()
        self._down_at = None
        self._down_in_corner = False
        self._last_tap_at = None

    def on_touch_down(self, pos: tuple[int, int], now: float) -> None:
        # Nur Beruehrungen, die IN der Ecke beginnen, zaehlen als Tipp.
        self._down_in_corner = self._in_corner(pos)
        self._down_at = now if self._down_in_corner else None

    def on_touch_up(self, pos: tuple[int, int], now: float) -> bool:
        """
        Liefert True, wenn mit diesem Loslassen die vollstaendige
        Geheim-Geste erkannt wurde. Beruehrungen ausserhalb der Ecke
        werden ignoriert (und setzen die laufende Sequenz NICHT zurueck).
        """
        if not self._down_in_corner or self._down_at is None:
            self._down_at = None
            self._down_in_corner = False
            return False

        held = now - self._down_at
        self._down_at = None
        self._down_in_corner = False

        # Pause zum vorherigen Tipp zu gross -> Sequenz neu beginnen.
        if self._last_tap_at is not None and (now - self._last_tap_at) > self.max_gap_seconds:
            self._buffer.clear()
        self._last_tap_at = now

        kind = TapKind.LONG if held >= self.long_press_seconds else TapKind.SHORT
        self._buffer.append(kind)

        if len(self._buffer) == len(self._pattern) and tuple(self._buffer) == self._pattern:
            self.reset()
            return True
        return False

    @classmethod
    def from_config(cls, shutdown_config, screen_width: int, screen_height: int) -> "SecretGestureDetector":
        fx, fy, fw, fh = shutdown_config.gesture_corner_fraction
        corner = (
            round(fx * screen_width),
            round(fy * screen_height),
            round(fw * screen_width),
            round(fh * screen_height),
        )
        return cls(
            corner=corner,
            long_press_seconds=shutdown_config.long_press_seconds,
            max_gap_seconds=shutdown_config.gesture_max_gap_seconds,
            pattern=shutdown_config.gesture_pattern,
        )


# ---------------------------------------------------------------------------
# PIN-Pruefung und persistente Sperre
# ---------------------------------------------------------------------------

_PIN_PLACEHOLDER = "BITTE_local_secrets.py_ANLEGEN"


def pin_is_configured(pin: str) -> bool:
    # True, wenn eine echte PIN gesetzt ist (nicht der Platzhalter, nicht
    # leer). Der App-Layer sollte den Shutdown-Ablauf gar nicht erst
    # anbieten, wenn dies False ist.
    return bool(pin) and pin != _PIN_PLACEHOLDER


class PinResult(Enum):
    ACCEPTED = auto()             # PIN korrekt
    REJECTED = auto()             # falsch, aber noch Versuche uebrig
    REJECTED_NOW_LOCKED = auto()  # falsch, Kontingent damit erschoepft -> jetzt gesperrt
    LOCKED = auto()               # Eingabe abgelehnt, weil bereits gesperrt
    NOT_CONFIGURED = auto()       # keine echte PIN hinterlegt (Platzhalter)


@dataclass
class PinLockout:
    lockout_path: Path
    max_attempts: int = 3
    lockout_seconds: int = 30 * 60

    def __post_init__(self) -> None:
        self._failed_attempts = 0
        self._locked_until = 0.0
        self._load()

    # -- Persistenz ---------------------------------------------------------

    def _load(self) -> None:
        try:
            raw = json.loads(self.lockout_path.read_text(encoding="utf-8"))
            self._failed_attempts = int(raw.get("failed_attempts", 0))
            self._locked_until = float(raw.get("locked_until", 0.0))
        except (FileNotFoundError, ValueError, OSError, TypeError):
            # Fehlende oder beschaedigte Datei -> sauberer Startzustand.
            self._failed_attempts = 0
            self._locked_until = 0.0

    def _save(self) -> None:
        self.lockout_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"failed_attempts": self._failed_attempts, "locked_until": self._locked_until}
        self.lockout_path.write_text(json.dumps(data), encoding="utf-8")

    # -- Abfragen -----------------------------------------------------------

    def is_locked(self, now_wall: float | None = None) -> bool:
        now_wall = time.time() if now_wall is None else now_wall
        return now_wall < self._locked_until

    def remaining_seconds(self, now_wall: float | None = None) -> float:
        now_wall = time.time() if now_wall is None else now_wall
        return max(0.0, self._locked_until - now_wall)

    def attempts_left(self) -> int:
        return max(0, self.max_attempts - self._failed_attempts)

    # -- Aktionen -----------------------------------------------------------

    def check(self, entered_pin: str, correct_pin: str, now_wall: float | None = None) -> PinResult:
        now_wall = time.time() if now_wall is None else now_wall

        if not pin_is_configured(correct_pin):
            return PinResult.NOT_CONFIGURED

        if self.is_locked(now_wall):
            return PinResult.LOCKED

        # Konstante-Zeit-Vergleich: die Pruefdauer verraet nichts ueber die
        # PIN (Timing-Seitenkanal - wird gezielt getestet).
        if hmac.compare_digest(str(entered_pin), str(correct_pin)):
            self.register_success()
            return PinResult.ACCEPTED

        self._failed_attempts += 1
        if self._failed_attempts >= self.max_attempts:
            self._locked_until = now_wall + self.lockout_seconds
            self._failed_attempts = 0
            self._save()
            return PinResult.REJECTED_NOW_LOCKED

        self._save()
        return PinResult.REJECTED

    def register_success(self) -> None:
        self._failed_attempts = 0
        self._locked_until = 0.0
        self._save()

    def clear(self) -> None:
        # Manuelles Zuruecksetzen (Wartung/Test): Sperre und Zaehler weg.
        self.register_success()

    @classmethod
    def from_config(cls, shutdown_config) -> "PinLockout":
        return cls(
            lockout_path=shutdown_config.lockout_file,
            max_attempts=shutdown_config.max_pin_attempts,
            lockout_seconds=shutdown_config.lockout_seconds,
        )
