from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from admin_usb_export import ExportConflict
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
    # --- Verstecktes Herunterfahren (Schritt 3) ---
    # Laeuft die Fehler-Optik (rot/gelb) nach einer falschen PIN: solange
    # now < pin_error_deadline, zeigen _sync_led()/_sync_button_led() den
    # Fehler-Blitz - zustandsgetrieben, analog zur Countdown-Logik.
    pin_error_deadline: float | None = None
    # Ende der Abschieds-Animation. Bei Erreichen loest die App SHUTDOWN_TIMEOUT
    # aus und faehrt den Pi herunter.
    shutdown_goodbye_deadline: float | None = None
    # NEU (4.3): Ende des kurzen Anzeige-Timers in ADMIN_RESTART_PENDING.
    admin_restart_deadline: float | None = None


@dataclass(slots=True, frozen=True)
class UiState:
    selected_gallery_index: int | None = None
    gallery_scroll_offset: int = 0
    countdown_value: int | None = None
    status_text: str = ""
    error_text: str | None = None
    # --- Verstecktes Herunterfahren (Schritt 3) ---
    # Bisher eingegebene PIN-Ziffern im PIN_ENTRY-Screen (fuer die maskierte
    # Anzeige). Wird beim Betreten und beim Verlassen des Screens geleert,
    # damit die getippte PIN nie in anderen Zustaenden liegen bleibt.
    pin_entry: str = ""
    # NEU (4.3): ermittelte Diagnosezeilen fuer AppState.ADMIN_STATUS.
    # Leer, solange die Ermittlung noch laeuft (siehe "collect_admin_status"
    # in app_with_hw.py).
    admin_status_lines: tuple[str, ...] = ()
    # NEU (4.4): Zusammenfassung nach dem Loeschen aller Bilder
    # (AppState.ADMIN_DELETE_DONE). Bewusst getrennt von
    # admin_status_lines, damit die Diagnoseseite und der Loesch-Report
    # sich nicht gegenseitig ueberschreiben.
    admin_delete_lines: tuple[str, ...] = ()
    # --- USB-Export (Etappe 4a) ---
    # Anzuzeigende Zeilen des jeweils aktuellen USB-Bildschirms.
    admin_usb_lines: tuple[str, ...] = ()
    # True, sobald ein Stick erkannt wurde - erst dann ist "Weiter" aktiv.
    admin_usb_device_ready: bool = False
    # True, wenn der Ablauf wegen eines Problems (zu klein / zu wenig
    # Platz) endete: nach dem Entfernen geht es dann zurueck zum
    # Wartebildschirm, damit ein anderer Stick probiert werden kann,
    # statt umstaendlich neu durchs Menue zu muessen.
    admin_usb_can_retry: bool = False
    # NEU (4.7): Problem-Typ-Unterscheidung (nur bei not_enough_free wird
    # "Stick leeren" angeboten; bei too_small hilft Aufraeumen nicht).
    admin_usb_not_enough_free: bool = False
    # NEU (4.7): nach erfolgreichem, verifiziertem Export fuehrt das
    # Entfernen des Sticks zur Loesch-Abfrage statt ins Service-Menue.
    admin_usb_offer_delete: bool = False
    # NEU (4.7): Fortschrittstext des laufenden Exports (wird von
    # app_with_hw direkt aus dem ExportProgress-Objekt gesetzt).
    admin_usb_export_progress: str = ""
    # NEU (4.8): Fuellstand des Fortschrittsbalkens, 0.0 bis 1.0.
    # Deckt BEIDE Phasen ab (Kopieren 0.0-0.5, Pruefen 0.5-1.0), damit
    # der Balken einmal durchlaeuft statt zweimal von vorn zu beginnen.
    admin_usb_progress_fraction: float = 0.0
    # NEU (4.9): Fortschritt des Loeschlaufs - gleiche Mechanik wie beim
    # Export, damit beide Vorgaenge sich gleich anfuehlen.
    admin_delete_progress: str = ""
    admin_delete_fraction: float = 0.0
    # NEU (6b): offene Namenskonflikte des laufenden USB-Exports (Etappe 6a
    # erkennt sie inhaltsbasiert per SHA256). Jeder Eintrag traegt seine
    # eigene Entscheidung (ExportConflict.decision) - eine Aenderung
    # ersetzt den betroffenen Eintrag per dataclasses.replace() und baut
    # daraus ein neues Tupel, das Modell selbst bleibt dabei frozen. Leer,
    # solange kein Konflikt offen ist (Normalfall).
    admin_usb_conflicts: tuple[ExportConflict, ...] = ()
    # NEU (Speicherplatz-Alarm): periodisch von app_with_hw.py aktualisiert
    # (siehe storage_service.assess_storage()), unabhaengig vom aktuellen
    # AppState - 0=unauffaellig, 1=Warnung, 2=kritisch (Aufnahmesperre).
    storage_alarm_level: int = 0
    storage_free_percent: float = 100.0
    storage_estimated_remaining_photos: int = 0


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