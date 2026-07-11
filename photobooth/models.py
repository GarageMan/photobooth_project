from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from events import AppEvent
from states import AppState


@dataclass(slots=True, frozen=True)
class TimerState:
    boot_deadline: float | None = None
    idle_deadline: float | None = None
    preview_warning_deadline: float | None = None
    preview_total_deadline: float | None = None
    # Zeitpunkt, zu dem der Countdown in PHOTO_PREVIEW automatisch startet
    # (ohne dass der Nutzer nochmal antippen muss).
    preview_auto_countdown_deadline: float | None = None
    delete_deadline: float | None = None
    qr_deadline: float | None = None
    attract_switch_deadline: float | None = None
    countdown_deadline: float | None = None
    capture_trigger_deadline: float | None = None


@dataclass(slots=True, frozen=True)
class UiState:
    selected_gallery_index: int | None = None
    gallery_scroll_offset: int = 0
    countdown_value: int | None = None
    status_text: str = ""
    error_text: str | None = None


@dataclass(slots=True, frozen=True)
class SessionState:
    current_photo_path: str | None = None
    qr_filename: str | None = None
    last_saved_photo_path: str | None = None
    photos: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class AppModel:
    state: AppState
    now: float = 0.0
    timers: TimerState = field(default_factory=TimerState)
    ui: UiState = field(default_factory=UiState)
    session: SessionState = field(default_factory=SessionState)
    last_event: AppEvent | None = None

    def evolve(self, **changes: Any) -> "AppModel":
        return replace(self, **changes)


@dataclass(slots=True, frozen=True)
class TransitionResult:
    model: AppModel
    actions: tuple[str, ...] = ()
