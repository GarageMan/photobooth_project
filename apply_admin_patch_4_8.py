#!/usr/bin/env python3
"""
apply_admin_patch_4_8.py
========================
Nachbesserungen am USB-Export (nach dem ersten Praxistest):

  1. FORTSCHRITTSBALKEN statt durchlaufender Dateinamen. Die Namen
     wechselten so schnell, dass sie nicht lesbar waren und keinen
     Erkenntniswert hatten. Der Balken laeuft EINMAL von 0 auf 100 %
     ueber beide Phasen (Kopieren 0-50 %, Pruefen 50-100 %) - nicht
     zweimal, was verwirrend waere.
  2. FEHLER WERDEN PROTOKOLLIERT. Bisher stand im Log nur die Anzahl
     ("Fehler: 1"), nicht welche Datei betroffen war. Jede Fehlermeldung
     und jeder Pruefsummenfehler wird jetzt einzeln nach
     data/logs/fotobox.log geschrieben.

Voraussetzung: Etappe 4b (apply_admin_patch_4_7.py) muss angewendet sein.
Ausserdem die aktualisierte admin_usb_export.py ablegen (ohne das nicht
darstellbare Haken-Zeichen).

Betrifft models.py, state_machine.py, renderer.py und app_with_hw.py.

Aufruf im Projektverzeichnis:

    python3 apply_admin_patch_4_8.py
"""

from __future__ import annotations

import py_compile
import shutil
import sys
from pathlib import Path


