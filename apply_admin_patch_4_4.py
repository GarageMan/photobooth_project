#!/usr/bin/env python3
"""
apply_admin_patch_4_4.py
========================
Etappe 3 des Service-/Admin-Menues: "Alle Bilder loeschen".

  - Neue Zustaende ADMIN_DELETE_CONFIRM, ADMIN_DELETE_RUNNING,
    ADMIN_DELETE_DONE.
  - Sicherheitsabfrage mit rotem Warnblinken am LED-Ring (neuer Effekt
    LedEffect.ADMIN_DELETE_WARN), "Nein" links, "Ja, loeschen" rechts.
  - Loeschung laeuft in einem HINTERGRUND-THREAD (kann durch die
    Kamera-Loeschung ueber USB deutlich laenger als eine Sekunde
    dauern) - der Hauptloop pollt das Ergebnis, statt einzufrieren.
  - Geloescht wird data/photos/, data/web/ UND die Kamera-Speicherkarte;
    testbild.png bleibt ueberall erhalten (config.gallery.excluded_filenames).
  - Loeschprotokoll wird nach data/logs/ geschrieben (nur lokal); der
    Abschluss-Screen weist darauf hin, dass es beim Betreiber erfragt
    werden kann.
  - Nicht erreichbare Kamera bricht den Lauf NICHT ab, sondern wird auf
    dem Abschluss-Screen ehrlich benannt.

Voraussetzung: admin_delete_service.py muss bereits im Projektverzeichnis
liegen. Etappe 1 (apply_admin_patch_4_1.py) und Etappe 2
(apply_admin_patch_4_3.py) muessen bereits angewendet sein.

Betrifft states.py, events.py, models.py, layout.py, state_machine.py,
renderer.py, app_with_hw.py, led_service.py, hw_led_provider.py und
admin_menu.py.

Sicherheitsmechanik (wie die bisherigen Patches):
  - Jeder Anker muss GENAU EINMAL vorkommen, sonst Abbruch OHNE Schreiben.
  - Alles-oder-nichts ueber ALLE Dateien.
  - Backups (*.bak).
  - py_compile-Selbstcheck; bei Fehler Rollback aller Dateien.
  - Bereits gepatchte Dateien werden am Marker erkannt und fuehren zum
    Abbruch (kein doppeltes Anwenden).

Aufruf im Projektverzeichnis:

    python3 apply_admin_patch_4_4.py
    # oder mit Basisverzeichnis:
    python3 apply_admin_patch_4_4.py /home/photobox/photobooth
"""

from __future__ import annotations

import py_compile
import shutil
import sys
from pathlib import Path


