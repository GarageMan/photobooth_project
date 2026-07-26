#!/usr/bin/env python3
"""
apply_admin_patch_4_9.py
========================
Fortschrittsbalken auch fuer den Loeschvorgang ("Alle Bilder loeschen").

  - Der in 4.8 eingefuehrte Balken wird wiederverwendet, bekommt aber
    einen Farbparameter: gruen beim Export, rot beim Loeschen. Ein
    gruener Balken waehrend einer unwiderruflichen Loeschung waere das
    falsche Signal.
  - Der Balken deckt das Loeschen von data/photos/ UND data/web/ ab
    (0-100 % ueber alle Dateien zusammen).
  - Fuer die Kamera-Loeschung gibt es keinen Zwischenstand - gphoto2
    meldet erst am Ende. Der Balken bleibt dort bewusst stehen und der
    Text wechselt zu "Kamera-Speicherkarte wird geleert ...", statt
    einen erfundenen Fortschritt vorzugaukeln.

Voraussetzung: Etappe 4.8 muss angewendet sein (_draw_progress_bar).
Ausserdem die aktualisierten admin_delete_service.py und
admin_usb_service.py ablegen.

Betrifft models.py, state_machine.py, renderer.py und app_with_hw.py.

Aufruf im Projektverzeichnis:

    python3 apply_admin_patch_4_9.py
"""

from __future__ import annotations

import py_compile
import shutil
import sys
from pathlib import Path


