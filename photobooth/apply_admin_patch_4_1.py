#!/usr/bin/env python3
"""
apply_admin_patch_4_1.py
========================
Etappe 1 des Service-/Admin-Menues:

  - Neuer Zustand AppState.ADMIN_MENU.
  - Die Wartungs-PIN fuehrt nicht mehr direkt zum Herunterfahren, sondern
    ins Service-Menue. Eine PIN-Huerde schuetzt damit ALLE Wartungs-
    funktionen (auch den spaeteren USB-Export).
  - Menue mit 6 Punkten (2 Spalten x 3 Zeilen), definiert in admin_menu.py.
    Funktionsfaehig in dieser Etappe: "Herunterfahren" und "Zurueck".
    Die uebrigen Punkte werden ausgegraut gezeichnet und reagieren nicht.
  - Idle-Timeout von 30 Sekunden zurueck ins Hauptmenue.
  - Hardware-Taster loest im Service-Menue nichts aus, Taster-LED bleibt aus.

Voraussetzung: admin_menu.py muss bereits im Projektverzeichnis liegen.

Betrifft states.py, events.py, config.py, state_machine.py, renderer.py
und app_with_hw.py.

Sicherheitsmechanik (wie apply_led_patch_3_5.py):
  - Jeder Anker muss GENAU EINMAL vorkommen, sonst Abbruch OHNE Schreiben.
  - Alles-oder-nichts ueber ALLE Dateien.
  - Backups (*.bak).
  - py_compile-Selbstcheck; bei Fehler Rollback aller Dateien.
  - Bereits gepatchte Dateien werden am Marker erkannt und fuehren zum
    Abbruch (kein doppeltes Anwenden).

Aufruf im Projektverzeichnis:

    python3 apply_admin_patch_4_1.py
    # oder mit Basisverzeichnis:
    python3 apply_admin_patch_4_1.py /home/photobox/photobooth
"""

from __future__ import annotations

import py_compile
import shutil
import sys
from pathlib import Path


