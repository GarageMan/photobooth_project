"""
app_with_hw.py  →  Umbenennen zu app.py auf dem Pi
====================================================
Hauptschleife der Fotobox.
Schaltet per Feature-Flag zwischen echten Hardware-Providern
und Fake-Providern um – ohne die State-Machine zu berühren.

Feature-Flags in config.py:
  use_fake_preview    = True   → FakePreviewService
                        False  → HwGphoto2PreviewProvider (USB/gphoto2)

  use_fake_capture    = True   → FakeCaptureService
                        False  → HwCaptureProvider (GPIO + gphoto2)

  enable_leds         = True   → HwLedProvider (rpi_ws281x)
                        False  → Kein LED-Ausgang (nur LedService intern)

  enable_gpio_button  = True   → HwButtonProvider (RPi.GPIO)
                        False  → Nur Touch / Maus

Für Windows/PC-Test:
  Alle vier Flags auf True / False belassen (Fake-Modus), dann:
    python app_with_hw.py

Für Raspberry Pi (Schritt-für-Schritt):
  1. Fake-Modus: alle Flags False/True wie oben → testen
  2. enable_leds = True          → LED-Ring testen
  3. enable_gpio_button = True   → Taster testen
  4. use_fake_preview = False    → gphoto2-Vorschau testen (USB, kein HDMI mehr nötig)
  5. use_fake_capture = False    → Vollständige Aufnahme testen
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pygame

from camera_capture import CameraCaptureService
from camera_preview import CameraPreviewService
from config import DEFAULT_CONFIG, AppConfig
from events import AppEvent, EventType
from gallery_service import GalleryService
from led_service import LedEffect, LedService
from models import AppModel
from qr_service import QrService
from state_machine import StateMachine
from states import AppState
from admin_menu import ADMIN_MENU_ITEMS, build_admin_rects  # NEU (4.1)
from admin_diagnostics import collect_status_lines  # NEU (4.3)
from admin_delete_service import DeleteProgress, delete_all_photos  # NEU (4.4/4.9)
import admin_usb_service  # NEU (4.6)
from admin_usb_export import (  # NEU (4.7); NEU (6b): apply_conflict_resolutions
    ExportProgress,
    apply_conflict_resolutions,
    clear_stick,
    export_photos,
)
from storage_service import StorageService
from storage_alarm import assess_storage  # NEU (Speicherplatz-Alarm)
from layout import build_layout, button_rects_for_state
from renderer import Renderer
from shutdown_service import PinLockout, SecretGestureDetector  # NEU (3.4)
import subprocess  # NEU (3.4b): fuer das echte Herunterfahren

# ------------------------------------------------------------------------------
# Provider-Auswahl per Feature-Flag
# ------------------------------------------------------------------------------

def _build_preview_provider(config: AppConfig, camera_lock: threading.Lock):
    if config.features.use_fake_preview:
        from fake_preview_service import FakePreviewService
        print("[App] Preview: FakePreviewService (Testmodus)")
        return FakePreviewService(
            width=config.screen.width,
            height=config.screen.height,
        )
    else:
        from hw_gphoto2_preview_provider import HwGphoto2PreviewProvider
        print("[App] Preview: HwGphoto2PreviewProvider (USB/gphoto2)")
        return HwGphoto2PreviewProvider(camera_lock=camera_lock)


def _build_capture_provider(config: AppConfig, camera_lock: threading.Lock):
    if config.features.use_fake_capture:
        from fake_capture_service import FakeCaptureService
        print("[App] Capture: FakeCaptureService (Testmodus)")
        return FakeCaptureService(fixture_dir=config.assets_dir)
    else:
        from hw_capture_provider import HwCaptureProvider
        print("[App] Capture: HwCaptureProvider (GPIO + gphoto2)")
        return HwCaptureProvider(camera_lock=camera_lock, config=config)


def _build_led_provider(config: AppConfig):
    if config.features.enable_leds:
        from hw_led_provider import HwLedProvider
        print("[App] LEDs: HwLedProvider (rpi_ws281x)")
        provider = HwLedProvider()
        provider.start()
        return provider
    else:
        print("[App] LEDs: deaktiviert (Feature-Flag)")
        return None


def _build_button_provider(config: AppConfig, on_press_callback):
    if config.features.enable_gpio_button:
        from hw_button_provider import HwButtonProvider
        print("[App] Button: HwButtonProvider (GPIO 15)")
        provider = HwButtonProvider(on_press_callback=on_press_callback)
        provider.start()
        return provider
    else:
        print("[App] Button: deaktiviert (nur Touch/Maus)")
        return None


# ------------------------------------------------------------------------------
# Haupt-App
# ------------------------------------------------------------------------------

class PhotoboothApp:
    def __init__(self, config: AppConfig = DEFAULT_CONFIG) -> None:
        self.config = config
        config.ensure_directories()

        # State Machine
        self.state_machine = StateMachine(config)
        self.model: AppModel = self.state_machine.initial_model(time.monotonic())

        # Services
        # vorher: self.gallery_service = GalleryService(config.photo_dir)
        self.gallery_service = GalleryService(
            config.photo_dir,
            excluded_filenames=config.gallery.excluded_filenames,
        )
        self.storage_service = StorageService(config.photo_dir, config.web_dir)
        self.storage_service.ensure_directories()
        self.qr_service = QrService(photo_url_prefix=config.network.photo_url_prefix)
        self.led_service = LedService()

        # Pygame
        pygame.init()
        flags = pygame.FULLSCREEN if config.screen.fullscreen else 0
        self.screen = pygame.display.set_mode(
            (config.screen.width, config.screen.height), flags
        )
        pygame.display.set_caption(config.screen.title)
        pygame.mouse.set_visible(not config.screen.hide_mouse)
        self.clock = pygame.time.Clock()
        self.layout = build_layout(config.screen.width, config.screen.height)

        # Hardware-Provider (per Feature-Flag)
        self._led_provider = _build_led_provider(config)
        self._button_provider = _build_button_provider(
            config, on_press_callback=self._on_hardware_button_press
        )
        # Gemeinsames Lock: Preview (gphoto2 capture_preview) und Capture
        # (gphoto2-Download) teilen sich dieselbe Kamera-Verbindung und
        # duerfen nicht gleichzeitig zugreifen.
        camera_lock = threading.Lock()
        # NEU (4.4): auch das Loeschen auf der Kamera-Speicherkarte muss
        # sich dieses Lock teilen - gphoto2 erlaubt nur eine Verbindung.
        self._camera_lock = camera_lock
        preview_provider = _build_preview_provider(config, camera_lock)
        capture_provider = _build_capture_provider(config, camera_lock)

        self.preview_service = CameraPreviewService(provider=preview_provider)
        self.capture_service = CameraCaptureService(
            provider=capture_provider,
            target_dir=config.photo_dir,
        )

        # Renderer
        self.renderer = Renderer(config=config, screen=self.screen)

        # Interne Zustandsvariablen
        self.touch_start_x: int | None = None
        self.touch_start_y: int | None = None
        self._qr_surface: pygame.Surface | None = None
        self.running = True
        # NEU (4.3): Startzeitpunkt fuer die Laufzeit-Anzeige im Status-Screen.
        self._app_start_monotonic = time.monotonic()
        # NEU (Speicherplatz-Alarm): 0.0 sorgt dafuer, dass die allererste
        # Pruefung sofort beim Start laeuft (nicht erst nach
        # check_interval_seconds Wartezeit).
        self._last_storage_check = 0.0
        # NEU (4.4): Hintergrund-Thread fuer das Loeschen aller Bilder.
        # _delete_result wird vom Thread genau einmal gesetzt und vom
        # Hauptloop in _emit_due_timers gepollt - eine einzelne Referenz-
        # zuweisung ist unter dem GIL unteilbar, daher genuegt hier das
        # Fehlen/Vorhandensein des Werts als Fertigsignal (kein Lock noetig).
        self._delete_thread: threading.Thread | None = None
        self._delete_result = None
        # NEU (4.9): Fortschritt des Loeschlaufs (Muster wie beim Export).
        self._delete_progress: DeleteProgress | None = None
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
        self._usb_unusable_reported = False          # Hinweis nur einmal zeigen
        # NEU (4.7): Fortschritt des laufenden Exports. Gleiches Muster wie
        # bei Loeschlauf und Pruefung - der Thread setzt am Ende genau eine
        # Referenz, der Hauptloop pollt sie in _emit_due_timers.
        self._usb_export_progress: ExportProgress | None = None
        self._usb_export_result = None
        # NEU (6b): das ExportResult-Objekt aus Phase 1 (Kopieren) wird
        # hier zwischengehalten, falls Konflikte offen bleiben - Phase 2
        # (usb_apply_resolutions) baut darauf auf, statt bei null Zaehlern
        # neu anzufangen. Die aktuellen Entscheidungen kommen dabei aus
        # dem UI-Zustand (model.ui.admin_usb_conflicts), nicht aus diesem
        # Objekt - der Nutzer kann sie auf dem Konflikt-Screen jederzeit
        # noch aendern, bevor "Ausfuehren" getippt wird.
        self._usb_export_pending_result = None

        # Verstecktes Herunterfahren (Schritt 3.4): PIN-Sperre (persistent)
        # und Geheim-Geste-Detektor. PinLockout lebt hier in der App (nicht
        # in der State Machine), da es eine Datei schreibt; die State
        # Machine bekommt bei PIN_SUBMIT nur das fertige PinResult im Payload.
        self._pin_lockout = PinLockout.from_config(config.shutdown)
        self._gesture_detector = SecretGestureDetector.from_config(
            config.shutdown, config.screen.width, config.screen.height
        )
        # Verhindert wiederholtes Ausloesen des Poweroffs in SHUTDOWN_GOODBYE.
        self._power_off_requested = False

        print("[App] Initialisierung abgeschlossen.")

    # -- Hauptschleife ---------------------------------------------------------

    def run(self) -> None:
        self.dispatch(AppEvent(EventType.APP_STARTED, source="system"))
        try:
            while self.running:
                now = time.monotonic()

                # 1. Events verarbeiten
                for event in pygame.event.get():
                    self._handle_pygame_event(event)

                # 2. Timer-Events auslösen
                self._emit_due_timers(now)

                # 2.5 Speicherplatz periodisch pruefen (Speicherplatz-Alarm)
                self._check_storage(now)

                # 3. LED-Provider synchronisieren
                self._sync_led()

                # 4. Taster-LED synchronisieren (falls Button-Provider aktiv)
                self._sync_button_led()

                # 5. Frame rendern
                fps = self.clock.get_fps()
                preview_frame = self._get_preview_frame()
                self.renderer.render(self.model, fps, preview_frame=preview_frame, qr_surface=self._qr_surface)

                self.clock.tick(self.config.screen.target_fps)

        except KeyboardInterrupt:
            print("\n[App] KeyboardInterrupt - beende...")
        finally:
            self._shutdown()

    # -- Event-Handling ----------------------------------------------------------

    def _handle_pygame_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            self.running = False
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.running = False
            return

        if event.type == pygame.MOUSEBUTTONDOWN:
            # Nur Startposition merken - NICHT sofort einen Klick/Tap ausloesen.
            # Wuerde hier schon gemappt (wie frueher), feuert bei der Galerie
            # ein Tap auf ein Thumbnail sofort TAP_FULLSCREEN_PHOTO, noch bevor
            # erkennbar ist, ob eigentlich ein Swipe (Scrollen) gemeint war -
            # da die Thumbnails fast die gesamte Grid-Flaeche einnehmen, wuerde
            # dadurch praktisch jeder Scroll-Versuch sofort ins Vollbild springen.
            self.touch_start_x = event.pos[0]
            self.touch_start_y = event.pos[1]
            # NEU (3.4): Geheim-Geste nur im Hauptmenue mitschneiden (Down).
            if self.model.state == AppState.MAIN_MENU:
                self._gesture_detector.on_touch_down(event.pos, time.monotonic())
            return

        if event.type == pygame.MOUSEBUTTONUP and self.touch_start_x is not None:
            dx = event.pos[0] - self.touch_start_x
            dy = event.pos[1] - (self.touch_start_y or event.pos[1])
            start_pos = (self.touch_start_x, self.touch_start_y)
            self.touch_start_x = None
            self.touch_start_y = None

            # NEU (3.4): Geheim-Geste nur im Hauptmenue auswerten (Up). Erst
            # die vollstaendige Sequenz loest SHUTDOWN_GESTURE_DETECTED aus;
            # Beruehrungen ausserhalb der unsichtbaren Ecke ignoriert der
            # Detektor selbst.
            if self.model.state == AppState.MAIN_MENU:
                if self._gesture_detector.on_touch_up(event.pos, time.monotonic()):
                    self.dispatch(AppEvent(EventType.SHUTDOWN_GESTURE_DETECTED, source="gesture"))
                    return

            if self.model.state == AppState.GALLERY_FULLSCREEN:
                if dx < -100:
                    self.dispatch(AppEvent(EventType.SWIPE_LEFT, source="touch"))
                    return
                if dx > 100:
                    self.dispatch(AppEvent(EventType.SWIPE_RIGHT, source="touch"))
                    return
            elif self.model.state == AppState.GALLERY_GRID:
                if dy < -80:
                    self.dispatch(AppEvent(EventType.SWIPE_UP, source="touch"))
                    return
                if dy > 80:
                    self.dispatch(AppEvent(EventType.SWIPE_DOWN, source="touch"))
                    return
            elif self.model.state == AppState.INSTRUCTIONS:
                # Scroll-Position lebt nur im Renderer (reine Anzeigesache,
                # siehe renderer.py) - kein Event/State-Machine noetig.
                if dy < -60:
                    self.renderer.instructions_scroll_offset += 150
                    return
                if dy > 60:
                    self.renderer.instructions_scroll_offset = max(
                        0, self.renderer.instructions_scroll_offset - 150
                    )
                    return
            elif self.model.state == AppState.TERMS:
                # Gleiches Prinzip wie INSTRUCTIONS - eigener Scroll-Offset,
                # damit ein Wechsel zwischen beiden Ansichten die jeweils
                # andere Scroll-Position nicht durcheinanderbringt.
                if dy < -60:
                    self.renderer.terms_scroll_offset += 150
                    return
                if dy > 60:
                    self.renderer.terms_scroll_offset = max(
                        0, self.renderer.terms_scroll_offset - 150
                    )
                    return
            elif self.model.state == AppState.ADMIN_USB_CONFLICTS:
                # NEU (6c): gleiches Prinzip wie INSTRUCTIONS/TERMS - reiner
                # Anzeige-Offset im Renderer, kein Event/State-Machine noetig.
                if dy < -60:
                    self.renderer.usb_conflicts_scroll_offset += 150
                    return
                if dy > 60:
                    self.renderer.usb_conflicts_scroll_offset = max(
                        0, self.renderer.usb_conflicts_scroll_offset - 150
                    )
                    return

            # Kein Swipe erkannt -> als normaler Tap an der Startposition werten.
            # Kleine Toleranz (Zittern beim Antippen soll nicht dazu fuehren,
            # dass der Klick knapp neben dem Button "verloren" geht).
            if abs(dx) < 30 and abs(dy) < 30:
                mapped = self._map_click_to_event(start_pos)
                if mapped is not None:
                    self.dispatch(mapped)
            return

        if event.type == pygame.USEREVENT and getattr(event, "subtype", None) == "BUTTON_PRESS":
            # Physischer Taster: eigener Event-Typ, den die State Machine bereits
            # überall dort wie TAP_PHOTO behandelt (Vorwärts-Aktion) - und beim
            # COUNTDOWN zusätzlich zum Abbrechen nutzt.
            self.dispatch(AppEvent(EventType.BUTTON_PRESS, source="hardware_button"))

    def _on_hardware_button_press(self) -> None:
        """Callback vom HwButtonProvider (läuft im GPIO-Thread → thread-sicher)."""
        # pygame.event.post ist thread-sicher
        pygame.event.post(
            pygame.event.Event(
                pygame.USEREVENT,
                {"subtype": "BUTTON_PRESS"},
            )
        )

    def _map_click_to_event(self, pos: tuple[int, int]) -> AppEvent | None:
        state = self.model.state

        if state == AppState.ATTRACT_GALLERY:
            # Kein sichtbarer Button hier - jedes Antippen fuehrt zurueck.
            return AppEvent(EventType.TAP_BACK, source="touch")

        if state == AppState.GALLERY_GRID:
            for rect, index in self.renderer.gallery_thumbnail_hitboxes:
                if rect.collidepoint(pos):
                    return AppEvent(EventType.TAP_FULLSCREEN_PHOTO, payload={"index": index}, source="touch")

        # NEU (6c): Einzelentscheidung je Konfliktzeile - eigene Trefferpruefung,
        # da die Zeilenposition vom Scroll-Offset abhaengt und daher nicht ueber
        # das statische layout.py-Rect-System abgebildet werden kann (gleiches
        # Prinzip wie gallery_thumbnail_hitboxes oben).
        if state == AppState.ADMIN_USB_CONFLICTS:
            for rect, name, decision in self.renderer.usb_conflict_row_hitboxes:
                if rect.collidepoint(pos):
                    return AppEvent(
                        EventType.TAP_ADMIN_USB_CONFLICT_DECISION,
                        payload={"name": name, "decision": decision},
                        source="touch",
                    )

        if state == AppState.PIN_ENTRY:                       # NEU (3.4)
            return self._map_pin_entry_click(pos)

        # NEU (4.1): Service-Menue - Treffererkennung gegen exakt dieselben
        # Rechtecke, die der Renderer zeichnet (admin_menu.build_admin_rects).
        if state == AppState.ADMIN_MENU:
            return self._map_admin_menu_click(pos)

        rects = button_rects_for_state(state, self.layout)

        mapping = {
            "photo":          AppEvent(EventType.TAP_PHOTO, source="touch"),
            "gallery":        AppEvent(EventType.TAP_GALLERY, source="touch"),
            "instructions":   AppEvent(EventType.TAP_INSTRUCTIONS, source="touch"),
            "terms":          AppEvent(EventType.TAP_TERMS, source="touch"),
            "back":           AppEvent(EventType.TAP_BACK, source="touch"),
            "cancel":         AppEvent(EventType.TAP_CANCEL, source="touch"),
            "save":           AppEvent(EventType.TAP_SAVE, payload={"filename": None}, source="touch"),
            "delete":         AppEvent(EventType.TAP_DELETE, source="touch"),
            "confirm_delete": AppEvent(EventType.TAP_CONFIRM_DELETE, source="touch"),
            "abort_delete":   AppEvent(EventType.TAP_ABORT_DELETE, source="touch"),
            # NEU (4.4): Gesamtbestand loeschen - bewusst eigene Events,
            # nicht die des Einzelfoto-Loeschens im Review-Ablauf.
            "admin_delete_confirm": AppEvent(EventType.TAP_ADMIN_DELETE_CONFIRM, source="touch"),
            "admin_delete_abort":   AppEvent(EventType.TAP_ADMIN_DELETE_ABORT, source="touch"),
            # NEU (4.6): "Weiter" der USB-Bildschirme.
            "usb_continue":   AppEvent(EventType.TAP_ADMIN_USB_CONTINUE, source="touch"),
            "usb_clear":      AppEvent(EventType.TAP_ADMIN_USB_CLEAR, source="touch"),    # NEU (4.7)
            # NEU (6c): Sammelaktionen + "Ausfuehren" auf dem Konflikt-Screen.
            "usb_conflicts_overwrite_all": AppEvent(EventType.TAP_ADMIN_USB_CONFLICTS_OVERWRITE_ALL, source="touch"),
            "usb_conflicts_rename_all":    AppEvent(EventType.TAP_ADMIN_USB_CONFLICTS_RENAME_ALL, source="touch"),
            "usb_conflicts_apply":         AppEvent(EventType.TAP_ADMIN_USB_CONFLICTS_APPLY, source="touch"),
        }
        for name, rect in rects.items():
            if rect.collidepoint(pos) and name in mapping:
                return mapping[name]
        return None

    def _map_admin_menu_click(self, pos: tuple[int, int]) -> AppEvent | None:
        rects = build_admin_rects(self.config.screen.width, self.config.screen.height)
        for item in ADMIN_MENU_ITEMS:
            if not item.enabled:
                continue  # ausgegraute Punkte reagieren bewusst nicht
            if rects[item.key].collidepoint(pos):
                return AppEvent(item.event_type, source="touch")
        return None

    def _map_pin_entry_click(self, pos: tuple[int, int]) -> AppEvent | None:
        # Ziffernfeld: Treffer gegen layout.pin_keys pruefen und in das
        # passende Event uebersetzen.
        for name, rect in self.layout.pin_keys.items():
            if not rect.collidepoint(pos):
                continue
            if name == "cancel":
                return AppEvent(EventType.PIN_ENTRY_CANCEL, source="touch")
            if name == "backspace":
                return AppEvent(EventType.PIN_BACKSPACE, source="touch")
            if name == "submit":
                entered = self.model.ui.pin_entry
                if not entered:
                    return None  # leere Eingabe: keinen Fehlversuch verbrennen
                # PIN pruefen (PinLockout lebt in der App, nicht in der State
                # Machine). Ergebnis + Restinfos als Payload; die State Machine
                # entscheidet nur ueber den Uebergang.
                result = self._pin_lockout.check(entered, self.config.shutdown.pin, now_wall=time.time())
                return AppEvent(
                    EventType.PIN_SUBMIT,
                    payload={
                        "pin_result": result,
                        "attempts_left": self._pin_lockout.attempts_left(),
                        "remaining_seconds": self._pin_lockout.remaining_seconds(),
                    },
                    source="touch",
                )
            if name.isdigit():
                return AppEvent(EventType.PIN_DIGIT, payload={"digit": name}, source="touch")
        return None


    # -- Timer-Events ----------------------------------------------------------

    def _emit_due_timers(self, now: float) -> None:
        timers = self.model.timers
        state = self.model.state

        # NEU (3.4): PIN_ENTRY-Idle und Abschieds-Timeout vorab und in sich
        # abgeschlossen behandeln - unabhaengig von der uebrigen Timer-Kette.
        if state == AppState.PIN_ENTRY:
            if self._due(timers.idle_deadline, now):
                self.dispatch(AppEvent(EventType.IDLE_TIMEOUT, source="timer"), now)
            return
        if state == AppState.SHUTDOWN_GOODBYE:
            if not self._power_off_requested and self._due(timers.shutdown_goodbye_deadline, now):
                self._power_off_requested = True
                self.dispatch(AppEvent(EventType.SHUTDOWN_TIMEOUT, source="timer"), now)
            return
        # NEU (4.3): kurzer Zwischenscreen vor dem App-Neustart - eigener,
        # nicht abbrechbarer Timer, analog zu SHUTDOWN_GOODBYE.
        if state == AppState.ADMIN_RESTART_PENDING:
            if self._due(timers.admin_restart_deadline, now):
                self.dispatch(AppEvent(EventType.ADMIN_RESTART_TIMEOUT, source="timer"), now)
            return
        # NEU (4.4): Loeschlauf im Hintergrund - hier wird lediglich
        # gepollt, ob der Thread fertig ist. Bewusst KEIN Timeout: eine
        # laufende Loeschung darf nicht unterbrochen werden.
        # NEU (4.7): Kopierlauf mit Fortschrittsanzeige. Der Hintergrund-
        # Thread aktualisiert _usb_export_progress, hier wird es gepollt
        # und in den UI-Zustand uebertragen.
        if state == AppState.ADMIN_USB_COPY:
            progress = self._usb_export_progress
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
                self.model = self.model.evolve(ui=ui)
            result = self._usb_export_result
            if result is not None:
                self._usb_export_result = None
                self._usb_export_progress = None
                self._usb_thread = None
                self.dispatch(
                    AppEvent(
                        EventType.ADMIN_USB_EXPORT_FINISHED,
                        # NEU (6b): "conflicts" durchreichen - leer im
                        # Normalfall (dann unveraendertes altes Verhalten).
                        payload={
                            "lines": result.summary_lines(),
                            "ok": result.ok,
                            "conflicts": tuple(getattr(result, "conflicts", ())),
                        },
                        source="usb",
                    ),
                    now,
                )
            return

        # NEU (6b): Konfliktaufloesung (Phase 2) - gleiches Fortschritts-
        # und Polling-Muster wie ADMIN_USB_COPY oben, eigenes Zielevent.
        if state == AppState.ADMIN_USB_RESOLVE:
            progress = self._usb_export_progress
            if progress is not None:
                total = max(1, progress.total_files)
                if progress.phase == "resolve":
                    text = f"Konflikte werden aufgelöst ... ({progress.resolved_files} von {progress.total_files})"
                    fraction = 0.5 * progress.resolved_files / total
                elif progress.phase == "verify":
                    text = f"Prüfsummen werden geprüft ... ({progress.verified_files} von {progress.total_files})"
                    fraction = 0.5 + 0.5 * progress.verified_files / total
                elif progress.phase == "done":
                    text = "Abschluss ..."
                    fraction = 1.0
                else:
                    text = "Auflösung wird vorbereitet ..."
                    fraction = 0.0
                from dataclasses import replace as dc_replace
                ui = dc_replace(
                    self.model.ui,
                    admin_usb_export_progress=text,
                    admin_usb_progress_fraction=fraction,
                )
                self.model = self.model.evolve(ui=ui)
            result = self._usb_export_result
            if result is not None:
                self._usb_export_result = None
                self._usb_export_progress = None
                self._usb_thread = None
                self.dispatch(
                    AppEvent(
                        EventType.ADMIN_USB_RESOLVE_FINISHED,
                        payload={"lines": result.summary_lines(), "ok": result.ok},
                        source="usb",
                    ),
                    now,
                )
            return

        # NEU (4.6): laufende USB-Jobs (Pruefen / Auswerfen). Beide sind
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

            result = self._delete_result
            if result is not None:
                self._delete_result = None
                self._delete_thread = None
                self._delete_progress = None   # NEU (4.9)
                self.dispatch(
                    AppEvent(
                        EventType.ADMIN_DELETE_FINISHED,
                        payload={"lines": result.summary_lines()},
                        source="delete",
                    ),
                    now,
                )
            return

        idle_states = {
            AppState.MAIN_MENU,
            AppState.PHOTO_INTRO,
            AppState.PHOTO_PREVIEW,
            AppState.GALLERY_GRID,
            AppState.GALLERY_EMPTY,   # NEU (Etappe 7)
            AppState.GALLERY_FULLSCREEN,
            AppState.REVIEW,
            # TERMS: anders als INSTRUCTIONS soll diese Ansicht nach
            # terms_idle_seconds (3 Minuten) automatisch verlassen werden,
            # falls der Gast "Verstanden" vergisst zu druecken.
            AppState.TERMS,
            # NEU (4.1): Service-Menue schliesst sich nach
            # admin_menu_idle_seconds automatisch (Standard 30s).
            AppState.ADMIN_MENU,
            # NEU (4.3): Diagnoseseite - gleiches Idle-Verhalten. (Bewusst
            # OHNE ADMIN_RESTART_PENDING - der hat einen eigenen, nicht
            # abbrechbaren Timer, siehe oben.)
            AppState.ADMIN_STATUS,
            # NEU (4.4): Sicherheitsabfrage vor dem Loeschen - bleibt sie
            # unbeantwortet stehen, ist "nicht loeschen" die richtige
            # Annahme. (Bewusst OHNE ADMIN_DELETE_RUNNING/_DONE: dort ist
            # idle_deadline absichtlich None.)
            AppState.ADMIN_DELETE_CONFIRM,
            # NEU (4.6): USB-Bildschirme mit Timeout. Bewusst OHNE
            # ADMIN_USB_CHECK und ADMIN_USB_EJECT - dort laeuft ein Job,
            # der nicht unterbrochen werden darf (idle_deadline ist dort
            # ohnehin None).
            AppState.ADMIN_USB_WAIT, AppState.ADMIN_USB_READY,
            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_REMOVE,
            # NEU (4.7): Ergebnis-Screen. Bewusst OHNE ADMIN_USB_COPY (nicht
            # unterbrechbar - idle_deadline dort ohnehin None).
            AppState.ADMIN_USB_EXPORT_DONE,
            # NEU (6b): interaktive Konfliktauswahl - bewusst OHNE
            # ADMIN_USB_RESOLVE (dort laeuft ein Hintergrund-Thread, nicht
            # unterbrechbar, idle_deadline ist dort ohnehin None).
            AppState.ADMIN_USB_CONFLICTS,
            # NEU (4.5): Ergebnis-Screen - nach 30s zurueck ins Hauptmenue.
            AppState.ADMIN_DELETE_DONE,
        }

        if state == AppState.BOOT and self._due(timers.boot_deadline, now):
            self.dispatch(AppEvent(EventType.TICK, source="timer"), now)
        elif state == AppState.PHOTO_PREVIEW and self._due(timers.preview_auto_countdown_deadline, now):
            # Nach preview_auto_start_seconds automatisch weiter zum Countdown -
            # kein erneutes Antippen von "Countdown starten" mehr noetig.
            # TAP_PHOTO wird hier bewusst wiederverwendet (gleiche Wirkung wie
            # ein manueller Tap/Tasterdruck in PHOTO_PREVIEW).
            self.dispatch(AppEvent(EventType.TAP_PHOTO, source="timer"), now)
        elif state in idle_states and self._due(timers.idle_deadline, now):
            self.dispatch(AppEvent(EventType.IDLE_TIMEOUT, source="timer"), now)
        elif state == AppState.COUNTDOWN and self._due(timers.countdown_deadline, now):
            self._advance_countdown(now)
        elif state == AppState.CAPTURE_PENDING and self._due(timers.capture_trigger_deadline, now):
            self.model = self.model.evolve(
                timers=replace(self.model.timers, capture_trigger_deadline=None)
            )
            self._do_capture(now)
        elif state == AppState.DELETE_CONFIRM and self._due(timers.delete_deadline, now):
            self.dispatch(AppEvent(EventType.DELETE_TIMEOUT, source="timer"), now)
        elif state == AppState.QR_DISPLAY and self._due(timers.qr_deadline, now):
            self.dispatch(AppEvent(EventType.QR_TIMEOUT, source="timer"), now)

    def _advance_countdown(self, now: float) -> None:
        current = self.model.ui.countdown_value or 0
        if current > 1:
            self.model = self.model.evolve(
                ui=replace(self.model.ui, countdown_value=current - 1),
                timers=replace(self.model.timers, countdown_deadline=now + 1.0),
            )
        else:
            self.dispatch(AppEvent(EventType.COUNTDOWN_FINISHED, source="timer"), now)

    # -- Kamera-Aufnahme -------------------------------------------------------

    def _do_capture(self, now: float) -> None:
        """Foto auslösen und Ergebnis als Event einliefern."""
        # LED-Ring VOR dem blockierenden gphoto2-Aufruf explizit auf "gruen,
        # Verarbeitung laeuft" setzen: _sync_led() laeuft im Hauptloop erst
        # NACH _emit_due_timers() (das diese Methode hier aufruft). Waehrend
        # capture_photo() blockiert (mehrere Sekunden gphoto2-Download),
        # kaeme _sync_led() nicht mehr rechtzeitig zum Zug, weil der State
        # bis zum naechsten Durchlauf schon auf REVIEW steht. Der LED-
        # Hintergrund-Thread liest den Effekt aber unabhaengig vom Haupt-
        # Thread, daher wirkt das direkte Setzen hier sofort.
        if self._led_provider is not None:
            self.led_service.set_effect(LedEffect.CAPTURE_PROCESSING)
            self._led_provider.set_effect(LedEffect.CAPTURE_PROCESSING)

        result = self.capture_service.capture_photo()
        if result.ok and result.photo_path:
            self.dispatch(
                AppEvent(
                    EventType.CAPTURE_OK,
                    payload={"photo_path": str(result.photo_path)},
                    source="capture",
                ),
                now,
            )
        else:
            self.dispatch(
                AppEvent(
                    EventType.CAPTURE_FAILED,
                    payload={"message": result.error_message or "Aufnahme fehlgeschlagen."},
                    source="capture",
                ),
                now,
            )

    # -- Actions ---------------------------------------------------------------

    def dispatch(self, event: AppEvent, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        # Modellstand VOR der Transition merken: die State Machine setzt
        # current_photo_path beim Loeschen (siehe state_machine.py
        # _handle_delete_confirm / IDLE_TIMEOUT in _handle_review) bereits
        # im selben Schritt auf None, in dem auch die "delete_photo"-Aktion
        # ausgeloest wird. Wuerde _delete_photo() erst NACH self.model =
        # result.model laufen und dabei self.model lesen, waere der Pfad
        # schon weg und die Datei wuerde NIE tatsaechlich geloescht (Bug:
        # Foto blieb trotz Loesch-Bestaetigung auf der Karte liegen und
        # tauchte spaeter wieder in der Galerie auf).
        previous_model = self.model
        result = self.state_machine.transition(self.model, event, now)
        self.model = result.model
        self._apply_actions(result.actions, now, previous_model)
        if self.model.state in {
            AppState.GALLERY_GRID, AppState.GALLERY_FULLSCREEN, AppState.ATTRACT_GALLERY,
            # NEU (Etappe 7): MAIN_MENU ist das Drehkreuz nach jeder Aufnahme
            # (REVIEW -> QR_DISPLAY -> MAIN_MENU) - session.photos wird
            # NIRGENDS sonst beim Speichern aktualisiert. Ohne diesen
            # Eintrag saehe die neue TAP_GALLERY-Weiche (state_machine.py,
            # "if not model.session.photos") immer noch die veraltete Liste
            # vom letzten Aufruf, und da GALLERY_EMPTY selbst NICHT
            # aktualisiert, waere die Galerie fuer immer "leer" - ein sich
            # selbst verstaerkender Fehler. Mit MAIN_MENU hier ist die Liste
            # bereits aktuell, bevor "Galerie" ueberhaupt angetippt wird.
            AppState.MAIN_MENU,
        }:
            photos = tuple(self.gallery_service.list_photos())
            self.model = self.model.evolve(session=replace(self.model.session, photos=photos))

    def _apply_actions(self, actions: tuple[str, ...], now: float, previous_model: AppModel) -> None:
        for action in actions:
            if action == "start_preview":
                self.preview_service.start()
            elif action == "stop_preview":
                self.preview_service.stop()
            elif action == "export_photo":
                self._export_photo()
            elif action == "generate_qr":
                self._generate_qr_surface()
            elif action == "delete_photo":
                self._delete_photo(previous_model)
            elif action == "power_off":                      # NEU (3.4)
                self._power_off()
            elif action == "collect_admin_status":            # NEU (4.3)
                self._collect_admin_status()
            elif action == "restart_app":                     # NEU (4.3)
                self._restart_app()
            elif action == "start_delete_all":                # NEU (4.4)
                self._start_delete_all()
            elif action == "usb_prepare":                     # NEU (4.6)
                self._usb_prepare()
            elif action == "usb_check":                       # NEU (4.6)
                self._usb_start_check()
            elif action == "usb_eject":                       # NEU (4.6)
                self._usb_start_eject()
            elif action == "usb_start_export":                # NEU (4.7)
                self._usb_start_export()
            elif action == "usb_clear_and_check":             # NEU (4.7)
                self._usb_start_clear_and_check()
            elif action == "usb_apply_resolutions":            # NEU (6b)
                self._usb_start_resolve()

    def _export_photo(self) -> None:
        path = self.model.session.current_photo_path
        if not path:
            return
        try:
            filename = self.model.session.qr_filename
            exported = self.storage_service.export_to_web(path, target_name=filename)
            print(f"[App] Foto exportiert: {exported}")
            # Galerie-Cache invalidieren
            self.gallery_service.clear_caches()
        except Exception as exc:
            print(f"[App] Export fehlgeschlagen: {exc}")

    def _generate_qr_surface(self) -> None:
        filename = self.model.session.qr_filename
        if not filename:
            self._qr_surface = None
            return
        try:
            pil_image = self.qr_service.create_qr_image(filename)
            self._qr_surface = pygame.image.fromstring(
                pil_image.tobytes(), pil_image.size, "RGB"
            )
        except Exception as exc:
            print(f"[App] QR-Code konnte nicht erzeugt werden: {exc}")
            self._qr_surface = None

    def _delete_photo(self, previous_model: AppModel) -> None:
        # Bewusst previous_model (Stand VOR der Transition) statt
        # self.model verwenden - siehe Kommentar in dispatch().
        path = previous_model.session.current_photo_path
        if not path:
            print("[App] Loeschen angefordert, aber kein current_photo_path im vorherigen Modellstand vorhanden.")
            return
        deleted = self.gallery_service.delete_photo(path)
        if deleted:
            print(f"[App] Foto gelöscht: {path}")
        else:
            print(f"[App] Foto konnte nicht geloescht werden (nicht gefunden?): {path}")

    def _power_off(self) -> None:
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

    # --- USB-Export (NEU 4.6) ---

    def _usb_prepare(self) -> None:
        """Platzbedarf ermitteln und anzeigen. Laeuft synchron - es werden
        nur Dateigroessen addiert, das dauert auch bei mehreren hundert
        Fotos nur Millisekunden."""
        self._usb_partition = None
        self._usb_stick = None
        self._usb_next_scan = 0.0
        self._usb_unusable_reported = False
        # NEU (4.7): Reste eines vorherigen Exportlaufs verwerfen.
        self._usb_export_progress = None
        self._usb_export_result = None
        count, net, gross = admin_usb_service.required_export_bytes(
            self.config.photo_dir, self.config.gallery.excluded_filenames,
        )
        self._usb_required_bytes = gross
        if count == 0:
            lines = (
                "Es sind keine Bilder zum Exportieren vorhanden.",
                "Bitte mit \"Abbrechen\" zurück ins Service-Menü.",
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

    def _usb_start_export(self) -> None:
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
                    # NEU: Zielordner traegt den Event-Titel statt eines Zeitstempels.
                    folder_name=self.config.screen.title,
                    verify=True,
                    # NEU (6b): schaltet die inhaltsbasierte Konflikterkennung
                    # aus Etappe 6a scharf (war dort dormant, Default=False).
                    collect_conflicts=True,
                )
                print(
                    f"[App] Export beendet: {result.copied} kopiert, "
                    f"{result.skipped} uebersprungen, {result.verified} verifiziert, "
                    f"Konflikte: {len(result.conflicts)}, "
                    f"Fehler: {len(result.errors)}, Pruefsummenfehler: {len(result.failed_verify)}"
                )
                # NEU (4.8): bisher stand nur die ANZAHL im Log - welche
                # Datei betroffen war, liess sich nicht nachvollziehen.
                for message in result.errors:
                    print(f"[App]   Exportfehler: {message}")
                for name in result.failed_verify:
                    print(f"[App]   PRUEFSUMMENFEHLER: {name}")
                self._write_usb_export_log(result.log_actions)      # NEU (6b)
            except Exception as exc:
                print(f"[App] FEHLER beim Export: {exc}")
                from admin_usb_export import ExportResult
                result = ExportResult()
                result.errors.append(str(exc))
                progress.phase = "error"
            # NEU (6b): fuer eine evtl. folgende Konfliktaufloesung (Phase 2)
            # vorhalten - unabhaengig davon, ob tatsaechlich Konflikte
            # offen sind (dann bleibt das Objekt einfach ungenutzt).
            self._usb_export_pending_result = result
            self._usb_export_result = result

        self._usb_thread = threading.Thread(target=worker, name="usb-export", daemon=True)
        self._usb_thread.start()

    def _usb_start_resolve(self) -> None:
        """NEU (6b): Konfliktaufloesung (Phase 2) im Hintergrund-Thread
        starten. Baut auf dem in Phase 1 zwischengehaltenen ExportResult
        auf und uebernimmt die aktuellen Entscheidungen aus dem UI-Zustand
        (der Nutzer kann sie auf dem Konflikt-Screen bis zuletzt aendern)."""
        result = self._usb_export_pending_result
        if result is None:
            self._usb_export_result = type(
                "R", (), {"summary_lines": lambda: ("Kein Exportergebnis vorhanden.",), "ok": False}
            )()
            return

        # Aktuelle Entscheidungen aus dem Modell uebernehmen, BEVOR der
        # Thread startet - der Hauptloop darf model.ui danach unbehelligt
        # weiterlaufen lassen (der Thread liest ab hier nur noch "result").
        result.conflicts = list(self.model.ui.admin_usb_conflicts)

        progress = ExportProgress()
        progress.total_files = len(result.conflicts)
        self._usb_export_progress = progress

        def worker() -> None:
            try:
                apply_conflict_resolutions(
                    photo_dir=self.config.photo_dir,
                    result=result,
                    progress=progress,
                    verify=True,
                )
                print(
                    f"[App] Konfliktaufloesung beendet: {result.overwritten} ueberschrieben, "
                    f"{result.renamed} umbenannt, Fehler: {len(result.errors)}, "
                    f"Pruefsummenfehler: {len(result.failed_verify)}"
                )
                for message in result.errors:
                    print(f"[App]   Fehler bei der Konfliktaufloesung: {message}")
                self._write_usb_export_log(result.log_actions)
            except Exception as exc:
                print(f"[App] FEHLER bei der Konfliktaufloesung: {exc}")
                result.errors.append(str(exc))
                progress.phase = "error"
            self._usb_export_pending_result = None
            self._usb_export_result = result

        self._usb_thread = threading.Thread(target=worker, name="usb-resolve", daemon=True)
        self._usb_thread.start()

    def _write_usb_export_log(self, messages) -> None:
        """NEU (6b): haengt Kopier-/Ueberschreib-/Umbenennungsaktionen des
        USB-Exports an eine fortlaufende Logdatei an (data/logs/usb_export.log).
        Ergaenzend zu den bestehenden print()-Ausgaben - die Datei ueberlebt
        einen Neustart der App, das Terminal nicht."""
        if not messages:
            return
        try:
            self.config.log_dir.mkdir(parents=True, exist_ok=True)
            log_path = self.config.log_dir / "usb_export.log"
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(log_path, "a", encoding="utf-8") as fh:
                for message in messages:
                    fh.write(f"{timestamp}  {message}\n")
        except OSError as exc:
            print(f"[App] USB-Exportprotokoll konnte nicht geschrieben werden: {exc}")

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

    def _start_delete_all(self) -> None:
        # NEU (4.4): Loeschung in einem Hintergrund-Thread starten. Anders
        # als die Diagnose (die synchron laeuft, weil sie unter einer
        # Sekunde bleibt) kann das Leeren der Kamera-Speicherkarte ueber
        # USB deutlich laenger dauern - synchron wuerde die Pygame-Schleife
        # so lange stehen und die App wirkte abgestuerzt.
        if self._delete_thread is not None and self._delete_thread.is_alive():
            print("[App] Loeschlauf laeuft bereits - Anforderung ignoriert.")
            return

        progress = DeleteProgress()          # NEU (4.9)
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
                )
                print(
                    f"[App] Loeschlauf beendet: {result.deleted_photos} Fotos, "
                    f"{result.deleted_web_copies} Web-Kopien, Kamera: {result.camera_status}"
                )
                if result.report_path is not None:
                    print(f"[App] Loeschprotokoll: {result.report_path}")
                # NEU (4.9): wie beim Export - nicht nur die Anzahl, sondern
                # auch die betroffene Datei ins Log schreiben.
                for message in result.errors:
                    print(f"[App]   Loeschfehler: {message}")
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

    def _check_storage(self, now: float) -> None:
        """NEU (Speicherplatz-Alarm): periodische Pruefung (Standard alle 30s,
        siehe config.storage.check_interval_seconds) - bewusst UNABHAENGIG
        vom aktuellen AppState direkt im Hauptloop, nicht ueber ein
        State-Machine-Event: die Sperre muss ja schon GREIFEN, bevor
        ueberhaupt ein Tap auf "Fotografieren" verarbeitet wird, und
        model.ui.storage_alarm_level muss auch dann aktuell bleiben, wenn
        gerade niemand an der Box ist (fuer den Warnbanner/das Blinken im
        Hauptmenue waehrend des Attract-Modus).

        Fehler beim Zugriff auf die Partition (z.B. kurzzeitig nicht
        gemountet) werden protokolliert, aber die App laeuft mit dem
        zuletzt bekannten Stand weiter, statt abzustuerzen."""
        if now - self._last_storage_check < self.config.storage.check_interval_seconds:
            return
        self._last_storage_check = now
        try:
            status = assess_storage(
                photo_dir=self.config.photo_dir,
                photo_paths=self.gallery_service.list_photos(),
                warn_threshold_percent=self.config.storage.warn_threshold_percent,
                critical_threshold_percent=self.config.storage.critical_threshold_percent,
                fallback_avg_photo_size_bytes=self.config.storage.fallback_avg_photo_size_bytes,
            )
        except OSError as exc:
            print(f"[App] Speicherplatz-Pruefung fehlgeschlagen: {exc}")
            return

        if status.alarm_level >= 2 and self.model.ui.storage_alarm_level < 2:
            print(f"[App] KRITISCH: nur noch {status.free_percent:.1f}% Speicherplatz frei!")
        elif status.alarm_level == 1 and self.model.ui.storage_alarm_level == 0:
            print(f"[App] WARNUNG: nur noch {status.free_percent:.1f}% Speicherplatz frei.")

        ui = replace(
            self.model.ui,
            storage_alarm_level=status.alarm_level,
            storage_free_percent=status.free_percent,
            storage_estimated_remaining_photos=status.estimated_remaining_photos,
        )
        self.model = self.model.evolve(ui=ui)

    def _collect_admin_status(self) -> None:
        # NEU (4.3): Diagnosezeilen synchron ermitteln (dauert i.d.R. < 1s,
        # hoechstens ein paar Sekunden bei der Kamera-Pruefung) - ausgeloest
        # durch einen bewussten Tap im Service-Menue, daher kein Hintergrund-
        # Thread noetig. Ergebnis kommt als eigenes Event zurueck, damit die
        # State Machine (die keine Hardware kennt) unveraendert bleibt.
        photo_paths = self.gallery_service.list_photos()
        photo_count = len(photo_paths)
        lines = collect_status_lines(
            photo_dir=self.config.photo_dir,
            photo_count=photo_count,
            app_start_monotonic=self._app_start_monotonic,
            photo_url_prefix=self.config.network.photo_url_prefix,  # NEU (Diagnose-Feedback)
        )
        # NEU (Speicherplatz-Alarm): frisch berechnet (nicht der ggf. bis zu
        # 30s alte periodische Wert) - ein bewusst angeforderter Diagnose-
        # Screen soll den aktuellsten Stand zeigen.
        try:
            storage_status = assess_storage(
                photo_dir=self.config.photo_dir,
                photo_paths=photo_paths,
                warn_threshold_percent=self.config.storage.warn_threshold_percent,
                critical_threshold_percent=self.config.storage.critical_threshold_percent,
                fallback_avg_photo_size_bytes=self.config.storage.fallback_avg_photo_size_bytes,
            )
            avg_mb = storage_status.average_photo_size_bytes / (1024 * 1024)
            herkunft = "geschätzt, noch keine eigenen Fotos" if storage_status.average_is_fallback else "aus vorhandenen Fotos"
            # NEU (Feedback): auf zwei Zeilen umgebrochen (nach dem zweiten
            # Komma) - eine Zeile lief auf dem Diagnose-Screen ueber den
            # rechten Bildschirmrand hinaus.
            lines = lines + (
                f"Geschätzte Rest-Kapazität: ca. {storage_status.estimated_remaining_photos} Fotos "
                f"({storage_status.free_percent:.1f}% frei, im Schnitt {avg_mb:.1f} MB/Foto,",
                f"   {herkunft})",
            )
        except OSError as exc:
            lines = lines + (f"Speicherplatz-Schätzung fehlgeschlagen: {exc}",)
        # NEU (Etappe 8, Feedback): sichtbarer Hinweis in der Diagnose,
        # falls die Event-Konfiguration noch auf den Platzhaltern steht -
        # ergaenzt die Konsolen-Warnung aus config.py um eine Stelle, die
        # auch ohne Terminal-Zugriff einsehbar ist.
        if self.config.needs_event_setup:
            lines = lines + (
                "Event-Konfiguration: Standardwerte aktiv (data/event_config.json anpassen)",
            )
        self.dispatch(AppEvent(EventType.ADMIN_STATUS_READY, payload={"lines": lines}, source="diagnostics"))


    # -- LED & Button-LED synchronisieren --------------------------------------

    # Identischer 10s-Bereitschafts-Blink-Zyklus wie in hw_led_provider.py's
    # MAIN_MENU-Sync-Blitz - beide Threads lesen unabhaengig dieselbe
    # time.monotonic()-Uhr mit derselben Formel, dadurch laufen LED-Ring und
    # Taster-LED garantiert im Gleichtakt ohne Parameteruebergabe.
    _BUTTON_SYNC_CYCLE_SEC = 10.0
    _BUTTON_SYNC_FLASH_WINDOW = 0.75
    _BUTTON_SYNC_FLASH_PERIOD = 0.15

    # Zunehmend schnelleres Blinken der Taster-LED je naeher der Auslösung
    # (Ziffer 5 -> 4 -> 3 -> 2); bei Ziffer 1 ist die Taster-LED aus (siehe unten).
    _COUNTDOWN_BUTTON_HZ = {5: 1.5, 4: 2.5, 3: 3.5, 2: 5.0}

    def _sync_led(self) -> None:
        """
        LED-Ring komplett zustandsgetrieben: Der passende Effekt wird jeden
        Frame direkt aus dem aktuellen Modell (Zustand + countdown_value +
        Timer) berechnet, statt sich auf einmalig ausgeloeste 'set_led_*'-
        Aktionen aus der State Machine zu verlassen. Dadurch ist der Ring
        garantiert synchron zu dem, was tatsaechlich auf dem Bildschirm zu
        sehen ist (v.a. wichtig fuer die zifferngenauen Countdown-Farben).
        """
        if self._led_provider is None:
            return

        state = self.model.state
        now = time.monotonic()

        # NEU (Speicherplatz-Alarm): auf den beiden Einstiegs-Screens (dort,
        # wo die Aufnahme-Sperre in state_machine.py auch tatsaechlich
        # greift) ERZWINGT das schnelle Rotblinken den sonst dort gezeigten
        # Effekt, unabhaengig vom Zustand selbst. Bewusst NUR hier und
        # nicht global - ein laufender Countdown oder eine bereits
        # begonnene Aufnahme soll dadurch nicht gestoert werden.
        if state in {AppState.MAIN_MENU, AppState.GALLERY_EMPTY} and self.model.ui.storage_alarm_level >= 2:
            effect = LedEffect.ERROR
        elif state == AppState.BOOT:
            effect = LedEffect.BOOT
        elif state in {AppState.MAIN_MENU, AppState.ATTRACT_GALLERY, AppState.TERMS}:
            effect = LedEffect.MAIN_MENU
        elif state == AppState.PHOTO_INTRO:
            effect = LedEffect.PHOTO_INTRO
        elif state == AppState.INSTRUCTIONS:
            effect = LedEffect.INSTRUCTIONS_WAVE
        elif state == AppState.PHOTO_PREVIEW:
            effect = LedEffect.PREVIEW
        elif state == AppState.COUNTDOWN:
            value = self.model.ui.countdown_value
            if value == 5:
                effect = LedEffect.COUNTDOWN_5
            elif value == 4:
                effect = LedEffect.COUNTDOWN_4
            elif value == 3:
                effect = LedEffect.COUNTDOWN_3
            elif value == 2:
                effect = LedEffect.COUNTDOWN_2
            else:
                # Ziffer 1 (oder Uebergang) - weiss blitzen, siehe Doku
                effect = LedEffect.COUNTDOWN_1_FLASH
        elif state == AppState.CAPTURE_PENDING:
            # Das Weiss-Blitzen aus Ziffer "1" laeuft in CAPTURE_PENDING noch
            # kurz weiter, geht aber rechtzeitig vor dem eigentlichen GPIO-
            # Ausloeseimpuls (capture_trigger_deadline) wieder aus - keine
            # Reflexionen in Brillen im Moment der Aufnahme. CAPTURE_PROCESSING
            # (gruen) wird NICHT hier gesetzt, sondern direkt in _do_capture(),
            # da diese Methode waehrend des blockierenden gphoto2-Aufrufs
            # nicht mehr rechtzeitig zum Zug kaeme.
            deadline = self.model.timers.capture_trigger_deadline
            if deadline is not None and (deadline - now) > 0.25:
                effect = LedEffect.COUNTDOWN_1_FLASH
            else:
                effect = LedEffect.PRE_TRIGGER_DARK
        elif state == AppState.REVIEW:
            effect = LedEffect.REVIEW_BREATHE
        elif state == AppState.DELETE_CONFIRM:
            effect = LedEffect.DELETE_CONFIRM
        elif state == AppState.QR_DISPLAY:
            effect = LedEffect.QR
        elif state == AppState.GALLERY_GRID:
            effect = LedEffect.GALLERY_GRID_BREATHE
        elif state == AppState.GALLERY_EMPTY:      # NEU (Etappe 7)
            effect = LedEffect.GALLERY_EMPTY_INVITE
        elif state == AppState.GALLERY_FULLSCREEN:
            effect = LedEffect.GALLERY_STARFIELD
        elif state == AppState.ERROR_SCREEN:
            effect = LedEffect.ERROR
        elif state == AppState.ADMIN_MENU:
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
            effect = LedEffect.CAPTURE_PROCESSING
        elif state in {AppState.ADMIN_DELETE_CONFIRM, AppState.ADMIN_DELETE_RUNNING}:
            # NEU (4.4): langsames, kraeftiges rotes Warnblinken - eigener
            # Effekt, damit es sich klar von LedEffect.ERROR (schnelles
            # Blinken bei einer Stoerung) unterscheidet.
            effect = LedEffect.ADMIN_DELETE_WARN
        elif state == AppState.ADMIN_DELETE_DONE:
            # NEU (4.4): zurueck zur ruhigen Welle - die Gefahr ist vorbei.
            effect = LedEffect.INSTRUCTIONS_WAVE
        elif state == AppState.ADMIN_USB_WAIT:
            # NEU (4.6): oranges Blinken als Aufforderung, den Stick
            # einzustecken (eigener Effekt, siehe led_service.py).
            effect = LedEffect.ADMIN_USB_WAIT
        elif state == AppState.ADMIN_USB_COPY:
            # NEU (4.7): rotierender Teilkreis waehrend des Exports.
            effect = LedEffect.ADMIN_USB_COPY
        elif state == AppState.ADMIN_USB_CONFLICTS:
            # NEU (6b): gelbes Atmen - Aufmerksamkeit noetig (Entscheidung
            # gefragt), aber keine Stoerung. Gleicher Effekt wie beim
            # nicht-verwendbaren Stick (ADMIN_USB_PROBLEM).
            effect = LedEffect.REVIEW_BREATHE
        elif state == AppState.ADMIN_USB_RESOLVE:
            # NEU (6b): weiterhin "es wird kopiert" - derselbe rotierende
            # Teilkreis wie beim eigentlichen Export, kein eigener Effekt
            # noetig.
            effect = LedEffect.ADMIN_USB_COPY
        elif state == AppState.ADMIN_USB_EXPORT_DONE:
            # NEU (4.7): zurueck zur ruhigen Welle - Export ist fertig.
            effect = LedEffect.INSTRUCTIONS_WAVE
        elif state in {AppState.ADMIN_USB_CHECK, AppState.ADMIN_USB_EJECT}:
            # NEU (4.6): "es passiert gerade etwas" - wie beim Neustart.
            effect = LedEffect.CAPTURE_PROCESSING
        elif state == AppState.ADMIN_USB_PROBLEM:
            # NEU (4.6): gelbes Atmen - Aufmerksamkeit, aber keine Stoerung.
            effect = LedEffect.REVIEW_BREATHE
        elif state in {AppState.ADMIN_USB_READY, AppState.ADMIN_USB_REMOVE}:
            effect = LedEffect.INSTRUCTIONS_WAVE
        elif state == AppState.PIN_ENTRY:
            # NEU (3.5): nur waehrend der Fehler-Optik rot/gelb, sonst dunkel.
            deadline = self.model.timers.pin_error_deadline
            if deadline is not None and now < deadline:
                effect = LedEffect.PIN_ERROR
            else:
                effect = LedEffect.OFF
        elif state == AppState.SHUTDOWN_GOODBYE:
            effect = LedEffect.SHUTDOWN_SEQUENCE  # NEU (3.5): Sonnenuntergang
        else:
            effect = LedEffect.OFF

        self.led_service.set_effect(effect)
        self._led_provider.set_effect(effect)

    def _sync_button_led(self) -> None:
        """
        Taster-LED: Der Gast soll (fast) jederzeit sehen koennen, dass ein
        Tasterdruck ein Foto ausloest - daher ueberall im Bereitschafts-
        Blink-Modus, AUSSER waehrend der eigentlichen Aufnahme-Sequenz
        (PHOTO_PREVIEW/COUNTDOWN, dort eigenes Verhalten) und waehrend
        Review/QR/Loeschbestaetigung/Fehler/Verarbeitung (dort loest ein
        Tasterdruck aktuell nichts Sinnvolles aus).
        """
        if self._button_provider is None:
            return
        state = self.model.state
        now = time.monotonic()

        if state == AppState.PHOTO_PREVIEW:
            self._button_provider.set_led(True)  # dauerhaft an
            return

        if state == AppState.COUNTDOWN:
            value = self.model.ui.countdown_value
            if value == 1:
                self._button_provider.set_led(False)
            else:
                hz = self._COUNTDOWN_BUTTON_HZ.get(value, 2.0)
                self._button_provider.set_led(int(now * hz) % 2 == 0)
            return

        if state == AppState.PIN_ENTRY:
            # NEU (3.5): bei falscher PIN Taster-LED synchron zum Ring blitzen,
            # sonst aus.
            deadline = self.model.timers.pin_error_deadline
            if deadline is not None and now < deadline:
                hz = self.config.shutdown.error_button_flash_hz
                self._button_provider.set_led(int(now * hz) % 2 == 0)
            else:
                self._button_provider.set_led(False)
            return

        if state in {
            AppState.CAPTURE_PENDING, AppState.REVIEW, AppState.QR_DISPLAY,
            AppState.DELETE_CONFIRM, AppState.ERROR_SCREEN,
            AppState.BOOT, AppState.MAINTENANCE,
            # NEU (4.1): im Service-Menue loest der Taster nichts aus.
            AppState.ADMIN_MENU,
            # NEU (4.3): Diagnoseseite und Neustart-Zwischenscreen - gleiche
            # Begruendung wie ADMIN_MENU.
            AppState.ADMIN_STATUS, AppState.ADMIN_RESTART_PENDING,
            # NEU (4.4): waehrend Abfrage, Loeschlauf und Ergebnis darf der
            # Taster nichts ausloesen.
            AppState.ADMIN_DELETE_CONFIRM, AppState.ADMIN_DELETE_RUNNING,
            AppState.ADMIN_DELETE_DONE,
            # NEU (4.6): auch im gesamten USB-Ablauf darf der Taster nichts
            # ausloesen.
            AppState.ADMIN_USB_WAIT, AppState.ADMIN_USB_CHECK, AppState.ADMIN_USB_READY,
            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_EJECT, AppState.ADMIN_USB_REMOVE,
            AppState.ADMIN_USB_COPY, AppState.ADMIN_USB_EXPORT_DONE,   # NEU (4.7)
            AppState.ADMIN_USB_CONFLICTS, AppState.ADMIN_USB_RESOLVE,  # NEU (6b)
            AppState.SHUTDOWN_GOODBYE,   # (PIN_ENTRY jetzt oben separat, 3.5)
        }:
            self._button_provider.set_led(False)
            return

        # Alle uebrigen Screens (MAIN_MENU, ATTRACT_GALLERY, PHOTO_INTRO,
        # GALLERY_GRID, GALLERY_FULLSCREEN, INSTRUCTIONS, TERMS): Bereitschafts-
        # signal, 3 kurze Blitze alle 10s - der Taster loest ueberall hier
        # ein Foto aus (bzw. fuehrt zurueck ins Fotografieren-Menue). In
        # TERMS bewirkt ein Tasterdruck aktuell nichts (siehe state_machine.py
        # _handle_terms), das Blinken signalisiert also nur allgemeine
        # Betriebsbereitschaft, keine Aktion an dieser Stelle.
        cycle = now % self._BUTTON_SYNC_CYCLE_SEC
        on = cycle < self._BUTTON_SYNC_FLASH_WINDOW and int(cycle / self._BUTTON_SYNC_FLASH_PERIOD) % 2 == 0
        self._button_provider.set_led(on)

    # -- Preview-Frame -----------------------------------------------------------

    def _get_preview_frame(self) -> pygame.Surface | None:
        """Preview-Frame nur holen, wenn der Zustand es erfordert. Waehrend
        des Countdowns nur bis inkl. Ziffer 2 - bei Ziffer 1 ist das
        Liveview aus (stattdessen "bitte laecheln"-Bild, siehe renderer.py)."""
        if self.model.state == AppState.PHOTO_PREVIEW:
            return self.preview_service.get_frame()
        if self.model.state == AppState.COUNTDOWN and self.model.ui.countdown_value not in (None, 1):
            return self.preview_service.get_frame()
        return None

    # -- Aufräumen ---------------------------------------------------------------

    def _shutdown(self) -> None:
        print("[App] Shutdown...")
        self.preview_service.stop()
        if self._led_provider is not None:
            self._led_provider.stop()
        if self._button_provider is not None:
            self._button_provider.stop()
        # GPIO für Capture aufräumen (nur wenn echter Provider)
        if not self.config.features.use_fake_capture:
            try:
                self.capture_service.provider.cleanup_gpio()  # type: ignore[attr-defined]
            except AttributeError:
                pass
        pygame.quit()
        print("[App] Sauber beendet.")

    @staticmethod
    def _due(deadline: float | None, now: float) -> bool:
        return deadline is not None and now >= deadline


# ------------------------------------------------------------------------------

def main() -> int:
    app = PhotoboothApp()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
