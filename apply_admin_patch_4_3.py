#!/usr/bin/env python3
"""
apply_admin_patch_4_3.py
========================
Etappe 2 des Service-/Admin-Menues: "Status / Diagnose" und "App neu
starten" (die verworfene Option "Software-Updates pruefen" wurde nicht
umgesetzt - siehe Chatverlauf).

  - Neue Zustaende AppState.ADMIN_STATUS, AppState.ADMIN_RESTART_PENDING.
  - "Status / Diagnose": zeigt Speicherplatz, Fotoanzahl, Kamera-Status,
    IP-Adresse und Laufzeit. Diagnosezeilen werden synchron beim Betreten
    ermittelt (admin_diagnostics.py) und per ADMIN_STATUS_READY-Event an
    die State Machine zurueckgemeldet.
  - "App neu starten": kurzer, nicht abbrechbarer Zwischenscreen (analog
    SHUTDOWN_GOODBYE), danach beendet sich die App sauber (Exit-Code 0) -
    die Auto-Restart-Schleife in start_fotobox.sh startet sie neu. Anders
    als "Herunterfahren" faehrt dabei NICHT das Betriebssystem herunter.
  - admin_menu.py: beide Menuepunkte auf enabled=True gesetzt.

Voraussetzung: admin_diagnostics.py muss bereits im Projektverzeichnis
liegen. Etappe 1 (apply_admin_patch_4_1.py) muss bereits angewendet sein.

Betrifft states.py, events.py, config.py, models.py, layout.py,
state_machine.py, renderer.py, app_with_hw.py und admin_menu.py.

Sicherheitsmechanik (wie apply_admin_patch_4_1.py):
  - Jeder Anker muss GENAU EINMAL vorkommen, sonst Abbruch OHNE Schreiben.
  - Alles-oder-nichts ueber ALLE Dateien.
  - Backups (*.bak).
  - py_compile-Selbstcheck; bei Fehler Rollback aller Dateien.
  - Bereits gepatchte Dateien werden am Marker erkannt und fuehren zum
    Abbruch (kein doppeltes Anwenden).

Aufruf im Projektverzeichnis:

    python3 apply_admin_patch_4_3.py
    # oder mit Basisverzeichnis:
    python3 apply_admin_patch_4_3.py /home/photobox/photobooth
"""

from __future__ import annotations

import py_compile
import shutil
import sys
from pathlib import Path


