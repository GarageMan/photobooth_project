from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class EventType(Enum):
    APP_STARTED = auto()
    TICK = auto()
    TAP_PHOTO = auto()
    TAP_GALLERY = auto()
    TAP_INSTRUCTIONS = auto()
    TAP_TERMS = auto()
    TAP_BACK = auto()
    TAP_CANCEL = auto()
    TAP_SAVE = auto()
    TAP_DELETE = auto()
    TAP_CONFIRM_DELETE = auto()
    TAP_ABORT_DELETE = auto()
    TAP_FULLSCREEN_PHOTO = auto()
    BUTTON_PRESS = auto()
    SWIPE_LEFT = auto()
    SWIPE_RIGHT = auto()
    SWIPE_UP = auto()
    SWIPE_DOWN = auto()
    IDLE_TIMEOUT = auto()
    WARNING_TIMEOUT = auto()
    DELETE_TIMEOUT = auto()
    QR_TIMEOUT = auto()
    COUNTDOWN_FINISHED = auto()
    CAPTURE_REQUESTED = auto()
    CAPTURE_OK = auto()
    CAPTURE_FAILED = auto()
    PREVIEW_READY = auto()
    PREVIEW_FAILED = auto()
    ERROR_ACKNOWLEDGED = auto()
    # --- Verstecktes Herunterfahren (Schritt 3) ---
    # Geheim-Geste im Hauptmenue erkannt -> Wechsel nach PIN_ENTRY.
    SHUTDOWN_GESTURE_DETECTED = auto()
    # Ziffernfeld-Eingaben. PIN_DIGIT traegt die getippte Ziffer im payload
    # als {"digit": "0".."9"}; der State-/App-Layer haengt sie an den
    # Eingabepuffer an.
    PIN_DIGIT = auto()
    PIN_BACKSPACE = auto()      # letzte Ziffer loeschen
    PIN_SUBMIT = auto()         # aktuelle Eingabe pruefen (PinLockout.check)
    PIN_ENTRY_CANCEL = auto()   # Eingabe abbrechen -> zurueck ins Hauptmenue
    # Abschieds-Animation (SHUTDOWN_GOODBYE) abgelaufen -> App loest das
    # eigentliche Poweroff aus. Analog zu den uebrigen *_TIMEOUT-Events.
    SHUTDOWN_TIMEOUT = auto()


@dataclass(slots=True, frozen=True)
class AppEvent:
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "app"