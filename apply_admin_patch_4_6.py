#!/usr/bin/env python3
"""
apply_admin_patch_4_6.py
========================
Etappe 4a des Service-/Admin-Menues: "Bilder auf USB-Stick" - erster
Teil. Stick erkennen, einbinden, Kapazitaet und freien Platz pruefen,
sauber wieder aushaengen. Das Kopieren selbst folgt in Etappe 4b.

Ablauf (entspricht Punkt 1-4 der Vorgabe):
  ADMIN_USB_WAIT    - benoetigten Speicherplatz anzeigen, zum Einstecken
                      auffordern, im Hintergrund nach einem Stick suchen.
                      LED-Ring blinkt orange. "Weiter" wird erst aktiv,
                      wenn tatsaechlich ein Stick erkannt wurde.
  ADMIN_USB_CHECK   - einbinden + messen (Hintergrund-Thread).
  ADMIN_USB_READY   - genug Platz. In 4a endet der Ablauf hier mit
                      "Fertig"; in 4b wird daraus "Export starten".
  ADMIN_USB_PROBLEM - zu klein / zu wenig frei / nicht beschreibbar.
  ADMIN_USB_EJECT   - sync + umount (Hintergrund-Thread).
  ADMIN_USB_REMOVE  - "Stick kann entfernt werden". Nach einem Problem
                      geht es von hier zurueck zum Wartebildschirm
                      (anderer Stick), sonst ins Service-Menue.

Voraussetzung: admin_usb_service.py muss bereits im Projektverzeichnis
liegen; Etappen 4.1, 4.3 und 4.4 muessen angewendet sein. Ausserdem auf
dem Pi einmalig:  sudo apt install exfatprogs ntfs-3g -y

Betrifft states.py, events.py, config.py, models.py, layout.py,
state_machine.py, renderer.py, app_with_hw.py, led_service.py,
hw_led_provider.py und admin_menu.py.

Sicherheitsmechanik wie bisher: Anker genau 1x, alles-oder-nichts,
Backups, py_compile-Selbstcheck mit Rollback, Schutz gegen doppeltes
Anwenden.

Aufruf im Projektverzeichnis:

    python3 apply_admin_patch_4_6.py
"""

from __future__ import annotations

import py_compile
import shutil
import sys
from pathlib import Path


