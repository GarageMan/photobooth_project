"""
app_with_hw.py  →  Umbenennen zu app.py auf dem Pi
====================================================
Hauptschleife der Fotobox V2.
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
from storage_service import StorageService
from layout import build_layout, button_rects_for_state
from renderer import Renderer


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
        return HwCaptureProvider(camera_lock=camera_lock)


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
        self.gallery_service = GalleryService(config.photo_dir)
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
            return

        if event.type == pygame.MOUSEBUTTONUP and self.touch_start_x is not None:
            dx = event.pos[0] - self.touch_start_x
            dy = event.pos[1] - (self.touch_start_y or event.pos[1])
            start_pos = (self.touch_start_x, self.touch_start_y)
            self.touch_start_x = None
            self.touch_start_y = None

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

        rects = button_rects_for_state(state, self.layout)

        mapping = {
            "photo":          AppEvent(EventType.TAP_PHOTO, source="touch"),
            "gallery":        AppEvent(EventType.TAP_GALLERY, source="touch"),
            "instructions":   AppEvent(EventType.TAP_INSTRUCTIONS, source="touch"),
            "back":           AppEvent(EventType.TAP_BACK, source="touch"),
            "cancel":         AppEvent(EventType.TAP_CANCEL, source="touch"),
            "save":           AppEvent(EventType.TAP_SAVE, payload={"filename": None}, source="touch"),
            "delete":         AppEvent(EventType.TAP_DELETE, source="touch"),
            "confirm_delete": AppEvent(EventType.TAP_CONFIRM_DELETE, source="touch"),
            "abort_delete":   AppEvent(EventType.TAP_ABORT_DELETE, source="touch"),
        }
        for name, rect in rects.items():
            if rect.collidepoint(pos) and name in mapping:
                return mapping[name]
        return None

    # -- Timer-Events ----------------------------------------------------------

    def _emit_due_timers(self, now: float) -> None:
        timers = self.model.timers
        state = self.model.state

        idle_states = {
            AppState.MAIN_MENU,
            AppState.PHOTO_INTRO,
            AppState.PHOTO_PREVIEW,
            AppState.GALLERY_GRID,
            AppState.GALLERY_FULLSCREEN,
            AppState.REVIEW,
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
        result = self.state_machine.transition(self.model, event, now)
        self.model = result.model
        self._apply_actions(result.actions, now)
        if self.model.state in {AppState.GALLERY_GRID, AppState.GALLERY_FULLSCREEN, AppState.ATTRACT_GALLERY}:
            photos = tuple(self.gallery_service.list_photos())
            self.model = self.model.evolve(session=replace(self.model.session, photos=photos))

    def _apply_actions(self, actions: tuple[str, ...], now: float) -> None:
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
                self._delete_photo()

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

    def _delete_photo(self) -> None:
        path = self.model.session.current_photo_path
        if not path:
            return
        deleted = self.gallery_service.delete_photo(path)
        if deleted:
            print(f"[App] Foto gelöscht: {path}")

    # -- LED & Button-LED synchronisieren --------------------------------------

    # Identischer 10s-Bereitschafts-Blink-Zyklus wie in hw_led_provider.py's
    # MAIN_MENU-Sync-Blitz - beide Threads lesen unabhaengig dieselbe
    # time.monotonic()-Uhr mit derselben Formel, dadurch laufen LED-Ring und
    # Taster-LED garantiert im Gleichtakt ohne Parameteruebergabe.
    _BUTTON_SYNC_CYCLE_SEC = 10.0
    _BUTTON_SYNC_FLASH_WINDOW = 0.75
    _BUTTON_SYNC_FLASH_PERIOD = 0.15

    # Zunehmend schnelleres Blinken der Taster-LED je naeher der Auslösung
    # (Ziffer 4 -> 3 -> 2); bei Ziffer 1 ist die Taster-LED aus (siehe unten).
    _COUNTDOWN_BUTTON_HZ = {4: 2.0, 3: 3.5, 2: 5.0}

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

        if state in {
            AppState.MAIN_MENU, AppState.ATTRACT_GALLERY,
            AppState.PHOTO_INTRO, AppState.INSTRUCTIONS,
        }:
            effect = LedEffect.MAIN_MENU
        elif state == AppState.PHOTO_PREVIEW:
            effect = LedEffect.PREVIEW
        elif state == AppState.COUNTDOWN:
            value = self.model.ui.countdown_value
            if value == 4:
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
        elif state in {AppState.GALLERY_GRID, AppState.GALLERY_FULLSCREEN}:
            effect = LedEffect.GALLERY_STARFIELD
        elif state == AppState.ERROR_SCREEN:
            effect = LedEffect.ERROR
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

        if state in {
            AppState.CAPTURE_PENDING, AppState.REVIEW, AppState.QR_DISPLAY,
            AppState.DELETE_CONFIRM, AppState.ERROR_SCREEN,
            AppState.BOOT, AppState.MAINTENANCE,
        }:
            self._button_provider.set_led(False)
            return

        # Alle uebrigen Screens (MAIN_MENU, ATTRACT_GALLERY, PHOTO_INTRO,
        # GALLERY_GRID, GALLERY_FULLSCREEN, INSTRUCTIONS): Bereitschafts-
        # signal, 3 kurze Blitze alle 10s - der Taster loest ueberall hier
        # ein Foto aus (bzw. fuehrt zurueck ins Fotografieren-Menue).
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