# Struktur: (Dateiname, Marker fuer "schon gepatcht", [(Name, alt, neu), ...])
FILES: list[tuple[str, str, list[tuple[str, str, str]]]] = [

    # ------------------------------------------------------------------
    ("states.py", "ADMIN_MENU = auto()", [
        (
            "ST1) Neuer Zustand ADMIN_MENU",
            '''    SHUTDOWN_GOODBYE = auto()''',
            '''    SHUTDOWN_GOODBYE = auto()
    # --- Service-/Admin-Menue (Schritt 4) ---
    # ADMIN_MENU: erscheint nach korrekt eingegebener Wartungs-PIN und
    # buendelt alle Wartungsfunktionen (Status, USB-Export, alle Bilder
    # loeschen, App-Neustart, Herunterfahren). Die PIN schuetzt bewusst
    # das gesamte Menue statt einzelner Punkte - siehe admin_menu.py.
    ADMIN_MENU = auto()''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("events.py", "TAP_ADMIN_SHUTDOWN", [
        (
            "EV1) Events der Menuepunkte",
            '''    SHUTDOWN_TIMEOUT = auto()''',
            '''    SHUTDOWN_TIMEOUT = auto()
    # --- Service-/Admin-Menue (Schritt 4) ---
    # Antippen der einzelnen Menuepunkte. Welcher Button welches Event
    # ausloest, steht in admin_menu.py (ADMIN_MENU_ITEMS), nicht hier.
    # "Zurueck" nutzt bewusst das bestehende TAP_BACK.
    TAP_ADMIN_STATUS = auto()
    TAP_ADMIN_USB_EXPORT = auto()
    TAP_ADMIN_DELETE_ALL = auto()
    TAP_ADMIN_RESTART_APP = auto()
    TAP_ADMIN_SHUTDOWN = auto()''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("config.py", "admin_menu_idle_seconds", [
        (
            "CF1) Idle-Timeout des Service-Menues",
            '''    countdown_seconds: tuple[int, ...] = (5, 4, 3, 2, 1)''',
            '''    countdown_seconds: tuple[int, ...] = (5, 4, 3, 2, 1)
    # Service-/Admin-Menue: wird es so lange nicht bedient, geht es
    # automatisch zurueck ins Hauptmenue. Bewusst kurz - das Menue soll
    # waehrend einer Veranstaltung nie versehentlich offen stehen bleiben.
    # ACHTUNG: Bei langlaufenden Aktionen (Loeschen, USB-Export, Etappe 3/4)
    # muss dieser Timer pausiert werden, sonst reisst er die laufende
    # Aktion mittendrin weg.
    admin_menu_idle_seconds: float = 30.0''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("state_machine.py", "_handle_admin_menu", [
        (
            "SM1) PIN fuehrt ins Service-Menue statt direkt zum Shutdown",
            '''        if result == PinResult.ACCEPTED:
            return self._go_shutdown_goodbye(model, now)''',
            '''        if result == PinResult.ACCEPTED:
            # NEU (4.1): Die PIN fuehrt nicht mehr direkt zum Herunterfahren,
            # sondern ins Service-Menue. Damit schuetzt die eine PIN-Huerde
            # alle Wartungsfunktionen - insbesondere den spaeteren USB-Export,
            # der sonst saemtliche Gaestefotos ungeschuetzt kopierbar machte.
            return self._go_admin_menu(model, now)''',
        ),
        (
            "SM2) Handler und Uebergang fuer ADMIN_MENU",
            '''    def _handle_error_screen(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {EventType.ERROR_ACKNOWLEDGED, EventType.TAP_BACK}:
            return self._go_main_menu(model, now)
        return TransitionResult(model=model)''',
            '''    def _handle_error_screen(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
        if event.type in {EventType.ERROR_ACKNOWLEDGED, EventType.TAP_BACK}:
            return self._go_main_menu(model, now)
        return TransitionResult(model=model)

    # NEU (4.1): Service-/Admin-Menue. Erreichbar ausschliesslich ueber
    # Geheim-Geste im Hauptmenue + korrekte Wartungs-PIN.
    def _handle_admin_menu(self, model: AppModel, event: AppEvent, now: float) -> TransitionResult:
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
        return TransitionResult(model=model.evolve(state=AppState.ADMIN_MENU, ui=ui, timers=timers))''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("renderer.py", "_draw_admin_menu_buttons", [
        (
            "RN1) Import admin_menu",
            '''from states import AppState''',
            '''from states import AppState
from admin_menu import ADMIN_MENU_ITEMS, build_admin_rects  # NEU (4.1)''',
        ),
        (
            "RN2) Buttons des Service-Menues zeichnen",
            '''        elif state == AppState.ERROR_SCREEN:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))''',
            '''        elif state == AppState.ERROR_SCREEN:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
        elif state == AppState.ADMIN_MENU:
            self._draw_admin_menu_buttons()''',
        ),
        (
            "RN3) Methode _draw_admin_menu_buttons",
            '''    def _draw_button(self, label: str, rect: pygame.Rect, color: tuple[int, int, int]) -> None:''',
            '''    def _draw_admin_menu_buttons(self) -> None:
        # Beschriftung, Farbe und Position kommen vollstaendig aus
        # admin_menu.ADMIN_MENU_ITEMS - hier wird nichts dupliziert, damit
        # Zeichnung und Treffererkennung (app_with_hw._map_admin_menu_click)
        # nicht auseinanderlaufen koennen.
        # Noch nicht implementierte Punkte (enabled=False) werden dunkelgrau
        # gezeichnet, damit sichtbar ist, dass sie noch nichts tun.
        rects = build_admin_rects(self.config.screen.width, self.config.screen.height)
        for item in ADMIN_MENU_ITEMS:
            color = item.color if item.enabled else (55, 55, 60)
            self._draw_button(item.label, rects[item.key], color)

    def _draw_button(self, label: str, rect: pygame.Rect, color: tuple[int, int, int]) -> None:''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("app_with_hw.py", "_map_admin_menu_click", [
        (
            "AP1) Import admin_menu",
            '''from states import AppState''',
            '''from states import AppState
from admin_menu import ADMIN_MENU_ITEMS, build_admin_rects  # NEU (4.1)''',
        ),
        (
            "AP2) Klick-Zuordnung im Service-Menue",
            '''            return self._map_pin_entry_click(pos)''',
            '''            return self._map_pin_entry_click(pos)

        # NEU (4.1): Service-Menue - Treffererkennung gegen exakt dieselben
        # Rechtecke, die der Renderer zeichnet (admin_menu.build_admin_rects).
        if state == AppState.ADMIN_MENU:
            return self._map_admin_menu_click(pos)''',
        ),
        (
            "AP3) Methode _map_admin_menu_click",
            '''    def _map_pin_entry_click(self, pos: tuple[int, int]) -> AppEvent | None:''',
            '''    def _map_admin_menu_click(self, pos: tuple[int, int]) -> AppEvent | None:
        rects = build_admin_rects(self.config.screen.width, self.config.screen.height)
        for item in ADMIN_MENU_ITEMS:
            if not item.enabled:
                continue  # ausgegraute Punkte reagieren bewusst nicht
            if rects[item.key].collidepoint(pos):
                return AppEvent(item.event_type, source="touch")
        return None

    def _map_pin_entry_click(self, pos: tuple[int, int]) -> AppEvent | None:''',
        ),
        (
            "AP4) Idle-Timeout auch fuer ADMIN_MENU",
            '''            AppState.TERMS,
        }''',
            '''            AppState.TERMS,
            # NEU (4.1): Service-Menue schliesst sich nach
            # admin_menu_idle_seconds automatisch (Standard 30s).
            AppState.ADMIN_MENU,
        }''',
        ),
        (
            "AP5) LED-Effekt fuer das Service-Menue",
            '''        elif state == AppState.ERROR_SCREEN:
            effect = LedEffect.ERROR''',
            '''        elif state == AppState.ERROR_SCREEN:
            effect = LedEffect.ERROR
        elif state == AppState.ADMIN_MENU:
            # NEU (4.1): ruhige Violett-Blau-Welle. Bewusst ein bereits
            # vorhandener Effekt - klar unterscheidbar vom Amber-Atmen des
            # Hauptmenues, ohne den LedEffect-Enum erweitern zu muessen.
            # Eigene Effekte (rotes Warnblinken beim Loeschen, oranges
            # USB-Blinken, rotierender Teilkreis) folgen in Etappe 3 und 4.
            effect = LedEffect.INSTRUCTIONS_WAVE''',
        ),
        (
            "AP6) Taster-LED im Service-Menue aus",
            '''            AppState.PIN_ENTRY, AppState.SHUTDOWN_GOODBYE,''',
            '''            AppState.PIN_ENTRY, AppState.SHUTDOWN_GOODBYE, AppState.ADMIN_MENU,''',
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

    if not (base / "admin_menu.py").exists():
        fail("admin_menu.py fehlt im Projektverzeichnis. "
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
        py_compile.compile(str(base / "admin_menu.py"), doraise=True)
    except py_compile.PyCompileError as exc:
        print(f"\nSyntaxfehler in admin_menu.py: {exc} - setze alles zurueck ...")
        rollback(written)
        sys.exit(1)

    print("\nFertig. Alle Aenderungen angewendet, Syntax-Check bestanden.")
    print("Backups liegen als *.bak daneben.")
    print("\nNaechster Schritt: App neu starten und Geheim-Geste + PIN testen.")


def rollback(written: list[Path]) -> None:
    for path in written:
        backup = path.with_suffix(path.suffix + ".bak")
        if backup.exists():
            shutil.copy2(backup, path)
            print(f"  zurueckgesetzt: {path.name}")


if __name__ == "__main__":
    main()