FILES: list[tuple[str, str, list[tuple[str, str, str]]]] = [

    # ------------------------------------------------------------------
    ("states.py", "ADMIN_USB_WAIT = auto()", [
        (
            "ST1) Sechs neue Zustaende fuer den USB-Ablauf",
            '''    ADMIN_DELETE_DONE = auto()''',
            '''    ADMIN_DELETE_DONE = auto()
    # --- USB-Export (Etappe 4a) ---
    # ADMIN_USB_WAIT: zeigt den benoetigten Platz und wartet darauf, dass
    # ein Stick eingesteckt wird (Hintergrund-Suche in app_with_hw).
    ADMIN_USB_WAIT = auto()
    # ADMIN_USB_CHECK: einbinden und messen (Hintergrund-Thread).
    ADMIN_USB_CHECK = auto()
    # ADMIN_USB_READY: Stick geprueft und ausreichend gross/frei.
    ADMIN_USB_READY = auto()
    # ADMIN_USB_PROBLEM: zu klein, zu wenig frei oder nicht beschreibbar.
    ADMIN_USB_PROBLEM = auto()
    # ADMIN_USB_EJECT: sync + umount laeuft (Hintergrund-Thread).
    ADMIN_USB_EJECT = auto()
    # ADMIN_USB_REMOVE: "Stick kann jetzt entfernt werden".
    ADMIN_USB_REMOVE = auto()''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("events.py", "ADMIN_USB_DETECTED = auto()", [
        (
            "EV1) Events des USB-Ablaufs",
            '''    ADMIN_DELETE_FINISHED = auto()''',
            '''    ADMIN_DELETE_FINISHED = auto()
    # --- USB-Export (Etappe 4a) ---
    # Platzbedarf ist berechnet; payload["lines"] enthaelt die Anzeige.
    ADMIN_USB_INFO_READY = auto()
    # Ein Stick wurde gefunden; payload["name"] beschreibt ihn.
    ADMIN_USB_DETECTED = auto()
    # "Weiter" auf dem Warte- bzw. Bereit-Bildschirm.
    TAP_ADMIN_USB_CONTINUE = auto()
    # Pruefung abgeschlossen; payload: ok, too_small, not_enough_free, lines.
    ADMIN_USB_CHECK_DONE = auto()
    # sync + umount abgeschlossen; payload["lines"].
    ADMIN_USB_EJECTED = auto()''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("config.py", "admin_usb_wait_seconds", [
        (
            "CF1) Eigener, laengerer Timeout fuers Einstecken",
            '''    admin_restart_delay_seconds: float = 1.5''',
            '''    admin_restart_delay_seconds: float = 1.5
    # NEU (4.6): Wartebildschirm "Bitte USB-Stick einstecken". Bewusst
    # deutlich laenger als admin_menu_idle_seconds - Stick suchen,
    # Gehaeuse aufklappen und einstecken dauert laenger als 30 Sekunden.
    admin_usb_wait_seconds: float = 120.0''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("models.py", "admin_usb_lines", [
        (
            "MD1) Felder fuer den USB-Ablauf",
            '''    admin_delete_lines: tuple[str, ...] = ()''',
            '''    admin_delete_lines: tuple[str, ...] = ()
    # --- USB-Export (Etappe 4a) ---
    # Anzuzeigende Zeilen des jeweils aktuellen USB-Bildschirms.
    admin_usb_lines: tuple[str, ...] = ()
    # True, sobald ein Stick erkannt wurde - erst dann ist "Weiter" aktiv.
    admin_usb_device_ready: bool = False
    # True, wenn der Ablauf wegen eines Problems (zu klein / zu wenig
    # Platz) endete: nach dem Entfernen geht es dann zurueck zum
    # Wartebildschirm, damit ein anderer Stick probiert werden kann,
    # statt umstaendlich neu durchs Menue zu muessen.
    admin_usb_can_retry: bool = False''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("layout.py", "AppState.ADMIN_USB_WAIT:", [
        (
            "LY1) Buttons der USB-Bildschirme",
            '''    if state == AppState.ADMIN_DELETE_DONE:      # NEU (4.4)
        return {"back": rects.back}
    # ADMIN_DELETE_RUNNING: bewusst leer - nicht abbrechbar.
    return {}''',
            '''    if state == AppState.ADMIN_DELETE_DONE:      # NEU (4.4)
        return {"back": rects.back}
    # --- USB-Export (NEU 4.6) ---
    if state == AppState.ADMIN_USB_WAIT:
        # "Weiter" wird immer gezeichnet, ist aber erst wirksam, sobald ein
        # Stick erkannt wurde (Entscheidung faellt in der State Machine).
        return {"cancel": rects.left, "usb_continue": rects.right}
    if state == AppState.ADMIN_USB_READY:
        return {"cancel": rects.left, "usb_continue": rects.right}
    if state == AppState.ADMIN_USB_PROBLEM:
        return {"usb_continue": rects.right}
    if state == AppState.ADMIN_USB_REMOVE:
        return {"back": rects.back}
    # ADMIN_DELETE_RUNNING, ADMIN_USB_CHECK, ADMIN_USB_EJECT:
    # bewusst leer - laufende Vorgaenge sind nicht abbrechbar.
    return {}''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("state_machine.py", "_handle_admin_usb_wait", [
        (
            "SM1) Menuepunkt 'Bilder auf USB-Stick' verdrahten",
            '''        if event.type in {EventType.TAP_BACK, EventType.IDLE_TIMEOUT}:
            return self._go_main_menu(model, now)
        # Noch nicht implementierter Menuepunkt (Etappe 4): Zustand
        # unveraendert lassen, aber den Idle-Timer neu aufziehen - ein
        # Fehlgriff soll das Menue nicht vorzeitig schliessen.
        if event.type in {
            EventType.TAP_ADMIN_USB_EXPORT,
        }:
            timers = replace(
                model.timers,
                idle_deadline=now + self.config.timeouts.admin_menu_idle_seconds,
            )
            return TransitionResult(model=model.evolve(timers=timers))''',
            '''        if event.type == EventType.TAP_ADMIN_USB_EXPORT:      # NEU (4.6)
            return self._go_admin_usb_wait(model, now)
        if event.type in {EventType.TAP_BACK, EventType.IDLE_TIMEOUT}:
            return self._go_main_menu(model, now)''',
        ),
        (
            "SM2) Handler der sechs USB-Zustaende",
            '''    def _handle_admin_delete_done(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:''',
            '''    # --- USB-Export (NEU 4.6) ---

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
            return self._go_admin_usb_problem(model, now, lines)
        return TransitionResult(model=model)

    def _handle_admin_usb_ready(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        # In Etappe 4a fuehren beide Wege zum Aushaengen; in 4b wird aus
        # TAP_ADMIN_USB_CONTINUE der Start des Kopiervorgangs.
        if event.type in {
            EventType.TAP_ADMIN_USB_CONTINUE,
            EventType.TAP_CANCEL,
            EventType.TAP_BACK,
            EventType.IDLE_TIMEOUT,
        }:
            return self._go_admin_usb_eject(model, now, can_retry=False)
        return TransitionResult(model=model)

    def _handle_admin_usb_problem(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {
            EventType.TAP_ADMIN_USB_CONTINUE,
            EventType.TAP_CANCEL,
            EventType.TAP_BACK,
            EventType.IDLE_TIMEOUT,
        }:
            # can_retry=True: nach dem Entfernen zurueck zum Wartebildschirm,
            # damit direkt ein anderer Stick probiert werden kann.
            return self._go_admin_usb_eject(model, now, can_retry=True)
        return TransitionResult(model=model)

    def _handle_admin_usb_eject(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type == EventType.ADMIN_USB_EJECTED:
            return self._go_admin_usb_remove(model, now, tuple(event.payload.get("lines", ())))
        return TransitionResult(model=model)

    def _handle_admin_usb_remove(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {EventType.TAP_BACK, EventType.TAP_CANCEL, EventType.IDLE_TIMEOUT}:
            if model.ui.admin_usb_can_retry:
                return self._go_admin_usb_wait(model, now)
            return self._go_admin_menu(model, now)
        return TransitionResult(model=model)

    def _handle_admin_delete_done(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:''',
        ),
        (
            "SM3) Uebergangs-Methoden der USB-Zustaende",
            '''    def _go_admin_restart_pending(self, model: AppModel, now: float) -> TransitionResult:''',
            '''    # --- USB-Export (NEU 4.6) ---

    def _go_admin_usb_wait(self, model: AppModel, now: float) -> TransitionResult:
        ui = replace(
            model.ui,
            status_text="Bilder auf USB-Stick",
            error_text=None,
            admin_usb_lines=("Ermittle benötigten Speicherplatz ...",),
            admin_usb_device_ready=False,
            admin_usb_can_retry=False,
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

    def _go_admin_usb_ready(self, model: AppModel, now: float, lines: tuple[str, ...]) -> TransitionResult:
        ui = replace(model.ui, status_text="USB-Stick bereit", error_text=None, admin_usb_lines=lines)
        timers = replace(model.timers, idle_deadline=now + self.config.timeouts.admin_menu_idle_seconds)
        return TransitionResult(model=model.evolve(state=AppState.ADMIN_USB_READY, ui=ui, timers=timers))

    def _go_admin_usb_problem(self, model: AppModel, now: float, lines: tuple[str, ...]) -> TransitionResult:
        ui = replace(model.ui, status_text="USB-Stick nicht verwendbar", error_text=None, admin_usb_lines=lines)
        timers = replace(model.timers, idle_deadline=now + self.config.timeouts.admin_menu_idle_seconds)
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
        timers = replace(model.timers, idle_deadline=now + self.config.timeouts.admin_menu_idle_seconds)
        return TransitionResult(model=model.evolve(state=AppState.ADMIN_USB_REMOVE, ui=ui, timers=timers))

    def _go_admin_restart_pending(self, model: AppModel, now: float) -> TransitionResult:''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("renderer.py", "_draw_admin_usb_lines", [
        (
            "RN1) Titel fuer die USB-Bildschirme",
            '''            AppState.ADMIN_DELETE_DONE,                                                  # NEU (4.4)
        }''',
            '''            AppState.ADMIN_DELETE_DONE,                                                  # NEU (4.4)
            AppState.ADMIN_USB_WAIT, AppState.ADMIN_USB_CHECK, AppState.ADMIN_USB_READY, # NEU (4.6)
            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_EJECT, AppState.ADMIN_USB_REMOVE,
        }''',
        ),
        (
            "RN2) Ueberschrift der USB-Bildschirme",
            '''        elif model.state == AppState.ADMIN_DELETE_DONE:
            # NEU (4.4): Ergebnis der Loeschung.
            self._draw_text("Löschen abgeschlossen", self.font_title, (255, 255, 255), (60, 60))''',
            '''        elif model.state == AppState.ADMIN_DELETE_DONE:
            # NEU (4.4): Ergebnis der Loeschung.
            self._draw_text("Löschen abgeschlossen", self.font_title, (255, 255, 255), (60, 60))
        elif model.state in {
            AppState.ADMIN_USB_WAIT, AppState.ADMIN_USB_READY,
            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_REMOVE,
        }:
            # NEU (4.6): der jeweilige Schrittname steht in ui.status_text -
            # eine Ueberschrift fuer alle vier Bildschirme, kein Sonderfall
            # je Zustand.
            self._draw_text(model.ui.status_text, self.font_title, (255, 255, 255), (60, 60))''',
        ),
        (
            "RN3) Zeichenmethoden aufrufen",
            '''        if model.state == AppState.ADMIN_DELETE_DONE:      # NEU (4.4)
            self._draw_admin_delete_done(model)''',
            '''        if model.state == AppState.ADMIN_DELETE_DONE:      # NEU (4.4)
            self._draw_admin_delete_done(model)

        if model.state in {                                # NEU (4.6)
            AppState.ADMIN_USB_WAIT, AppState.ADMIN_USB_READY,
            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_REMOVE,
        }:
            self._draw_admin_usb_lines(model)

        if model.state in {AppState.ADMIN_USB_CHECK, AppState.ADMIN_USB_EJECT}:  # NEU (4.6)
            self._draw_admin_usb_busy(model)''',
        ),
        (
            "RN4) Buttons der USB-Bildschirme",
            '''        elif state == AppState.ADMIN_DELETE_DONE:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))''',
            '''        elif state == AppState.ADMIN_DELETE_DONE:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
        elif state == AppState.ADMIN_USB_WAIT:
            # NEU (4.6): "Weiter" bleibt ausgegraut, solange kein Stick
            # erkannt wurde - dieselbe Bedingung, die auch die State
            # Machine prueft (_handle_admin_usb_wait).
            ready = self._usb_continue_enabled
            self._draw_button("Abbrechen", self.layout.left, (100, 100, 100))
            self._draw_button("Weiter", self.layout.right, (0, 130, 110) if ready else (55, 55, 60))
        elif state == AppState.ADMIN_USB_READY:
            self._draw_button("Abbrechen", self.layout.left, (100, 100, 100))
            self._draw_button("Fertig", self.layout.right, (0, 130, 110))
        elif state == AppState.ADMIN_USB_PROBLEM:
            self._draw_button("Weiter", self.layout.right, (120, 90, 0))
        elif state == AppState.ADMIN_USB_REMOVE:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))''',
        ),
        (
            "RN5) Hintergrundfarben der USB-Bildschirme",
            '''            AppState.ADMIN_DELETE_DONE: (18, 22, 30),
        }[state]''',
            '''            AppState.ADMIN_DELETE_DONE: (18, 22, 30),
            # NEU (4.6): gedecktes Blaugruen - klar unterscheidbar vom
            # Rot der Loeschwege, gleiche Ruhe wie die uebrigen Admin-Screens.
            AppState.ADMIN_USB_WAIT: (12, 28, 28),
            AppState.ADMIN_USB_CHECK: (12, 28, 28),
            AppState.ADMIN_USB_READY: (10, 32, 26),
            AppState.ADMIN_USB_PROBLEM: (45, 32, 8),
            AppState.ADMIN_USB_EJECT: (12, 28, 28),
            AppState.ADMIN_USB_REMOVE: (10, 32, 26),
        }[state]''',
        ),
        (
            "RN6) Zeichenmethoden der USB-Bildschirme",
            '''    def _draw_admin_delete_confirm(self, model: AppModel) -> None:''',
            '''    # NEU (4.6): merkt sich fuer _draw_buttons, ob "Weiter" aktiv sein
    # darf. Wird in render() aus dem Modell gesetzt - _draw_buttons
    # bekommt nur den Zustand uebergeben, nicht das Modell.
    _usb_continue_enabled: bool = False

    def _draw_admin_usb_lines(self, model: AppModel) -> None:
        # NEU (4.6): Zeilenliste wie bei Diagnose und Loesch-Ergebnis.
        self._usb_continue_enabled = model.ui.admin_usb_device_ready
        height = self.config.screen.height
        y = round(0.22 * height)
        line_height = self.font_body.get_linesize() + 14
        for line in model.ui.admin_usb_lines:
            self._draw_text(line, self.font_body, (230, 230, 230), (60, y))
            y += line_height

    def _draw_admin_usb_busy(self, model: AppModel) -> None:
        # NEU (4.6): laufender Vorgang - zentrierter Hinweis, kein Button.
        self._blit_center(
            model.ui.status_text or "Bitte warten ...",
            self.font_status_main_menu, (200, 235, 225),
            round(0.45 * self.config.screen.height),
        )

    def _draw_admin_delete_confirm(self, model: AppModel) -> None:''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("app_with_hw.py", "_poll_usb_detect", [
        (
            "AP1) Import admin_usb_service",
            '''from admin_delete_service import delete_all_photos  # NEU (4.4)''',
            '''from admin_delete_service import delete_all_photos  # NEU (4.4)
import admin_usb_service  # NEU (4.6)''',
        ),
        (
            "AP2) Zustandsvariablen des USB-Ablaufs",
            '''        self._delete_thread: threading.Thread | None = None
        self._delete_result = None''',
            '''        self._delete_thread: threading.Thread | None = None
        self._delete_result = None
        # NEU (4.6): USB-Export. Gleiches Muster wie beim Loeschlauf -
        # ein Hintergrund-Thread setzt am Ende genau eine Referenz, der
        # Hauptloop pollt sie in _emit_due_timers.
        self._usb_thread: threading.Thread | None = None
        self._usb_job_result = None
        self._usb_partition = None      # zuletzt erkannter Stick
        self._usb_stick = None          # eingebundener Stick (MountedStick)
        self._usb_required_bytes = 0
        self._usb_next_scan = 0.0       # Drosselung der Stick-Suche
        self._usb_info_lines: tuple[str, ...] = ()   # Grundtext des Wartebildschirms
        self._usb_unusable_reported = False          # Hinweis nur einmal zeigen''',
        ),
        (
            "AP3) Hintergrundjobs und Stick-Suche pollen",
            '''        if state == AppState.ADMIN_DELETE_RUNNING:
            result = self._delete_result''',
            '''        # NEU (4.6): laufende USB-Jobs (Pruefen / Auswerfen). Beide sind
        # bewusst nicht abbrechbar, daher wie beim Loeschlauf mit return.
        if state in {AppState.ADMIN_USB_CHECK, AppState.ADMIN_USB_EJECT}:
            job = self._usb_job_result
            if job is not None:
                self._usb_job_result = None
                self._usb_thread = None
                event_type = (
                    EventType.ADMIN_USB_CHECK_DONE
                    if state == AppState.ADMIN_USB_CHECK
                    else EventType.ADMIN_USB_EJECTED
                )
                self.dispatch(AppEvent(event_type, payload=job, source="usb"), now)
            return

        # NEU (4.6): Wartebildschirm - alle 1.5s nach einem Stick suchen.
        # KEIN return: der Wartebildschirm braucht zusaetzlich den
        # Idle-Timeout weiter unten.
        if state == AppState.ADMIN_USB_WAIT:
            self._poll_usb_detect(now)

        if state == AppState.ADMIN_DELETE_RUNNING:
            result = self._delete_result''',
        ),
        (
            "AP4) Idle-Timeout fuer die USB-Bildschirme",
            '''            AppState.ADMIN_DELETE_CONFIRM,
''',
            '''            AppState.ADMIN_DELETE_CONFIRM,
            # NEU (4.6): USB-Bildschirme mit Timeout. Bewusst OHNE
            # ADMIN_USB_CHECK und ADMIN_USB_EJECT - dort laeuft ein Job,
            # der nicht unterbrochen werden darf (idle_deadline ist dort
            # ohnehin None).
            AppState.ADMIN_USB_WAIT, AppState.ADMIN_USB_READY,
            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_REMOVE,
''',
        ),
        (
            "AP5) Aktionen des USB-Ablaufs",
            '''            elif action == "start_delete_all":                # NEU (4.4)
                self._start_delete_all()''',
            '''            elif action == "start_delete_all":                # NEU (4.4)
                self._start_delete_all()
            elif action == "usb_prepare":                     # NEU (4.6)
                self._usb_prepare()
            elif action == "usb_check":                       # NEU (4.6)
                self._usb_start_check()
            elif action == "usb_eject":                       # NEU (4.6)
                self._usb_start_eject()''',
        ),
        (
            "AP6) Methoden des USB-Ablaufs",
            '''    def _start_delete_all(self) -> None:''',
            '''    # --- USB-Export (NEU 4.6) ---

    def _usb_prepare(self) -> None:
        """Platzbedarf ermitteln und anzeigen. Laeuft synchron - es werden
        nur Dateigroessen addiert, das dauert auch bei mehreren hundert
        Fotos nur Millisekunden."""
        self._usb_partition = None
        self._usb_stick = None
        self._usb_next_scan = 0.0
        self._usb_unusable_reported = False
        count, net, gross = admin_usb_service.required_export_bytes(
            self.config.photo_dir, self.config.gallery.excluded_filenames,
        )
        self._usb_required_bytes = gross
        if count == 0:
            lines = (
                "Es sind keine Bilder zum Exportieren vorhanden.",
                "Bitte mit \\"Abbrechen\\" zurück ins Service-Menü.",
            )
        else:
            lines = (
                f"Zu exportieren: {count} Bilder ({admin_usb_service.format_bytes(net)})",
                f"Benötigter Platz auf dem Stick: {admin_usb_service.format_bytes(gross)}",
                "",
                "Bitte einen USB-Stick mit ausreichend freiem Speicher",
                "in den USB-Port links am Gehäuse einstecken.",
            )
        self._usb_info_lines = lines
        self.dispatch(AppEvent(EventType.ADMIN_USB_INFO_READY, payload={"lines": lines}, source="usb"))

    def _poll_usb_detect(self, now: float) -> None:
        """Alle 1.5s nach einem Wechseldatentraeger suchen. Gedrosselt,
        weil lsblk sonst 30x pro Sekunde aufgerufen wuerde."""
        if self._usb_partition is not None:
            return  # bereits gefunden
        if now < self._usb_next_scan:
            return
        self._usb_next_scan = now + 1.5
        partitions = admin_usb_service.find_usb_partitions()
        if not partitions:
            return
        # Nicht einfach die erste Partition nehmen: ein bootfaehiger
        # Installationsstick bringt eine grosse read-only-ISO-Partition und
        # eine winzige EFI-Partition mit - beide waeren die falsche Wahl
        # (siehe admin_usb_service.pick_best_partition).
        partition = admin_usb_service.pick_best_partition(partitions, self._usb_required_bytes)
        if partition is None:
            # Nur schreibgeschuetzte Datentraeger angeschlossen. Einmalig
            # melden, damit der Wartebildschirm nicht wortlos weiterwartet.
            if not self._usb_unusable_reported:
                self._usb_unusable_reported = True
                self.dispatch(
                    AppEvent(
                        EventType.ADMIN_USB_INFO_READY,
                        payload={"lines": self._usb_info_lines + (
                            "",
                            "Hinweis: Der angeschlossene Datenträger ist",
                            "schreibgeschützt (z.B. ein Boot-Stick) und kann",
                            "nicht verwendet werden.",
                        )},
                        source="usb",
                    ),
                    now,
                )
            return
        self._usb_partition = partition
        print(f"[App] USB-Stick erkannt: {partition.device} ({partition.fstype})")
        self.dispatch(
            AppEvent(EventType.ADMIN_USB_DETECTED, payload={"name": partition.display_name()}, source="usb"),
            now,
        )

    def _usb_start_check(self) -> None:
        partition = self._usb_partition
        if partition is None:
            # Sollte nicht vorkommen (Weiter ist ohne Stick wirkungslos),
            # darf den Bildschirm aber nicht haengen lassen.
            self._usb_job_result = {"ok": False, "lines": ("Kein USB-Stick mehr erkannt.",)}
            return

        def worker() -> None:
            try:
                check = admin_usb_service.check_stick_for_export(
                    partition, self._usb_required_bytes,
                )
                self._usb_stick = check.stick
                payload = {
                    "ok": check.ok,
                    "too_small": check.too_small,
                    "not_enough_free": check.not_enough_free,
                    "lines": check.lines,
                }
            except Exception as exc:
                print(f"[App] FEHLER bei der USB-Pruefung: {exc}")
                payload = {"ok": False, "lines": ("Fehler bei der Prüfung des USB-Sticks.", str(exc)[:70])}
            self._usb_job_result = payload

        self._usb_thread = threading.Thread(target=worker, name="usb-check", daemon=True)
        self._usb_thread.start()

    def _usb_start_eject(self) -> None:
        stick = self._usb_stick

        def worker() -> None:
            lines: tuple[str, ...]
            if stick is None:
                lines = ("Der USB-Stick kann entfernt werden.",)
            else:
                try:
                    ok, message = admin_usb_service.unmount(stick.mountpoint)
                    if ok:
                        lines = (
                            "Der USB-Stick wurde sicher ausgeworfen.",
                            "Er kann jetzt abgezogen werden.",
                        )
                    else:
                        # Ehrlich bleiben: ein fehlgeschlagenes umount darf
                        # nicht als "sicher" gemeldet werden.
                        lines = (
                            "ACHTUNG: Der Stick konnte nicht ausgehängt werden.",
                            message,
                            "Bitte noch einige Sekunden warten, bevor er abgezogen wird.",
                        )
                except Exception as exc:
                    print(f"[App] FEHLER beim Auswerfen: {exc}")
                    lines = ("Fehler beim Auswerfen des USB-Sticks.", str(exc)[:70])
            self._usb_stick = None
            self._usb_partition = None
            self._usb_job_result = {"lines": lines}

        self._usb_thread = threading.Thread(target=worker, name="usb-eject", daemon=True)
        self._usb_thread.start()

    def _start_delete_all(self) -> None:''',
        ),
        (
            "AP7) Klick-Zuordnung fuer 'Weiter'",
            '''            "admin_delete_abort":   AppEvent(EventType.TAP_ADMIN_DELETE_ABORT, source="touch"),''',
            '''            "admin_delete_abort":   AppEvent(EventType.TAP_ADMIN_DELETE_ABORT, source="touch"),
            # NEU (4.6): "Weiter" der USB-Bildschirme.
            "usb_continue":   AppEvent(EventType.TAP_ADMIN_USB_CONTINUE, source="touch"),''',
        ),
        (
            "AP8) LED-Effekte der USB-Bildschirme",
            '''        elif state == AppState.ADMIN_DELETE_DONE:
            # NEU (4.4): zurueck zur ruhigen Welle - die Gefahr ist vorbei.
            effect = LedEffect.INSTRUCTIONS_WAVE''',
            '''        elif state == AppState.ADMIN_DELETE_DONE:
            # NEU (4.4): zurueck zur ruhigen Welle - die Gefahr ist vorbei.
            effect = LedEffect.INSTRUCTIONS_WAVE
        elif state == AppState.ADMIN_USB_WAIT:
            # NEU (4.6): oranges Blinken als Aufforderung, den Stick
            # einzustecken (eigener Effekt, siehe led_service.py).
            effect = LedEffect.ADMIN_USB_WAIT
        elif state in {AppState.ADMIN_USB_CHECK, AppState.ADMIN_USB_EJECT}:
            # NEU (4.6): "es passiert gerade etwas" - wie beim Neustart.
            effect = LedEffect.CAPTURE_PROCESSING
        elif state == AppState.ADMIN_USB_PROBLEM:
            # NEU (4.6): gelbes Atmen - Aufmerksamkeit, aber keine Stoerung.
            effect = LedEffect.REVIEW_BREATHE
        elif state in {AppState.ADMIN_USB_READY, AppState.ADMIN_USB_REMOVE}:
            effect = LedEffect.INSTRUCTIONS_WAVE''',
        ),
        (
            "AP9) Taster-LED in den USB-Bildschirmen aus",
            '''            AppState.ADMIN_DELETE_CONFIRM, AppState.ADMIN_DELETE_RUNNING,
            AppState.ADMIN_DELETE_DONE,''',
            '''            AppState.ADMIN_DELETE_CONFIRM, AppState.ADMIN_DELETE_RUNNING,
            AppState.ADMIN_DELETE_DONE,
            # NEU (4.6): auch im gesamten USB-Ablauf darf der Taster nichts
            # ausloesen.
            AppState.ADMIN_USB_WAIT, AppState.ADMIN_USB_CHECK, AppState.ADMIN_USB_READY,
            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_EJECT, AppState.ADMIN_USB_REMOVE,''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("led_service.py", "ADMIN_USB_WAIT", [
        (
            "LS1) Neuer LedEffect fuer die Einsteck-Aufforderung",
            '''    ADMIN_DELETE_WARN = auto()''',
            '''    ADMIN_DELETE_WARN = auto()
    # NEU (4.6): oranges Blinken waehrend "Bitte USB-Stick einstecken".
    # Auffordernd, aber nicht alarmierend - deutlich langsamer als der
    # Fehlerblitz und in warmem Orange statt Rot.
    ADMIN_USB_WAIT = auto()''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("hw_led_provider.py", "ADMIN_USB_WAIT", [
        (
            "HW1) Render-Zweig fuer das orange Blinken",
            '''            elif effect == LedEffect.ADMIN_DELETE_WARN:''',
            '''            elif effect == LedEffect.ADMIN_USB_WAIT:
                # NEU (4.6): oranges Blinken (ca. 1.5 Hz) als Aufforderung,
                # den Stick einzustecken. Hartes An/Aus statt weichem
                # Pulsieren - es soll auffallen, waehrend der Blick
                # womoeglich am USB-Port und nicht am Bildschirm ist.
                on = int(now * 3.0) % 2 == 0
                self._fill((255, 120, 0) if on else (25, 12, 0))
                time.sleep(0.02)

            elif effect == LedEffect.ADMIN_DELETE_WARN:''',
        ),
        (
            "HW2) Effekt im manuellen Schnelltest verfuegbar machen",
            '''        "delete_warn":      LedEffect.ADMIN_DELETE_WARN,   # NEU (4.4)''',
            '''        "delete_warn":      LedEffect.ADMIN_DELETE_WARN,   # NEU (4.4)
        "usb_wait":         LedEffect.ADMIN_USB_WAIT,      # NEU (4.6)''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("admin_menu.py", "Etappe 4a - implementiert", [
        (
            "AM1) 'Bilder auf USB-Stick' aktivieren",
            '''        enabled=False,          # Etappe 4''',
            '''        enabled=True,           # Etappe 4a - implementiert''',
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

    if not (base / "admin_usb_service.py").exists():
        fail("admin_usb_service.py fehlt im Projektverzeichnis. "
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
        py_compile.compile(str(base / "admin_usb_service.py"), doraise=True)
    except py_compile.PyCompileError as exc:
        print(f"\nSyntaxfehler in admin_usb_service.py: {exc} - setze alles zurueck ...")
        rollback(written)
        sys.exit(1)

    print("\nFertig. Alle Aenderungen angewendet, Syntax-Check bestanden.")
    print("Backups liegen als *.bak daneben.")
    print("\nNaechster Schritt:")
    print("  python3 -m pytest test_state_machine_admin.py test_admin_usb_service.py -v")
    print("  sudo python3 hw_led_provider.py usb_wait     # oranges Blinken pruefen")
    print("  sudo pkill -f app_with_hw.py")


def rollback(written: list[Path]) -> None:
    for path in written:
        backup = path.with_suffix(path.suffix + ".bak")
        if backup.exists():
            shutil.copy2(backup, path)
            print(f"  zurueckgesetzt: {path.name}")


if __name__ == "__main__":
    main()
