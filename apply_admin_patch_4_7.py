#!/usr/bin/env python3
"""
apply_admin_patch_4_7.py
========================
Etappe 4b des Service-/Admin-Menues: USB-Export mit SHA256-Verifikation.

  - Neue Zustaende ADMIN_USB_COPY, ADMIN_USB_EXPORT_DONE.
  - "Export starten" auf dem Bereit-Bildschirm (ersetzt "Fertig" aus 4a).
  - Kopieren + SHA256-Verifikation im Hintergrund-Thread mit
    Fortschrittsanzeige (Foto X von Y, Phasen "Kopieren" und "Pruefen").
  - Rotierender LED-Teilkreis waehrend des Exports (neuer Effekt
    LedEffect.ADMIN_USB_COPY).
  - Bereits vorhandene Dateien (gleicher Name + gleiche Groesse) werden
    uebersprungen - ein erneuter Export auf denselben Stick kopiert nur
    die fehlenden Bilder.
  - "Stick leeren"-Option bei zu wenig freiem Platz (nicht bei zu kleinem
    Stick - da hilft Aufraeumen nicht).
  - Nach erfolgreichem Export + Auswerfen: Uebergang zur Loesch-Abfrage
    ("Alle Bilder loeschen?"). Nur bei bestandener SHA256-Pruefung -
    Pruefsummenfehler blockieren das Angebot explizit.
  - Zielordner: Fotobox_JJJJ-MM-TT_HHMM (mit Uhrzeit, damit ein
    zweiter Export am selben Tag nicht kollidiert).

Voraussetzung: admin_usb_export.py muss bereits im Projektverzeichnis
liegen; Etappen 4.1, 4.3, 4.4 und 4.6 muessen angewendet sein.

Betrifft states.py, events.py, models.py, layout.py, state_machine.py,
renderer.py, app_with_hw.py, led_service.py, hw_led_provider.py.

Aufruf im Projektverzeichnis:

    python3 apply_admin_patch_4_7.py
"""

from __future__ import annotations

import py_compile
import shutil
import sys
from pathlib import Path


