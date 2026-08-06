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

from camera_capture import CameraCaptureService, CaptureResult
from camera_preview import CameraPreviewService
import capture_timing  # NEU (Sprint 11, Feature 1)
from hw_capture_provider import CaptureProgress  # NEU (Sprint 11, Feature 1)
import hw_camera_settings_provider  # NEU (Sprint 11, Feature 2)
import event_config_service  # NEU (Veranstaltungsdaten)
from config import DEFAULT_CONFIG, AppConfig, EVENT_CONFIG_PATH
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
from layout import KEYBOARD_SHIFT_MAP, build_layout, button_rects_for_state
from renderer import Renderer
from admin_service import PinLockout, SecretGestureDetector  # NEU (3.4), umbenannt (Sprint 11, vormals shutdown_service.py)
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
        # NEU (Sprint 11, Feature 4): Doppeltap-Erkennung in
        # GALLERY_FULLSCREEN (siehe _handle_pygame_event).
        self._last_fullscreen_tap_time: float | None = None
        self._last_fullscreen_tap_pos: tuple[int, int] | None = None
        self._qr_surface: pygame.Surface | None = None
        self.running = True
        # NEU (4.3): Startzeitpunkt fuer die Laufzeit-Anzeige im Status-Screen.
        self._app_start_monotonic = time.monotonic()
        # NEU (Sprint 11, Feature 1): Ausloesen + gphoto2-Download laufen
        # jetzt in einem Hintergrund-Thread (vorher blockierend im Haupt-
        # thread - siehe _capture_start_transfer/_do_capture), damit
        # Renderer und LED-Ring waehrend der Uebertragung weiter animieren
        # koennen. Gleiches Poll-Muster wie beim Loesch-/USB-Export-Thread.
        self._capture_thread: threading.Thread | None = None
        self._capture_progress: CaptureProgress | None = None
        # Aktuelle Sollzeit-Schaetzung fuer die Uebertragungs-Animation -
        # startet mit dem zuletzt persistierten Wert (siehe
        # capture_timing.py), wird nach jeder echten Aufnahme aktualisiert.
        self._capture_expected_duration = capture_timing.load_expected_duration(
            config.capture_timing_file, config.timeouts.capture_transfer_estimate_seconds,
        )
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

        # NEU (Veranstaltungsdaten): Wallpaper-Auswahl von USB - gleiches
        # Einzelwert-Poll-Muster wie bei _usb_job_result oben (Thread setzt
        # genau einmal, _emit_due_timers pollt).
        self._wallpaper_thread: threading.Thread | None = None
        self._wallpaper_list_job_result = None
        # NEU (Nutzer-Feedback): der Stick bleibt waehrend der gesamten
        # Auswahlliste gemountet (Bugfix-Voraussetzung: erst nach der
        # tatsaechlichen Auswahl wird EIN Bild kopiert, nicht mehr blind das
        # erste gefundene) - gleiches Prinzip wie self._usb_stick beim
        # USB-Export, nur fuer diesen eigenen Ablauf.
        self._wallpaper_pick_stick = None

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
                # NEU (Sprint 11, Feature 1): Fortschritt der laufenden
                # Bilduebertragung (0..1, None wenn gerade keine Uebertragung
                # laeuft) - treibt die Datei-Symbol-Animation im Renderer.
                capture_progress = self._capture_progress_fraction(now)
                self.renderer.render(
                    self.model, fps, preview_frame=preview_frame, qr_surface=self._qr_surface,
                    capture_progress=capture_progress,
                )

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
            # NEU (Nutzer-Feedback): NUR die linke Maustaste/den eigentlichen
            # Touch-Kontakt (button 1) als Tap-Start werten. SDL meldet ein
            # Mausrad-Scrollen (z.B. per VNC-Client vom PC aus) ebenfalls als
            # MOUSEBUTTONDOWN/-UP mit button 4/5 - ohne diese Pruefung wurde
            # jeder Rad-"Klick" wie ein Antippen an der aktuellen Mausposition
            # behandelt und loeste dort einen Tastendruck aus. Echte Touch-
            # Ereignisse auf dem Pi melden immer button==1, daher keine
            # Regression fuer den eigentlichen Touchscreen.
            if event.button != 1:
                return
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

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self.touch_start_x is not None:
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
                # NEU (Sprint 11, Feature 4): Doppeltap auf das Foto blendet
                # (gleichwertig zum Icon "QR-Code anfordern", siehe
                # _map_click_to_event) den QR-Code fuer dieses eine Foto
                # ein. Nur bei einem "stillen" Tap relevant (kein Swipe,
                # s.o.) - ein Einzeltap in der Bildmitte loeste bisher schon
                # nichts aus (siehe _map_click_to_event), daher hier ohne
                # Regressionsrisiko fuer bestehendes Verhalten.
                # GEAENDERT (Sprint-11-Nachbesserung): ganzer Doppeltap-Zweig
                # entfaellt ohne QR-Funktion (config.qr_codes_enabled) - die
                # state_machine wuerde das Event zwar ohnehin ignorieren
                # (siehe _handle_gallery_fullscreen), aber so bleibt auch die
                # Tap-Zeit-/Positions-Verfolgung ungenutzt.
                if self.config.qr_codes_enabled and abs(dx) < 30 and abs(dy) < 30:
                    tap_time = time.monotonic()
                    is_double_tap = (
                        self._last_fullscreen_tap_time is not None
                        and tap_time - self._last_fullscreen_tap_time < 0.4
                        and self._last_fullscreen_tap_pos is not None
                        and abs(start_pos[0] - self._last_fullscreen_tap_pos[0]) < 40
                        and abs(start_pos[1] - self._last_fullscreen_tap_pos[1]) < 40
                    )
                    if is_double_tap:
                        self._last_fullscreen_tap_time = None
                        self._last_fullscreen_tap_pos = None
                        self.dispatch(AppEvent(EventType.TAP_GALLERY_QR, source="touch"))
                        return
                    self._last_fullscreen_tap_time = tap_time
                    self._last_fullscreen_tap_pos = start_pos
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
            elif self.model.state == AppState.ADMIN_EVENT_WALLPAPER_PICK:
                # NEU (Nutzer-Feedback): gleiches Prinzip wie ADMIN_USB_CONFLICTS.
                if dy < -60:
                    self.renderer.wallpaper_pick_scroll_offset += 150
                    return
                if dy > 60:
                    self.renderer.wallpaper_pick_scroll_offset = max(
                        0, self.renderer.wallpaper_pick_scroll_offset - 150
                    )
                    return
            elif self.model.state == AppState.ADMIN_STATUS:
                # NEU (Nutzer-Feedback, Bugfix): gleiches Prinzip wie
                # ADMIN_USB_CONFLICTS - die Diagnosezeilen sind inzwischen
                # zu lang fuer eine Seite, siehe renderer._draw_admin_status.
                if dy < -60:
                    self.renderer.admin_status_scroll_offset += 150
                    return
                if dy > 60:
                    self.renderer.admin_status_scroll_offset = max(
                        0, self.renderer.admin_status_scroll_offset - 150
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

        # NEU (Nutzer-Feedback): Zeilen der Wallpaper-Auswahlliste - gleiches
        # Prinzip wie ADMIN_USB_CONFLICTS oben (Zeilenposition haengt vom
        # Scroll-Offset ab, daher dynamische Hitboxen statt layout.py-Rects).
        if state == AppState.ADMIN_EVENT_WALLPAPER_PICK:
            for rect, name in self.renderer.wallpaper_pick_row_hitboxes:
                if rect.collidepoint(pos):
                    return AppEvent(EventType.TAP_ADMIN_EVENT_WALLPAPER_SELECT, payload={"name": name}, source="touch")

        if state == AppState.PIN_ENTRY:                       # NEU (3.4)
            return self._map_pin_entry_click(pos)

        if state == AppState.ADMIN_EVENT_TEXT_ENTRY:          # NEU (Veranstaltungsdaten)
            return self._map_admin_event_text_entry_click(pos)

        # NEU (4.1): Service-Menue - Treffererkennung gegen exakt dieselben
        # Rechtecke, die der Renderer zeichnet (admin_menu.build_admin_rects).
        if state == AppState.ADMIN_MENU:
            return self._map_admin_menu_click(pos)

        rects = button_rects_for_state(state, self.layout)
        # NEU (Sprint-11-Nachbesserung): kein Icon "QR-Code anfordern" ohne
        # QR-Funktion (config.qr_codes_enabled) - state_machine ignoriert das
        # Event zwar ohnehin (siehe _handle_gallery_fullscreen), aber ein
        # unsichtbar weiter antippbares Icon waere trotzdem verwirrend.
        if not self.config.qr_codes_enabled:
            rects = {name: rect for name, rect in rects.items() if name != "gallery_qr"}
        # NEU (Sprint 11): kein "Galerie"-Button im Hauptmenue ohne
        # Galerie-Funktion (config.gallery_enabled) - gleiches Prinzip wie
        # bei gallery_qr oben. state_machine ignoriert TAP_GALLERY zwar
        # ohnehin (siehe _handle_main_menu), aber ein unsichtbar weiter
        # antippbarer Button waere trotzdem verwirrend.
        if not self.config.gallery_enabled:
            rects = {name: rect for name, rect in rects.items() if name != "gallery"}
        # NEU (Kamera-Menue 2.0): button_rects_for_state() liefert bewusst
        # die Buttons BEIDER Seiten (siehe layout.py) - hier wird auf die
        # aktuell sichtbare Seite eingeschraenkt, sonst waeren z.B. die
        # Weissabgleich-Buttons der Seite 2 schon auf Seite 1 unsichtbar
        # antippbar.
        if state == AppState.ADMIN_CAMERA_SETTINGS:
            page1_only = {
                "admin_camera_iso_minus", "admin_camera_iso_plus",
                "admin_camera_aperture_minus", "admin_camera_aperture_plus",
                "admin_camera_expcomp_minus", "admin_camera_expcomp_plus",
                "admin_camera_metering_minus", "admin_camera_metering_plus",
            }
            page2_only = {
                "admin_camera_wb_minus", "admin_camera_wb_plus",
                "admin_camera_quality_minus", "admin_camera_quality_plus",
                "admin_camera_imagesize_minus", "admin_camera_imagesize_plus",
                "admin_camera_drive_minus", "admin_camera_drive_plus",
            }
            if self.model.ui.admin_camera_page == 0:
                rects = {
                    name: r for name, r in rects.items()
                    if name not in page2_only and name != "admin_camera_page_prev"
                }
            else:
                rects = {
                    name: r for name, r in rects.items()
                    if name not in page1_only and name != "admin_camera_page_next"
                }

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
            # NEU (Sprint-11-Nachbesserung): Sicherheitsabfrage vor dem
            # Herunterfahren - gleiches Prinzip wie admin_delete_confirm/_abort.
            "admin_shutdown_confirm": AppEvent(EventType.TAP_ADMIN_SHUTDOWN_CONFIRM, source="touch"),
            "admin_shutdown_abort":   AppEvent(EventType.TAP_ADMIN_SHUTDOWN_ABORT, source="touch"),
            # NEU (Nutzer-Feedback): Sicherheitsabfrage vor dem App-Neustart -
            # gleiches Prinzip wie admin_shutdown_confirm/_abort.
            "admin_restart_confirm": AppEvent(EventType.TAP_ADMIN_RESTART_CONFIRM, source="touch"),
            "admin_restart_abort":   AppEvent(EventType.TAP_ADMIN_RESTART_ABORT, source="touch"),
            # NEU (4.6): "Weiter" der USB-Bildschirme.
            "usb_continue":   AppEvent(EventType.TAP_ADMIN_USB_CONTINUE, source="touch"),
            "usb_clear":      AppEvent(EventType.TAP_ADMIN_USB_CLEAR, source="touch"),    # NEU (4.7)
            # NEU (6c): Sammelaktionen + "Ausfuehren" auf dem Konflikt-Screen.
            "usb_conflicts_overwrite_all": AppEvent(EventType.TAP_ADMIN_USB_CONFLICTS_OVERWRITE_ALL, source="touch"),
            "usb_conflicts_rename_all":    AppEvent(EventType.TAP_ADMIN_USB_CONFLICTS_RENAME_ALL, source="touch"),
            "usb_conflicts_apply":         AppEvent(EventType.TAP_ADMIN_USB_CONFLICTS_APPLY, source="touch"),
            # NEU (Sprint 11, Feature 4): Icon "QR-Code anfordern" unten
            # rechts in GALLERY_FULLSCREEN - gleichwertige Alternative zum
            # Doppeltap (siehe _handle_pygame_event).
            "gallery_qr": AppEvent(EventType.TAP_GALLERY_QR, source="touch"),
            # NEU (Sprint 11, Feature 2): +/- fuer ISO/Blende im Service-Menue.
            "admin_camera_iso_minus": AppEvent(EventType.TAP_ADMIN_CAMERA_ISO_DOWN, source="touch"),
            "admin_camera_iso_plus": AppEvent(EventType.TAP_ADMIN_CAMERA_ISO_UP, source="touch"),
            "admin_camera_aperture_minus": AppEvent(EventType.TAP_ADMIN_CAMERA_APERTURE_DOWN, source="touch"),
            "admin_camera_aperture_plus": AppEvent(EventType.TAP_ADMIN_CAMERA_APERTURE_UP, source="touch"),
            # NEU (Kamera-Menue 2.0): weitere +/--Paare (Seite 1: Belichtung),
            # Seiten-Navigation, Speichern/Abbrechen statt Zurueck.
            "admin_camera_expcomp_minus": AppEvent(EventType.TAP_ADMIN_CAMERA_EXPCOMP_DOWN, source="touch"),
            "admin_camera_expcomp_plus": AppEvent(EventType.TAP_ADMIN_CAMERA_EXPCOMP_UP, source="touch"),
            "admin_camera_metering_minus": AppEvent(EventType.TAP_ADMIN_CAMERA_METERING_DOWN, source="touch"),
            "admin_camera_metering_plus": AppEvent(EventType.TAP_ADMIN_CAMERA_METERING_UP, source="touch"),
            # Seite 2: Sonstiges.
            "admin_camera_wb_minus": AppEvent(EventType.TAP_ADMIN_CAMERA_WB_DOWN, source="touch"),
            "admin_camera_wb_plus": AppEvent(EventType.TAP_ADMIN_CAMERA_WB_UP, source="touch"),
            "admin_camera_quality_minus": AppEvent(EventType.TAP_ADMIN_CAMERA_QUALITY_DOWN, source="touch"),
            "admin_camera_quality_plus": AppEvent(EventType.TAP_ADMIN_CAMERA_QUALITY_UP, source="touch"),
            "admin_camera_imagesize_minus": AppEvent(EventType.TAP_ADMIN_CAMERA_IMAGESIZE_DOWN, source="touch"),
            "admin_camera_imagesize_plus": AppEvent(EventType.TAP_ADMIN_CAMERA_IMAGESIZE_UP, source="touch"),
            "admin_camera_drive_minus": AppEvent(EventType.TAP_ADMIN_CAMERA_DRIVE_DOWN, source="touch"),
            "admin_camera_drive_plus": AppEvent(EventType.TAP_ADMIN_CAMERA_DRIVE_UP, source="touch"),
            "admin_camera_page_next": AppEvent(EventType.TAP_ADMIN_CAMERA_PAGE_NEXT, source="touch"),
            "admin_camera_page_prev": AppEvent(EventType.TAP_ADMIN_CAMERA_PAGE_PREV, source="touch"),
            "admin_camera_save": AppEvent(EventType.TAP_ADMIN_CAMERA_SAVE, source="touch"),
            "admin_camera_cancel": AppEvent(EventType.TAP_ADMIN_CAMERA_CANCEL, source="touch"),
            # NEU (Veranstaltungsdaten): Uebersichts-Zeilen (oeffnen die
            # Tastatur fuer das jeweilige Feld bzw. kippen einen Schalter
            # direkt) sowie die Bestaetigungs-Screens.
            "admin_event_edit_title": AppEvent(
                EventType.TAP_ADMIN_EVENT_FIELD_EDIT, payload={"field": "title"}, source="touch",
            ),
            "admin_event_edit_prefix": AppEvent(
                EventType.TAP_ADMIN_EVENT_FIELD_EDIT, payload={"field": "prefix"}, source="touch",
            ),
            "admin_event_edit_wifi_ssid": AppEvent(
                EventType.TAP_ADMIN_EVENT_FIELD_EDIT, payload={"field": "wifi_ssid"}, source="touch",
            ),
            "admin_event_edit_wifi_password": AppEvent(
                EventType.TAP_ADMIN_EVENT_FIELD_EDIT, payload={"field": "wifi_password"}, source="touch",
            ),
            "admin_event_toggle_qr": AppEvent(
                EventType.TAP_ADMIN_EVENT_TOGGLE, payload={"field": "qr"}, source="touch",
            ),
            "admin_event_toggle_gallery": AppEvent(
                EventType.TAP_ADMIN_EVENT_TOGGLE, payload={"field": "gallery"}, source="touch",
            ),
            "admin_event_wallpaper": AppEvent(EventType.TAP_ADMIN_EVENT_WALLPAPER_IMPORT, source="touch"),
            # NEU (Nutzer-Feedback): "Standardwerte"-Taste.
            "admin_event_defaults": AppEvent(EventType.TAP_ADMIN_EVENT_DEFAULTS, source="touch"),
            "admin_event_save": AppEvent(EventType.TAP_ADMIN_EVENT_SAVE, source="touch"),
            "admin_event_restart_now": AppEvent(EventType.TAP_ADMIN_EVENT_RESTART_NOW, source="touch"),
            # NEU (Nutzer-Feedback): statische Buttons des Wallpaper-
            # Auswahl-Screens (die Listenzeilen selbst sind dynamisch, siehe
            # das eigene Hitbox-Handling weiter oben in dieser Methode).
            "admin_event_wallpaper_pick_save": AppEvent(EventType.TAP_ADMIN_EVENT_WALLPAPER_PICK_SAVE, source="touch"),
            "admin_event_wallpaper_pick_cancel": AppEvent(
                EventType.TAP_ADMIN_EVENT_WALLPAPER_PICK_CANCEL, source="touch",
            ),
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

    def _map_admin_event_text_entry_click(self, pos: tuple[int, int]) -> AppEvent | None:
        # Bildschirmtastatur: Treffer gegen layout.keyboard_keys pruefen und
        # in das passende Event uebersetzen - gleiches Prinzip wie
        # _map_pin_entry_click oben, nur fuer beliebigen Text statt nur
        # Ziffern.
        for name, rect in self.layout.keyboard_keys.items():
            if not rect.collidepoint(pos):
                continue
            if name == "cancel":
                return AppEvent(EventType.TEXT_ENTRY_CANCEL, source="touch")
            if name == "submit":
                return AppEvent(EventType.TEXT_ENTRY_SUBMIT, source="touch")
            if name == "backspace":
                return AppEvent(EventType.TEXT_ENTRY_BACKSPACE, source="touch")
            if name == "shift":
                return AppEvent(EventType.TEXT_ENTRY_SHIFT, source="touch")
            if name == "space":
                return AppEvent(EventType.TEXT_ENTRY_CHAR, payload={"char": " "}, source="touch")
            # GEAENDERT (Nutzer-Feedback): Umschalt wirkt jetzt zusaetzlich
            # ueber KEYBOARD_SHIFT_MAP auf Ziffern/,.-  (deutsche QWERTZ-
            # Sonderzeichen-Ebene). GEAENDERT (Nutzer-Feedback, Bugfix):
            # ae/oe/ue wurden bisher durch den isascii()-Check bewusst
            # ausgeschlossen (blieben immer klein) - das war nicht das
            # gewuenschte Verhalten. str.upper() wandelt Umlaute in Python
            # korrekt um ("ä" -> "Ä" usw.), daher reicht jetzt ein reiner
            # isalpha()-Check ohne isascii().
            shift = self.model.ui.admin_event_keyboard_shift
            if shift and name in KEYBOARD_SHIFT_MAP:
                char = KEYBOARD_SHIFT_MAP[name]
            elif shift and name.isalpha():
                char = name.upper()
            else:
                char = name
            return AppEvent(EventType.TEXT_ENTRY_CHAR, payload={"char": char}, source="touch")
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

        # GEAENDERT (Nutzer-Feedback): Wallpaper-Auswahl-Job (Stick suchen/
        # mounten/Bilder AUFLISTEN, noch nichts kopiert) - bewusst nicht
        # abbrechbar, gleiches Einzelwert-Poll-Muster wie ADMIN_USB_CHECK/
        # _EJECT oben. Bei Erfolg bleibt der Stick gemountet
        # (self._wallpaper_pick_stick) fuer die anschliessende Auswahlliste -
        # kein invalidate_main_menu_background() mehr hier, das passiert erst
        # beim tatsaechlichen Uebernehmen in _save_admin_event_settings.
        if state == AppState.ADMIN_EVENT_WALLPAPER_PICK_LOADING:
            job = self._wallpaper_list_job_result
            if job is not None:
                self._wallpaper_list_job_result = None
                self._wallpaper_thread = None
                self.dispatch(
                    AppEvent(EventType.ADMIN_EVENT_WALLPAPER_LIST_FINISHED, payload=job, source="wallpaper"),
                    now,
                )
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
            # NEU (Sprint 11, Feature 2): Kamera-Einstellungen - gleiches
            # Idle-Verhalten wie ADMIN_STATUS (schliesst sich nach
            # admin_menu_idle_seconds automatisch, siehe
            # state_machine._go_admin_camera_settings).
            AppState.ADMIN_CAMERA_SETTINGS,
            # NEU (Sprint-11-Nachbesserung): Sicherheitsabfrage vor dem
            # Herunterfahren - gleiche Begruendung wie ADMIN_DELETE_CONFIRM.
            AppState.ADMIN_SHUTDOWN_CONFIRM,
            # NEU (Nutzer-Feedback): Sicherheitsabfrage vor dem App-Neustart -
            # gleiche Begruendung wie ADMIN_SHUTDOWN_CONFIRM. (Bewusst OHNE
            # ADMIN_RESTART_PENDING - der hat weiterhin seinen eigenen, nicht
            # abbrechbaren Timer, siehe oben.)
            AppState.ADMIN_RESTART_CONFIRM,
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
            # NEU (Veranstaltungsdaten): Uebersicht/Tastatur/Wallpaper-Auswahl/
            # -Ergebnis/Gespeichert-Bestaetigung - bewusst OHNE
            # ADMIN_EVENT_WALLPAPER_PICK_LOADING (dort laeuft ein Hintergrund-
            # Thread, nicht unterbrechbar, idle_deadline ist dort ohnehin
            # None; umbenannt von ADMIN_EVENT_WALLPAPER_IMPORT).
            AppState.ADMIN_EVENT_SETTINGS, AppState.ADMIN_EVENT_TEXT_ENTRY,
            AppState.ADMIN_EVENT_WALLPAPER_PICK,
            AppState.ADMIN_EVENT_WALLPAPER_RESULT, AppState.ADMIN_EVENT_SAVED,
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
            self._capture_start_transfer(now)
        # NEU (Sprint 11, Feature 1): pollt den Hintergrund-Thread aus
        # _capture_start_transfer - gleiches Poll-Prinzip wie beim Loesch-/
        # USB-Export-Thread (_delete_result/_usb_job_result).
        elif (
            state == AppState.CAPTURE_PENDING
            and self._capture_progress is not None
            and self._capture_progress.done
        ):
            self._finish_capture_transfer(now)
        elif state == AppState.DELETE_CONFIRM and self._due(timers.delete_deadline, now):
            self.dispatch(AppEvent(EventType.DELETE_TIMEOUT, source="timer"), now)
        elif state == AppState.QR_DISPLAY and self._due(timers.qr_deadline, now):
            self.dispatch(AppEvent(EventType.QR_TIMEOUT, source="timer"), now)
        # NEU (Sprint 11, Feature 4): analog zu QR_DISPLAY/qr_deadline oben.
        elif state == AppState.GALLERY_PHOTO_QR and self._due(timers.gallery_qr_deadline, now):
            self.dispatch(AppEvent(EventType.GALLERY_QR_TIMEOUT, source="timer"), now)

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

    def _capture_start_transfer(self, now: float) -> None:
        """NEU (Sprint 11, Feature 1): startet den kompletten Aufnahme-
        Ablauf (Ausloesen inkl. GPIO-Puls + gphoto2-Download,
        capture_service.capture_photo()) in einem Hintergrund-Thread -
        vorher blockierte das den Hauptthread komplett (mehrere Sekunden),
        wodurch weder ein neuer Frame gezeichnet noch der LED-Ring pro
        Frame aktualisiert werden konnte. Gleiches Poll-Muster wie beim
        Loesch-/USB-Export-Thread (_start_delete_all/_usb_start_check):
        der Worker setzt hier am Ende genau eine Referenz
        (`progress.done = True`), _emit_due_timers pollt sie jeden Frame.

        `expected_duration` treibt sowohl die Datei-Symbol-Animation im
        Renderer (_capture_progress_fraction) als auch den wandernden
        LED-Punkt (_sync_led/LedEffect.CAPTURE_TRANSFER) - beide nutzen
        denselben, aus fruaheren echten Messungen abgeleiteten Schaetzwert
        (siehe capture_timing.py), damit sie synchron zueinander laufen.
        """
        progress = CaptureProgress(started_at=now, expected_duration=self._capture_expected_duration)
        self._capture_progress = progress

        def worker() -> None:
            start = time.monotonic()
            try:
                result = self.capture_service.capture_photo()
            except Exception as exc:  # Sicherheitsnetz - darf den Thread nie stumm sterben lassen
                result = CaptureResult(ok=False, error_message=str(exc))
            progress.measured_seconds = time.monotonic() - start
            progress.result = result
            progress.done = True  # zuletzt setzen - das ist das Fertigsignal fuer den Hauptloop

        self._capture_thread = threading.Thread(target=worker, name="capture-transfer", daemon=True)
        self._capture_thread.start()

    def _finish_capture_transfer(self, now: float) -> None:
        """Wird von _emit_due_timers aufgerufen, sobald der Hintergrund-
        Thread aus _capture_start_transfer fertig ist. Aktualisiert die
        persistierte Zeitschaetzung (capture_timing.py - "einfach mal die
        Uebertragungszeit stoppen") und dispatcht wie vorher CAPTURE_OK/
        CAPTURE_FAILED."""
        progress = self._capture_progress
        self._capture_progress = None
        result = progress.result if progress is not None else None

        # Nur nach einer ERFOLGREICHEN Uebertragung in die Schaetzung
        # einfliessen lassen - ein schnell fehlgeschlagener Versuch (z.B.
        # Kamera nicht erreichbar) wuerde die Zeitschaetzung sonst
        # faelschlich nach unten ziehen, obwohl gar keine echte Uebertragung
        # stattgefunden hat.
        if progress is not None and result is not None and result.ok:
            self._capture_expected_duration = capture_timing.record_duration(
                self.config.capture_timing_file, progress.measured_seconds, self._capture_expected_duration,
            )

        if result is not None and result.ok and result.photo_path:
            self.dispatch(
                AppEvent(
                    EventType.CAPTURE_OK,
                    payload={"photo_path": str(result.photo_path)},
                    source="capture",
                ),
                now,
            )
        else:
            message = result.error_message if result is not None and result.error_message else "Aufnahme fehlgeschlagen."
            self.dispatch(
                AppEvent(EventType.CAPTURE_FAILED, payload={"message": message}, source="capture"),
                now,
            )

    def _capture_progress_fraction(self, now: float) -> float | None:
        """NEU (Sprint 11, Feature 1): 0..1 waehrend eine Uebertragung
        laeuft (fuer die Datei-Symbol-Animation im Renderer), sonst None.
        Bleibt bei 1.0 stehen, falls die echte Uebertragung laenger dauert
        als geschaetzt (kein Ueberschiessen/Zittern der Animation)."""
        progress = self._capture_progress
        if progress is None or progress.expected_duration <= 0:
            return None
        elapsed = now - progress.started_at
        return max(0.0, min(1.0, elapsed / progress.expected_duration))

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
            elif action == "generate_gallery_qr":              # NEU (Sprint 11, Feature 4)
                self._generate_gallery_qr()
            elif action == "read_admin_camera_settings":       # NEU (Sprint 11, Feature 2)
                self._read_admin_camera_settings()
            elif action == "set_admin_camera_iso":             # NEU (Sprint 11, Feature 2)
                self._set_admin_camera_setting(iso=self.model.ui.admin_camera_iso)
            elif action == "set_admin_camera_aperture":        # NEU (Sprint 11, Feature 2)
                self._set_admin_camera_setting(aperture=self.model.ui.admin_camera_aperture)
            elif action == "set_admin_camera_expcomp":         # NEU (Kamera-Menue 2.0)
                self._set_admin_camera_setting(expcomp=self.model.ui.admin_camera_expcomp)
            elif action == "set_admin_camera_metering":        # NEU (Kamera-Menue 2.0)
                self._set_admin_camera_setting(metering=self.model.ui.admin_camera_metering)
            elif action == "set_admin_camera_wb":              # NEU (Kamera-Menue 2.0)
                self._set_admin_camera_setting(white_balance=self.model.ui.admin_camera_wb)
            elif action == "set_admin_camera_quality":         # NEU (Kamera-Menue 2.0)
                self._set_admin_camera_setting(quality=self.model.ui.admin_camera_quality)
            elif action == "set_admin_camera_imagesize":       # NEU (Kamera-Menue 2.0)
                self._set_admin_camera_setting(image_size=self.model.ui.admin_camera_imagesize)
            elif action == "set_admin_camera_drive":           # NEU (Kamera-Menue 2.0)
                self._set_admin_camera_setting(drive_mode=self.model.ui.admin_camera_drive)
            elif action == "revert_admin_camera_settings":     # NEU (Kamera-Menue 2.0)
                self._revert_admin_camera_settings()
            elif action == "collect_admin_event_settings":     # NEU (Veranstaltungsdaten)
                self._collect_admin_event_settings()
            elif action == "save_event_config":                # NEU (Veranstaltungsdaten)
                self._save_admin_event_settings()
            elif action == "wallpaper_pick_list":              # NEU (Nutzer-Feedback), war "wallpaper_import"
                self._wallpaper_start_list()
            elif action == "wallpaper_pick_stage":             # NEU (Nutzer-Feedback)
                self._wallpaper_stage_selected()
            elif action == "wallpaper_pick_discard":           # NEU (Nutzer-Feedback)
                self._wallpaper_pick_discard()
            elif action == "discard_pending_wallpaper":        # NEU (Nutzer-Feedback, Bugfix)
                event_config_service.discard_pending_wallpaper(
                    self.config.assets_dir / event_config_service.WALLPAPER_PENDING_FILENAME
                )

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

    def _generate_qr_surface(self, filename: str | None = None) -> None:
        """GEAENDERT (Sprint 11, Feature 4): `filename` ist jetzt optional
        parametrisierbar (vorher immer `session.qr_filename`) - wird von
        `_generate_gallery_qr()` fuer den QR-Code eines BELIEBIGEN, bereits
        gespeicherten Galerie-Fotos wiederverwendet, nicht nur fuer das
        zuletzt aufgenommene."""
        filename = filename or self.model.session.qr_filename
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

    def _generate_gallery_qr(self) -> None:
        """NEU (Sprint 11, Feature 4): QR-Code fuer das aktuell in
        GALLERY_FULLSCREEN ausgewaehlte Foto (nicht notwendigerweise das
        zuletzt aufgenommene). Da jedes gespeicherte Foto bereits beim
        Speichern (TAP_SAVE -> "export_photo") unter demselben Dateinamen
        ins Web-Verzeichnis kopiert wurde, reicht im Normalfall der reine
        Dateiname - der defensive Re-Export deckt den Randfall ab, dass ein
        Foto aus irgendeinem Grund (z.B. aelterer Bestand vor dieser
        Funktion) noch nicht im Web-Verzeichnis liegt."""
        index = self.model.ui.selected_gallery_index
        photos = self.model.session.photos
        if index is None or not (0 <= index < len(photos)):
            self._qr_surface = None
            return
        path = photos[index]
        name = Path(path).name
        try:
            self.storage_service.export_to_web(path, target_name=name)
        except Exception as exc:
            print(f"[App] Galerie-Foto konnte nicht (erneut) exportiert werden: {exc}")
        self._generate_qr_surface(filename=name)

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
                    # NEU (Feedback): Loesch-/Kopierschutz statt reiner
                    # Anzeige-Ausblendung - siehe config.protected_filenames.
                    excluded_filenames=self.config.protected_filenames,
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
                    # NEU (Feedback): Loesch-/Kopierschutz statt reiner
                    # Anzeige-Ausblendung - siehe config.protected_filenames.
                    excluded_filenames=self.config.protected_filenames,
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
            web_dir=self.config.web_dir,  # NEU (Feedback: geschuetzte Dateien)
            photo_count=photo_count,
            app_start_monotonic=self._app_start_monotonic,
            photo_url_prefix=self.config.network.photo_url_prefix,  # NEU (Diagnose-Feedback)
            protected_photo_filenames=self.config.gallery.example_fly_in_filenames,  # NEU (Feedback)
            protected_web_filenames=("testbild.png",),  # NEU (Feedback)
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
        # NEU (Veranstaltungsdaten): Gaeste-WLAN im Klartext, damit Lutz es
        # vor Ort nachschlagen kann, ohne die JSON-Datei oeffnen zu muessen -
        # nur auf diesem Admin-Screen, NICHT gaeste-sichtbar.
        lines = lines + (
            f"Gäste-WLAN: {self.config.network.guest_wifi_ssid} "
            f"(Passwort: {self.config.network.guest_wifi_password})",
        )
        self.dispatch(AppEvent(EventType.ADMIN_STATUS_READY, payload={"lines": lines}, source="diagnostics"))

    def _collect_admin_event_settings(self) -> None:
        # NEU (Veranstaltungsdaten): synchron ermittelt (reines Auslesen der
        # bereits geladenen AppConfig, kein Datei-/Hardwarezugriff) - gleiches
        # Prinzip wie _collect_admin_status. Bewusst die WERTE DER LAUFENDEN
        # AppConfig, nicht ein frischer load_event_config()-Aufruf: das
        # spiegelt korrekt wider, was die App gerade tatsaechlich verwendet
        # (und damit auch, dass Aenderungen erst nach einem Neustart wirken).
        self.dispatch(
            AppEvent(
                EventType.ADMIN_EVENT_SETTINGS_READY,
                payload={
                    "title": self.config.screen.title,
                    "prefix": self.config.photo_prefix,
                    "wifi_ssid": self.config.network.guest_wifi_ssid,
                    "wifi_password": self.config.network.guest_wifi_password,
                    "qr_enabled": self.config.qr_codes_enabled,
                    "gallery_enabled": self.config.gallery_enabled,
                },
                source="event_settings",
            )
        )

    def _save_admin_event_settings(self) -> None:
        # NEU (Veranstaltungsdaten): schreibt die im Screen bearbeiteten
        # Entwurfswerte nach event_config.json - synchron (ein kleiner JSON-
        # Schreibvorgang, kein Hintergrund-Thread noetig, gleiches Prinzip
        # wie _usb_prepare).
        ui = self.model.ui
        data = {
            "event_title": ui.admin_event_title,
            "photo_prefix": ui.admin_event_prefix,
            "guest_wifi_ssid": ui.admin_event_wifi_ssid,
            "guest_wifi_password": ui.admin_event_wifi_password,
            "qr_codes_enabled": ui.admin_event_qr_enabled,
            "gallery_enabled": ui.admin_event_gallery_enabled,
        }
        ok, message = event_config_service.save_event_config(EVENT_CONFIG_PATH, data)
        if ok:
            # GEAENDERT (Nutzer-Feedback): save_event_config() liefert bei
            # Erfolg "event_config.json gespeichert." zurueck - der
            # Dateiname ist fuer den Admin keine relevante Information
            # (siehe _draw_admin_event_saved). Der Fehlerfall behaelt
            # bewusst die Original-Meldung samt Dateiname/Fehlergrund, das
            # ist beim Fehlersuchen hilfreich.
            message = "Gespeichert."
        # NEU (Nutzer-Feedback, Bugfix): ein per Auswahlliste zwischen-
        # gelagertes Wallpaper (admin_event_wallpaper_pending) wird JETZT,
        # bei erfolgreichem "Speichern" - und nur jetzt - zum echten
        # Hauptmenue-Wallpaper befoerdert. Bei save-Fehlschlag bewusst NICHT
        # befoerdert (bleibt in der Zwischenablage liegen, bis der naechste
        # Speichern-/Abbrechen-Versuch es befoerdert bzw. verwirft).
        if ok and ui.admin_event_wallpaper_pending:
            pending = self.config.assets_dir / event_config_service.WALLPAPER_PENDING_FILENAME
            target = self.config.assets_dir / "hauptmenu_wallpaper.png"
            promoted, _promote_message = event_config_service.promote_pending_wallpaper(pending, target)
            if promoted:
                self.renderer.invalidate_main_menu_background()
        self.dispatch(
            AppEvent(EventType.ADMIN_EVENT_SAVE_RESULT, payload={"ok": ok, "message": message}, source="event_settings")
        )

    def _wallpaper_start_list(self) -> None:
        # NEU (Nutzer-Feedback): Hintergrund-Thread nach demselben
        # Einzelwert-Poll-Muster wie _usb_start_check - Stick suchen,
        # einbinden, ALLE Bilder AUFLISTEN (noch nichts kopieren). Anders
        # als der fruehere Einmal-Ablauf bleibt der Stick bei Erfolg
        # gemountet (self._wallpaper_pick_stick) - der Admin braucht ihn
        # noch fuer die Auswahl auf dem naechsten Screen, gleiches Prinzip
        # wie self._usb_stick beim USB-Export. Setzt NIE self.dispatch() aus
        # dem Thread heraus auf (siehe _emit_due_timers, das den Job
        # pollt), sondern nur self._wallpaper_list_job_result.
        def worker() -> None:
            try:
                partition = admin_usb_service.pick_best_partition(admin_usb_service.find_usb_partitions())
                if partition is None:
                    self._wallpaper_list_job_result = {"ok": False, "lines": ("Kein USB-Stick gefunden.",)}
                    return
                stick, mount_message = admin_usb_service.mount_partition(partition)
                if stick is None:
                    self._wallpaper_list_job_result = {"ok": False, "lines": (mount_message,)}
                    return
                candidates = event_config_service.find_wallpaper_candidates(stick.mountpoint)
                if not candidates:
                    if stick.mounted_by_us:
                        admin_usb_service.unmount(stick.mountpoint)
                    self._wallpaper_list_job_result = {
                        "ok": False,
                        "lines": ("Kein Bild (.png/.jpg) auf dem Stick gefunden.",),
                    }
                    return
                # Stick bleibt bewusst gemountet - erst _wallpaper_stage_selected
                # (Speichern) oder _wallpaper_pick_discard (Abbrechen) haengt
                # ihn wieder aus.
                self._wallpaper_pick_stick = stick
                self._wallpaper_list_job_result = {
                    "ok": True, "candidates": tuple(p.name for p in candidates),
                }
            except Exception as exc:
                print(f"[App] FEHLER bei der Wallpaper-Suche: {exc}")
                self._wallpaper_list_job_result = {
                    "ok": False,
                    "lines": ("Unerwarteter Fehler bei der Wallpaper-Suche.", str(exc)[:70]),
                }

        self._wallpaper_thread = threading.Thread(target=worker, name="wallpaper-list", daemon=True)
        self._wallpaper_thread.start()

    def _wallpaper_stage_selected(self) -> None:
        # NEU (Nutzer-Feedback): "Speichern" im Auswahl-Screen - kopiert NUR
        # in die Zwischenablage (event_config_service.WALLPAPER_PENDING_
        # FILENAME), macht die Auswahl noch NICHT zum echten Hauptmenue-
        # Wallpaper (das passiert erst in _save_admin_event_settings, siehe
        # Bugfix-Kommentar dort). Synchron statt Hintergrund-Thread: die
        # Datei ist bereits auf dem gemounteten Stick lokalisiert (kein
        # erneutes Suchen/Mounten noetig) und durch _MAX_WALLPAPER_BYTES auf
        # maximal 30 MB begrenzt - ein kurzer, tolerierbarer Kopiervorgang,
        # gleiches Prinzip wie das synchrone save_event_config oben.
        stick = self._wallpaper_pick_stick
        selected = self.model.ui.admin_event_wallpaper_selected
        ok = False
        message = "Kein USB-Stick mehr eingebunden."
        if stick is not None and selected:
            source = stick.mountpoint / selected
            target = self.config.assets_dir / event_config_service.WALLPAPER_PENDING_FILENAME
            ok, message = event_config_service.import_wallpaper(source, target)
        if stick is not None:
            if stick.mounted_by_us:
                admin_usb_service.unmount(stick.mountpoint)
            self._wallpaper_pick_stick = None
        self.dispatch(
            AppEvent(EventType.ADMIN_EVENT_WALLPAPER_STAGE_RESULT, payload={"ok": ok, "message": message}, source="wallpaper")
        )

    def _wallpaper_pick_discard(self) -> None:
        # NEU (Nutzer-Feedback): "Abbrechen" im Auswahl-Screen (oder
        # Idle-Timeout) - haengt den Stick nur wieder aus, kein Datei-
        # Vorgang (noch nichts wurde kopiert).
        stick = self._wallpaper_pick_stick
        if stick is not None:
            if stick.mounted_by_us:
                admin_usb_service.unmount(stick.mountpoint)
            self._wallpaper_pick_stick = None

    def _read_admin_camera_settings(self) -> None:
        # NEU (Sprint 11, Feature 2): synchron ermittelt (ein gphoto2-
        # get_config()-Aufruf, ueblicherweise deutlich unter einer Sekunde,
        # siehe hw_camera_settings_provider.py) - ausgeloest durch einen
        # bewussten Tap im Service-Menue, gleiches Prinzip wie
        # _collect_admin_status.
        # GEAENDERT (Kamera-Menue 2.0): laeuft bereits eine Live-Vorschau fuer
        # diesen Screen (start_preview beim Betreten, siehe state_machine.
        # _go_admin_camera_settings), wird deren bereits offene Kamera-
        # Sitzung mitbenutzt (siehe hw_camera_settings_provider.py-Docstring,
        # gphoto2-Issue #491) statt einer zweiten, unabhaengigen Sitzung.
        used_shared, snapshot = self.preview_service.run_with_camera(
            lambda camera, context: hw_camera_settings_provider.read_current(
                self._camera_lock, camera=camera, context=context
            )
        )
        if not used_shared:
            snapshot = hw_camera_settings_provider.read_current(self._camera_lock)
        self.dispatch(AppEvent(
            EventType.ADMIN_CAMERA_SETTINGS_READY,
            payload={
                "available": snapshot.available,
                "error": snapshot.error,
                "iso": snapshot.iso,
                "iso_choices": snapshot.iso_choices,
                "aperture": snapshot.aperture,
                "aperture_choices": snapshot.aperture_choices,
                "shutter": snapshot.shutter,
                "expcomp": snapshot.expcomp,
                "expcomp_choices": snapshot.expcomp_choices,
                "metering": snapshot.metering,
                "metering_choices": snapshot.metering_choices,
                "white_balance": snapshot.white_balance,
                "white_balance_choices": snapshot.white_balance_choices,
                "quality": snapshot.quality,
                "quality_choices": snapshot.quality_choices,
                "image_size": snapshot.image_size,
                "image_size_choices": snapshot.image_size_choices,
                "drive_mode": snapshot.drive_mode,
                "drive_mode_choices": snapshot.drive_mode_choices,
            },
            source="camera_settings",
        ))

    # NEU (Kamera-Menue 2.0): Name des jeweiligen set_*-Aufrufs in
    # hw_camera_settings_provider.py, ueber das UiState-Feld indiziert -
    # vermeidet eine 8-fache if/elif-Kette in _set_admin_camera_setting().
    _CAMERA_SETTER_BY_KWARG = {
        "iso": "set_iso",
        "aperture": "set_aperture",
        "expcomp": "set_expcomp",
        "metering": "set_metering",
        "white_balance": "set_white_balance",
        "quality": "set_quality",
        "image_size": "set_image_size",
        "drive_mode": "set_drive_mode",
    }

    def _set_admin_camera_setting(self, **kwargs: str) -> None:
        # NEU (Sprint 11, Feature 2, erweitert Kamera-Menue 2.0): der neue Wert
        # steht bereits optimistisch in model.ui (state_machine.
        # _step_admin_camera_field hat ihn vor dem Dispatch dieser Aktion
        # gesetzt) - hier wird er nur noch an die Kamera durchgereicht.
        # Erwartet genau EIN Keyword-Argument (iso=... ODER aperture=...
        # ODER ...), siehe die Aufrufer unten in _apply_actions().
        name, value = next(iter(kwargs.items()), (None, None))
        setter_name = self._CAMERA_SETTER_BY_KWARG.get(name or "")
        if setter_name is None or value is None:
            return
        setter = getattr(hw_camera_settings_provider, setter_name)
        used_shared, result = self.preview_service.run_with_camera(
            lambda camera, context: setter(self._camera_lock, value, camera=camera, context=context)
        )
        ok, error = result if used_shared else setter(self._camera_lock, value)
        if not ok:
            print(f"[App] Kamera-Einstellung konnte nicht gesetzt werden ({name}): {error}")
        # BUGFIX (Nutzer-Feedback nach Live-Test): frueher wurde nur im
        # Fehlerfall neu gelesen - die Verschlusszeit (reiner Info-Wert, von
        # der Kamera im Modus A automatisch aus ISO/Blende/Belichtungs-
        # korrektur/Messfeld errechnet) blieb dadurch nach einer
        # ERFOLGREICHEN Aenderung auf dem zuletzt gelesenen, jetzt veralteten
        # Stand stehen. Jetzt wird nach JEDER Aenderung (Erfolg oder
        # Fehlschlag) neu gelesen - stellt ausserdem sicher, dass auch der
        # geaenderte Wert selbst garantiert den tatsaechlich aktiven
        # Kamera-Stand zeigt, nicht nur den zuletzt angeforderten.
        self._read_admin_camera_settings()

    def _revert_admin_camera_settings(self) -> None:
        """NEU (Kamera-Menue 2.0): 'Abbrechen' - sendet alle Werte zurueck,
        mit denen der Kamera-Einstellungen-Screen betreten wurde (siehe
        models.UiState.admin_camera_entry_*). Ohne Momentaufnahme (Kamera
        beim Betreten nicht erreichbar) gibt es nichts zurueckzusetzen."""
        ui = self.model.ui
        if not ui.admin_camera_entry_captured:
            return
        entries = (
            ("iso", ui.admin_camera_entry_iso),
            ("aperture", ui.admin_camera_entry_aperture),
            ("expcomp", ui.admin_camera_entry_expcomp),
            ("metering", ui.admin_camera_entry_metering),
            ("white_balance", ui.admin_camera_entry_wb),
            ("quality", ui.admin_camera_entry_quality),
            ("image_size", ui.admin_camera_entry_imagesize),
            ("drive_mode", ui.admin_camera_entry_drive),
        )

        def _apply(camera, context) -> None:
            for name, value in entries:
                if not value:
                    continue
                setter = getattr(hw_camera_settings_provider, self._CAMERA_SETTER_BY_KWARG[name])
                ok, error = setter(self._camera_lock, value, camera=camera, context=context)
                if not ok:
                    print(f"[App] Abbrechen: {name} konnte nicht zurückgesetzt werden: {error}")

        used_shared, _ = self.preview_service.run_with_camera(lambda camera, context: _apply(camera, context))
        if not used_shared:
            _apply(None, None)

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
        # NEU (Sprint 11, Feature 1): Sollzeit fuer zeitgesteuerte Effekte
        # (aktuell nur CAPTURE_TRANSFER, siehe CAPTURE_PENDING-Zweig unten).
        duration: float | None = None

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
            if self._capture_progress is not None:
                # NEU (Sprint 11, Feature 1): Uebertragung laeuft bereits im
                # Hintergrund-Thread (siehe _capture_start_transfer) - der
                # wandernde gruene Punkt ersetzt das vorherige statische
                # CAPTURE_PROCESSING. Anders als vorher kann _sync_led()
                # hier jeden Frame normal durchlaufen, weil der Hauptthread
                # waehrend der Uebertragung nicht mehr blockiert.
                effect = LedEffect.CAPTURE_TRANSFER
                duration = self._capture_progress.expected_duration
            else:
                # Das Weiss-Blitzen aus Ziffer "1" laeuft in CAPTURE_PENDING
                # noch kurz weiter, geht aber rechtzeitig vor dem eigentlichen
                # GPIO-Ausloeseimpuls (capture_trigger_deadline) wieder aus -
                # keine Reflexionen in Brillen im Moment der Aufnahme.
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
        elif state == AppState.GALLERY_PHOTO_QR:
            # NEU (Sprint 11, Feature 4): gleicher Effekt/gleiche Bedeutung
            # wie QR_DISPLAY ("ein QR-Code ist gerade auf dem Schirm") -
            # kein neuer LedEffect noetig.
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
        elif state == AppState.ADMIN_CAMERA_SETTINGS:
            # NEU (Sprint 11, Feature 2): gleiche ruhige Welle wie die
            # uebrigen Service-Menue-Unterseiten (ADMIN_STATUS).
            effect = LedEffect.INSTRUCTIONS_WAVE
        elif state in {
            AppState.ADMIN_EVENT_SETTINGS, AppState.ADMIN_EVENT_TEXT_ENTRY,
            AppState.ADMIN_EVENT_WALLPAPER_PICK,
            AppState.ADMIN_EVENT_WALLPAPER_RESULT, AppState.ADMIN_EVENT_SAVED,
        }:
            # NEU (Veranstaltungsdaten): gleiche ruhige Welle wie die
            # uebrigen Service-Menue-Unterseiten.
            effect = LedEffect.INSTRUCTIONS_WAVE
        elif state == AppState.ADMIN_EVENT_WALLPAPER_PICK_LOADING:
            # NEU (Veranstaltungsdaten): "es passiert gerade etwas" - wie
            # beim USB-Pruefen/Neustart. Umbenannt von
            # ADMIN_EVENT_WALLPAPER_IMPORT.
            effect = LedEffect.CAPTURE_PROCESSING
        elif state == AppState.ADMIN_RESTART_PENDING:
            # NEU (4.3): gruen wie waehrend der Kamera-Verarbeitung -
            # signalisiert "es passiert gerade etwas", kein neuer Effekt noetig.
            effect = LedEffect.CAPTURE_PROCESSING
        elif state == AppState.ADMIN_RESTART_CONFIRM:
            # NEU (Nutzer-Feedback): bewusst die ruhige Welle statt des roten
            # Warnblinkens von ADMIN_SHUTDOWN_CONFIRM/ADMIN_DELETE_CONFIRM -
            # ein App-Neustart ist (anders als Herunterfahren oder
            # unwiderrufliches Loeschen) folgenlos/jederzeit wiederholbar,
            # das Warnblinken waere hier unangemessen dramatisch.
            effect = LedEffect.INSTRUCTIONS_WAVE
        elif state in {
            AppState.ADMIN_DELETE_CONFIRM, AppState.ADMIN_DELETE_RUNNING,
            # NEU (Sprint-11-Nachbesserung): gleiches Warnblinken fuer die
            # Herunterfahren-Sicherheitsabfrage - beide sind "gefaehrliche"
            # Bestaetigungen.
            AppState.ADMIN_SHUTDOWN_CONFIRM,
        }:
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

        self.led_service.set_effect(effect, duration=duration)
        self._led_provider.set_effect(effect, duration=duration)

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
            # NEU (Sprint 11, Feature 2): gleiche Begruendung - der Taster
            # loest hier keine ISO-/Blendenaenderung aus.
            AppState.ADMIN_CAMERA_SETTINGS,
            # NEU (Sprint 11, Feature 4): waehrend die QR-Karte fuer EIN
            # Foto angezeigt wird, soll ein Tasterdruck (anders als sonst in
            # der Galerie) KEINE neue Aufnahme starten - state_machine.
            # _handle_gallery_photo_qr behandelt BUTTON_PRESS bewusst nicht,
            # das "Bereitschafts"-Blinken waere hier also irrefuehrend.
            AppState.GALLERY_PHOTO_QR,
            # NEU (Sprint-11-Nachbesserung): waehrend der Herunterfahren-
            # Sicherheitsabfrage darf der Taster nichts ausloesen.
            AppState.ADMIN_SHUTDOWN_CONFIRM,
            # NEU (Nutzer-Feedback): gleiches Prinzip fuer die Neustart-
            # Sicherheitsabfrage.
            AppState.ADMIN_RESTART_CONFIRM,
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
            # NEU (Veranstaltungsdaten): auch hier darf der Taster nichts
            # ausloesen (Service-Menue-Unterseite wie ADMIN_STATUS).
            AppState.ADMIN_EVENT_SETTINGS, AppState.ADMIN_EVENT_TEXT_ENTRY,
            AppState.ADMIN_EVENT_WALLPAPER_PICK_LOADING, AppState.ADMIN_EVENT_WALLPAPER_PICK,
            AppState.ADMIN_EVENT_WALLPAPER_RESULT,
            AppState.ADMIN_EVENT_SAVED,
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
        Liveview aus (stattdessen "bitte laecheln"-Bild, siehe renderer.py).
        GEAENDERT (Kamera-Menue 2.0): auch im Kamera-Einstellungen-Screen
        wird die Vorschau geholt - dort aber NICHT vollflaechig gezeichnet
        (siehe render()/_draw_admin_camera_settings), sondern in einem
        kleineren Panel neben den Einstell-Zeilen."""
        if self.model.state == AppState.PHOTO_PREVIEW:
            return self.preview_service.get_frame()
        if self.model.state == AppState.COUNTDOWN and self.model.ui.countdown_value not in (None, 1):
            return self.preview_service.get_frame()
        if self.model.state == AppState.ADMIN_CAMERA_SETTINGS:
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
