"""
hw_button_provider.py
=====================
Echter Hardware-Provider für den Auslöse-Taster.

Hardware:
  - Taster mit integrierter LED (5V-LED über Transistor NPN BC547)
  - Taster-Signal: GPIO 15 (BCM), physischer Pin 22
  - Taster-LED-Steuerung: GPIO 16 (BCM), physischer Pin 36 → Transistor-Basis
  - Taster-GND: Pin 20

Schaltung Taster:
  GPIO 15 --[interner PullUp]--> Taster-Pin 1
  GND (Pi)                    --> Taster-Pin 2
  → Drücken zieht GPIO 15 auf LOW (active-low)

Schaltung Taster-LED:
  GPIO 16 --[1 kΩ]--> Basis (B) NPN-Transistor
  Emitter (E)       --> GND Pi
  Kollektor (C)     --> Kathode (-) Taster-LED
  Anode (+)         --> 5V Pi (Pin 2 oder 4)

Installation:
  sudo pip install RPi.GPIO --break-system-packages
  (auf neueren Pi OS: gpiod / lgpio bevorzugt, RPi.GPIO funktioniert aber noch)

Einbindung in app.py:
  if config.features.enable_gpio_button:
      from hw_button_provider import HwButtonProvider
      button_provider = HwButtonProvider(config)
      button_provider.start()
  else:
      button_provider = None
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

try:
    import RPi.GPIO as GPIO
    _HW_AVAILABLE = True
except ImportError:
    _HW_AVAILABLE = False
    GPIO = None  # type: ignore

from button_service import ButtonService


# ------------------------------------------------------------------------------
# Konfigurations-Konstanten
# ------------------------------------------------------------------------------
_BUTTON_PIN     = 15   # BCM-Nummer; Taster-Signal
_BUTTON_LED_PIN = 16   # BCM-Nummer; Transistor-Basis (Taster-LED)
_DEBOUNCE_SEC   = 0.25 # Sekunden; elektrisches Prellen unterdrücken
_POLL_INTERVAL  = 0.02 # Sekunden; Abfrage-Takt im Polling-Modus


@dataclass
class HwButtonProvider:
    """
    Liest den physischen Taster aus und übergibt Ereignisse per Callback.

    Strategie: Interrupt-basiert über RPi.GPIO (add_event_detect),
    mit ButtonService als Software-Debounce-Schicht dahinter.

    Verwendung:
        def on_press():
            app.dispatch(AppEvent(EventType.BUTTON_PRESS, source="hardware"))

        provider = HwButtonProvider()
        provider.on_press_callback = on_press
        provider.start()
        # ... Hauptloop ...
        provider.stop()
    """

    on_press_callback: Callable[[], None] | None = None
    debounce_seconds: float = _DEBOUNCE_SEC
    _button_service: ButtonService = field(init=False)
    _running: bool = field(default=False, init=False)
    _last_led_state: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self._button_service = ButtonService(debounce_seconds=self.debounce_seconds)
        if not _HW_AVAILABLE:
            print("[HwButtonProvider] RPi.GPIO nicht verfügbar - Button deaktiviert.")

    # -- Public API ------------------------------------------------------------

    def start(self) -> None:
        """GPIO initialisieren und Interrupt für den Taster registrieren."""
        if not _HW_AVAILABLE:
            return
        self._running = True
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        # Taster: Pull-Up, fällt bei Druck auf LOW
        GPIO.setup(_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        # Taster-LED: Ausgangs-Pin (LOW = LED aus)
        GPIO.setup(_BUTTON_LED_PIN, GPIO.OUT, initial=GPIO.LOW)

        # Interrupt-Callback beim fallenden Flankenwechsel (Drücken)
        # bouncetime in ms (erste Entprellung auf Hardware-Ebene)
        GPIO.add_event_detect(
            _BUTTON_PIN,
            GPIO.FALLING,
            callback=self._gpio_callback,
            bouncetime=int(_DEBOUNCE_SEC * 1000),
        )
        print(f"[HwButtonProvider] Taster aktiv auf GPIO {_BUTTON_PIN}.")

    def stop(self) -> None:
        """GPIO aufräumen."""
        self._running = False
        if not _HW_AVAILABLE:
            return
        try:
            GPIO.remove_event_detect(_BUTTON_PIN)
            GPIO.cleanup([_BUTTON_PIN, _BUTTON_LED_PIN])
        except Exception as exc:
            print(f"[HwButtonProvider] Fehler beim Cleanup: {exc}")

    def set_led(self, on: bool) -> None:
        """
        Taster-LED ein- oder ausschalten.
        Wird von app.py / LED-Logik aufgerufen – nicht vom GPIO-Interrupt.
        """
        if not _HW_AVAILABLE or on == self._last_led_state:
            return
        GPIO.output(_BUTTON_LED_PIN, GPIO.HIGH if on else GPIO.LOW)
        self._last_led_state = on

    def is_pressed(self) -> bool:
        """Aktuellen Taster-Zustand lesen (für Polling-Fallback)."""
        if not _HW_AVAILABLE:
            return False
        return GPIO.input(_BUTTON_PIN) == GPIO.LOW

    # -- GPIO Interrupt-Callback -----------------------------------------------

    def _gpio_callback(self, channel: int) -> None:
        """
        Wird vom GPIO-Interrupt-Thread aufgerufen (nicht im Pygame-Thread!).
        Deshalb: Nur ButtonService befragen und dann Callback dispatchen.
        Der Callback (app.dispatch) muss thread-sicher sein.
        """
        now = time.monotonic()
        if not self._running:
            return
        # Software-Debounce als zweite Sicherheitsstufe
        if self._button_service.register_press(now):
            if self.on_press_callback is not None:
                try:
                    self.on_press_callback()
                except Exception as exc:
                    print(f"[HwButtonProvider] Fehler im Callback: {exc}")


# ------------------------------------------------------------------------------
# Manuelle Schnell-Tests (direkt auf dem Pi ausführen)
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    press_count = 0

    def on_press() -> None:
        global press_count
        press_count += 1
        print(f"  -> Taster gedrückt! (#{press_count})")

    print("HwButtonProvider Schnelltest - Taster drücken, STRG+C zum Beenden.")
    provider = HwButtonProvider(on_press_callback=on_press)
    provider.start()

    # Taster-LED als Bereitschaftsanzeige ein
    provider.set_led(True)

    try:
        while True:
            time.sleep(0.5)
            # LED blinken als visuelles Feedback
            provider.set_led(press_count % 2 == 0)
    except KeyboardInterrupt:
        print("\nTest beendet.")
    finally:
        provider.set_led(False)
        provider.stop()