FILES: list[tuple[str, str, list[tuple[str, str, str]]]] = [

    # ------------------------------------------------------------------
    ("models.py", "admin_usb_progress_fraction", [
        (
            "MD1) Fortschrittswert fuer den Balken",
            '''    admin_usb_export_progress: str = ""''',
            '''    admin_usb_export_progress: str = ""
    # NEU (4.8): Fuellstand des Fortschrittsbalkens, 0.0 bis 1.0.
    # Deckt BEIDE Phasen ab (Kopieren 0.0-0.5, Pruefen 0.5-1.0), damit
    # der Balken einmal durchlaeuft statt zweimal von vorn zu beginnen.
    admin_usb_progress_fraction: float = 0.0''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("state_machine.py", "admin_usb_progress_fraction=0.0", [
        (
            "SM1) Balken beim Start des Exports zuruecksetzen",
            '''        ui = replace(model.ui, status_text="Export läuft ...", error_text=None, admin_usb_export_progress="")''',
            '''        ui = replace(
            model.ui,
            status_text="Export läuft ...",
            error_text=None,
            admin_usb_export_progress="",
            admin_usb_progress_fraction=0.0,   # NEU (4.8)
        )''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("renderer.py", "_draw_progress_bar", [
        (
            "RN1) Fortschrittsbalken statt Dateinamen-Text",
            '''    def _draw_admin_usb_copy(self, model: AppModel) -> None:
        # NEU (4.7): Fortschrittsanzeige waehrend des Kopierlaufs.
        height = self.config.screen.height
        text = model.ui.admin_usb_export_progress or "Export wird vorbereitet ..."
        self._blit_center(text, self.font_status_main_menu, (200, 235, 225), round(0.42 * height))''',
            '''    def _draw_admin_usb_copy(self, model: AppModel) -> None:
        # GEAENDERT (4.8): Fortschrittsbalken statt durchlaufender
        # Dateinamen. Die Namen wechselten zu schnell zum Mitlesen; ein
        # Balken beantwortet die eigentliche Frage ("wie lange noch?")
        # deutlich besser.
        height = self.config.screen.height
        text = model.ui.admin_usb_export_progress or "Export wird vorbereitet ..."
        self._blit_center(text, self.font_status_main_menu, (200, 235, 225), round(0.32 * height))
        self._draw_progress_bar(model.ui.admin_usb_progress_fraction, round(0.48 * height))

    def _draw_progress_bar(self, fraction: float, y: int) -> None:
        """NEU (4.8): waagerechter Fortschrittsbalken, mittig, mit
        Prozentangabe darunter. Bewusst als eigene Methode - der
        Loeschlauf koennte sie spaeter ebenfalls gebrauchen."""
        width, height = self.config.screen.width, self.config.screen.height
        fraction = max(0.0, min(1.0, float(fraction)))

        bar_w = round(0.70 * width)
        bar_h = round(0.070 * height)
        bar_x = (width - bar_w) // 2
        radius = bar_h // 2

        outer = pygame.Rect(bar_x, y, bar_w, bar_h)
        # Hintergrund (leerer Teil)
        pygame.draw.rect(self.screen, (22, 52, 48), outer, border_radius=radius)
        # Gefuellter Teil - Mindestbreite, damit bei 1 % nicht nichts zu sehen ist
        if fraction > 0.0:
            fill_w = max(bar_h, round(bar_w * fraction))
            inner = pygame.Rect(bar_x, y, fill_w, bar_h)
            pygame.draw.rect(self.screen, (0, 185, 110), inner, border_radius=radius)
        # Rahmen
        pygame.draw.rect(self.screen, (90, 145, 135), outer, width=3, border_radius=radius)

        self._blit_center(
            f"{round(fraction * 100)} %", self.font_body, (220, 240, 235), y + bar_h + 20,
        )''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("app_with_hw.py", "admin_usb_progress_fraction=fraction", [
        (
            "AP1) Fortschritt als Zahl statt Dateiname",
            '''            progress = self._usb_export_progress
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
                self.model = self.model.evolve(ui=ui)''',
            '''            progress = self._usb_export_progress
            if progress is not None:
                # GEAENDERT (4.8): Dateinamen wechselten zu schnell zum
                # Mitlesen - stattdessen Phase, Zaehler und ein
                # Fortschrittswert fuer den Balken.
                total = max(1, progress.total_files)
                if progress.phase == "copy":
                    text = f"Bilder werden kopiert ... ({progress.copied_files} von {progress.total_files})"
                    # Kopieren belegt die erste Haelfte des Balkens.
                    fraction = 0.5 * progress.copied_files / total
                elif progress.phase == "verify":
                    text = f"Prüfsummen werden geprüft ... ({progress.verified_files} von {progress.total_files})"
                    # Pruefen die zweite - so laeuft der Balken einmal
                    # durch statt zweimal von vorn zu beginnen.
                    fraction = 0.5 + 0.5 * progress.verified_files / total
                elif progress.phase == "done":
                    text = "Abschluss ..."
                    fraction = 1.0
                else:
                    text = "Export wird vorbereitet ..."
                    fraction = 0.0
                from dataclasses import replace as dc_replace
                ui = dc_replace(
                    self.model.ui,
                    admin_usb_export_progress=text,
                    admin_usb_progress_fraction=fraction,
                )
                self.model = self.model.evolve(ui=ui)''',
        ),
        (
            "AP2) Fehler einzeln ins Log schreiben",
            '''                print(
                    f"[App] Export beendet: {result.copied} kopiert, "
                    f"{result.skipped} uebersprungen, {result.verified} verifiziert, "
                    f"Fehler: {len(result.errors)}, Pruefsummenfehler: {len(result.failed_verify)}"
                )''',
            '''                print(
                    f"[App] Export beendet: {result.copied} kopiert, "
                    f"{result.skipped} uebersprungen, {result.verified} verifiziert, "
                    f"Fehler: {len(result.errors)}, Pruefsummenfehler: {len(result.failed_verify)}"
                )
                # NEU (4.8): bisher stand nur die ANZAHL im Log - welche
                # Datei betroffen war, liess sich nicht nachvollziehen.
                for message in result.errors:
                    print(f"[App]   Exportfehler: {message}")
                for name in result.failed_verify:
                    print(f"[App]   PRUEFSUMMENFEHLER: {name}")''',
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

    print("\nFertig. Alle Aenderungen angewendet, Syntax-Check bestanden.")
    print("Backups liegen als *.bak daneben.")
    print("\nNaechster Schritt:")
    print("  python3 -m pytest test_admin_usb_export.py test_state_machine_admin.py -v")
    print("  sudo pkill -f app_with_hw.py")


def rollback(written: list[Path]) -> None:
    for path in written:
        backup = path.with_suffix(path.suffix + ".bak")
        if backup.exists():
            shutil.copy2(backup, path)
            print(f"  zurueckgesetzt: {path.name}")


if __name__ == "__main__":
    main()
