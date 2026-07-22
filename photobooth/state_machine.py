from __future__ import annotations

from dataclasses import replace

from config import AppConfig
from events import AppEvent, EventType
from models import AppModel, SessionState, TimerState, TransitionResult, UiState
from states import AppState
from shutdown_service import PinResult  # NEU (3.2): nur der Ergebnis-Enum, keine Logik/Dateizugriff


# NEU (3.2): Obergrenze fuer die PIN-Eingabe (verhindert unbegrenztes Anwachsen
# des Puffers). Keine Geheimhaltung noetig, daher Modulkonstante statt config.
_MAX_PIN_LENGTH = 12


class StateMachine:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def initial_model(self, now: float) -> AppModel:
        timers = TimerState(boot_deadline=now + self.config.timeouts.boot_seconds)
        ui = UiState(status_text="System startet...")
        return AppModel(state=AppState.BOOT, now=now, timers=timers, ui=ui)

    def transition(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        model = model.evolve(now=now, last_event=event)
        handler_name = f"_handle_{model.state.name.lower()}"
        handler = getattr(self, handler_name, self._handle_fallback)
        return handler(model, event, now)

    def _handle_fallback(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.ERROR_ACKNOWLEDGED:
            return self._go_main_menu(model, now)
        return TransitionResult(model=model)

    def _handle_boot(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {EventType.APP_STARTED, EventType.TICK} and self._deadline_reached(model.timers.boot_deadline, now):
            return self._go_main_menu(model, now)
        return TransitionResult(model=model)

    def _handle_main_menu(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {EventType.TAP_PHOTO, EventType.BUTTON_PRESS}:
            return self._go_photo_intro(model, now)
        if event.type == EventType.TAP_GALLERY:
            return self._go_gallery_grid(model, now)
        if event.type == EventType.TAP_INSTRUCTIONS:
            return self._go_instructions(model, now)
        if event.type == EventType.TAP_TERMS:
            return self._go_terms(model, now)
        if event.type == EventType.SHUTDOWN_GESTURE_DETECTED:  # NEU (3.2)
            return self._go_pin_entry(model, now)
        if event.type == EventType.IDLE_TIMEOUT:
            return self._go_attract_gallery(model, now)
        return TransitionResult(model=model)

    def _handle_instructions(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.TAP_BACK:
            return self._go_main_menu(model, now)
        return TransitionResult(model=model)

    def _handle_terms(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        # Rueckkehr entweder ueber den "Verstanden"-Button (TAP_BACK, gleiche
        # Konvention wie INSTRUCTIONS' "Zurueck") oder automatisch nach
        # Untaetigkeit (IDLE_TIMEOUT, siehe _go_terms/terms_idle_seconds).
        if event.type in {EventType.TAP_BACK, EventType.IDLE_TIMEOUT}:
            return self._go_main_menu(model, now)
        return TransitionResult(model=model)

    def _handle_photo_intro(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.TAP_CANCEL:
            return self._go_main_menu(model, now)
        if event.type in {EventType.TAP_PHOTO, EventType.BUTTON_PRESS}:
            return self._go_preview(model, now)
        if event.type == EventType.IDLE_TIMEOUT:
            return self._go_main_menu(model, now)
        return TransitionResult(model=model)

    def _handle_attract_gallery(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {EventType.TAP_BACK, EventType.BUTTON_PRESS, EventType.TAP_PHOTO, EventType.TAP_GALLERY}:
            return self._go_main_menu(model, now)
        return TransitionResult(model=model)

    def _handle_gallery_grid(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.TAP_BACK:
            return self._go_main_menu(model, now)
        if event.type == EventType.TAP_FULLSCREEN_PHOTO:
            index = int(event.payload.get("index", 0))
            timers = replace(model.timers, idle_deadline=now + self.config.timeouts.gallery_fullscreen_idle_seconds)
            new_ui = replace(model.ui, selected_gallery_index=index)
            return TransitionResult(model=model.evolve(state=AppState.GALLERY_FULLSCREEN, timers=timers, ui=new_ui))
        if event.type in {EventType.TAP_PHOTO, EventType.BUTTON_PRESS}:
            return self._go_photo_intro(model, now)
        if event.type == EventType.IDLE_TIMEOUT:
            return self._go_main_menu(model, now)
        if event.type in {EventType.SWIPE_UP, EventType.SWIPE_DOWN}:
            columns = max(1, self.config.gallery.grid_columns)
            total_rows = max(1, (len(model.session.photos) + columns - 1) // columns)
            current = model.ui.gallery_scroll_offset
            if event.type == EventType.SWIPE_UP:
                new_offset = min(current + 1, max(0, total_rows - 1))
            else:
                new_offset = max(current - 1, 0)
            new_ui = replace(model.ui, gallery_scroll_offset=new_offset)
            return TransitionResult(model=model.evolve(ui=new_ui))
        return TransitionResult(model=model)

    def _handle_gallery_fullscreen(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        index = model.ui.selected_gallery_index or 0
        photo_count = len(model.session.photos)
        if event.type == EventType.TAP_BACK:
            timers = replace(model.timers, idle_deadline=now + self.config.timeouts.gallery_idle_seconds)
            new_ui = replace(model.ui, selected_gallery_index=None)
            return TransitionResult(model=model.evolve(state=AppState.GALLERY_GRID, timers=timers, ui=new_ui))
        if event.type == EventType.IDLE_TIMEOUT:
            timers = replace(model.timers, idle_deadline=now + self.config.timeouts.gallery_idle_seconds)
            new_ui = replace(model.ui, selected_gallery_index=None)
            return TransitionResult(model=model.evolve(state=AppState.GALLERY_GRID, timers=timers, ui=new_ui))
        if event.type == EventType.SWIPE_LEFT and photo_count:
            new_ui = replace(model.ui, selected_gallery_index=min(photo_count - 1, index + 1))
            return TransitionResult(model=model.evolve(ui=new_ui))
        if event.type == EventType.SWIPE_RIGHT and photo_count:
            new_ui = replace(model.ui, selected_gallery_index=max(0, index - 1))
            return TransitionResult(model=model.evolve(ui=new_ui))
        if event.type in {EventType.TAP_PHOTO, EventType.BUTTON_PRESS}:
            return self._go_photo_intro(model, now)
        return TransitionResult(model=model)

    def _handle_photo_preview(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.TAP_CANCEL:
            return self._go_main_menu(model, now)
        if event.type in {EventType.TAP_PHOTO, EventType.BUTTON_PRESS}:
            # TAP_PHOTO kommt entweder von einem manuellen Tastendruck (Taster)
            # oder automatisch vom Timer (preview_auto_countdown_deadline in
            # app_with_hw.py) - beide Faelle sollen gleich behandelt werden.
            return self._go_countdown(model, now)
        if event.type == EventType.IDLE_TIMEOUT:
            return self._go_main_menu(model, now)
        return TransitionResult(model=model)

    def _handle_countdown(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {EventType.TAP_CANCEL, EventType.BUTTON_PRESS}:
            # Bewusst _go_photo_intro statt _go_preview: PHOTO_PREVIEW startet
            # den Countdown nach preview_auto_start_seconds automatisch neu -
            # ein Abbruch wuerde sich dadurch wie ein sofortiger Neustart des
            # Countdowns anfuehlen. PHOTO_INTRO wartet stattdessen wieder auf
            # einen bewussten Tap auf "Countdown starten" oder "Zurück".
            return self._go_photo_intro(model, now)
        if event.type == EventType.COUNTDOWN_FINISHED:
            # Auslösung wird NICHT sofort ausgelöst: Die Taster-LED soll erst
            # kurz schnell rot blinken und dann ausgehen, bevor überhaupt
            # etwas passiert (keine Reflexion in Brillen während der Aufnahme).
            # 0.6s muss zur Blink-Dauer in hw_led_provider.py passen.
            new_ui = replace(model.ui, countdown_value=None, status_text="Foto wird von der Kamera heruntergeladen und verarbeitet...")
            timers = replace(model.timers, capture_trigger_deadline=now + 0.6)
            return TransitionResult(
                model=model.evolve(state=AppState.CAPTURE_PENDING, timers=timers, ui=new_ui),
                actions=("set_led_capture", "stop_preview"),
            )
        return TransitionResult(model=model)

    def _handle_capture_pending(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.CAPTURE_OK:
            path = str(event.payload.get("photo_path", "")) or None
            session = replace(model.session, current_photo_path=path)
            timers = replace(model.timers, idle_deadline=now + self.config.timeouts.review_idle_seconds)
            ui = replace(model.ui, status_text="Möchtest du dieses Foto speichern?")
            return TransitionResult(model=model.evolve(state=AppState.REVIEW, session=session, timers=timers, ui=ui), actions=("set_led_review",))
        if event.type == EventType.CAPTURE_FAILED:
            ui = replace(model.ui, error_text=str(event.payload.get("message", "Aufnahme fehlgeschlagen.")), status_text="Fehler")
            return TransitionResult(model=model.evolve(state=AppState.ERROR_SCREEN, ui=ui), actions=("set_led_error",))
        return TransitionResult(model=model)

    def _handle_review(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.TAP_SAVE:
            filename = event.payload.get("filename") or self._filename_from_path(model.session.current_photo_path)
            session = replace(
                model.session,
                qr_filename=filename,
                last_saved_photo_path=model.session.current_photo_path,
            )
            timers = replace(model.timers, qr_deadline=now + self.config.timeouts.qr_display_seconds)
            ui = replace(model.ui, status_text="")
            return TransitionResult(
                model=model.evolve(state=AppState.QR_DISPLAY, session=session, timers=timers, ui=ui),
                actions=("export_photo", "generate_qr", "set_led_qr", "stop_preview"),
            )
        if event.type == EventType.TAP_DELETE:
            timers = replace(model.timers, delete_deadline=now + self.config.timeouts.delete_confirm_seconds)
            ui = replace(model.ui, status_text="Foto wirklich löschen?")
            return TransitionResult(model=model.evolve(state=AppState.DELETE_CONFIRM, timers=timers, ui=ui), actions=("set_led_delete_confirm",))
        if event.type == EventType.IDLE_TIMEOUT:
            # 180s Untätigkeit im Review: automatisch löschen, ohne Rückfrage
            session = replace(model.session, current_photo_path=None)
            return TransitionResult(
                model=self._main_menu_model(model.evolve(session=session), now),
                actions=("delete_photo", "set_led_main_menu", "stop_preview"),
            )
        return TransitionResult(model=model)

    def _handle_delete_confirm(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.TAP_ABORT_DELETE:
            return TransitionResult(model=model.evolve(state=AppState.REVIEW), actions=("set_led_review",))
        if event.type in {EventType.TAP_CONFIRM_DELETE, EventType.DELETE_TIMEOUT}:
            session = replace(model.session, current_photo_path=None)
            return TransitionResult(
                model=self._main_menu_model(model.evolve(session=session), now),
                actions=("delete_photo", "set_led_main_menu", "stop_preview"),
            )
        return TransitionResult(model=model)

    def _handle_qr_display(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {EventType.TAP_CANCEL, EventType.QR_TIMEOUT}:
            return self._go_photo_intro(model, now)
        return TransitionResult(model=model)

    def _handle_error_screen(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {EventType.ERROR_ACKNOWLEDGED, EventType.TAP_BACK}:
            return self._go_main_menu(model, now)
        return TransitionResult(model=model)

    # NEU (3.2): PIN-Eingabe (Ziffernfeld)
    def _handle_pin_entry(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.PIN_DIGIT:
            digit = str(event.payload.get("digit", ""))
            if digit.isdigit() and len(model.ui.pin_entry) < _MAX_PIN_LENGTH:
                ui = replace(model.ui, pin_entry=model.ui.pin_entry + digit, error_text=None)
                timers = replace(model.timers, idle_deadline=now + self.config.shutdown.pin_entry_idle_seconds)
                return TransitionResult(model=model.evolve(ui=ui, timers=timers))
            return TransitionResult(model=model)
        if event.type == EventType.PIN_BACKSPACE:
            ui = replace(model.ui, pin_entry=model.ui.pin_entry[:-1], error_text=None)
            timers = replace(model.timers, idle_deadline=now + self.config.shutdown.pin_entry_idle_seconds)
            return TransitionResult(model=model.evolve(ui=ui, timers=timers))
        if event.type == EventType.PIN_SUBMIT:
            return self._handle_pin_submit(model, event, now)
        if event.type in {EventType.PIN_ENTRY_CANCEL, EventType.IDLE_TIMEOUT}:
            return self._go_main_menu(model, now)
        return TransitionResult(model=model)

    # NEU (3.2): Auswertung des PIN-Ergebnisses. Die App reicht das PinResult
    # (plus attempts_left / remaining_seconds) im Payload herein - die State
    # Machine fasst die Sperr-Datei bewusst nicht selbst an.
    def _handle_pin_submit(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        result = event.payload.get("pin_result")

        if result == PinResult.ACCEPTED:
            return self._go_shutdown_goodbye(model, now)

        if result == PinResult.NOT_CONFIGURED:
            ui = replace(model.ui, pin_entry="", error_text="Shutdown-PIN ist nicht eingerichtet.")
            return TransitionResult(model=model.evolve(ui=ui))

        # Ab hier: falsche PIN oder Sperre -> Puffer leeren, Fehler-Optik
        # zuenden (pin_error_deadline wird in _sync_led/_sync_button_led gelesen).
        timers = replace(model.timers, pin_error_deadline=now + self.config.shutdown.error_flash_seconds)

        if result == PinResult.REJECTED:
            attempts_left = int(event.payload.get("attempts_left", 0))
            ui = replace(model.ui, pin_entry="", error_text=f"Falsche PIN - noch {attempts_left} Versuch(e).")
            return TransitionResult(model=model.evolve(ui=ui, timers=timers))

        # PinResult.LOCKED oder PinResult.REJECTED_NOW_LOCKED
        remaining = float(event.payload.get("remaining_seconds", 0.0))
        minutes = max(1, int((remaining + 59) // 60))
        ui = replace(model.ui, pin_entry="", error_text=f"Gesperrt - bitte {minutes} Min warten.")
        return TransitionResult(model=model.evolve(ui=ui, timers=timers))

    # NEU (3.2): Abschieds-Animation. Bewusst nicht abbrechbar - der Shutdown
    # wurde per PIN bestaetigt. Das eigentliche Poweroff loest die App bei
    # SHUTDOWN_TIMEOUT ueber die "power_off"-Action aus.
    def _handle_shutdown_goodbye(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.SHUTDOWN_TIMEOUT:
            return TransitionResult(model=model, actions=("power_off",))
        return TransitionResult(model=model)

    def _go_main_menu(self, model: AppModel, now: float) -> TransitionResult:
        return TransitionResult(model=self._main_menu_model(model, now), actions=("stop_preview", "set_led_main_menu"))

    def _go_attract_gallery(self, model: AppModel, now: float) -> TransitionResult:
        timers = replace(model.timers, idle_deadline=None, preview_warning_deadline=None, preview_total_deadline=None)
        ui = replace(model.ui, status_text="", countdown_value=None, selected_gallery_index=None)
        return TransitionResult(model=model.evolve(state=AppState.ATTRACT_GALLERY, timers=timers, ui=ui), actions=("stop_preview", "set_led_attract_gallery"))

    def _go_gallery_grid(self, model: AppModel, now: float) -> TransitionResult:
        timers = replace(
            model.timers,
            idle_deadline=now + self.config.timeouts.gallery_idle_seconds,
            preview_warning_deadline=None,
            preview_total_deadline=None,
        )
        ui = replace(model.ui, selected_gallery_index=None, gallery_scroll_offset=0, status_text="")
        return TransitionResult(model=model.evolve(state=AppState.GALLERY_GRID, timers=timers, ui=ui), actions=("stop_preview", "set_led_gallery"))

    def _go_photo_intro(self, model: AppModel, now: float) -> TransitionResult:
        timers = replace(
            model.timers,
            idle_deadline=now + self.config.timeouts.preview_total_seconds,
            preview_warning_deadline=None,
            preview_total_deadline=None,
        )
        ui = replace(
            model.ui,
            status_text=(
                "Du willst dich fotografieren lassen?\n"
                "Bitte drücke dazu die Taste 'Countdown starten'\n"
                "und stell dich dann auf die Markierung."
            ),
            countdown_value=None,
            error_text=None,
        )
        return TransitionResult(model=model.evolve(state=AppState.PHOTO_INTRO, timers=timers, ui=ui), actions=("stop_preview", "set_led_photo_intro"))

    def _go_instructions(self, model: AppModel, now: float) -> TransitionResult:
        timers = replace(model.timers, idle_deadline=None, preview_warning_deadline=None, preview_total_deadline=None)
        ui = replace(model.ui, status_text="", error_text=None)
        return TransitionResult(model=model.evolve(state=AppState.INSTRUCTIONS, timers=timers, ui=ui), actions=("stop_preview", "set_led_instructions"))

    def _go_terms(self, model: AppModel, now: float) -> TransitionResult:
        timers = replace(
            model.timers,
            idle_deadline=now + self.config.timeouts.terms_idle_seconds,
            preview_warning_deadline=None,
            preview_total_deadline=None,
        )
        ui = replace(model.ui, status_text="", error_text=None)
        return TransitionResult(model=model.evolve(state=AppState.TERMS, timers=timers, ui=ui), actions=("stop_preview", "set_led_terms"))

    def _go_preview(self, model: AppModel, now: float) -> TransitionResult:
        timers = replace(
            model.timers,
            idle_deadline=now + self.config.timeouts.preview_total_seconds,
            preview_warning_deadline=None,
            preview_total_deadline=None,
            # Nach preview_auto_start_seconds startet der Countdown automatisch
            # (siehe app_with_hw.py::_emit_due_timers) - kein Tap mehr noetig.
            preview_auto_countdown_deadline=now + self.config.timeouts.preview_auto_start_seconds,
        )
        ui = replace(model.ui, status_text="Bitte auf die Markierung stellen!", countdown_value=None, error_text=None)
        return TransitionResult(model=model.evolve(state=AppState.PHOTO_PREVIEW, timers=timers, ui=ui), actions=("start_preview", "set_led_preview"))

    def _go_countdown(self, model: AppModel, now: float) -> TransitionResult:
        countdown_start = self.config.timeouts.countdown_seconds[0]
        timers = replace(
            model.timers,
            countdown_deadline=now + 1.0,
            preview_auto_countdown_deadline=None,
        )
        ui = replace(model.ui, countdown_value=countdown_start, status_text="")
        return TransitionResult(model=model.evolve(state=AppState.COUNTDOWN, timers=timers, ui=ui), actions=("set_led_countdown",))

    # NEU (3.2): Wechsel ins Ziffernfeld nach erkannter Geheim-Geste.
    def _go_pin_entry(self, model: AppModel, now: float) -> TransitionResult:
        timers = replace(
            model.timers,
            idle_deadline=now + self.config.shutdown.pin_entry_idle_seconds,
            preview_warning_deadline=None,
            preview_total_deadline=None,
            preview_auto_countdown_deadline=None,
            pin_error_deadline=None,
        )
        ui = replace(model.ui, pin_entry="", status_text="Wartungs-PIN eingeben", error_text=None, countdown_value=None)
        return TransitionResult(model=model.evolve(state=AppState.PIN_ENTRY, timers=timers, ui=ui), actions=("stop_preview",))

    # NEU (3.2): Abschieds-Animation, danach faehrt die App den Pi herunter.
    def _go_shutdown_goodbye(self, model: AppModel, now: float) -> TransitionResult:
        timers = replace(
            model.timers,
            idle_deadline=None,
            pin_error_deadline=None,
            shutdown_goodbye_deadline=now + self.config.shutdown.goodbye_seconds,
        )
        ui = replace(model.ui, pin_entry="", status_text="Auf Wiedersehen!", error_text=None, countdown_value=None)
        return TransitionResult(model=model.evolve(state=AppState.SHUTDOWN_GOODBYE, timers=timers, ui=ui), actions=("stop_preview",))

    def _main_menu_model(self, model: AppModel, now: float) -> AppModel:
        timers = replace(
            model.timers,
            idle_deadline=now + self.config.timeouts.main_menu_idle_seconds,
            preview_warning_deadline=None,
            preview_total_deadline=None,
            preview_auto_countdown_deadline=None,
            delete_deadline=None,
            qr_deadline=None,
            countdown_deadline=None,
            pin_error_deadline=None,           # GEAENDERT (3.2): Shutdown-Deadlines mit aufraeumen
            shutdown_goodbye_deadline=None,    # GEAENDERT (3.2)
        )
        ui = replace(
            model.ui,
            selected_gallery_index=None,
            countdown_value=None,
            status_text="Willkommen an der Fotobox!",
            error_text=None,
            pin_entry="",                       # GEAENDERT (3.2): getippte PIN nie liegen lassen
        )
        return model.evolve(state=AppState.MAIN_MENU, timers=timers, ui=ui)

    @staticmethod
    def _deadline_reached(deadline: float | None, now: float) -> bool:
        return deadline is not None and now >= deadline

    @staticmethod
    def _filename_from_path(path: str | None) -> str | None:
        if not path:
            return None
        return path.rsplit("/", 1)[-1]