FILES: list[tuple[str, str, list[tuple[str, str, str]]]] = [

    # ------------------------------------------------------------------
    ("states.py", "ADMIN_USB_COPY = auto()", [
        (
            "ST1) Zwei neue Zustaende fuer den Export",
            '''    ADMIN_USB_REMOVE = auto()''',
            '''    ADMIN_USB_REMOVE = auto()
    # NEU (4.7): Kopierlauf mit Fortschrittsanzeige.
    ADMIN_USB_COPY = auto()
    # NEU (4.7): Ergebnis-Screen nach dem Export.
    ADMIN_USB_EXPORT_DONE = auto()''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("events.py", "ADMIN_USB_EXPORT_FINISHED = auto()", [
        (
            "EV1) Events fuer Export-Abschluss und Stick-Leeren",
            '''    ADMIN_USB_EJECTED = auto()''',
            '''    ADMIN_USB_EJECTED = auto()
    # NEU (4.7): Hintergrund-Thread (Kopieren+Verifikation) ist fertig.
    ADMIN_USB_EXPORT_FINISHED = auto()
    # NEU (4.7): "Stick leeren" im Problem-Screen (nur bei not_enough_free).
    TAP_ADMIN_USB_CLEAR = auto()''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("models.py", "admin_usb_not_enough_free", [
        (
            "MD1) Felder fuer den Export-Ablauf",
            '''    admin_usb_can_retry: bool = False''',
            '''    admin_usb_can_retry: bool = False
    # NEU (4.7): Problem-Typ-Unterscheidung (nur bei not_enough_free wird
    # "Stick leeren" angeboten; bei too_small hilft Aufraeumen nicht).
    admin_usb_not_enough_free: bool = False
    # NEU (4.7): nach erfolgreichem, verifiziertem Export fuehrt das
    # Entfernen des Sticks zur Loesch-Abfrage statt ins Service-Menue.
    admin_usb_offer_delete: bool = False
    # NEU (4.7): Fortschrittstext des laufenden Exports (wird von
    # app_with_hw direkt aus dem ExportProgress-Objekt gesetzt).
    admin_usb_export_progress: str = ""''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("layout.py", "usb_clear", [
        (
            "LY1) Problem-Buttons: Abbrechen + Stick leeren",
            '''    if state == AppState.ADMIN_USB_PROBLEM:
        return {"usb_continue": rects.right}''',
            '''    if state == AppState.ADMIN_USB_PROBLEM:
        # NEU (4.7): "usb_clear" steht immer auf dem rechten Button; der
        # Renderer aendert die Beschriftung je nach Problemtyp, die State
        # Machine entscheidet ueber die Wirkung.
        return {"cancel": rects.left, "usb_clear": rects.right}''',
        ),
        (
            "LY2) Buttons fuer den Ergebnis-Screen",
            '''    if state == AppState.ADMIN_USB_REMOVE:
        return {"back": rects.back}''',
            '''    if state == AppState.ADMIN_USB_EXPORT_DONE:   # NEU (4.7)
        return {"usb_continue": rects.right}
    if state == AppState.ADMIN_USB_REMOVE:
        return {"back": rects.back}''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("state_machine.py", "_handle_admin_usb_copy", [
        (
            "SM1) ADMIN_USB_READY: 'Export starten' statt 'Fertig'",
            '''    def _handle_admin_usb_ready(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        # In Etappe 4a fuehren beide Wege zum Aushaengen; in 4b wird aus
        # TAP_ADMIN_USB_CONTINUE der Start des Kopiervorgangs.
        if event.type in {
            EventType.TAP_ADMIN_USB_CONTINUE,
            EventType.TAP_CANCEL,
            EventType.TAP_BACK,
            EventType.IDLE_TIMEOUT,
        }:
            return self._go_admin_usb_eject(model, now, can_retry=False)
        return TransitionResult(model=model)''',
            '''    def _handle_admin_usb_ready(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        # NEU (4.7): "Export starten" startet den Kopierlauf.
        if event.type == EventType.TAP_ADMIN_USB_CONTINUE:
            return self._go_admin_usb_copy(model, now)
        # Abbruch/Timeout: Stick sauber auswerfen, ohne zu kopieren.
        if event.type in {EventType.TAP_CANCEL, EventType.TAP_BACK, EventType.IDLE_TIMEOUT}:
            return self._go_admin_usb_eject(model, now, can_retry=False)
        return TransitionResult(model=model)''',
        ),
        (
            "SM2) ADMIN_USB_PROBLEM: 'Stick leeren' bei zu wenig freiem Platz",
            '''    def _handle_admin_usb_problem(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {
            EventType.TAP_ADMIN_USB_CONTINUE,
            EventType.TAP_CANCEL,
            EventType.TAP_BACK,
            EventType.IDLE_TIMEOUT,
        }:
            # can_retry=True: nach dem Entfernen zurueck zum Wartebildschirm,
            # damit direkt ein anderer Stick probiert werden kann.
            return self._go_admin_usb_eject(model, now, can_retry=True)
        return TransitionResult(model=model)''',
            '''    def _handle_admin_usb_problem(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
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
        return TransitionResult(model=model)''',
        ),
        (
            "SM3) Handler fuer Export-Lauf und Ergebnis-Screen",
            '''    def _handle_admin_usb_eject(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:''',
            '''    # NEU (4.7): Kopierlauf laeuft im Hintergrund, nicht abbrechbar.
    def _handle_admin_usb_copy(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.ADMIN_USB_EXPORT_FINISHED:
            lines = tuple(event.payload.get("lines", ()))
            ok = bool(event.payload.get("ok", False))
            return self._go_admin_usb_export_done(model, now, lines, ok)
        return TransitionResult(model=model)

    # NEU (4.7): Ergebnis-Screen - zeigt Zusammenfassung des Exports.
    def _handle_admin_usb_export_done(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {EventType.TAP_ADMIN_USB_CONTINUE, EventType.TAP_BACK, EventType.IDLE_TIMEOUT}:
            return self._go_admin_usb_eject(model, now, can_retry=False)
        return TransitionResult(model=model)

    def _handle_admin_usb_eject(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:''',
        ),
        (
            "SM4) ADMIN_USB_REMOVE: nach Export -> Loesch-Abfrage",
            '''    def _handle_admin_usb_remove(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {EventType.TAP_BACK, EventType.TAP_CANCEL, EventType.IDLE_TIMEOUT}:
            if model.ui.admin_usb_can_retry:
                return self._go_admin_usb_wait(model, now)
            return self._go_admin_menu(model, now)
        return TransitionResult(model=model)''',
            '''    def _handle_admin_usb_remove(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {EventType.TAP_BACK, EventType.TAP_CANCEL, EventType.IDLE_TIMEOUT}:
            if model.ui.admin_usb_can_retry:
                return self._go_admin_usb_wait(model, now)
            # NEU (4.7): nach einem erfolgreichen, verifizierten Export
            # direkt zur Loesch-Abfrage statt ins Service-Menue.
            if model.ui.admin_usb_offer_delete:
                return self._go_admin_delete_confirm(model, now)
            return self._go_admin_menu(model, now)
        return TransitionResult(model=model)''',
        ),
        (
            "SM5) USB-CHECK: not_enough_free an Problem-Screen weitergeben",
            '''    def _handle_admin_usb_check(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.ADMIN_USB_CHECK_DONE:
            lines = tuple(event.payload.get("lines", ()))
            if bool(event.payload.get("ok", False)):
                return self._go_admin_usb_ready(model, now, lines)
            return self._go_admin_usb_problem(model, now, lines)
        return TransitionResult(model=model)''',
            '''    def _handle_admin_usb_check(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.ADMIN_USB_CHECK_DONE:
            lines = tuple(event.payload.get("lines", ()))
            if bool(event.payload.get("ok", False)):
                return self._go_admin_usb_ready(model, now, lines)
            # NEU (4.7): not_enough_free durchreichen - der Problem-Screen
            # zeigt "Stick leeren" nur dann an.
            not_enough_free = bool(event.payload.get("not_enough_free", False))
            return self._go_admin_usb_problem(model, now, lines, not_enough_free)
        return TransitionResult(model=model)''',
        ),
        (
            "SM6) Uebergangs-Methoden: Copy, ExportDone, Problem erweitern, Wait erweitern",
            '''    def _go_admin_usb_ready(self, model: AppModel, now: float, lines: tuple[str, ...]) -> TransitionResult:''',
            '''    # NEU (4.7): Kopierlauf - Idle-Timeout ist None (nicht unterbrechbar).
    def _go_admin_usb_copy(self, model: AppModel, now: float) -> TransitionResult:
        ui = replace(model.ui, status_text="Export läuft ...", error_text=None, admin_usb_export_progress="")
        timers = replace(model.timers, idle_deadline=None)
        return TransitionResult(
            model=model.evolve(state=AppState.ADMIN_USB_COPY, ui=ui, timers=timers),
            actions=("usb_start_export",),
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
        )
        timers = replace(model.timers, idle_deadline=now + self.config.timeouts.admin_menu_idle_seconds)
        return TransitionResult(model=model.evolve(state=AppState.ADMIN_USB_EXPORT_DONE, ui=ui, timers=timers))

    def _go_admin_usb_ready(self, model: AppModel, now: float, lines: tuple[str, ...]) -> TransitionResult:''',
        ),
        (
            "SM7) _go_admin_usb_problem: not_enough_free-Parameter",
            '''    def _go_admin_usb_problem(self, model: AppModel, now: float, lines: tuple[str, ...]) -> TransitionResult:
        ui = replace(model.ui, status_text="USB-Stick nicht verwendbar", error_text=None, admin_usb_lines=lines)''',
            '''    def _go_admin_usb_problem(self, model: AppModel, now: float, lines: tuple[str, ...], not_enough_free: bool = False) -> TransitionResult:
        ui = replace(model.ui, status_text="USB-Stick nicht verwendbar", error_text=None, admin_usb_lines=lines, admin_usb_not_enough_free=not_enough_free)''',
        ),
        (
            "SM8) _go_admin_usb_wait: neue Felder zuruecksetzen",
            '''            admin_usb_device_ready=False,
            admin_usb_can_retry=False,
        )''',
            '''            admin_usb_device_ready=False,
            admin_usb_can_retry=False,
            admin_usb_offer_delete=False,
            admin_usb_not_enough_free=False,
            admin_usb_export_progress="",
        )''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("renderer.py", "_draw_admin_usb_copy", [
        (
            "RN1) Titel des Ergebnis-Screens",
            '''        elif model.state in {
            AppState.ADMIN_USB_WAIT, AppState.ADMIN_USB_READY,
            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_REMOVE,
        }:''',
            '''        elif model.state == AppState.ADMIN_USB_EXPORT_DONE:
            # NEU (4.7): eigener Titel statt des generischen status_text.
            self._draw_text("Export abgeschlossen", self.font_title, (255, 255, 255), (60, 60))
        elif model.state in {
            AppState.ADMIN_USB_WAIT, AppState.ADMIN_USB_READY,
            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_REMOVE,
        }:''',
        ),
        (
            "RN2) text_screens um die zwei neuen Zustaende erweitern",
            '''            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_EJECT, AppState.ADMIN_USB_REMOVE,
        }''',
            '''            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_EJECT, AppState.ADMIN_USB_REMOVE,
            AppState.ADMIN_USB_COPY, AppState.ADMIN_USB_EXPORT_DONE,   # NEU (4.7)
        }''',
        ),
        (
            "RN3) Zeichenmethoden der neuen Screens aufrufen",
            '''        if model.state in {AppState.ADMIN_USB_CHECK, AppState.ADMIN_USB_EJECT}:  # NEU (4.6)
            self._draw_admin_usb_busy(model)''',
            '''        if model.state in {AppState.ADMIN_USB_CHECK, AppState.ADMIN_USB_EJECT}:  # NEU (4.6)
            self._draw_admin_usb_busy(model)

        if model.state == AppState.ADMIN_USB_COPY:           # NEU (4.7)
            self._draw_admin_usb_copy(model)

        if model.state == AppState.ADMIN_USB_EXPORT_DONE:    # NEU (4.7)
            self._draw_admin_usb_lines(model)''',
        ),
        (
            "RN4) Button-Beschriftung READY und PROBLEM aendern",
            '''        elif state == AppState.ADMIN_USB_READY:
            self._draw_button("Abbrechen", self.layout.left, (100, 100, 100))
            self._draw_button("Fertig", self.layout.right, (0, 130, 110))
        elif state == AppState.ADMIN_USB_PROBLEM:
            self._draw_button("Weiter", self.layout.right, (120, 90, 0))''',
            '''        elif state == AppState.ADMIN_USB_READY:
            self._draw_button("Abbrechen", self.layout.left, (100, 100, 100))
            self._draw_button("Export starten", self.layout.right, (0, 130, 110))
        elif state == AppState.ADMIN_USB_PROBLEM:
            # NEU (4.7): bei not_enough_free wird "Stick leeren" angeboten.
            # Bei too_small hilft Aufraeumen nicht - dort ersetzt der
            # rechte Button die Wirkung von "Weiter".
            if self._usb_not_enough_free:
                self._draw_button("Abbrechen", self.layout.left, (100, 100, 100))
                self._draw_button("Stick leeren", self.layout.right, (180, 80, 0))
            else:
                self._draw_button("Weiter", self.layout.right, (120, 90, 0))
        elif state == AppState.ADMIN_USB_EXPORT_DONE:
            self._draw_button("Weiter", self.layout.right, (0, 130, 110))''',
        ),
        (
            "RN5) Hintergrundfarben der zwei neuen Zustaende",
            '''            AppState.ADMIN_USB_REMOVE: (10, 32, 26),
        }[state]''',
            '''            AppState.ADMIN_USB_REMOVE: (10, 32, 26),
            AppState.ADMIN_USB_COPY: (12, 28, 28),      # NEU (4.7)
            AppState.ADMIN_USB_EXPORT_DONE: (10, 32, 26),  # NEU (4.7)
        }[state]''',
        ),
        (
            "RN6) Flag und Zeichenmethode fuer den Export-Fortschritt",
            '''    def _draw_admin_usb_lines(self, model: AppModel) -> None:
        # NEU (4.6): Zeilenliste wie bei Diagnose und Loesch-Ergebnis.
        self._usb_continue_enabled = model.ui.admin_usb_device_ready''',
            '''    # NEU (4.7): merkt sich, ob "Stick leeren" angeboten werden darf.
    _usb_not_enough_free: bool = False

    def _draw_admin_usb_copy(self, model: AppModel) -> None:
        # NEU (4.7): Fortschrittsanzeige waehrend des Kopierlaufs.
        height = self.config.screen.height
        text = model.ui.admin_usb_export_progress or "Export wird vorbereitet ..."
        self._blit_center(text, self.font_status_main_menu, (200, 235, 225), round(0.42 * height))

    def _draw_admin_usb_lines(self, model: AppModel) -> None:
        # NEU (4.6): Zeilenliste wie bei Diagnose und Loesch-Ergebnis.
        self._usb_continue_enabled = model.ui.admin_usb_device_ready
        self._usb_not_enough_free = model.ui.admin_usb_not_enough_free''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("app_with_hw.py", "_usb_start_export", [
        (
            "AP1) Import admin_usb_export",
            '''import admin_usb_service  # NEU (4.6)''',
            '''import admin_usb_service  # NEU (4.6)
from admin_usb_export import ExportProgress, export_photos, clear_stick  # NEU (4.7)''',
        ),
        (
            "AP2) Zustandsvariablen des Exports",
            '''        self._usb_unusable_reported = False          # Hinweis nur einmal zeigen''',
            '''        self._usb_unusable_reported = False          # Hinweis nur einmal zeigen
        # NEU (4.7): Fortschritt des laufenden Exports. Gleiches Muster wie
        # bei Loeschlauf und Pruefung - der Thread setzt am Ende genau eine
        # Referenz, der Hauptloop pollt sie in _emit_due_timers.
        self._usb_export_progress: ExportProgress | None = None
        self._usb_export_result = None''',
        ),
        (
            "AP3) Fortschritt des Exports pollen und an den Renderer weitergeben",
            '''        # NEU (4.6): laufende USB-Jobs (Pruefen / Auswerfen). Beide sind
        # bewusst nicht abbrechbar, daher wie beim Loeschlauf mit return.
        if state in {AppState.ADMIN_USB_CHECK, AppState.ADMIN_USB_EJECT}:''',
            '''        # NEU (4.7): Kopierlauf mit Fortschrittsanzeige. Der Hintergrund-
        # Thread aktualisiert _usb_export_progress, hier wird es gepollt
        # und in den UI-Zustand uebertragen.
        if state == AppState.ADMIN_USB_COPY:
            progress = self._usb_export_progress
            if progress is not None:
                if progress.phase == "copy":
                    text = f"Kopiere {progress.current_file} ({progress.copied_files}/{progress.total_files})"
                elif progress.phase == "verify":
                    text = f"Prüfe {progress.current_file} ({progress.verified_files}/{progress.total_files})"
                elif progress.phase == "done":
                    text = "Abschluss ..."
                else:
                    text = "Export wird vorbereitet ..."
                from dataclasses import replace as dc_replace
                ui = dc_replace(self.model.ui, admin_usb_export_progress=text)
                self.model = self.model.evolve(ui=ui)
            result = self._usb_export_result
            if result is not None:
                self._usb_export_result = None
                self._usb_export_progress = None
                self._usb_thread = None
                self.dispatch(
                    AppEvent(
                        EventType.ADMIN_USB_EXPORT_FINISHED,
                        payload={"lines": result.summary_lines(), "ok": result.ok},
                        source="usb",
                    ),
                    now,
                )
            return

        # NEU (4.6): laufende USB-Jobs (Pruefen / Auswerfen). Beide sind
        # bewusst nicht abbrechbar, daher wie beim Loeschlauf mit return.
        if state in {AppState.ADMIN_USB_CHECK, AppState.ADMIN_USB_EJECT}:''',
        ),
        (
            "AP4) Idle-Timeout fuer den Ergebnis-Screen",
            '''            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_REMOVE,''',
            '''            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_REMOVE,
            # NEU (4.7): Ergebnis-Screen. Bewusst OHNE ADMIN_USB_COPY (nicht
            # unterbrechbar - idle_deadline dort ohnehin None).
            AppState.ADMIN_USB_EXPORT_DONE,''',
        ),
        (
            "AP5) Aktionen des Exports und des Stick-Leerens",
            '''            elif action == "usb_eject":                       # NEU (4.6)
                self._usb_start_eject()''',
            '''            elif action == "usb_eject":                       # NEU (4.6)
                self._usb_start_eject()
            elif action == "usb_start_export":                # NEU (4.7)
                self._usb_start_export()
            elif action == "usb_clear_and_check":             # NEU (4.7)
                self._usb_start_clear_and_check()''',
        ),
        (
            "AP6) Methoden fuer Export und Stick-Leeren",
            '''    def _usb_start_eject(self) -> None:''',
            '''    def _usb_start_export(self) -> None:
        """NEU (4.7): Kopierlauf im Hintergrund-Thread starten."""
        stick = self._usb_stick
        if stick is None:
            self._usb_export_result = type("R", (), {"summary_lines": lambda: ("Kein Stick eingebunden.",), "ok": False})()
            return

        progress = ExportProgress()
        self._usb_export_progress = progress

        def worker() -> None:
            try:
                result = export_photos(
                    photo_dir=self.config.photo_dir,
                    mountpoint=stick.mountpoint,
                    excluded_filenames=self.config.gallery.excluded_filenames,
                    progress=progress,
                    verify=True,
                )
                print(
                    f"[App] Export beendet: {result.copied} kopiert, "
                    f"{result.skipped} uebersprungen, {result.verified} verifiziert, "
                    f"Fehler: {len(result.errors)}, Pruefsummenfehler: {len(result.failed_verify)}"
                )
            except Exception as exc:
                print(f"[App] FEHLER beim Export: {exc}")
                from admin_usb_export import ExportResult
                result = ExportResult()
                result.errors.append(str(exc))
                progress.phase = "error"
            self._usb_export_result = result

        self._usb_thread = threading.Thread(target=worker, name="usb-export", daemon=True)
        self._usb_thread.start()

    def _usb_start_clear_and_check(self) -> None:
        """NEU (4.7): Stick leeren, dann erneut pruefen - laeuft im selben
        Hintergrund-Thread-Muster wie die normale Pruefung."""
        stick = self._usb_stick
        if stick is None:
            self._usb_job_result = {"ok": False, "lines": ("Kein Stick eingebunden.",)}
            return

        def worker() -> None:
            try:
                deleted, errors = clear_stick(stick.mountpoint)
                print(f"[App] Stick geleert: {deleted} Eintraege, {len(errors)} Fehler")
                check = admin_usb_service.check_stick_for_export(
                    self._usb_partition, self._usb_required_bytes,
                    mountpoint=stick.mountpoint,
                )
                self._usb_stick = check.stick
                payload = {
                    "ok": check.ok,
                    "too_small": check.too_small,
                    "not_enough_free": check.not_enough_free,
                    "lines": check.lines,
                }
            except Exception as exc:
                print(f"[App] FEHLER beim Leeren/Pruefen: {exc}")
                payload = {"ok": False, "lines": ("Fehler beim Leeren des Sticks.", str(exc)[:70])}
            self._usb_job_result = payload

        self._usb_thread = threading.Thread(target=worker, name="usb-clear-check", daemon=True)
        self._usb_thread.start()

    def _usb_start_eject(self) -> None:''',
        ),
        (
            "AP7) Klick-Zuordnung fuer 'Stick leeren'",
            '''            "usb_continue":   AppEvent(EventType.TAP_ADMIN_USB_CONTINUE, source="touch"),''',
            '''            "usb_continue":   AppEvent(EventType.TAP_ADMIN_USB_CONTINUE, source="touch"),
            "usb_clear":      AppEvent(EventType.TAP_ADMIN_USB_CLEAR, source="touch"),    # NEU (4.7)''',
        ),
        (
            "AP8) LED-Effekte fuer den Kopierlauf",
            '''        elif state in {AppState.ADMIN_USB_CHECK, AppState.ADMIN_USB_EJECT}:
            # NEU (4.6): "es passiert gerade etwas" - wie beim Neustart.
            effect = LedEffect.CAPTURE_PROCESSING''',
            '''        elif state == AppState.ADMIN_USB_COPY:
            # NEU (4.7): rotierender Teilkreis waehrend des Exports.
            effect = LedEffect.ADMIN_USB_COPY
        elif state == AppState.ADMIN_USB_EXPORT_DONE:
            # NEU (4.7): zurueck zur ruhigen Welle - Export ist fertig.
            effect = LedEffect.INSTRUCTIONS_WAVE
        elif state in {AppState.ADMIN_USB_CHECK, AppState.ADMIN_USB_EJECT}:
            # NEU (4.6): "es passiert gerade etwas" - wie beim Neustart.
            effect = LedEffect.CAPTURE_PROCESSING''',
        ),
        (
            "AP9) Taster-LED in den neuen Screens aus",
            '''            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_EJECT, AppState.ADMIN_USB_REMOVE,''',
            '''            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_EJECT, AppState.ADMIN_USB_REMOVE,
            AppState.ADMIN_USB_COPY, AppState.ADMIN_USB_EXPORT_DONE,   # NEU (4.7)''',
        ),
        (
            "AP10) _usb_prepare: Fortschritts-Variablen zuruecksetzen",
            '''        self._usb_unusable_reported = False
        count, net, gross = admin_usb_service.required_export_bytes(''',
            '''        self._usb_unusable_reported = False
        # NEU (4.7): Reste eines vorherigen Exportlaufs verwerfen.
        self._usb_export_progress = None
        self._usb_export_result = None
        count, net, gross = admin_usb_service.required_export_bytes(''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("led_service.py", "ADMIN_USB_COPY", [
        (
            "LS1) Neuer LedEffect fuer den rotierenden Teilkreis",
            '''    ADMIN_USB_WAIT = auto()''',
            '''    ADMIN_USB_WAIT = auto()
    # NEU (4.7): rotierender Teilkreis waehrend des USB-Exports.
    ADMIN_USB_COPY = auto()''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("hw_led_provider.py", "ADMIN_USB_COPY", [
        (
            "HW1) Render-Zweig fuer den rotierenden Teilkreis",
            '''            elif effect == LedEffect.ADMIN_USB_WAIT:''',
            '''            elif effect == LedEffect.ADMIN_USB_COPY:
                # NEU (4.7): rotierender Teilkreis (~0.8 Umdrehungen/s).
                # 6 LEDs breit mit weichem Auf-/Abblenden an den Raendern.
                num = len(self._pixels)
                center = (now * num * 0.8) % num
                for i in range(num):
                    dist = min((i - center) % num, (center - i) % num)
                    if dist < 3:
                        b = max(0.0, 1.0 - dist / 3.0)
                        self._pixels[i] = (0, int(160 * b), int(200 * b))
                    else:
                        self._pixels[i] = (0, 0, 0)
                self._pixels.show()
                time.sleep(0.02)

            elif effect == LedEffect.ADMIN_USB_WAIT:''',
        ),
        (
            "HW2) Effekt im manuellen Schnelltest verfuegbar machen",
            '''        "usb_wait":         LedEffect.ADMIN_USB_WAIT,      # NEU (4.6)''',
            '''        "usb_wait":         LedEffect.ADMIN_USB_WAIT,      # NEU (4.6)
        "usb_copy":         LedEffect.ADMIN_USB_COPY,      # NEU (4.7)''',
        ),
    ]),
]


def fail(message: str) -> None:
    print(f"\nABBRUCH: {message}")
    print("Es wurde KEINE Datei veraendert.")
    sys.exit(1)


def main() -> None:
    base = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    print(f"Projektverzeichnis: {base}")

    if not (base / "admin_usb_export.py").exists():
        fail("admin_usb_export.py fehlt im Projektverzeichnis. "
             "Zuerst diese Datei ablegen, dann das Patch-Skript erneut aufrufen.")

    planned: list[tuple[Path, str]] = []
    for filename, marker, patches in FILES:
        path = base / filename
        if not path.exists():
            fail(f"{filename} nicht gefunden.")

        text = path.read_text(encoding="utf-8")

        if marker in text:
            fail(f"{filename} enthaelt bereits '{marker}' - Patch wurde "
                 f"offenbar schon angewendet.")

        for name, old, new in patches:
            count = text.count(old)
            if count != 1:
                fail(f"{filename} / {name}: Anker kommt {count}x vor "
                     f"(erwartet: genau 1x).\nGesuchter Text:\n{old}")
            text = text.replace(old, new, 1)

        planned.append((path, text))
        print(f"  geprueft: {filename} ({len(patches)} Aenderung(en))")

    written: list[Path] = []
    try:
        for path, new_text in planned:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
            path.write_text(new_text, encoding="utf-8")
            written.append(path)
            print(f"  geschrieben: {path.name} (Backup: {path.name}.bak)")
    except Exception as exc:
        print(f"\nSchreibfehler: {exc} - setze zurueck ...")
        rollback(written)
        sys.exit(1)

    for path, _ in planned:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            print(f"\nSyntaxfehler in {path.name}: {exc} - setze alles zurueck ...")
            rollback(written)
            sys.exit(1)
    try:
        py_compile.compile(str(base / "admin_usb_export.py"), doraise=True)
    except py_compile.PyCompileError as exc:
        print(f"\nSyntaxfehler in admin_usb_export.py: {exc} - setze alles zurueck ...")
        rollback(written)
        sys.exit(1)

    print("\nFertig. Alle Aenderungen angewendet, Syntax-Check bestanden.")
    print("Backups liegen als *.bak daneben.")
    print("\nNaechster Schritt:")
    print("  sudo chown -R photobox:photobox ~/photobooth")
    print("  python3 -m pytest test_state_machine_admin.py test_admin_usb_export.py test_admin_usb_service.py -v")
    print("  sudo python3 hw_led_provider.py usb_copy    # rotierenden Teilkreis pruefen")
    print("  sudo pkill -f app_with_hw.py")


def rollback(written: list[Path]) -> None:
    for path in written:
        backup = path.with_suffix(path.suffix + ".bak")
        if backup.exists():
            shutil.copy2(backup, path)
            print(f"  zurueckgesetzt: {path.name}")


if __name__ == "__main__":
    main()