FILES: list[tuple[str, str, list[tuple[str, str, str]]]] = [

    # ------------------------------------------------------------------
    ("states.py", "ADMIN_STATUS = auto()", [
        (
            "ST1) Zwei neue Zustaende",
            '''    ADMIN_MENU = auto()''',
            '''    ADMIN_MENU = auto()
    # NEU (4.3): Diagnose-Unterseite des Service-Menues.
    ADMIN_STATUS = auto()
    # NEU (4.3): kurzer, nicht abbrechbarer Zwischenscreen nach "App neu
    # starten" - gibt sichtbares Feedback, bevor die App sich beendet
    # (die Auto-Restart-Schleife in start_fotobox.sh startet sie danach
    # automatisch neu).
    ADMIN_RESTART_PENDING = auto()''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("events.py", "ADMIN_STATUS_READY = auto()", [
        (
            "EV1) Neue Events fuer Status-Ergebnis und Neustart-Timer",
            '''    TAP_ADMIN_SHUTDOWN = auto()''',
            '''    TAP_ADMIN_SHUTDOWN = auto()
    # NEU (4.3): Diagnosezeilen sind fertig ermittelt (app_with_hw sammelt
    # sie synchron nach TAP_ADMIN_STATUS und liefert sie im payload zurueck).
    ADMIN_STATUS_READY = auto()
    # NEU (4.3): der kurze Anzeige-Timer in ADMIN_RESTART_PENDING ist
    # abgelaufen - loest die "restart_app"-Action aus.
    ADMIN_RESTART_TIMEOUT = auto()''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("config.py", "admin_restart_delay_seconds", [
        (
            "CF1) Anzeigedauer des Neustart-Zwischenscreens",
            '''    admin_menu_idle_seconds: float = 30.0''',
            '''    admin_menu_idle_seconds: float = 30.0
    # NEU (4.3): so lange steht der "App wird neu gestartet ..."-Screen,
    # bevor die App sich tatsaechlich beendet - reines Feedback, damit der
    # Bildschirm nicht unvermittelt schwarz wird.
    admin_restart_delay_seconds: float = 1.5''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("models.py", "admin_status_lines", [
        (
            "MD1) Neuer Timer fuer den Neustart-Zwischenscreen",
            '''    shutdown_goodbye_deadline: float | None = None''',
            '''    shutdown_goodbye_deadline: float | None = None
    # NEU (4.3): Ende des kurzen Anzeige-Timers in ADMIN_RESTART_PENDING.
    admin_restart_deadline: float | None = None''',
        ),
        (
            "MD2) Diagnosezeilen im UI-Zustand",
            '''    pin_entry: str = ""''',
            '''    pin_entry: str = ""
    # NEU (4.3): ermittelte Diagnosezeilen fuer AppState.ADMIN_STATUS.
    # Leer, solange die Ermittlung noch laeuft (siehe "collect_admin_status"
    # in app_with_hw.py).
    admin_status_lines: tuple[str, ...] = ()''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("layout.py", "AppState.ADMIN_STATUS:", [
        (
            "LY1) Zurueck-Button fuer die Diagnoseseite",
            '''    if state == AppState.PIN_ENTRY:          # NEU (3.3)
        return rects.pin_keys
    return {}''',
            '''    if state == AppState.PIN_ENTRY:          # NEU (3.3)
        return rects.pin_keys
    if state == AppState.ADMIN_STATUS:       # NEU (4.3)
        return {"back": rects.back}
    return {}''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("state_machine.py", "_handle_admin_status", [
        (
            "SM1) _handle_admin_menu: Status/Neustart routen statt Platzhalter",
            '''    def _handle_admin_menu(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.TAP_ADMIN_SHUTDOWN:
            return self._go_shutdown_goodbye(model, now)
        if event.type in {EventType.TAP_BACK, EventType.IDLE_TIMEOUT}:
            return self._go_main_menu(model, now)
        # Noch nicht implementierte Menuepunkte (Etappe 2-4): Zustand
        # unveraendert lassen, aber den Idle-Timer neu aufziehen - ein
        # Fehlgriff soll das Menue nicht vorzeitig schliessen.
        if event.type in {
            EventType.TAP_ADMIN_STATUS,
            EventType.TAP_ADMIN_USB_EXPORT,
            EventType.TAP_ADMIN_DELETE_ALL,
            EventType.TAP_ADMIN_RESTART_APP,
        }:
            timers = replace(
                model.timers,
                idle_deadline=now + self.config.timeouts.admin_menu_idle_seconds,
            )
            return TransitionResult(model=model.evolve(timers=timers))
        # BUTTON_PRESS wird hier bewusst NICHT behandelt: der Hardware-
        # Taster darf im Service-Menue kein Foto ausloesen.
        return TransitionResult(model=model)''',
            '''    def _handle_admin_menu(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.TAP_ADMIN_SHUTDOWN:
            return self._go_shutdown_goodbye(model, now)
        if event.type == EventType.TAP_ADMIN_STATUS:          # NEU (4.3)
            return self._go_admin_status(model, now)
        if event.type == EventType.TAP_ADMIN_RESTART_APP:     # NEU (4.3)
            return self._go_admin_restart_pending(model, now)
        if event.type in {EventType.TAP_BACK, EventType.IDLE_TIMEOUT}:
            return self._go_main_menu(model, now)
        # Noch nicht implementierte Menuepunkte (Etappe 3-4): Zustand
        # unveraendert lassen, aber den Idle-Timer neu aufziehen - ein
        # Fehlgriff soll das Menue nicht vorzeitig schliessen.
        if event.type in {
            EventType.TAP_ADMIN_USB_EXPORT,
            EventType.TAP_ADMIN_DELETE_ALL,
        }:
            timers = replace(
                model.timers,
                idle_deadline=now + self.config.timeouts.admin_menu_idle_seconds,
            )
            return TransitionResult(model=model.evolve(timers=timers))
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

    # NEU (4.3): kurzer, nicht abbrechbarer Zwischenscreen vor dem
    # eigentlichen Neustart (analog zu SHUTDOWN_GOODBYE).
    def _handle_admin_restart_pending(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.ADMIN_RESTART_TIMEOUT:
            return TransitionResult(model=model, actions=("restart_app",))
        return TransitionResult(model=model)''',
        ),
        (
            "SM2) Uebergangs-Methoden fuer die zwei neuen Zustaende",
            '''    def _go_admin_menu(self, model: AppModel, now: float) -> TransitionResult:
        # pin_entry wird geleert, damit die getippte PIN nicht im Modell
        # liegen bleibt (gleiche Disziplin wie beim Verlassen von PIN_ENTRY).
        ui = replace(model.ui, pin_entry="", error_text=None, status_text="Service-Menü")
        timers = replace(
            model.timers,
            idle_deadline=now + self.config.timeouts.admin_menu_idle_seconds,
            pin_error_deadline=None,
        )
        return TransitionResult(model=model.evolve(state=AppState.ADMIN_MENU, ui=ui, timers=timers))''',
            '''    def _go_admin_menu(self, model: AppModel, now: float) -> TransitionResult:
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

    # NEU (4.3): kurzer Zwischenscreen vor dem eigentlichen App-Neustart.
    # Bewusst NICHT abbrechbar (wie SHUTDOWN_GOODBYE) - "App neu starten"
    # ist bereits die bestaetigte Handlung, ein zweiter Tap sollte nichts
    # mehr aendern koennen.
    def _go_admin_restart_pending(self, model: AppModel, now: float) -> TransitionResult:
        ui = replace(model.ui, status_text="App wird neu gestartet ...", error_text=None)
        timers = replace(
            model.timers,
            idle_deadline=None,
            admin_restart_deadline=now + self.config.timeouts.admin_restart_delay_seconds,
        )
        return TransitionResult(model=model.evolve(state=AppState.ADMIN_RESTART_PENDING, ui=ui, timers=timers))''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("renderer.py", "_draw_admin_status", [
        (
            "RN1) Titel: eigene Ueberschriften fuer die beiden neuen Screens",
            '''        text_screens = {AppState.INSTRUCTIONS, AppState.TERMS, AppState.PIN_ENTRY, AppState.SHUTDOWN_GOODBYE, AppState.ADMIN_MENU}

        if model.state not in text_screens and not hide_all_text:
            self._draw_text(self.config.screen.title, self.font_title, (255, 255, 255), (60, 60))
        elif model.state == AppState.ADMIN_MENU:
            # NEU (4.2): statt des Fotobox-Titels an gleicher Position/
            # Schrift/Farbe der Menuename - der Titel ist hier nicht der
            # passende Kontext.
            self._draw_text("Service-Menü", self.font_title, (255, 255, 255), (60, 60))''',
            '''        text_screens = {
            AppState.INSTRUCTIONS, AppState.TERMS, AppState.PIN_ENTRY, AppState.SHUTDOWN_GOODBYE,
            AppState.ADMIN_MENU, AppState.ADMIN_STATUS, AppState.ADMIN_RESTART_PENDING,  # NEU (4.3)
        }

        if model.state not in text_screens and not hide_all_text:
            self._draw_text(self.config.screen.title, self.font_title, (255, 255, 255), (60, 60))
        elif model.state == AppState.ADMIN_MENU:
            # NEU (4.2): statt des Fotobox-Titels an gleicher Position/
            # Schrift/Farbe der Menuename - der Titel ist hier nicht der
            # passende Kontext.
            self._draw_text("Service-Menü", self.font_title, (255, 255, 255), (60, 60))
        elif model.state == AppState.ADMIN_STATUS:
            # NEU (4.3): eigener Titel statt des Fotobox-Titels, wie ADMIN_MENU.
            self._draw_text("Status / Diagnose", self.font_title, (255, 255, 255), (60, 60))
        # ADMIN_RESTART_PENDING zeigt bewusst gar keinen Titel - nur die
        # grosse zentrierte Statuszeile (siehe _draw_admin_restart_pending).''',
        ),
        (
            "RN2) Eigene Zeichenmethoden aufrufen",
            '''        if model.state == AppState.SHUTDOWN_GOODBYE:      # NEU (3.3)
            self._draw_shutdown_goodbye(model)''',
            '''        if model.state == AppState.SHUTDOWN_GOODBYE:      # NEU (3.3)
            self._draw_shutdown_goodbye(model)

        if model.state == AppState.ADMIN_STATUS:          # NEU (4.3)
            self._draw_admin_status(model)

        if model.state == AppState.ADMIN_RESTART_PENDING:  # NEU (4.3)
            self._draw_admin_restart_pending(model)''',
        ),
        (
            "RN3) Zurueck-Button fuer die Diagnoseseite zeichnen",
            '''        elif state == AppState.ADMIN_MENU:
            self._draw_admin_menu_buttons()''',
            '''        elif state == AppState.ADMIN_MENU:
            self._draw_admin_menu_buttons()
        elif state == AppState.ADMIN_STATUS:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
        # ADMIN_RESTART_PENDING: bewusst kein Button - nicht abbrechbar.''',
        ),
        (
            "RN4) Hintergrundfarben fuer die beiden neuen Zustaende",
            '''            AppState.ADMIN_MENU: (18, 22, 30),         # NEU (4.1) - wie PIN_ENTRY
        }[state]''',
            '''            AppState.ADMIN_MENU: (18, 22, 30),         # NEU (4.1) - wie PIN_ENTRY
            AppState.ADMIN_STATUS: (18, 22, 30),       # NEU (4.3) - wie ADMIN_MENU
            AppState.ADMIN_RESTART_PENDING: (20, 40, 20),  # NEU (4.3) - wie CAPTURE_PENDING
        }[state]''',
        ),
        (
            "RN5) Zeichenmethoden fuer Diagnoseseite und Neustart-Screen",
            '''    def _draw_button(self, label: str, rect: pygame.Rect, color: tuple[int, int, int]) -> None:''',
            '''    def _draw_admin_status(self, model: AppModel) -> None:
        # NEU (4.3): einfache Zeilenliste, kein Scrollen noetig - fuenf
        # kurze Zeilen passen bequem zwischen Titel und "Zurueck"-Button.
        width, height = self.config.screen.width, self.config.screen.height
        y = round(0.22 * height)
        line_height = self.font_body.get_linesize() + 14
        if not model.ui.admin_status_lines:
            self._draw_text("Ermittle Status ...", self.font_body, (200, 200, 200), (60, y))
            return
        for line in model.ui.admin_status_lines:
            self._draw_text(line, self.font_body, (230, 230, 230), (60, y))
            y += line_height

    def _draw_admin_restart_pending(self, model: AppModel) -> None:
        # NEU (4.3): grosse, zentrierte Statuszeile - bewusst kein Titel,
        # kein Button (nicht abbrechbar, siehe state_machine.py).
        self._blit_center(
            model.ui.status_text or "App wird neu gestartet ...",
            self.font_status_main_menu,
            (255, 220, 120),
            round(0.45 * self.config.screen.height),
        )

    def _draw_button(self, label: str, rect: pygame.Rect, color: tuple[int, int, int]) -> None:''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("app_with_hw.py", "_collect_admin_status", [
        (
            "AP1) Import admin_diagnostics",
            '''from admin_menu import ADMIN_MENU_ITEMS, build_admin_rects  # NEU (4.1)''',
            '''from admin_menu import ADMIN_MENU_ITEMS, build_admin_rects  # NEU (4.1)
from admin_diagnostics import collect_status_lines  # NEU (4.3)''',
        ),
        (
            "AP2) Startzeitpunkt fuer die Laufzeit-Anzeige merken",
            '''        self._qr_surface: pygame.Surface | None = None
        self.running = True''',
            '''        self._qr_surface: pygame.Surface | None = None
        self.running = True
        # NEU (4.3): Startzeitpunkt fuer die Laufzeit-Anzeige im Status-Screen.
        self._app_start_monotonic = time.monotonic()''',
        ),
        (
            "AP3) Neustart-Zwischenscreen: eigener Timer wie SHUTDOWN_GOODBYE",
            '''        if state == AppState.SHUTDOWN_GOODBYE:
            if not self._power_off_requested and self._due(timers.shutdown_goodbye_deadline, now):
                self._power_off_requested = True
                self.dispatch(AppEvent(EventType.SHUTDOWN_TIMEOUT, source="timer"), now)
            return''',
            '''        if state == AppState.SHUTDOWN_GOODBYE:
            if not self._power_off_requested and self._due(timers.shutdown_goodbye_deadline, now):
                self._power_off_requested = True
                self.dispatch(AppEvent(EventType.SHUTDOWN_TIMEOUT, source="timer"), now)
            return
        # NEU (4.3): kurzer Zwischenscreen vor dem App-Neustart - eigener,
        # nicht abbrechbarer Timer, analog zu SHUTDOWN_GOODBYE.
        if state == AppState.ADMIN_RESTART_PENDING:
            if self._due(timers.admin_restart_deadline, now):
                self.dispatch(AppEvent(EventType.ADMIN_RESTART_TIMEOUT, source="timer"), now)
            return''',
        ),
        (
            "AP4) Idle-Timeout auch fuer die Diagnoseseite",
            '''            # NEU (4.1): Service-Menue schliesst sich nach
            # admin_menu_idle_seconds automatisch (Standard 30s).
            AppState.ADMIN_MENU,
        }''',
            '''            # NEU (4.1): Service-Menue schliesst sich nach
            # admin_menu_idle_seconds automatisch (Standard 30s).
            AppState.ADMIN_MENU,
            # NEU (4.3): Diagnoseseite - gleiches Idle-Verhalten. (Bewusst
            # OHNE ADMIN_RESTART_PENDING - der hat einen eigenen, nicht
            # abbrechbaren Timer, siehe oben.)
            AppState.ADMIN_STATUS,
        }''',
        ),
        (
            "AP5) Aktionen ausfuehren: Status sammeln, App neu starten",
            '''            elif action == "power_off":                      # NEU (3.4)
                self._power_off()''',
            '''            elif action == "power_off":                      # NEU (3.4)
                self._power_off()
            elif action == "collect_admin_status":            # NEU (4.3)
                self._collect_admin_status()
            elif action == "restart_app":                     # NEU (4.3)
                self._restart_app()''',
        ),
        (
            "AP6) Methoden fuer Status-Sammlung und Neustart",
            '''    def _power_off(self) -> None:
        # Scharfes Herunterfahren (Schritt 3.4b). Die App laeuft als root,
        # daher genuegt der direkte Aufruf - die sudoers-Regel muss dafuer
        # NICHT erweitert werden. Ring- und Taster-LED sind in SHUTDOWN_GOODBYE
        # bereits aus, die Kamera ist freigegeben (stop_preview beim Wechsel in
        # den Abschieds-Screen). Der Abschieds-Screen bleibt bewusst stehen
        # (kein self.running = False), bis das System die App beendet.
        print("[App] Fahre Pi herunter (shutdown -h now).")
        try:
            subprocess.Popen(["shutdown", "-h", "now"])
        except Exception as exc:
            # Falls das Kommando nicht ausfuehrbar ist (z.B. nicht als root
            # gestartet), nicht ewig im Abschieds-Screen haengen bleiben.
            print(f"[App] FEHLER beim Herunterfahren: {exc}")
            self.running = False''',
            '''    def _power_off(self) -> None:
        # Scharfes Herunterfahren (Schritt 3.4b). Die App laeuft als root,
        # daher genuegt der direkte Aufruf - die sudoers-Regel muss dafuer
        # NICHT erweitert werden. Ring- und Taster-LED sind in SHUTDOWN_GOODBYE
        # bereits aus, die Kamera ist freigegeben (stop_preview beim Wechsel in
        # den Abschieds-Screen). Der Abschieds-Screen bleibt bewusst stehen
        # (kein self.running = False), bis das System die App beendet.
        print("[App] Fahre Pi herunter (shutdown -h now).")
        try:
            subprocess.Popen(["shutdown", "-h", "now"])
        except Exception as exc:
            # Falls das Kommando nicht ausfuehrbar ist (z.B. nicht als root
            # gestartet), nicht ewig im Abschieds-Screen haengen bleiben.
            print(f"[App] FEHLER beim Herunterfahren: {exc}")
            self.running = False

    def _restart_app(self) -> None:
        # NEU (4.3): "sanfter" Neustart - im Unterschied zu _power_off()
        # wird hier NICHT das Betriebssystem heruntergefahren, sondern nur
        # die App selbst beendet (Exit-Code 0). Die Auto-Restart-Schleife in
        # start_fotobox.sh faengt das ab und startet die App innerhalb
        # weniger Sekunden neu - derselbe Mechanismus wie beim manuellen
        # "sudo pkill -f app_with_hw.py" aus der Notfallkarte.
        print("[App] Neustart angefordert - beende App (Exit-Code 0).")
        self.running = False

    def _collect_admin_status(self) -> None:
        # NEU (4.3): Diagnosezeilen synchron ermitteln (dauert i.d.R. < 1s,
        # hoechstens ein paar Sekunden bei der Kamera-Pruefung) - ausgeloest
        # durch einen bewussten Tap im Service-Menue, daher kein Hintergrund-
        # Thread noetig. Ergebnis kommt als eigenes Event zurueck, damit die
        # State Machine (die keine Hardware kennt) unveraendert bleibt.
        photo_count = len(self.gallery_service.list_photos())
        lines = collect_status_lines(
            photo_dir=self.config.photo_dir,
            photo_count=photo_count,
            app_start_monotonic=self._app_start_monotonic,
        )
        self.dispatch(AppEvent(EventType.ADMIN_STATUS_READY, payload={"lines": lines}, source="diagnostics"))''',
        ),
        (
            "AP7) LED-Effekte fuer die beiden neuen Screens",
            '''        elif state == AppState.ADMIN_MENU:
            # NEU (4.1): ruhige Violett-Blau-Welle. Bewusst ein bereits
            # vorhandener Effekt - klar unterscheidbar vom Amber-Atmen des
            # Hauptmenues, ohne den LedEffect-Enum erweitern zu muessen.
            # Eigene Effekte (rotes Warnblinken beim Loeschen, oranges
            # USB-Blinken, rotierender Teilkreis) folgen in Etappe 3 und 4.
            effect = LedEffect.INSTRUCTIONS_WAVE''',
            '''        elif state == AppState.ADMIN_MENU:
            # NEU (4.1): ruhige Violett-Blau-Welle. Bewusst ein bereits
            # vorhandener Effekt - klar unterscheidbar vom Amber-Atmen des
            # Hauptmenues, ohne den LedEffect-Enum erweitern zu muessen.
            # Eigene Effekte (rotes Warnblinken beim Loeschen, oranges
            # USB-Blinken, rotierender Teilkreis) folgen in Etappe 3 und 4.
            effect = LedEffect.INSTRUCTIONS_WAVE
        elif state == AppState.ADMIN_STATUS:
            # NEU (4.3): gleiche ruhige Welle wie das Menue selbst.
            effect = LedEffect.INSTRUCTIONS_WAVE
        elif state == AppState.ADMIN_RESTART_PENDING:
            # NEU (4.3): gruen wie waehrend der Kamera-Verarbeitung -
            # signalisiert "es passiert gerade etwas", kein neuer Effekt noetig.
            effect = LedEffect.CAPTURE_PROCESSING''',
        ),
        (
            "AP8) Taster-LED auch in den neuen Screens aus",
            '''            # NEU (4.1): im Service-Menue loest der Taster nichts aus.
            AppState.ADMIN_MENU,
            AppState.SHUTDOWN_GOODBYE,   # (PIN_ENTRY jetzt oben separat, 3.5)''',
            '''            # NEU (4.1): im Service-Menue loest der Taster nichts aus.
            AppState.ADMIN_MENU,
            # NEU (4.3): Diagnoseseite und Neustart-Zwischenscreen - gleiche
            # Begruendung wie ADMIN_MENU.
            AppState.ADMIN_STATUS, AppState.ADMIN_RESTART_PENDING,
            AppState.SHUTDOWN_GOODBYE,   # (PIN_ENTRY jetzt oben separat, 3.5)''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("admin_menu.py", "Etappe 2 - implementiert", [
        (
            "AM1) 'Status / Diagnose' aktivieren",
            '''    AdminMenuItem(
        key="status",
        label="Status / Diagnose",
        event_type=EventType.TAP_ADMIN_STATUS,
        color=(0, 100, 150),
        column=0,
        row=0,
        enabled=False,          # Etappe 2
    ),''',
            '''    AdminMenuItem(
        key="status",
        label="Status / Diagnose",
        event_type=EventType.TAP_ADMIN_STATUS,
        color=(0, 100, 150),
        column=0,
        row=0,
        enabled=True,           # Etappe 2 - implementiert
    ),''',
        ),
        (
            "AM2) 'App neu starten' aktivieren",
            '''    AdminMenuItem(
        key="restart_app",
        label="App neu starten",
        event_type=EventType.TAP_ADMIN_RESTART_APP,
        color=(120, 90, 0),
        column=0,
        row=1,
        enabled=False,          # Etappe 2
    ),''',
            '''    AdminMenuItem(
        key="restart_app",
        label="App neu starten",
        event_type=EventType.TAP_ADMIN_RESTART_APP,
        color=(120, 90, 0),
        column=0,
        row=1,
        enabled=True,           # Etappe 2 - implementiert
    ),''',
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

    if not (base / "admin_diagnostics.py").exists():
        fail("admin_diagnostics.py fehlt im Projektverzeichnis. "
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
        py_compile.compile(str(base / "admin_diagnostics.py"), doraise=True)
    except py_compile.PyCompileError as exc:
        print(f"\nSyntaxfehler in admin_diagnostics.py: {exc} - setze alles zurueck ...")
        rollback(written)
        sys.exit(1)

    print("\nFertig. Alle Aenderungen angewendet, Syntax-Check bestanden.")
    print("Backups liegen als *.bak daneben.")
    print("\nNaechster Schritt: python3 -m pytest test_state_machine_admin.py -v")
    print("dann App neu starten und 'Status / Diagnose' + 'App neu starten' testen.")


def rollback(written: list[Path]) -> None:
    for path in written:
        backup = path.with_suffix(path.suffix + ".bak")
        if backup.exists():
            shutil.copy2(backup, path)
            print(f"  zurueckgesetzt: {path.name}")


if __name__ == "__main__":
    main()