FILES: list[tuple[str, str, list[tuple[str, str, str]]]] = [

    # ------------------------------------------------------------------
    ("states.py", "ADMIN_DELETE_CONFIRM = auto()", [
        (
            "ST1) Drei neue Zustaende fuer die Loeschung",
            '''    ADMIN_RESTART_PENDING = auto()''',
            '''    ADMIN_RESTART_PENDING = auto()
    # NEU (4.4): Sicherheitsabfrage vor dem Loeschen aller Bilder.
    # Bewusst ein eigener Zustand statt einer Wiederverwendung von
    # DELETE_CONFIRM - jener loescht nur das eine gerade aufgenommene
    # Foto, hier geht es um den kompletten Bestand inklusive Kamera.
    ADMIN_DELETE_CONFIRM = auto()
    # NEU (4.4): Loeschung laeuft (Hintergrund-Thread), nicht abbrechbar.
    ADMIN_DELETE_RUNNING = auto()
    # NEU (4.4): Ergebnis-Screen mit Zusammenfassung und Protokoll-Hinweis.
    ADMIN_DELETE_DONE = auto()''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("events.py", "ADMIN_DELETE_FINISHED = auto()", [
        (
            "EV1) Events fuer Bestaetigung, Abbruch und Abschluss",
            '''    ADMIN_RESTART_TIMEOUT = auto()''',
            '''    ADMIN_RESTART_TIMEOUT = auto()
    # NEU (4.4): Ja/Nein der Sicherheitsabfrage vor dem Loeschen. Bewusst
    # eigene Events statt TAP_CONFIRM_DELETE/TAP_ABORT_DELETE - jene
    # gehoeren zum Loeschen eines EINZELNEN Fotos im Review-Ablauf und
    # duerfen sich mit dem Loeschen des Gesamtbestands nicht vermischen.
    TAP_ADMIN_DELETE_CONFIRM = auto()
    TAP_ADMIN_DELETE_ABORT = auto()
    # NEU (4.4): Hintergrund-Thread ist fertig; payload enthaelt unter
    # "lines" die Zusammenfassung fuer den Abschluss-Screen.
    ADMIN_DELETE_FINISHED = auto()''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("models.py", "admin_delete_lines", [
        (
            "MD1) Ergebniszeilen der Loeschung im UI-Zustand",
            '''    admin_status_lines: tuple[str, ...] = ()''',
            '''    admin_status_lines: tuple[str, ...] = ()
    # NEU (4.4): Zusammenfassung nach dem Loeschen aller Bilder
    # (AppState.ADMIN_DELETE_DONE). Bewusst getrennt von
    # admin_status_lines, damit die Diagnoseseite und der Loesch-Report
    # sich nicht gegenseitig ueberschreiben.
    admin_delete_lines: tuple[str, ...] = ()''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("layout.py", "AppState.ADMIN_DELETE_CONFIRM:", [
        (
            "LY1) Buttons fuer Sicherheitsabfrage und Ergebnis-Screen",
            '''    if state == AppState.ADMIN_STATUS:       # NEU (4.3)
        return {"back": rects.back}
    return {}''',
            '''    if state == AppState.ADMIN_STATUS:       # NEU (4.3)
        return {"back": rects.back}
    if state == AppState.ADMIN_DELETE_CONFIRM:   # NEU (4.4)
        # "Nein" bewusst LINKS (die harmlose Wahl an der Stelle, an der
        # sonst die Standardaktion liegt), "Ja, loeschen" rechts.
        return {"admin_delete_abort": rects.left, "admin_delete_confirm": rects.right}
    if state == AppState.ADMIN_DELETE_DONE:      # NEU (4.4)
        return {"back": rects.back}
    # ADMIN_DELETE_RUNNING: bewusst leer - nicht abbrechbar.
    return {}''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("state_machine.py", "_handle_admin_delete_confirm", [
        (
            "SM1) Menuepunkt 'Alle Bilder loeschen' verdrahten",
            '''        if event.type == EventType.TAP_ADMIN_RESTART_APP:     # NEU (4.3)
            return self._go_admin_restart_pending(model, now)
        if event.type in {EventType.TAP_BACK, EventType.IDLE_TIMEOUT}:
            return self._go_main_menu(model, now)
        # Noch nicht implementierte Menuepunkte (Etappe 3-4): Zustand
        # unveraendert lassen, aber den Idle-Timer neu aufziehen - ein
        # Fehlgriff soll das Menue nicht vorzeitig schliessen.
        if event.type in {
            EventType.TAP_ADMIN_USB_EXPORT,
            EventType.TAP_ADMIN_DELETE_ALL,
        }:''',
            '''        if event.type == EventType.TAP_ADMIN_RESTART_APP:     # NEU (4.3)
            return self._go_admin_restart_pending(model, now)
        if event.type == EventType.TAP_ADMIN_DELETE_ALL:      # NEU (4.4)
            return self._go_admin_delete_confirm(model, now)
        if event.type in {EventType.TAP_BACK, EventType.IDLE_TIMEOUT}:
            return self._go_main_menu(model, now)
        # Noch nicht implementierter Menuepunkt (Etappe 4): Zustand
        # unveraendert lassen, aber den Idle-Timer neu aufziehen - ein
        # Fehlgriff soll das Menue nicht vorzeitig schliessen.
        if event.type in {
            EventType.TAP_ADMIN_USB_EXPORT,
        }:''',
        ),
        (
            "SM2) Handler fuer die drei Loesch-Zustaende",
            '''    # NEU (4.3): kurzer, nicht abbrechbarer Zwischenscreen vor dem
    # eigentlichen Neustart (analog zu SHUTDOWN_GOODBYE).
    def _handle_admin_restart_pending(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.ADMIN_RESTART_TIMEOUT:
            return TransitionResult(model=model, actions=("restart_app",))
        return TransitionResult(model=model)''',
            '''    # NEU (4.3): kurzer, nicht abbrechbarer Zwischenscreen vor dem
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

    # NEU (4.4): Ergebnis-Screen. Kein Idle-Timeout - die Zusammenfassung
    # (inkl. eventueller Fehler und Kamera-Status) soll stehen bleiben,
    # bis sie bewusst weggetippt wird.
    def _handle_admin_delete_done(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {EventType.TAP_BACK, EventType.TAP_CANCEL}:
            return self._go_admin_menu(model, now)
        return TransitionResult(model=model)''',
        ),
        (
            "SM3) Uebergangs-Methoden fuer die drei Loesch-Zustaende",
            '''    def _go_admin_restart_pending(self, model: AppModel, now: float) -> TransitionResult:''',
            '''    def _go_admin_delete_confirm(self, model: AppModel, now: float) -> TransitionResult:
        ui = replace(
            model.ui,
            status_text=(
                "Alle Bilder werden unwiderruflich von der Fotobox\\n"
                "und der Kamera gelöscht. Bist du dir sicher?"
            ),
            error_text=None,
            admin_delete_lines=(),
        )
        timers = replace(model.timers, idle_deadline=now + self.config.timeouts.admin_menu_idle_seconds)
        return TransitionResult(model=model.evolve(state=AppState.ADMIN_DELETE_CONFIRM, ui=ui, timers=timers))

    def _go_admin_delete_running(self, model: AppModel, now: float) -> TransitionResult:
        # idle_deadline bewusst auf None: der Loeschlauf darf nicht durch
        # einen Timeout unterbrochen werden (siehe _handle_admin_delete_running).
        ui = replace(model.ui, status_text="Bilder werden gelöscht ...", error_text=None)
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
        timers = replace(model.timers, idle_deadline=None)
        return TransitionResult(
            model=model.evolve(state=AppState.ADMIN_DELETE_DONE, ui=ui, timers=timers, session=session),
        )

    def _go_admin_restart_pending(self, model: AppModel, now: float) -> TransitionResult:''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("renderer.py", "_draw_admin_delete_confirm", [
        (
            "RN1) Titel fuer die drei neuen Screens",
            '''            AppState.ADMIN_MENU, AppState.ADMIN_STATUS, AppState.ADMIN_RESTART_PENDING,  # NEU (4.3)
        }''',
            '''            AppState.ADMIN_MENU, AppState.ADMIN_STATUS, AppState.ADMIN_RESTART_PENDING,  # NEU (4.3)
            AppState.ADMIN_DELETE_CONFIRM, AppState.ADMIN_DELETE_RUNNING,                # NEU (4.4)
            AppState.ADMIN_DELETE_DONE,                                                  # NEU (4.4)
        }''',
        ),
        (
            "RN2) Eigene Ueberschrift fuer den Ergebnis-Screen",
            '''        elif model.state == AppState.ADMIN_STATUS:
            # NEU (4.3): eigener Titel statt des Fotobox-Titels, wie ADMIN_MENU.
            self._draw_text("Status / Diagnose", self.font_title, (255, 255, 255), (60, 60))''',
            '''        elif model.state == AppState.ADMIN_STATUS:
            # NEU (4.3): eigener Titel statt des Fotobox-Titels, wie ADMIN_MENU.
            self._draw_text("Status / Diagnose", self.font_title, (255, 255, 255), (60, 60))
        elif model.state == AppState.ADMIN_DELETE_DONE:
            # NEU (4.4): Ergebnis der Loeschung.
            self._draw_text("Löschen abgeschlossen", self.font_title, (255, 255, 255), (60, 60))''',
        ),
        (
            "RN3) Zeichenmethoden aufrufen",
            '''        if model.state == AppState.ADMIN_RESTART_PENDING:  # NEU (4.3)
            self._draw_admin_restart_pending(model)''',
            '''        if model.state == AppState.ADMIN_RESTART_PENDING:  # NEU (4.3)
            self._draw_admin_restart_pending(model)

        if model.state == AppState.ADMIN_DELETE_CONFIRM:   # NEU (4.4)
            self._draw_admin_delete_confirm(model)

        if model.state == AppState.ADMIN_DELETE_RUNNING:   # NEU (4.4)
            self._draw_admin_delete_running(model)

        if model.state == AppState.ADMIN_DELETE_DONE:      # NEU (4.4)
            self._draw_admin_delete_done(model)''',
        ),
        (
            "RN4) Buttons fuer Sicherheitsabfrage und Ergebnis",
            '''        elif state == AppState.ADMIN_STATUS:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
        # ADMIN_RESTART_PENDING: bewusst kein Button - nicht abbrechbar.''',
            '''        elif state == AppState.ADMIN_STATUS:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
        elif state == AppState.ADMIN_DELETE_CONFIRM:
            # NEU (4.4): "Nein" links neutral-grau, "Ja" rechts deutlich rot -
            # die gefaehrliche Wahl soll nicht wie die naheliegende aussehen.
            self._draw_button("Nein, abbrechen", self.layout.left, (70, 70, 75))
            self._draw_button("Ja, alles löschen", self.layout.right, (160, 0, 0))
        elif state == AppState.ADMIN_DELETE_DONE:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
        # ADMIN_RESTART_PENDING / ADMIN_DELETE_RUNNING: bewusst kein Button -
        # nicht abbrechbar.''',
        ),
        (
            "RN5) Hintergrundfarben fuer die drei neuen Zustaende",
            '''            AppState.ADMIN_RESTART_PENDING: (20, 40, 20),  # NEU (4.3) - wie CAPTURE_PENDING
        }[state]''',
            '''            AppState.ADMIN_RESTART_PENDING: (20, 40, 20),  # NEU (4.3) - wie CAPTURE_PENDING
            # NEU (4.4): kraeftiges Dunkelrot als unuebersehbares Warnsignal,
            # deutlich abgesetzt vom ruhigen Blaugrau der uebrigen Admin-Screens.
            AppState.ADMIN_DELETE_CONFIRM: (55, 8, 8),
            AppState.ADMIN_DELETE_RUNNING: (40, 8, 8),
            AppState.ADMIN_DELETE_DONE: (18, 22, 30),
        }[state]''',
        ),
        (
            "RN6) Zeichenmethoden fuer die drei Loesch-Screens",
            '''    def _draw_admin_restart_pending(self, model: AppModel) -> None:''',
            '''    def _draw_admin_delete_confirm(self, model: AppModel) -> None:
        # NEU (4.4): Warntext gross und zentriert. status_text enthaelt
        # bereits Zeilenumbrueche (siehe state_machine._go_admin_delete_confirm),
        # daher zeilenweise zentriert setzen statt in einem Rutsch.
        height = self.config.screen.height
        lines = (model.ui.status_text or "").split("\\n")
        y = round(0.30 * height)
        line_height = self.font_status_main_menu.get_linesize() + 10
        for line in lines:
            self._blit_center(line, self.font_status_main_menu, (255, 210, 210), y)
            y += line_height
        # Zusaetzlicher Hinweis, was "alles" konkret umfasst - beugt der
        # Fehlannahme vor, es gehe nur um die Bilder auf dem Bildschirm.
        self._blit_center(
            "Betrifft Fotobox, QR-Download und Kamera-Speicherkarte.",
            self.font_body, (230, 170, 170), y + 16,
        )

    def _draw_admin_delete_running(self, model: AppModel) -> None:
        # NEU (4.4): reiner Fortschrittshinweis, kein Button. Die Loeschung
        # laeuft im Hintergrund-Thread (siehe app_with_hw._start_delete_all).
        height = self.config.screen.height
        self._blit_center(
            model.ui.status_text or "Bilder werden gelöscht ...",
            self.font_status_main_menu, (255, 210, 210), round(0.42 * height),
        )
        self._blit_center(
            "Bitte warten - die Kamera kann etwas Zeit brauchen.",
            self.font_body, (220, 170, 170), round(0.42 * height) + 70,
        )

    def _draw_admin_delete_done(self, model: AppModel) -> None:
        # NEU (4.4): Zusammenfassung als Zeilenliste, gleiche Optik wie
        # die Diagnoseseite (_draw_admin_status).
        height = self.config.screen.height
        y = round(0.22 * height)
        line_height = self.font_body.get_linesize() + 14
        for line in model.ui.admin_delete_lines:
            self._draw_text(line, self.font_body, (230, 230, 230), (60, y))
            y += line_height

    def _draw_admin_restart_pending(self, model: AppModel) -> None:''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("app_with_hw.py", "_start_delete_all", [
        (
            "AP1) Import admin_delete_service",
            '''from admin_diagnostics import collect_status_lines  # NEU (4.3)''',
            '''from admin_diagnostics import collect_status_lines  # NEU (4.3)
from admin_delete_service import delete_all_photos  # NEU (4.4)''',
        ),
        (
            "AP2) Kamera-Lock merken (fuer die Loeschung auf der Karte)",
            '''        camera_lock = threading.Lock()''',
            '''        camera_lock = threading.Lock()
        # NEU (4.4): auch das Loeschen auf der Kamera-Speicherkarte muss
        # sich dieses Lock teilen - gphoto2 erlaubt nur eine Verbindung.
        self._camera_lock = camera_lock''',
        ),
        (
            "AP3) Zustandsvariablen des Loesch-Threads",
            '''        # NEU (4.3): Startzeitpunkt fuer die Laufzeit-Anzeige im Status-Screen.
        self._app_start_monotonic = time.monotonic()''',
            '''        # NEU (4.3): Startzeitpunkt fuer die Laufzeit-Anzeige im Status-Screen.
        self._app_start_monotonic = time.monotonic()
        # NEU (4.4): Hintergrund-Thread fuer das Loeschen aller Bilder.
        # _delete_result wird vom Thread genau einmal gesetzt und vom
        # Hauptloop in _emit_due_timers gepollt - eine einzelne Referenz-
        # zuweisung ist unter dem GIL unteilbar, daher genuegt hier das
        # Fehlen/Vorhandensein des Werts als Fertigsignal (kein Lock noetig).
        self._delete_thread: threading.Thread | None = None
        self._delete_result = None''',
        ),
        (
            "AP4) Ergebnis des Loesch-Threads pollen",
            '''        # NEU (4.3): kurzer Zwischenscreen vor dem App-Neustart - eigener,
        # nicht abbrechbarer Timer, analog zu SHUTDOWN_GOODBYE.
        if state == AppState.ADMIN_RESTART_PENDING:
            if self._due(timers.admin_restart_deadline, now):
                self.dispatch(AppEvent(EventType.ADMIN_RESTART_TIMEOUT, source="timer"), now)
            return''',
            '''        # NEU (4.3): kurzer Zwischenscreen vor dem App-Neustart - eigener,
        # nicht abbrechbarer Timer, analog zu SHUTDOWN_GOODBYE.
        if state == AppState.ADMIN_RESTART_PENDING:
            if self._due(timers.admin_restart_deadline, now):
                self.dispatch(AppEvent(EventType.ADMIN_RESTART_TIMEOUT, source="timer"), now)
            return
        # NEU (4.4): Loeschlauf im Hintergrund - hier wird lediglich
        # gepollt, ob der Thread fertig ist. Bewusst KEIN Timeout: eine
        # laufende Loeschung darf nicht unterbrochen werden.
        if state == AppState.ADMIN_DELETE_RUNNING:
            result = self._delete_result
            if result is not None:
                self._delete_result = None
                self._delete_thread = None
                self.dispatch(
                    AppEvent(
                        EventType.ADMIN_DELETE_FINISHED,
                        payload={"lines": result.summary_lines()},
                        source="delete",
                    ),
                    now,
                )
            return''',
        ),
        (
            "AP5) Aktion 'start_delete_all'",
            '''            elif action == "restart_app":                     # NEU (4.3)
                self._restart_app()''',
            '''            elif action == "restart_app":                     # NEU (4.3)
                self._restart_app()
            elif action == "start_delete_all":                # NEU (4.4)
                self._start_delete_all()''',
        ),
        (
            "AP6) Methode _start_delete_all",
            '''    def _collect_admin_status(self) -> None:''',
            '''    def _start_delete_all(self) -> None:
        # NEU (4.4): Loeschung in einem Hintergrund-Thread starten. Anders
        # als die Diagnose (die synchron laeuft, weil sie unter einer
        # Sekunde bleibt) kann das Leeren der Kamera-Speicherkarte ueber
        # USB deutlich laenger dauern - synchron wuerde die Pygame-Schleife
        # so lange stehen und die App wirkte abgestuerzt.
        if self._delete_thread is not None and self._delete_thread.is_alive():
            print("[App] Loeschlauf laeuft bereits - Anforderung ignoriert.")
            return

        def worker() -> None:
            try:
                result = delete_all_photos(
                    photo_dir=self.config.photo_dir,
                    web_dir=self.config.web_dir,
                    log_dir=self.config.log_dir,
                    excluded_filenames=self.config.gallery.excluded_filenames,
                    camera_lock=self._camera_lock,
                    delete_from_camera=True,
                )
                print(
                    f"[App] Loeschlauf beendet: {result.deleted_photos} Fotos, "
                    f"{result.deleted_web_copies} Web-Kopien, Kamera: {result.camera_status}"
                )
                if result.report_path is not None:
                    print(f"[App] Loeschprotokoll: {result.report_path}")
            except Exception as exc:
                # Darf den Thread niemals unbemerkt sterben lassen - sonst
                # bliebe der Bildschirm ewig auf "Bilder werden geloescht".
                print(f"[App] FEHLER im Loeschlauf: {exc}")
                from admin_delete_service import DeleteResult
                result = DeleteResult()
                result.camera_status = "nicht geprüft"
                result.errors.append(str(exc))
            # Galerie-Zwischenspeicher leeren, damit keine Vorschaubilder
            # bereits geloeschter Fotos zurueckbleiben.
            self.gallery_service.clear_caches()
            # Letzte Zuweisung = Fertigsignal fuer den Hauptloop.
            self._delete_result = result

        self._delete_thread = threading.Thread(target=worker, name="delete-all", daemon=True)
        self._delete_thread.start()

    def _collect_admin_status(self) -> None:''',
        ),
        (
            "AP7) Klick-Zuordnung fuer Ja/Nein der Sicherheitsabfrage",
            '''            "abort_delete":   AppEvent(EventType.TAP_ABORT_DELETE, source="touch"),''',
            '''            "abort_delete":   AppEvent(EventType.TAP_ABORT_DELETE, source="touch"),
            # NEU (4.4): Gesamtbestand loeschen - bewusst eigene Events,
            # nicht die des Einzelfoto-Loeschens im Review-Ablauf.
            "admin_delete_confirm": AppEvent(EventType.TAP_ADMIN_DELETE_CONFIRM, source="touch"),
            "admin_delete_abort":   AppEvent(EventType.TAP_ADMIN_DELETE_ABORT, source="touch"),''',
        ),
        (
            "AP8) LED-Effekte fuer die drei Loesch-Screens",
            '''        elif state == AppState.ADMIN_RESTART_PENDING:
            # NEU (4.3): gruen wie waehrend der Kamera-Verarbeitung -
            # signalisiert "es passiert gerade etwas", kein neuer Effekt noetig.
            effect = LedEffect.CAPTURE_PROCESSING''',
            '''        elif state == AppState.ADMIN_RESTART_PENDING:
            # NEU (4.3): gruen wie waehrend der Kamera-Verarbeitung -
            # signalisiert "es passiert gerade etwas", kein neuer Effekt noetig.
            effect = LedEffect.CAPTURE_PROCESSING
        elif state in {AppState.ADMIN_DELETE_CONFIRM, AppState.ADMIN_DELETE_RUNNING}:
            # NEU (4.4): langsames, kraeftiges rotes Warnblinken - eigener
            # Effekt, damit es sich klar von LedEffect.ERROR (schnelles
            # Blinken bei einer Stoerung) unterscheidet.
            effect = LedEffect.ADMIN_DELETE_WARN
        elif state == AppState.ADMIN_DELETE_DONE:
            # NEU (4.4): zurueck zur ruhigen Welle - die Gefahr ist vorbei.
            effect = LedEffect.INSTRUCTIONS_WAVE''',
        ),
        (
            "AP9) Taster-LED in den Loesch-Screens aus",
            '''            AppState.ADMIN_STATUS, AppState.ADMIN_RESTART_PENDING,''',
            '''            AppState.ADMIN_STATUS, AppState.ADMIN_RESTART_PENDING,
            # NEU (4.4): waehrend Abfrage, Loeschlauf und Ergebnis darf der
            # Taster nichts ausloesen.
            AppState.ADMIN_DELETE_CONFIRM, AppState.ADMIN_DELETE_RUNNING,
            AppState.ADMIN_DELETE_DONE,''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("led_service.py", "ADMIN_DELETE_WARN", [
        (
            "LS1) Neuer LedEffect fuer das rote Warnblinken",
            '''    PIN_ERROR = auto()''',
            '''    PIN_ERROR = auto()
    # NEU (4.4): langsames, kraeftiges rotes Warnblinken waehrend der
    # Sicherheitsabfrage und des Loeschlaufs ("Alle Bilder loeschen").
    # Bewusst langsamer als LedEffect.ERROR - dort signalisiert schnelles
    # Blinken eine Stoerung, hier geht es um eine bevorstehende, bewusst
    # ausgeloeste, unwiderrufliche Handlung.
    ADMIN_DELETE_WARN = auto()''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("hw_led_provider.py", "ADMIN_DELETE_WARN", [
        (
            "HW1) Render-Zweig fuer das rote Warnblinken",
            '''            elif effect == LedEffect.ERROR:
                # Schnelles rotes Blinken (5 Hz)
                on = int(now * 10) % 2 == 0
                self._fill((255, 0, 0) if on else (30, 0, 0))
                time.sleep(0.03)''',
            '''            elif effect == LedEffect.ADMIN_DELETE_WARN:
                # NEU (4.4): langsames Warn-Pulsieren in Rot (ca. 1.2 Hz),
                # bewusst gemaechlicher als LedEffect.ERROR - kein Stoerungs-,
                # sondern ein Achtung-Signal vor einer unwiderruflichen
                # Handlung. Weiches Auf-/Abblenden statt hartem Blinken,
                # damit es ueber die Dauer der Abfrage nicht aggressiv wirkt.
                level = (math.sin(now * 2.0 * math.pi * 1.2) + 1.0) / 2.0
                level = 0.15 + 0.85 * level
                self._fill((int(230 * level), 0, 0))
                time.sleep(0.02)

            elif effect == LedEffect.ERROR:
                # Schnelles rotes Blinken (5 Hz)
                on = int(now * 10) % 2 == 0
                self._fill((255, 0, 0) if on else (30, 0, 0))
                time.sleep(0.03)''',
        ),
        (
            "HW2) Effekt im manuellen Schnelltest verfuegbar machen",
            '''        "error":            LedEffect.ERROR,''',
            '''        "error":            LedEffect.ERROR,
        "delete_warn":      LedEffect.ADMIN_DELETE_WARN,   # NEU (4.4)''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("admin_menu.py", "Etappe 3 - implementiert", [
        (
            "AM1) 'Alle Bilder loeschen' aktivieren",
            '''        enabled=False,          # Etappe 3''',
            '''        enabled=True,           # Etappe 3 - implementiert''',
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

    if not (base / "admin_delete_service.py").exists():
        fail("admin_delete_service.py fehlt im Projektverzeichnis. "
             "Zuerst diese Datei ablegen, dann das Patch-Skript erneut aufrufen.")

    # -- Phase 1: alles pruefen, noch nichts schreiben ---------------------
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

    # -- Phase 2: Backups + Schreiben --------------------------------------
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

    # -- Phase 3: Syntax-Selbstcheck ---------------------------------------
    for path, _ in planned:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            print(f"\nSyntaxfehler in {path.name}: {exc} - setze alles zurueck ...")
            rollback(written)
            sys.exit(1)
    try:
        py_compile.compile(str(base / "admin_delete_service.py"), doraise=True)
    except py_compile.PyCompileError as exc:
        print(f"\nSyntaxfehler in admin_delete_service.py: {exc} - setze alles zurueck ...")
        rollback(written)
        sys.exit(1)

    print("\nFertig. Alle Aenderungen angewendet, Syntax-Check bestanden.")
    print("Backups liegen als *.bak daneben.")
    print("\nNaechster Schritt:")
    print("  python3 -m pytest test_state_machine_admin.py test_admin_delete_service.py -v")
    print("  sudo python3 hw_led_provider.py delete_warn   # rotes Warnblinken pruefen")
    print("  sudo pkill -f app_with_hw.py")


def rollback(written: list[Path]) -> None:
    for path in written:
        backup = path.with_suffix(path.suffix + ".bak")
        if backup.exists():
            shutil.copy2(backup, path)
            print(f"  zurueckgesetzt: {path.name}")


if __name__ == "__main__":
    main()
