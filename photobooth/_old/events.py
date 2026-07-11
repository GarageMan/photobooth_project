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


@dataclass(slots=True, frozen=True)
class AppEvent:
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "app"
