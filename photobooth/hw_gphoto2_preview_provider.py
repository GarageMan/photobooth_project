"""
hw_gphoto2_preview_provider.py
===============================
Preview-Provider, der Live-Vorschaubilder direkt per gphoto2 (USB/PTP) von
der Kamera abruft - Ersatz fuer den HDMI-Grabber.

Hintergrund (siehe Projektnotizen): Die Nikon D3300 deaktiviert ihr eigenes
HDMI-LiveView automatisch, sobald sie eine USB-Verbindung zu einem Host
erkennt, und aktiviert es nicht von selbst wieder, wenn die USB-Verbindung
getrennt wird. Da fuer den Bild-Download nach der Aufnahme (hw_capture_
provider.py) ohnehin eine dauerhafte USB-Verbindung noetig ist, kann der
HDMI-Grabber fuer die Live-Vorschau nicht mehr genutzt werden. Diese Klasse
ersetzt ihn durch gphoto2s eigene Vorschau-Funktion (capture_preview()).

Getestete Eckdaten (Nikon D3300): ca. 640x424 JPEG-Frames, fuer eine
Photobox-Vorschau ausreichend, aber deutlich weniger fluessig als das
vorherige HDMI-Bild (PTP/USB-2.0-Protokoll ist der Flaschenhals, nicht
kameraspezifisch - das betrifft praktisch alle DSLRs).

Hardware: Nikon D3300 per USB direkt am Raspberry Pi. Kein HDMI-Grabber
mehr fuer die Vorschau noetig (HDMI-Kabel kann entfernt werden).

Wichtig - gemeinsamer Kamera-Zugriff:
  gphoto2 erlaubt nur eine aktive Verbindung zur Kamera gleichzeitig. Diese
  Klasse und HwCaptureProvider (Bild-Download) teilen sich deshalb ein
  gemeinsames threading.Lock (camera_lock), das beim Erzeugen beider
  Provider in app_with_hw.py uebergeben wird. Waehrend eines echten Downloads
  pausiert die Vorschau-Schleife automatisch (wartet auf das Lock) und laeuft
  danach von selbst weiter.

Installation: siehe hw_capture_provider.py (python3-gphoto2, libgphoto2-6,
gvfs-gphoto2-volume-monitor deaktivieren).
"""

from __future__ import annotations

import io
import threading
import time
from dataclasses import dataclass, field

try:
    import gphoto2 as gp
    _GP_AVAILABLE = True
except ImportError:
    _GP_AVAILABLE = False
    gp = None  # type: ignore

try:
    import pygame
    _PYGAME_AVAILABLE = True
except ImportError:
    _PYGAME_AVAILABLE = False


_TARGET_FPS = 8  # realistische Grenze fuer gphoto2-Vorschau ueber USB2/PTP
_ERROR_RETRY_SEC = 0.5


@dataclass
class HwGphoto2PreviewProvider:
    """
    Liefert Live-Vorschaubilder als pygame.Surface, per gphoto2 capture_preview().

    Laeuft in einem eigenen Hintergrund-Thread, da jeder capture_preview()-
    Aufruf ein vollstaendiger USB-Roundtrip ist (im Gegensatz zum schnellen,
    synchronen V4L2-Zugriff des frueheren HDMI-Grabbers) und die Pygame-
    Hauptschleife sonst bei jedem Frame kurz haengen wuerde.
    """

    camera_lock: threading.Lock

    _thread: threading.Thread = field(init=False, repr=False)
    _running: bool = field(default=False, init=False)
    _frame_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _latest_frame: object = field(default=None, init=False, repr=False)

    def start(self) -> None:
        if not _GP_AVAILABLE or not _PYGAME_AVAILABLE:
            print("[HwGphoto2PreviewProvider] gphoto2 oder pygame nicht verfügbar - Vorschau deaktiviert.")
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._worker, name="gphoto2_preview_worker", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        with self._frame_lock:
            self._latest_frame = None

    def is_running(self) -> bool:
        return self._running

    def get_frame(self):
        with self._frame_lock:
            return self._latest_frame

    def _worker(self) -> None:
        context = gp.Context()
        camera = gp.Camera()
        try:
            with self.camera_lock:
                camera.init(context)
        except Exception as exc:
            print(f"[HwGphoto2PreviewProvider] Kamera-Init fehlgeschlagen: {exc}")
            self._running = False
            return

        print("[HwGphoto2PreviewProvider] Vorschau gestartet.")
        frame_interval = 1.0 / _TARGET_FPS

        while self._running:
            loop_start = time.monotonic()
            try:
                with self.camera_lock:
                    camera_file = gp.CameraFile()
                    camera.capture_preview(camera_file)
                    raw_bytes = bytes(camera_file.get_data_and_size())
                surface = pygame.image.load(io.BytesIO(raw_bytes), "preview.jpg")
                with self._frame_lock:
                    self._latest_frame = surface
            except Exception as exc:
                print(f"[HwGphoto2PreviewProvider] Vorschau-Fehler: {exc}")
                time.sleep(_ERROR_RETRY_SEC)
                continue

            elapsed = time.monotonic() - loop_start
            time.sleep(max(0.0, frame_interval - elapsed))

        try:
            with self.camera_lock:
                camera.exit(context)
        except Exception:
            pass
        print("[HwGphoto2PreviewProvider] Vorschau gestoppt.")


# ------------------------------------------------------------------------------
# Manueller Schnell-Test (direkt auf dem Pi ausführen)
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    pygame.init()
    screen = pygame.display.set_mode((720, 480))
    pygame.display.set_caption("HwGphoto2PreviewProvider - Schnelltest (ESC zum Beenden)")
    clock = pygame.time.Clock()

    provider = HwGphoto2PreviewProvider(camera_lock=threading.Lock())
    provider.start()

    running = True
    try:
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False

            frame = provider.get_frame()
            screen.fill((20, 20, 20))
            if frame is not None:
                scaled = pygame.transform.smoothscale(frame, screen.get_size())
                screen.blit(scaled, (0, 0))
            pygame.display.flip()
            clock.tick(30)
    finally:
        provider.stop()
        pygame.quit()