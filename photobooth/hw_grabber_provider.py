"""
hw_grabber_provider.py
======================
Echter Preview-Provider für den UGREEN HDMI Video Card MC629 (USB-Grabber).

Der Grabber stellt das Live-Bild der Nikon D3300 (via LiveView + HDMI)
als V4L2-Kameradevice bereit. pygame.camera kapselt den V4L2-Zugriff.

Hardware:
  - UGREEN HDMI Video Card MC629
  - Muss an einem der blauen USB 3.0-Ports des Raspberry Pi 5 angeschlossen sein
  - Nikon D3300: Modus M, LiveView EIN, Auto-Off AUS

Voraussetzungen auf dem Pi:
  sudo apt install python3-pygame -y
  # V4L2-Device prüfen:
  v4l2-ctl --list-devices
  # Erwartete Ausgabe enthält etwas wie: /dev/video0

Typisches Device: /dev/video0
Falls kein Gerät gefunden: USB-Port wechseln (zwingend USB 3.0 = blau).

Einbindung in app.py:
  if not config.features.use_fake_preview:
      from hw_grabber_provider import HwGrabberProvider
      preview_provider = HwGrabberProvider(config)
  else:
      from fake_preview_service import FakePreviewService
      preview_provider = FakePreviewService()
  preview_service = CameraPreviewService(provider=preview_provider)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pygame
import pygame.camera

# Pygame-Camera muss einmal initialisiert werden
_PYGAME_CAM_INITED = False


def _ensure_pygame_camera_init() -> None:
    global _PYGAME_CAM_INITED
    if not _PYGAME_CAM_INITED:
        pygame.camera.init()
        _PYGAME_CAM_INITED = True


@dataclass
class HwGrabberProvider:
    """
    V4L2-basierter Preview-Provider für den USB-HDMI-Grabber.

    Implementiert die PreviewProvider-Protocol-Schnittstelle aus camera_preview.py,
    damit er transparent gegen FakePreviewService austauschbar ist.

    Auflösung:
      Der Grabber liefert 1280×720 (720p). Das wird beim Rendern
      auf die Bildschirmgröße skaliert (1080×1920 Portrait).
      Die Skalierung geschieht im Renderer, nicht hier.
    """

    capture_width: int = 1280
    capture_height: int = 720
    preferred_device: str | None = None   # None = automatisch erstes Gerät

    _cam: Any = field(default=None, init=False, repr=False)
    _running: bool = field(default=False, init=False)
    _last_frame: pygame.Surface | None = field(default=None, init=False, repr=False)

    # -- PreviewProvider Protocol ----------------------------------------------

    def start(self) -> None:
        """
        HDMI-Grabber öffnen und Bilderfassung starten.
        Wirft RuntimeError wenn kein Gerät gefunden wird.
        """
        if self._running:
            return
        _ensure_pygame_camera_init()

        device = self._find_device()
        try:
            self._cam = pygame.camera.Camera(
                device,
                (self.capture_width, self.capture_height),
            )
            self._cam.start()
            self._running = True
            print(f"[HwGrabberProvider] Gestartet auf {device} "
                  f"({self.capture_width}×{self.capture_height}).")
        except Exception as exc:
            self._cam = None
            self._running = False
            raise RuntimeError(
                f"[HwGrabberProvider] Konnte Kamera '{device}' nicht öffnen: {exc}"
            ) from exc

    def stop(self) -> None:
        """Bilderfassung stoppen und Grabber freigeben."""
        if not self._running or self._cam is None:
            return
        try:
            self._cam.stop()
        except Exception as exc:
            print(f"[HwGrabberProvider] Fehler beim Stoppen: {exc}")
        finally:
            self._cam = None
            self._running = False
            self._last_frame = None
            print("[HwGrabberProvider] Gestoppt.")

    def is_running(self) -> bool:
        return self._running

    def get_frame(self) -> pygame.Surface | None:
        """
        Aktuelles Frame vom Grabber holen.
        Gibt None zurück, wenn kein Frame verfügbar ist.
        Puffert das letzte gültige Frame, damit der Renderer nie
        auf None prüfen muss (er bekommt immer das aktuellste Bild).
        """
        if not self._running or self._cam is None:
            return self._last_frame
        try:
            if self._cam.query_image():
                self._last_frame = self._cam.get_image()
        except Exception as exc:
            print(f"[HwGrabberProvider] Frame-Fehler: {exc}")
            # Grabber nicht sofort stoppen – könnte transient sein
        return self._last_frame

    # -- Hilfsmethoden ---------------------------------------------------------

    def _find_device(self) -> str:
        """
        Passendes V4L2-Device bestimmen.
        Bevorzugt self.preferred_device, sucht sonst das erste verfügbare.
        """
        if self.preferred_device:
            return self.preferred_device

        try:
            cameras = pygame.camera.list_cameras()
        except Exception:
            cameras = []

        if not cameras:
            raise RuntimeError(
                "[HwGrabberProvider] Kein HDMI-Grabber gefunden. "
                "USB-Port prüfen (blauer USB 3.0-Port!) und sicherstellen, "
                "dass die Nikon eingeschaltet und LiveView aktiv ist."
            )

        print(f"[HwGrabberProvider] Gefundene Kamera-Devices: {cameras}")
        # Ersten Eintrag nehmen – bei mehreren Kameras preferred_device setzen
        return cameras[0]

    def list_available_devices(self) -> list[str]:
        """Hilfsmethode für Diagnose – alle V4L2-Devices ausgeben."""
        _ensure_pygame_camera_init()
        try:
            return list(pygame.camera.list_cameras())
        except Exception:
            return []


# ------------------------------------------------------------------------------
# Manuelle Schnell-Tests (direkt auf dem Pi ausführen)
# Zeigt 5 Sekunden Live-Bild im Pygame-Fenster, dann Ende.
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    pygame.init()
    screen = pygame.display.set_mode((1280, 720))
    pygame.display.set_caption("HwGrabberProvider – Schnelltest (5 s)")
    clock = pygame.time.Clock()

    provider = HwGrabberProvider()
    print("Verfügbare Devices:", provider.list_available_devices())

    try:
        provider.start()
    except RuntimeError as e:
        print(e)
        pygame.quit()
        raise SystemExit(1)

    start = time.monotonic()
    running = True
    while running and (time.monotonic() - start) < 5.0:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False

        frame = provider.get_frame()
        if frame is not None:
            screen.blit(frame, (0, 0))
        pygame.display.flip()
        clock.tick(30)

    provider.stop()
    pygame.quit()
    print("Schnelltest beendet.")