FILES: list[tuple[str, str, list[tuple[str, str, str]]]] = [

    # ------------------------------------------------------------------
    ("models.py", "admin_delete_fraction", [
        (
            "MD1) Fortschrittsfelder fuer den Loeschlauf",
            '''    admin_usb_progress_fraction: float = 0.0''',
            '''    admin_usb_progress_fraction: float = 0.0
    # NEU (4.9): Fortschritt des Loeschlaufs - gleiche Mechanik wie beim
    # Export, damit beide Vorgaenge sich gleich anfuehlen.
    admin_delete_progress: str = ""
    admin_delete_fraction: float = 0.0''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("state_machine.py", "admin_delete_fraction=0.0", [
        (
            "SM1) Balken beim Start des Loeschlaufs zuruecksetzen",
            '''        ui = replace(model.ui, status_text="Bilder werden gelöscht ...", error_text=None)''',
            '''        ui = replace(
            model.ui,
            status_text="Bilder werden gelöscht ...",
            error_text=None,
            admin_delete_progress="",          # NEU (4.9)
            admin_delete_fraction=0.0,         # NEU (4.9)
        )''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("renderer.py", "def _draw_progress_bar(self, fraction: float, y: int, color", [
        (
            "RN1) Balken mit Farbparameter",
            '''    def _draw_progress_bar(self, fraction: float, y: int) -> None:
        """NEU (4.8): waagerechter Fortschrittsbalken, mittig, mit
        Prozentangabe darunter. Bewusst als eigene Methode - der
        Loeschlauf koennte sie spaeter ebenfalls gebrauchen."""
        width, height = self.config.screen.width, self.config.screen.height
        fraction = max(0.0, min(1.0, float(fraction)))''',
            '''    def _draw_progress_bar(
        self,
        fraction: float,
        y: int,
        color: tuple[int, int, int] = (0, 185, 110),
        track: tuple[int, int, int] = (22, 52, 48),
        border: tuple[int, int, int] = (90, 145, 135),
    ) -> None:
        """NEU (4.8): waagerechter Fortschrittsbalken, mittig, mit
        Prozentangabe darunter.

        GEAENDERT (4.9): Farben sind jetzt Parameter - der Loeschlauf nutzt
        denselben Balken in Rot. Ein gruener Balken waehrend einer
        unwiderruflichen Loeschung waere das falsche Signal.
        """
        width, height = self.config.screen.width, self.config.screen.height
        fraction = max(0.0, min(1.0, float(fraction)))''',
        ),
        (
            "RN2) Farben im Balken verwenden",
            '''        outer = pygame.Rect(bar_x, y, bar_w, bar_h)
        # Hintergrund (leerer Teil)
        pygame.draw.rect(self.screen, (22, 52, 48), outer, border_radius=radius)
        # Gefuellter Teil - Mindestbreite, damit bei 1 % nicht nichts zu sehen ist
        if fraction > 0.0:
            fill_w = max(bar_h, round(bar_w * fraction))
            inner = pygame.Rect(bar_x, y, fill_w, bar_h)
            pygame.draw.rect(self.screen, (0, 185, 110), inner, border_radius=radius)
        # Rahmen
        pygame.draw.rect(self.screen, (90, 145, 135), outer, width=3, border_radius=radius)''',
            '''        outer = pygame.Rect(bar_x, y, bar_w, bar_h)
        # Hintergrund (leerer Teil)
        pygame.draw.rect(self.screen, track, outer, border_radius=radius)
        # Gefuellter Teil - Mindestbreite, damit bei 1 % nicht nichts zu sehen ist
        if fraction > 0.0:
            fill_w = max(bar_h, round(bar_w * fraction))
            inner = pygame.Rect(bar_x, y, fill_w, bar_h)
            pygame.draw.rect(self.screen, color, inner, border_radius=radius)
        # Rahmen
        pygame.draw.rect(self.screen, border, outer, width=3, border_radius=radius)''',
        ),
        (
            "RN3) Loesch-Bildschirm mit Balken",
            '''    def _draw_admin_delete_running(self, model: AppModel) -> None:
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
        )''',
            '''    def _draw_admin_delete_running(self, model: AppModel) -> None:
        # GEAENDERT (4.9): Fortschrittsbalken wie beim Export, aber in Rot.
        # Kein Button - die Loeschung ist nicht abbrechbar.
        height = self.config.screen.height
        self._blit_center(
            model.ui.admin_delete_progress or "Bilder werden gelöscht ...",
            self.font_status_main_menu, (255, 210, 210), round(0.32 * height),
        )
        self._draw_progress_bar(
            model.ui.admin_delete_fraction, round(0.48 * height),
            color=(200, 45, 45), track=(58, 20, 20), border=(150, 95, 95),
        )''',
        ),
    ]),

    # ------------------------------------------------------------------
    ("app_with_hw.py", "_delete_progress", [
        (
            "AP1) Import DeleteProgress",
            '''from admin_delete_service import delete_all_photos  # NEU (4.4)''',
            '''from admin_delete_service import DeleteProgress, delete_all_photos  # NEU (4.4/4.9)''',
        ),
        (
            "AP2) Zustandsvariable fuer den Loeschfortschritt",
            '''        self._delete_thread: threading.Thread | None = None
        self._delete_result = None''',
            '''        self._delete_thread: threading.Thread | None = None
        self._delete_result = None
        # NEU (4.9): Fortschritt des Loeschlaufs (Muster wie beim Export).
        self._delete_progress: DeleteProgress | None = None''',
        ),
        (
            "AP3) Loeschfortschritt pollen und anzeigen",
            '''        if state == AppState.ADMIN_DELETE_RUNNING:
            result = self._delete_result''',
            '''        if state == AppState.ADMIN_DELETE_RUNNING:
            # NEU (4.9): Fortschritt in den UI-Zustand uebertragen, damit
            # der Renderer den Balken zeichnen kann.
            progress = self._delete_progress
            if progress is not None:
                total = max(1, progress.total_files)
                if progress.phase == "delete":
                    text = f"Bilder werden gelöscht ... ({progress.deleted_files} von {progress.total_files})"
                    # Dateien belegen 0-90 % - die Kamera braucht den Rest.
                    fraction = 0.90 * progress.deleted_files / total
                elif progress.phase == "camera":
                    text = "Kamera-Speicherkarte wird geleert ..."
                    # Kein Zwischenstand von gphoto2 - Balken bleibt stehen.
                    fraction = 0.90
                elif progress.phase == "report":
                    text = "Löschprotokoll wird geschrieben ..."
                    fraction = 0.97
                elif progress.phase == "done":
                    text = "Abschluss ..."
                    fraction = 1.0
                else:
                    text = "Löschvorgang wird vorbereitet ..."
                    fraction = 0.0
                from dataclasses import replace as dc_replace
                ui = dc_replace(
                    self.model.ui,
                    admin_delete_progress=text,
                    admin_delete_fraction=fraction,
                )
                self.model = self.model.evolve(ui=ui)

            result = self._delete_result''',
        ),
        (
            "AP4) Fortschritt beim Aufraeumen zuruecksetzen",
            '''                self._delete_result = None
                self._delete_thread = None''',
            '''                self._delete_result = None
                self._delete_thread = None
                self._delete_progress = None   # NEU (4.9)''',
        ),
        (
            "AP5) Fortschrittsobjekt an den Loeschlauf uebergeben",
            '''        def worker() -> None:
            try:
                result = delete_all_photos(
                    photo_dir=self.config.photo_dir,
                    web_dir=self.config.web_dir,
                    log_dir=self.config.log_dir,
                    excluded_filenames=self.config.gallery.excluded_filenames,
                    camera_lock=self._camera_lock,
                    delete_from_camera=True,
                )''',
            '''        progress = DeleteProgress()          # NEU (4.9)
        self._delete_progress = progress

        def worker() -> None:
            try:
                result = delete_all_photos(
                    photo_dir=self.config.photo_dir,
                    web_dir=self.config.web_dir,
                    log_dir=self.config.log_dir,
                    excluded_filenames=self.config.gallery.excluded_filenames,
                    camera_lock=self._camera_lock,
                    delete_from_camera=True,
                    progress=progress,          # NEU (4.9)
                )''',
        ),
        (
            "AP6) Loeschfehler einzeln ins Log schreiben",
            '''                if result.report_path is not None:
                    print(f"[App] Loeschprotokoll: {result.report_path}")''',
            '''                if result.report_path is not None:
                    print(f"[App] Loeschprotokoll: {result.report_path}")
                # NEU (4.9): wie beim Export - nicht nur die Anzahl, sondern
                # auch die betroffene Datei ins Log schreiben.
                for message in result.errors:
                    print(f"[App]   Loeschfehler: {message}")''',
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
    print("  python3 -m pytest test_admin_delete_service.py test_state_machine_admin.py -v")
    print("  sudo pkill -f app_with_hw.py")


def rollback(written: list[Path]) -> None:
    for path in written:
        backup = path.with_suffix(path.suffix + ".bak")
        if backup.exists():
            shutil.copy2(backup, path)
            print(f"  zurueckgesetzt: {path.name}")


if __name__ == "__main__":
    main()
