from __future__ import annotations

from dataclasses import replace

from admin_usb_export import ExportConflict
from config import AppConfig
from events import AppEvent, EventType
from models import AppModel, SessionState, TimerState, TransitionResult, UiState
from states import AppState
from admin_service import PinResult  # NEU (3.2): nur der Ergebnis-Enum, keine Logik/Dateizugriff - umbenannt (Sprint 11, vormals shutdown_service.py)


# NEU (3.2): Obergrenze fuer die PIN-Eingabe (verhindert unbegrenztes Anwachsen
# des Puffers). Keine Geheimhaltung noetig, daher Modulkonstante statt config.
_MAX_PIN_LENGTH = 12

# NEU (Sprint 11, Feature 3): Ersetzt die bisherige sofortige QR-Code-Anzeige
# nach dem Speichern (AppState.QR_DISPLAY zeigt seit diesem Umbau KEIN Bild
# mehr, nur noch diesen Hinweistext - siehe renderer.py). Gruende: Gaeste
# haben beim 150-Jahre-Event durchgaengig nicht verstanden, dass (a) dieser
# QR-Code nur fuer das eine gerade aufgenommene Bild gilt, (b) man sich davor
# ins Gaeste-WLAN einloggen muss, und (c) viele waren zu langsam zum Scannen,
# bevor der Screen weiterschaltete. Der QR-Code je Einzelbild ist jetzt
# stattdessen jederzeit aus der Galerie heraus abrufbar (siehe Feature 4,
# AppState.GALLERY_PHOTO_QR).
#
# GEAENDERT (Sprint-11-Nachbesserung): QR-Codes sind jetzt pro Veranstaltung
# ueber event_config.json (qr_codes_enabled, siehe config.py) ab-/anschaltbar
# - zwei Textvarianten statt einer festen Konstante, gewaehlt in
# _handle_review je nach self.config.qr_codes_enabled.
_SAVE_CONFIRMATION_TEXT_QR = (
    "Das Bild kann in der Galerie betrachtet werden. Dort hast du die "
    "Möglichkeit, einen QR-Code für jedes Bild aufzurufen und darüber "
    "einzelne Bilder von der Fotobox auf dein Mobiltelefon herunter zu "
    "laden. Die Bilder liegen NICHT auf einem Web-Server, sondern "
    "ausschließlich hier auf der Fotobox!"
)
_SAVE_CONFIRMATION_TEXT_NO_QR = (
    "Das Bild kann in der Galerie betrachtet werden."
)


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
            # NEU (Speicherplatz-Alarm): bei kritisch wenig freiem Speicher
            # (Stufe 2) keine neue Aufnahme mehr starten - der persistente
            # Warnbanner (renderer.py) erklaert bereits durchgehend, warum
            # nichts passiert; kein zusaetzlicher Text-/State-Wechsel noetig.
            if model.ui.storage_alarm_level >= 2:
                return TransitionResult(model=model)
            return self._go_photo_intro(model, now)
        # NEU (Sprint 11): ohne Galerie-Funktion fuer diese Veranstaltung
        # (config.gallery_enabled, siehe event_config.json) ignoriert die
        # State Machine TAP_GALLERY - der Button ist im Renderer/Layout
        # ohnehin schon nicht antippbar (siehe app_with_hw._map_click_to_
        # event), dies ist die zusaetzliche Absicherung, falls das Event
        # trotzdem irgendwie ausgeloest wird (Verteidigung in der Tiefe,
        # gleiches Muster wie config.qr_codes_enabled).
        if event.type == EventType.TAP_GALLERY and self.config.gallery_enabled:
            # NEU (Etappe 7): ohne Fotos zeigt GALLERY_GRID nur einen leeren
            # Dateipfad - stattdessen ein eigener, einladender Zustand.
            if not model.session.photos:
                return self._go_gallery_empty(model, now)
            return self._go_gallery_grid(model, now)
        if event.type == EventType.TAP_INSTRUCTIONS:
            return self._go_instructions(model, now)
        if event.type == EventType.TAP_TERMS:
            return self._go_terms(model, now)
        if event.type == EventType.SHUTDOWN_GESTURE_DETECTED:  # NEU (3.2)
            return self._go_pin_entry(model, now)
        if event.type == EventType.IDLE_TIMEOUT:
            # NEU (Sprint 11): der Attract-Modus zeigt bisherige Fotos als
            # Einladung - ohne Galerie-Funktion fuer diese Veranstaltung
            # inhaltlich nicht mehr passend (Gaeste sollen dann grundsaetzlich
            # keine fremden Fotos auf dem Display sehen). Stattdessen bleibt
            # das Hauptmenue einfach stehen (_go_main_menu haengt die
            # Idle-Deadline neu ein, sonst wuerde IDLE_TIMEOUT sofort wieder
            # feuern).
            if self.config.gallery_enabled:
                return self._go_attract_gallery(model, now)
            return self._go_main_menu(model, now)
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

    def _handle_gallery_empty(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        # NEU (Etappe 7): "Jetzt fotografieren" fuehrt direkt in
        # PHOTO_INTRO - gleiche Events wie auf GALLERY_GRID/MAIN_MENU
        # (TAP_PHOTO per Button, BUTTON_PRESS per Hardware-Taster).
        if event.type in {EventType.TAP_PHOTO, EventType.BUTTON_PRESS}:
            # NEU (Speicherplatz-Alarm): gleiche Sperre wie in
            # _handle_main_menu - siehe Kommentar dort.
            if model.ui.storage_alarm_level >= 2:
                return TransitionResult(model=model)
            return self._go_photo_intro(model, now)
        if event.type in {EventType.TAP_BACK, EventType.IDLE_TIMEOUT}:
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
        # NEU (Sprint 11, Feature 4): Doppeltap auf das Foto oder das Icon
        # "QR-Code anfordern" (beide loesen dasselbe Event aus, siehe
        # app_with_hw.py) - QR-Code fuer GENAU dieses Foto einblenden.
        # GEAENDERT (Sprint-11-Nachbesserung): ignoriert das Event komplett,
        # wenn QR-Codes fuer diese Veranstaltung deaktiviert sind
        # (config.qr_codes_enabled) - zentrale Stelle, die sowohl den
        # Doppeltap als auch das Icon (falls trotzdem angetippt) abdeckt,
        # unabhaengig davon, ob die Anzeige des Icons selbst auch schon
        # unterdrueckt wird (siehe app_with_hw.py/renderer.py).
        if event.type == EventType.TAP_GALLERY_QR and photo_count and self.config.qr_codes_enabled:
            return self._go_gallery_photo_qr(model, now)
        return TransitionResult(model=model)

    # NEU (Sprint 11, Feature 4): eigener Zustand statt eines Ueberlagerns
    # von GALLERY_FULLSCREEN, damit Ein-/Austritt (Timer, Actions) wie bei
    # den uebrigen Zustaenden ueber die State Machine laufen, nicht als
    # renderer-interner UI-Flag.
    def _handle_gallery_photo_qr(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {EventType.TAP_BACK, EventType.GALLERY_QR_TIMEOUT}:
            timers = replace(
                model.timers,
                gallery_qr_deadline=None,
                idle_deadline=now + self.config.timeouts.gallery_fullscreen_idle_seconds,
            )
            return TransitionResult(model=model.evolve(state=AppState.GALLERY_FULLSCREEN, timers=timers))
        return TransitionResult(model=model)

    def _go_gallery_photo_qr(self, model: AppModel, now: float) -> TransitionResult:
        timers = replace(
            model.timers,
            gallery_qr_deadline=now + self.config.timeouts.gallery_qr_seconds,
            idle_deadline=None,
        )
        return TransitionResult(
            model=model.evolve(state=AppState.GALLERY_PHOTO_QR, timers=timers),
            actions=("generate_gallery_qr", "set_led_qr"),
        )

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
            # NEU (Lesbarkeit): manueller Zeilenumbruch vor "und verarbeitet..."
            # - bei der um 50% vergroesserten Schrift (font_body) lief der
            # Text sonst ueber den rechten Bildschirmrand hinaus (_draw_text
            # unterstuetzt "\n" fuer mehrzeilige Statustexte, siehe renderer.py).
            new_ui = replace(model.ui, countdown_value=None, status_text="Foto wird von der Kamera heruntergeladen\nund verarbeitet...")
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
            # GEAENDERT (Sprint 11, Feature 3): kein sofortiger QR-Code mehr,
            # siehe _SAVE_CONFIRMATION_TEXT_QR oben. "export_photo" bleibt
            # bestehen - kopiert das Foto weiterhin unter demselben Dateinamen
            # ins Web-Verzeichnis, das braucht der spaetere Foto-QR aus der
            # Galerie (Feature 4, AppState.GALLERY_PHOTO_QR). "generate_qr"
            # entfaellt, da auf diesem Screen kein QR-Bild mehr gezeichnet wird.
            #
            # GEAENDERT (Sprint-11-Nachbesserung): ist qr_codes_enabled fuer
            # diese Veranstaltung aus, entfaellt zusaetzlich "export_photo" -
            # ohne QR-Funktion gibt es keinen Grund, das Foto in das (vom
            # Gaeste-WLAN aus erreichbare) Web-Verzeichnis zu kopieren. Das
            # ist nicht nur Aufraeumen, sondern auch Datenschutz: kein Foto
            # landet dann ueberhaupt erst dort, wo es theoretisch abrufbar
            # waere.
            if self.config.qr_codes_enabled:
                ui = replace(model.ui, status_text=_SAVE_CONFIRMATION_TEXT_QR)
                actions = ("export_photo", "set_led_qr", "stop_preview")
            else:
                ui = replace(model.ui, status_text=_SAVE_CONFIRMATION_TEXT_NO_QR)
                actions = ("set_led_qr", "stop_preview")
            return TransitionResult(
                model=model.evolve(state=AppState.QR_DISPLAY, session=session, timers=timers, ui=ui),
                actions=actions,
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
            # GEAENDERT (Sprint-11-Nachbesserung): nicht mehr zurueck ins
            # Hauptmenue, sondern direkt in den Countdown - der Gast war
            # mit der Aufnahme unzufrieden und will in aller Regel sofort
            # ein neues Foto aufnehmen, ohne erneut ueber PHOTO_INTRO/
            # PHOTO_PREVIEW gehen zu muessen. Die Live-Vorschau lief seit
            # dem Uebergang COUNTDOWN->CAPTURE_PENDING nicht mehr (siehe
            # _handle_countdown, "stop_preview") und wird hier deshalb
            # explizit neu gestartet - _go_countdown() selbst tut das
            # bewusst nicht, weil es sonst auf dem regulaeren Weg von
            # PHOTO_PREVIEW aus die dort bereits laufende Vorschau unnoetig
            # neu starten wuerde.
            session = replace(model.session, current_photo_path=None)
            countdown_result = self._go_countdown(model.evolve(session=session), now)
            return TransitionResult(
                model=countdown_result.model,
                actions=("delete_photo", "start_preview") + countdown_result.actions,
            )
        return TransitionResult(model=model)

    # GEAENDERT (Sprint 11, Feature 3): trotz des Namens QR_DISPLAY zeigt
    # dieser Screen seit diesem Umbau KEIN Bild mehr, sondern nur noch den
    # _SAVE_CONFIRMATION_TEXT-Hinweis (siehe _handle_review/TAP_SAVE oben).
    # Der Enum-Name wurde bewusst NICHT umbenannt, um den Aenderungsradius
    # klein zu halten (siehe README "Enum-getriebene Pipelines konsequent
    # pflegen").
    def _handle_qr_display(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {EventType.TAP_CANCEL, EventType.QR_TIMEOUT}:
            return self._go_photo_intro(model, now)
        return TransitionResult(model=model)

    def _handle_error_screen(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {EventType.ERROR_ACKNOWLEDGED, EventType.TAP_BACK}:
            return self._go_main_menu(model, now)
        return TransitionResult(model=model)

    # NEU (4.1): Service-/Admin-Menue. Erreichbar ausschliesslich ueber
    # Geheim-Geste im Hauptmenue + korrekte Wartungs-PIN.
    def _handle_admin_menu(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.TAP_ADMIN_SHUTDOWN:
            return self._go_shutdown_goodbye(model, now)
        if event.type == EventType.TAP_ADMIN_STATUS:          # NEU (4.3)
            return self._go_admin_status(model, now)
        if event.type == EventType.TAP_ADMIN_RESTART_APP:     # NEU (4.3)
            return self._go_admin_restart_pending(model, now)
        if event.type == EventType.TAP_ADMIN_DELETE_ALL:      # NEU (4.4)
            return self._go_admin_delete_confirm(model, now)
        if event.type == EventType.TAP_ADMIN_USB_EXPORT:      # NEU (4.6)
            return self._go_admin_usb_wait(model, now)
        if event.type == EventType.TAP_ADMIN_CAMERA_SETTINGS:  # NEU (Sprint 11, Feature 2)
            return self._go_admin_camera_settings(model, now)
        if event.type in {EventType.TAP_BACK, EventType.IDLE_TIMEOUT}:
            return self._go_main_menu(model, now)
        # BUTTON_PRESS wird hier bewusst NICHT behandelt: der Hardware-
        # Taster darf im Service-Menue kein Foto ausloesen.
        return TransitionResult(model=model)

    # NEU (4.3): Diagnose-Unterseite.
    def _handle_admin_status(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.ADMIN_STATUS_READY:
            lines = tuple(event.payload.get("lines", ()))
            ui = replace(model.ui, admin_status_lines=lines)
            return TransitionResult(model=model.evolve(ui=ui))
        if event.type in {EventType.TAP_BACK, EventType.IDLE_TIMEOUT}:
            return self._go_admin_menu(model, now)
        return TransitionResult(model=model)

    # NEU (Sprint 11, Feature 2): ISO/Blende ueber USB. read_admin_camera_
    # settings (synchron, siehe app_with_hw._read_admin_camera_settings)
    # liefert die aktuellen Werte + gueltigen Auswahllisten; +/- wandert
    # innerhalb dieser Liste. Die eigentliche gphoto2-set_config()-Aktion
    # laeuft ebenfalls synchron (kein Hintergrund-Thread noetig - einzelne
    # Config-Calls sind ueblicherweise deutlich unter einer Sekunde) und
    # liest den bereits optimistisch aktualisierten Wert direkt aus
    # model.ui (gleiches Prinzip wie "generate_gallery_qr" liest
    # ui.selected_gallery_index, siehe Feature 4).
    def _handle_admin_camera_settings(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.ADMIN_CAMERA_SETTINGS_READY:
            payload = event.payload
            ui = replace(
                model.ui,
                admin_camera_available=bool(payload.get("available", False)),
                admin_camera_error=payload.get("error"),
                admin_camera_iso=str(payload.get("iso", "")),
                admin_camera_iso_choices=tuple(payload.get("iso_choices", ())),
                admin_camera_aperture=str(payload.get("aperture", "")),
                admin_camera_aperture_choices=tuple(payload.get("aperture_choices", ())),
            )
            return TransitionResult(model=model.evolve(ui=ui))
        if event.type == EventType.TAP_ADMIN_CAMERA_ISO_UP and model.ui.admin_camera_iso_choices:
            return self._step_admin_camera_iso(model, +1)
        if event.type == EventType.TAP_ADMIN_CAMERA_ISO_DOWN and model.ui.admin_camera_iso_choices:
            return self._step_admin_camera_iso(model, -1)
        if event.type == EventType.TAP_ADMIN_CAMERA_APERTURE_UP and model.ui.admin_camera_aperture_choices:
            return self._step_admin_camera_aperture(model, +1)
        if event.type == EventType.TAP_ADMIN_CAMERA_APERTURE_DOWN and model.ui.admin_camera_aperture_choices:
            return self._step_admin_camera_aperture(model, -1)
        if event.type in {EventType.TAP_BACK, EventType.IDLE_TIMEOUT}:
            return self._go_admin_menu(model, now)
        return TransitionResult(model=model)

    @staticmethod
    def _step_choice(choices: tuple[str, ...], current: str, direction: int) -> str:
        """Wandert in `choices` einen Schritt vor/zurueck, ausgehend vom
        aktuellen Wert. Bleibt am jeweiligen Ende stehen (kein Umlaufen) -
        Blenden-/ISO-Listen sind geordnet (kleinster..groesster Wert), ein
        Umlauf waere fuer die Bedienung eher verwirrend als hilfreich.
        Ist der aktuelle Wert nicht in der Liste (z.B. Kamera meldet einen
        krummen Zwischenwert), wird bei Index 0 begonnen."""
        if not choices:
            return current
        try:
            index = choices.index(current)
        except ValueError:
            index = 0
        index = max(0, min(len(choices) - 1, index + direction))
        return choices[index]

    def _step_admin_camera_iso(self, model: AppModel, direction: int) -> TransitionResult:
        new_value = self._step_choice(model.ui.admin_camera_iso_choices, model.ui.admin_camera_iso, direction)
        ui = replace(model.ui, admin_camera_iso=new_value)
        return TransitionResult(model=model.evolve(ui=ui), actions=("set_admin_camera_iso",))

    def _step_admin_camera_aperture(self, model: AppModel, direction: int) -> TransitionResult:
        new_value = self._step_choice(model.ui.admin_camera_aperture_choices, model.ui.admin_camera_aperture, direction)
        ui = replace(model.ui, admin_camera_aperture=new_value)
        return TransitionResult(model=model.evolve(ui=ui), actions=("set_admin_camera_aperture",))

    # NEU (4.3): kurzer, nicht abbrechbarer Zwischenscreen vor dem
    # eigentlichen Neustart (analog zu SHUTDOWN_GOODBYE).
    def _handle_admin_restart_pending(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.ADMIN_RESTART_TIMEOUT:
            return TransitionResult(model=model, actions=("restart_app",))
        return TransitionResult(model=model)

    # NEU (4.4): Sicherheitsabfrage vor dem Loeschen des Gesamtbestands.
    def _handle_admin_delete_confirm(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.TAP_ADMIN_DELETE_CONFIRM:
            return self._go_admin_delete_running(model, now)
        # Jeder andere Ausstieg (Nein, Zurueck, Untaetigkeit) fuehrt
        # zurueck ins Menue, OHNE etwas zu loeschen. Der Idle-Timeout ist
        # hier bewusst erlaubt: bleibt die Abfrage unbeantwortet stehen,
        # ist "nicht loeschen" die richtige Annahme.
        if event.type in {
            EventType.TAP_ADMIN_DELETE_ABORT,
            EventType.TAP_BACK,
            EventType.IDLE_TIMEOUT,
        }:
            return self._go_admin_menu(model, now)
        return TransitionResult(model=model)

    # NEU (4.4): Loeschung laeuft im Hintergrund-Thread. Bewusst KEIN
    # Idle-Timeout und keine Abbruchmoeglichkeit - ein Abbruch mittendrin
    # wuerde einen halb geloeschten Bestand hinterlassen.
    def _handle_admin_delete_running(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.ADMIN_DELETE_FINISHED:
            lines = tuple(event.payload.get("lines", ()))
            return self._go_admin_delete_done(model, now, lines)
        return TransitionResult(model=model)

    # NEU (4.4): Ergebnis-Screen.
    # --- USB-Export (NEU 4.6) ---

    def _handle_admin_usb_wait(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.ADMIN_USB_INFO_READY:
            ui = replace(model.ui, admin_usb_lines=tuple(event.payload.get("lines", ())))
            return TransitionResult(model=model.evolve(ui=ui))
        if event.type == EventType.ADMIN_USB_DETECTED:
            name = str(event.payload.get("name", "USB-Stick"))
            ui = replace(
                model.ui,
                admin_usb_device_ready=True,
                admin_usb_lines=model.ui.admin_usb_lines + (f"Erkannt: {name}",),
            )
            # Idle-Timer neu aufziehen: ab jetzt muss nur noch "Weiter"
            # gedrueckt werden, dafuer reicht die volle Frist erneut.
            timers = replace(model.timers, idle_deadline=now + self.config.timeouts.admin_usb_wait_seconds)
            return TransitionResult(model=model.evolve(ui=ui, timers=timers))
        if event.type == EventType.TAP_ADMIN_USB_CONTINUE:
            # Ohne erkannten Stick bleibt "Weiter" wirkungslos - der
            # Renderer zeichnet den Button dann ausgegraut.
            if not model.ui.admin_usb_device_ready:
                return TransitionResult(model=model)
            return self._go_admin_usb_check(model, now)
        if event.type in {EventType.TAP_CANCEL, EventType.TAP_BACK, EventType.IDLE_TIMEOUT}:
            return self._go_admin_menu(model, now)
        return TransitionResult(model=model)

    def _handle_admin_usb_check(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.ADMIN_USB_CHECK_DONE:
            lines = tuple(event.payload.get("lines", ()))
            if bool(event.payload.get("ok", False)):
                return self._go_admin_usb_ready(model, now, lines)
            # NEU (4.7): not_enough_free durchreichen - der Problem-Screen
            # zeigt "Stick leeren" nur dann an.
            not_enough_free = bool(event.payload.get("not_enough_free", False))
            return self._go_admin_usb_problem(model, now, lines, not_enough_free)
        return TransitionResult(model=model)

    def _handle_admin_usb_ready(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        # NEU (4.7): "Export starten" startet den Kopierlauf.
        if event.type == EventType.TAP_ADMIN_USB_CONTINUE:
            return self._go_admin_usb_copy(model, now)
        # Abbruch/Timeout: Stick sauber auswerfen, ohne zu kopieren.
        if event.type in {EventType.TAP_CANCEL, EventType.TAP_BACK, EventType.IDLE_TIMEOUT}:
            return self._go_admin_usb_eject(model, now, can_retry=False)
        return TransitionResult(model=model)

    def _handle_admin_usb_problem(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        # NEU (4.7): "Stick leeren" (nur bei not_enough_free, nicht bei
        # too_small - da hilft Aufraeumen nicht). Bei too_small reagiert
        # der Button wie Abbrechen (eject + neuer Stick).
        if event.type == EventType.TAP_ADMIN_USB_CLEAR:
            if model.ui.admin_usb_not_enough_free:
                # Stick leeren und erneut pruefen (reuse ADMIN_USB_CHECK).
                ui = replace(model.ui, status_text="Stick wird geleert und geprüft ...", error_text=None)
                timers = replace(model.timers, idle_deadline=None)
                return TransitionResult(
                    model=model.evolve(state=AppState.ADMIN_USB_CHECK, ui=ui, timers=timers),
                    actions=("usb_clear_and_check",),
                )
            # too_small: gleiche Wirkung wie Abbrechen.
            return self._go_admin_usb_eject(model, now, can_retry=True)
        if event.type in {EventType.TAP_CANCEL, EventType.TAP_BACK, EventType.IDLE_TIMEOUT}:
            return self._go_admin_usb_eject(model, now, can_retry=True)
        return TransitionResult(model=model)

    # NEU (4.7): Kopierlauf laeuft im Hintergrund, nicht abbrechbar.
    def _handle_admin_usb_copy(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.ADMIN_USB_EXPORT_FINISHED:
            lines = tuple(event.payload.get("lines", ()))
            ok = bool(event.payload.get("ok", False))
            # NEU (6b): bleiben inhaltliche Konflikte offen, geht es NICHT
            # direkt zum Ergebnis-Screen, sondern zur interaktiven Auswahl.
            # Ohne Konflikte (Normalfall, altes Verhalten) unveraendert.
            conflicts = tuple(event.payload.get("conflicts", ()))
            if conflicts:
                return self._go_admin_usb_conflicts(model, now, conflicts)
            return self._go_admin_usb_export_done(model, now, lines, ok)
        return TransitionResult(model=model)

    # NEU (6b): interaktive Konfliktauswahl - der Nutzer entscheidet pro
    # Datei (oder per Sammelaktion) zwischen Ueberschreiben und Umbenennen,
    # bevor die Aufloesung angewendet wird.
    def _handle_admin_usb_conflicts(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.TAP_ADMIN_USB_CONFLICT_DECISION:
            name = event.payload.get("name")
            decision = event.payload.get("decision")
            if decision not in ("overwrite", "rename"):
                return TransitionResult(model=model)
            conflicts = tuple(
                replace(c, decision=decision) if c.name == name else c
                for c in model.ui.admin_usb_conflicts
            )
            ui = replace(model.ui, admin_usb_conflicts=conflicts)
            return TransitionResult(model=model.evolve(ui=ui))
        if event.type == EventType.TAP_ADMIN_USB_CONFLICTS_OVERWRITE_ALL:
            conflicts = tuple(replace(c, decision="overwrite") for c in model.ui.admin_usb_conflicts)
            ui = replace(model.ui, admin_usb_conflicts=conflicts)
            return TransitionResult(model=model.evolve(ui=ui))
        if event.type == EventType.TAP_ADMIN_USB_CONFLICTS_RENAME_ALL:
            conflicts = tuple(replace(c, decision="rename") for c in model.ui.admin_usb_conflicts)
            ui = replace(model.ui, admin_usb_conflicts=conflicts)
            return TransitionResult(model=model.evolve(ui=ui))
        # NEU (6b): "Ausfuehren" wendet die aktuellen Entscheidungen an.
        # TAP_BACK/TAP_CANCEL/IDLE_TIMEOUT tun bewusst dasselbe statt den
        # Screen kommentarlos zu verlassen - fuer bereits kopierte Neu-
        # Dateien gibt es kein sinnvolles "Abbrechen" mehr, und die
        # Standardentscheidung ("rename") ist ohnehin nicht-destruktiv.
        # So bleibt die Fotobox auch unbeaufsichtigt (Idle-Timeout) nicht
        # auf halbem Weg stehen.
        if event.type in {
            EventType.TAP_ADMIN_USB_CONFLICTS_APPLY,
            EventType.TAP_BACK,
            EventType.TAP_CANCEL,
            EventType.IDLE_TIMEOUT,
        }:
            return self._go_admin_usb_resolve(model, now)
        return TransitionResult(model=model)

    # NEU (6b): Aufloesung laeuft im Hintergrund, nicht abbrechbar - gleiche
    # Begruendung wie beim Kopierlauf selbst.
    def _handle_admin_usb_resolve(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.ADMIN_USB_RESOLVE_FINISHED:
            lines = tuple(event.payload.get("lines", ()))
            ok = bool(event.payload.get("ok", False))
            return self._go_admin_usb_export_done(model, now, lines, ok)
        return TransitionResult(model=model)

    # NEU (4.7): Ergebnis-Screen - zeigt Zusammenfassung des Exports.
    def _handle_admin_usb_export_done(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {EventType.TAP_ADMIN_USB_CONTINUE, EventType.TAP_BACK, EventType.IDLE_TIMEOUT}:
            return self._go_admin_usb_eject(model, now, can_retry=False)
        return TransitionResult(model=model)

    def _handle_admin_usb_eject(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.ADMIN_USB_EJECTED:
            return self._go_admin_usb_remove(model, now, tuple(event.payload.get("lines", ())))
        return TransitionResult(model=model)

    def _handle_admin_usb_remove(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {EventType.TAP_BACK, EventType.TAP_CANCEL, EventType.IDLE_TIMEOUT}:
            if model.ui.admin_usb_can_retry:
                return self._go_admin_usb_wait(model, now)
            # NEU (4.7): nach einem erfolgreichen, verifizierten Export
            # direkt zur Loesch-Abfrage statt ins Service-Menue.
            if model.ui.admin_usb_offer_delete:
                return self._go_admin_delete_confirm(model, now)
            return self._go_admin_menu(model, now)
        return TransitionResult(model=model)

    def _handle_admin_delete_done(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {EventType.TAP_BACK, EventType.TAP_CANCEL}:
            return self._go_admin_menu(model, now)
        # NEU (4.5): unbeaufsichtigt stehen gelassen -> ganz raus aus dem
        # PIN-geschuetzten Bereich, direkt ins Hauptmenue.
        if event.type == EventType.IDLE_TIMEOUT:
            return self._go_main_menu(model, now)
        return TransitionResult(model=model)

    def _go_admin_menu(self, model: AppModel, now: float) -> TransitionResult:
        # pin_entry wird geleert, damit die getippte PIN nicht im Modell
        # liegen bleibt (gleiche Disziplin wie beim Verlassen von PIN_ENTRY).
        ui = replace(model.ui, pin_entry="", error_text=None, status_text="Service-Menü")
        timers = replace(
            model.timers,
            idle_deadline=now + self.config.timeouts.admin_menu_idle_seconds,
            pin_error_deadline=None,
        )
        return TransitionResult(model=model.evolve(state=AppState.ADMIN_MENU, ui=ui, timers=timers))

    # NEU (4.3): Diagnose-Unterseite - Idle-Timer wie im Menue selbst,
    # admin_status_lines wird geleert; die Zeilen kommen etwas spaeter per
    # ADMIN_STATUS_READY (App sammelt sie synchron, siehe "collect_admin_status"
    # in app_with_hw.py).
    def _go_admin_status(self, model: AppModel, now: float) -> TransitionResult:
        ui = replace(model.ui, status_text="Status / Diagnose", error_text=None, admin_status_lines=())
        timers = replace(model.timers, idle_deadline=now + self.config.timeouts.admin_menu_idle_seconds)
        return TransitionResult(
            model=model.evolve(state=AppState.ADMIN_STATUS, ui=ui, timers=timers),
            actions=("collect_admin_status",),
        )

    # NEU (Sprint 11, Feature 2): Kamera-Einstellungen-Unterseite - gleiches
    # Muster wie _go_admin_status (Idle-Timer wie im Menue, Werte kommen kurz
    # darauf synchron per ADMIN_CAMERA_SETTINGS_READY).
    def _go_admin_camera_settings(self, model: AppModel, now: float) -> TransitionResult:
        ui = replace(
            model.ui,
            status_text="Kamera-Einstellungen",
            error_text=None,
            admin_camera_available=True,
            admin_camera_error=None,
            admin_camera_iso="",
            admin_camera_iso_choices=(),
            admin_camera_aperture="",
            admin_camera_aperture_choices=(),
        )
        timers = replace(model.timers, idle_deadline=now + self.config.timeouts.admin_menu_idle_seconds)
        return TransitionResult(
            model=model.evolve(state=AppState.ADMIN_CAMERA_SETTINGS, ui=ui, timers=timers),
            actions=("read_admin_camera_settings",),
        )

    # NEU (4.3): kurzer Zwischenscreen vor dem eigentlichen App-Neustart.
    # Bewusst NICHT abbrechbar (wie SHUTDOWN_GOODBYE) - "App neu starten"
    # ist bereits die bestaetigte Handlung, ein zweiter Tap sollte nichts
    # mehr aendern koennen.
    def _go_admin_delete_confirm(self, model: AppModel, now: float) -> TransitionResult:
        ui = replace(
            model.ui,
            status_text=(
                "Alle Bilder werden unwiderruflich\n"
                "von der Fotobox und der Kamera gelöscht.\n"
                "Bist du dir sicher?"
            ),
            error_text=None,
            admin_delete_lines=(),
        )
        timers = replace(model.timers, idle_deadline=now + self.config.timeouts.admin_menu_idle_seconds)
        return TransitionResult(model=model.evolve(state=AppState.ADMIN_DELETE_CONFIRM, ui=ui, timers=timers))

    def _go_admin_delete_running(self, model: AppModel, now: float) -> TransitionResult:
        # idle_deadline bewusst auf None: der Loeschlauf darf nicht durch
        # einen Timeout unterbrochen werden (siehe _handle_admin_delete_running).
        ui = replace(
            model.ui,
            status_text="Bilder werden gelöscht ...",
            error_text=None,
            admin_delete_progress="",          # NEU (4.9)
            admin_delete_fraction=0.0,         # NEU (4.9)
        )
        timers = replace(model.timers, idle_deadline=None)
        return TransitionResult(
            model=model.evolve(state=AppState.ADMIN_DELETE_RUNNING, ui=ui, timers=timers),
            actions=("start_delete_all",),
        )

    def _go_admin_delete_done(self, model: AppModel, now: float, lines: tuple[str, ...]) -> TransitionResult:
        # session.photos leeren: die Galerie darf nach dem Loeschen keine
        # Pfade mehr halten, die es nicht mehr gibt.
        session = replace(model.session, photos=(), current_photo_path=None, last_saved_photo_path=None)
        ui = replace(model.ui, status_text="Löschen abgeschlossen", error_text=None, admin_delete_lines=lines)
        timers = replace(model.timers, idle_deadline=now + self.config.timeouts.admin_menu_idle_seconds)
        return TransitionResult(
            model=model.evolve(state=AppState.ADMIN_DELETE_DONE, ui=ui, timers=timers, session=session),
        )

    # --- USB-Export (NEU 4.6) ---

    def _go_admin_usb_wait(self, model: AppModel, now: float) -> TransitionResult:
        ui = replace(
            model.ui,
            status_text="Bilder auf USB-Stick",
            error_text=None,
            admin_usb_lines=("Ermittle benötigten Speicherplatz ...",),
            admin_usb_device_ready=False,
            admin_usb_can_retry=False,
            admin_usb_offer_delete=False,
            admin_usb_not_enough_free=False,
            admin_usb_export_progress="",
        )
        timers = replace(model.timers, idle_deadline=now + self.config.timeouts.admin_usb_wait_seconds)
        return TransitionResult(
            model=model.evolve(state=AppState.ADMIN_USB_WAIT, ui=ui, timers=timers),
            actions=("usb_prepare",),
        )

    def _go_admin_usb_check(self, model: AppModel, now: float) -> TransitionResult:
        # Kein Idle-Timeout: Einbinden und Messen darf nicht unterbrochen
        # werden, sonst bliebe der Stick eingehaengt zurueck.
        ui = replace(model.ui, status_text="USB-Stick wird geprüft ...", error_text=None)
        timers = replace(model.timers, idle_deadline=None)
        return TransitionResult(
            model=model.evolve(state=AppState.ADMIN_USB_CHECK, ui=ui, timers=timers),
            actions=("usb_check",),
        )

    # NEU (4.7): Kopierlauf - Idle-Timeout ist None (nicht unterbrechbar).
    def _go_admin_usb_copy(self, model: AppModel, now: float) -> TransitionResult:
        ui = replace(
            model.ui,
            status_text="Export läuft ...",
            error_text=None,
            admin_usb_export_progress="",
            admin_usb_progress_fraction=0.0,   # NEU (4.8)
        )
        timers = replace(model.timers, idle_deadline=None)
        return TransitionResult(
            model=model.evolve(state=AppState.ADMIN_USB_COPY, ui=ui, timers=timers),
            actions=("usb_start_export",),
        )

    # NEU (6b): interaktive Konfliktauswahl - kein Hintergrund-Job, daher
    # regulaerer Idle-Timeout wie bei den anderen interaktiven USB-Screens.
    def _go_admin_usb_conflicts(
        self, model: AppModel, now: float, conflicts: tuple[ExportConflict, ...]
    ) -> TransitionResult:
        ui = replace(
            model.ui,
            status_text="Dateien mit abweichendem Inhalt gefunden",
            error_text=None,
            admin_usb_conflicts=conflicts,
            admin_usb_export_progress="",
        )
        timers = replace(model.timers, idle_deadline=now + self.config.timeouts.admin_usb_idle_seconds)
        return TransitionResult(model=model.evolve(state=AppState.ADMIN_USB_CONFLICTS, ui=ui, timers=timers))

    # NEU (6b): Aufloesung im Hintergrund - kein Idle-Timeout, gleiche
    # Begruendung wie beim eigentlichen Kopierlauf.
    def _go_admin_usb_resolve(self, model: AppModel, now: float) -> TransitionResult:
        ui = replace(
            model.ui,
            status_text="Konflikte werden aufgelöst ...",
            error_text=None,
            admin_usb_export_progress="",
            admin_usb_progress_fraction=0.0,
        )
        timers = replace(model.timers, idle_deadline=None)
        return TransitionResult(
            model=model.evolve(state=AppState.ADMIN_USB_RESOLVE, ui=ui, timers=timers),
            actions=("usb_apply_resolutions",),
        )

    # NEU (4.7): Ergebnis-Screen nach dem Export.
    def _go_admin_usb_export_done(self, model: AppModel, now: float, lines: tuple[str, ...], ok: bool) -> TransitionResult:
        ui = replace(
            model.ui,
            status_text="Export abgeschlossen",
            error_text=None,
            admin_usb_lines=lines,
            admin_usb_offer_delete=ok,
            admin_usb_export_progress="",
            # NEU (6b): eine evtl. noch gefuellte Konfliktliste aufraeumen,
            # damit sie bei einem spaeteren erneuten Export nicht als
            # veralteter Rest haengen bleibt (Vorbild: pin_entry wird beim
            # Verlassen des PIN-Screens ebenso geleert).
            admin_usb_conflicts=(),
        )
        timers = replace(model.timers, idle_deadline=now + self.config.timeouts.admin_usb_idle_seconds)
        return TransitionResult(model=model.evolve(state=AppState.ADMIN_USB_EXPORT_DONE, ui=ui, timers=timers))

    def _go_admin_usb_ready(self, model: AppModel, now: float, lines: tuple[str, ...]) -> TransitionResult:
        ui = replace(model.ui, status_text="USB-Stick bereit", error_text=None, admin_usb_lines=lines)
        timers = replace(model.timers, idle_deadline=now + self.config.timeouts.admin_usb_idle_seconds)
        return TransitionResult(model=model.evolve(state=AppState.ADMIN_USB_READY, ui=ui, timers=timers))

    def _go_admin_usb_problem(self, model: AppModel, now: float, lines: tuple[str, ...], not_enough_free: bool = False) -> TransitionResult:
        ui = replace(model.ui, status_text="USB-Stick nicht verwendbar", error_text=None, admin_usb_lines=lines, admin_usb_not_enough_free=not_enough_free)
        timers = replace(model.timers, idle_deadline=now + self.config.timeouts.admin_usb_idle_seconds)
        return TransitionResult(model=model.evolve(state=AppState.ADMIN_USB_PROBLEM, ui=ui, timers=timers))

    def _go_admin_usb_eject(self, model: AppModel, now: float, can_retry: bool) -> TransitionResult:
        # Kein Idle-Timeout: sync + umount muss zu Ende laufen, sonst
        # koennte der Stick mit vollem Schreibpuffer abgezogen werden.
        ui = replace(
            model.ui,
            status_text="USB-Stick wird ausgeworfen ...",
            error_text=None,
            admin_usb_can_retry=can_retry,
        )
        timers = replace(model.timers, idle_deadline=None)
        return TransitionResult(
            model=model.evolve(state=AppState.ADMIN_USB_EJECT, ui=ui, timers=timers),
            actions=("usb_eject",),
        )

    def _go_admin_usb_remove(self, model: AppModel, now: float, lines: tuple[str, ...]) -> TransitionResult:
        ui = replace(
            model.ui,
            status_text="USB-Stick kann jetzt entfernt werden",
            error_text=None,
            admin_usb_lines=lines,
            admin_usb_device_ready=False,
        )
        timers = replace(model.timers, idle_deadline=now + self.config.timeouts.admin_usb_idle_seconds)
        return TransitionResult(model=model.evolve(state=AppState.ADMIN_USB_REMOVE, ui=ui, timers=timers))

    def _go_admin_restart_pending(self, model: AppModel, now: float) -> TransitionResult:
        ui = replace(model.ui, status_text="App wird neu gestartet ...", error_text=None)
        timers = replace(
            model.timers,
            idle_deadline=None,
            admin_restart_deadline=now + self.config.timeouts.admin_restart_delay_seconds,
        )
        return TransitionResult(model=model.evolve(state=AppState.ADMIN_RESTART_PENDING, ui=ui, timers=timers))

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
            # NEU (4.1): Die PIN fuehrt nicht mehr direkt zum Herunterfahren,
            # sondern ins Service-Menue. Damit schuetzt die eine PIN-Huerde
            # alle Wartungsfunktionen - insbesondere den spaeteren USB-Export,
            # der sonst saemtliche Gaestefotos ungeschuetzt kopierbar machte.
            return self._go_admin_menu(model, now)

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

    def _go_gallery_empty(self, model: AppModel, now: float) -> TransitionResult:
        # NEU (Etappe 7): eigener Zustand statt eines Leer-Falls in
        # GALLERY_GRID - gleicher Idle-Timeout wie das normale Grid.
        timers = replace(
            model.timers,
            idle_deadline=now + self.config.timeouts.gallery_idle_seconds,
            preview_warning_deadline=None,
            preview_total_deadline=None,
        )
        ui = replace(model.ui, selected_gallery_index=None, gallery_scroll_offset=0, status_text="")
        return TransitionResult(
            model=model.evolve(state=AppState.GALLERY_EMPTY, timers=timers, ui=ui),
            actions=("stop_preview", "set_led_gallery_empty"),
        )

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
                "\n"
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
            status_text="Lass dich zur Erinnerung an die Veranstaltung fotografieren!",
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
