"""
hw_capture_provider.py
======================
Echter Capture-Provider: GPIO-Shutter (Optokoppler) + gphoto2-Download.

Hardware-Kette:
  Raspberry Pi GPIO 17 → 220 Ω → Optokoppler PC817 → Nikon MC-DC2
  Pin 1+2 Optokoppler = Eingang (von Pi)
  Pin 3+4 Optokoppler = Ausgang → Nikon: Pin 5 (FOCUS) + Pin 6 (SHUTTER)
                                  GND:   Pin 3 (GND)
  Hinweis: Focus und Shutter müssen gleichzeitig auf GND gelegt werden!

Nikon D3300 Einstellungen:
  - Modus: M (Manuell)
  - Fokus: MF (Manuell, einmalig eingestellt)
  - LiveView: EIN
  - Auto-Off / Energiesparen: AUS
  - Bildformat: JPEG (Fine)

Installation auf dem Pi:
  sudo apt install python3-gphoto2 libgphoto2-6 -y
  sudo pip install gphoto2 --break-system-packages

Bekannter Konflikt:
  gvfs-gphoto2-volume-monitor blockiert den gphoto2-Zugriff.
  Lösung: In kill_gvfs() unten oder per systemd-Unit deaktivieren.

Einbindung in app.py:
  if not config.features.use_fake_capture:
      from hw_capture_provider import HwCaptureProvider
      capture_provider = HwCaptureProvider(config)
  else:
      from fake_capture_service import FakeCaptureService
      capture_provider = FakeCaptureService(fixture_dir=config.assets_dir)
  capture_service = CameraCaptureService(
      provider=capture_provider,
      target_dir=config.photo_dir,
  )
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from config import AppConfig

try:
    import RPi.GPIO as GPIO
    _GPIO_AVAILABLE = True
except ImportError:
    _GPIO_AVAILABLE = False
    GPIO = None  # type: ignore

try:
    import gphoto2 as gp
    _GP_AVAILABLE = True
except ImportError:
    _GP_AVAILABLE = False
    gp = None  # type: ignore


# ------------------------------------------------------------------------------
# Konfigurations-Konstanten
# ------------------------------------------------------------------------------
_SHUTTER_PIN          = 17    # BCM-Nummer; GPIO 17 = physischer Pin 11
_SHUTTER_PULSE_SEC    = 0.25  # Sekunden; Auslösepuls (250 ms reicht der D3300)
_GPHOTO2_WAIT_SEC     = 2.5   # Sekunden; Wartezeit nach Auslösung (JPG-Puffer)
_GPHOTO2_RETRY_COUNT  = 3     # Anzahl Versuche bei transienten Fehlern
_GPHOTO2_RETRY_WAIT   = 1.0   # Sekunden zwischen Versuchen


@dataclass
class HwCaptureProvider:
    """
    Kapselt den kompletten Foto-Workflow:
      1. GVFS-Blocker beenden (einmalig beim Start)
      2. GPIO-Shutter-Pin initialisieren
      3. Bei capture(): GPIO-Puls → Kamera löst aus → gphoto2 lädt Bild herunter

    Implementiert die CaptureProvider-Protocol-Schnittstelle aus camera_capture.py.

    camera_lock: gemeinsames Lock mit HwGphoto2PreviewProvider - verhindert,
    dass Download und Live-Vorschau gleichzeitig auf die Kamera zugreifen
    (gphoto2 erlaubt immer nur eine aktive Verbindung).
    """

    camera_lock: threading.Lock = field(default_factory=threading.Lock)
    # Fuer photo_prefix (siehe config.py) beim Erzeugen des Dateinamens in
    # _fetch_image(). Default_factory nur als Sicherheitsnetz fuer
    # bestehende Aufrufer/Tests ohne explizites config - im echten Betrieb
    # wird stets die tatsaechliche AppConfig-Instanz von app_with_hw.py
    # uebergeben (siehe _build_capture_provider()).
    config: AppConfig = field(default_factory=AppConfig)
    _gpio_ready: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not _GPIO_AVAILABLE:
            print("[HwCaptureProvider] RPi.GPIO nicht verfügbar - Shutter deaktiviert.")
        if not _GP_AVAILABLE:
            print("[HwCaptureProvider] gphoto2 nicht verfügbar - Capture deaktiviert.")
        self._setup_gpio()
        self._kill_gvfs()

    # -- CaptureProvider Protocol ----------------------------------------------

    def capture(self, target_dir: Path) -> Path:
        """
        Löst aus und lädt das Bild herunter.
        Wirft bei Fehlern eine Exception (wird von CameraCaptureService gefangen).
        Gibt den lokalen Dateipfad zurück.
        """
        if not _GPIO_AVAILABLE or not _GP_AVAILABLE:
            raise RuntimeError(
                "[HwCaptureProvider] Hardware nicht verfügbar "
                "(RPi.GPIO oder gphoto2 fehlt)."
            )

        target_dir.mkdir(parents=True, exist_ok=True)

        # Schritt 1: GPIO-Shutter-Puls
        self._trigger_shutter()

        # Schritt 2: Kurz warten (D3300 schreibt JPG intern auf Speicherkarte)
        time.sleep(_GPHOTO2_WAIT_SEC)

        # Schritt 3: Bild per gphoto2 herunterladen (mit Retry)
        return self._fetch_image_with_retry(target_dir)

    # -- GPIO Shutter ----------------------------------------------------------

    def _setup_gpio(self) -> None:
        if not _GPIO_AVAILABLE:
            return
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(_SHUTTER_PIN, GPIO.OUT, initial=GPIO.LOW)
            self._gpio_ready = True
            print(f"[HwCaptureProvider] Shutter-Pin GPIO {_SHUTTER_PIN} bereit.")
        except Exception as exc:
            print(f"[HwCaptureProvider] GPIO-Fehler beim Setup: {exc}")
            self._gpio_ready = False

    def _trigger_shutter(self) -> None:
        """GPIO-Puls: HIGH → warten → LOW (löst Nikon aus)."""
        if not self._gpio_ready:
            raise RuntimeError("[HwCaptureProvider] GPIO Shutter-Pin nicht initialisiert.")
        try:
            GPIO.output(_SHUTTER_PIN, GPIO.HIGH)
            time.sleep(_SHUTTER_PULSE_SEC)
            GPIO.output(_SHUTTER_PIN, GPIO.LOW)
            print("[HwCaptureProvider] Shutter-Puls gesendet.")
        except Exception as exc:
            raise RuntimeError(f"[HwCaptureProvider] Shutter-Puls fehlgeschlagen: {exc}") from exc

    def cleanup_gpio(self) -> None:
        """
        Beim App-Ende aufrufen (in app.py's finally-Block).

        Wichtig: GPIO.cleanup() wuerde den Pin in einen schwebenden (floatenden)
        Zustand versetzen - das kann am Optokoppler-Eingang durch elektrisches
        Rauschen (z.B. beim Ein-/Ausstecken des Kabels) zu einem ungewollten
        Ausloese-Impuls fuehren. Deshalb wird der Pin hier stattdessen aktiv
        auf LOW gehalten, nicht freigegeben.
        """
        if _GPIO_AVAILABLE and self._gpio_ready:
            try:
                GPIO.output(_SHUTTER_PIN, GPIO.LOW)
            except Exception:
                pass

    # -- gphoto2 Download ------------------------------------------------------

    def _fetch_image_with_retry(self, target_dir: Path) -> Path:
        """Lädt das neueste Bild von der Kamera, mit Retry bei Fehlern."""
        last_exc: Exception | None = None
        for attempt in range(1, _GPHOTO2_RETRY_COUNT + 1):
            try:
                return self._fetch_image(target_dir)
            except Exception as exc:
                last_exc = exc
                print(
                    f"[HwCaptureProvider] gphoto2-Versuch {attempt}/{_GPHOTO2_RETRY_COUNT} "
                    f"fehlgeschlagen: {exc}"
                )
                if attempt < _GPHOTO2_RETRY_COUNT:
                    self._kill_gvfs()   # GVFS könnte sich zwischenzeitlich eingehängt haben
                    time.sleep(_GPHOTO2_RETRY_WAIT)

        raise RuntimeError(
            f"[HwCaptureProvider] Bild-Download nach {_GPHOTO2_RETRY_COUNT} Versuchen "
            f"fehlgeschlagen. Letzter Fehler: {last_exc}"
        )

    def _build_local_filename(self, target_dir: Path) -> str:
        """
        Erzeugt den Dateinamen nach dem Schema
        {photo_prefix}{JJJJMMTTHHMMSS}.jpg, z.B. "mina_20260711153045.jpg".

        Faellt bei einer (sehr unwahrscheinlichen) Namenskollision - zwei
        Aufnahmen innerhalb derselben Sekunde - auf einen angehaengten
        Zaehler zurueck (..._2.jpg, ..._3.jpg, ...), damit nie ein
        bestehendes Foto versehentlich ueberschrieben wird.
        """
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        base_name = f"{self.config.photo_prefix}{timestamp}"
        candidate = f"{base_name}.jpg"
        counter = 2
        while (target_dir / candidate).exists():
            candidate = f"{base_name}_{counter}.jpg"
            counter += 1
        return candidate

    def _fetch_image(self, target_dir: Path) -> Path:
        """Holt das zuletzt aufgenommene Bild von der Nikon (gphoto2)."""
        with self.camera_lock:
            context = gp.Context()
            camera = gp.Camera()
            try:
                camera.init(context)

                # Neuestes Bild auf der Kamera suchen
                folder, name = self._find_latest_file(camera, context)
                if folder is None or name is None:
                    raise RuntimeError(
                        "[HwCaptureProvider] Kein Bild auf der Kamera gefunden. "
                        "Wurde die Aufnahme erfolgreich ausgelöst?"
                    )

                # Dateiname: photo_prefix + Zeitstempel (JJJJMMTTHHMMSS),
                # z.B. "mina_20260711153045.jpg" - photo_prefix aus
                # config.py (config.photo_prefix), vom Aufrufer beim
                # Erstellen des Providers gesetzt (siehe app_with_hw.py).
                local_name = self._build_local_filename(target_dir)
                local_path = target_dir / local_name

                # Herunterladen
                camera_file = gp.CameraFile()
                camera.file_get(
                    folder, name, gp.GP_FILE_TYPE_NORMAL, camera_file, context
                )
                camera_file.save(str(local_path))
                print(f"[HwCaptureProvider] Bild gespeichert: {local_path}")

                # Bild von der Kamera-Speicherkarte löschen (spart Platz)
                camera.file_delete(folder, name, context)
                print(f"[HwCaptureProvider] Bild auf Kamera gelöscht: {folder}/{name}")

                return local_path

            finally:
                try:
                    camera.exit(context)
                except Exception:
                    pass

    @staticmethod
    def _find_latest_file(
        camera: "gp.Camera",
        context: "gp.Context",
        folder: str = "/",
    ) -> tuple[str | None, str | None]:
        """
        Rekursiv das neueste JPEG auf der Kamera-Speicherkarte/-RAM suchen.
        Gibt (folder, filename) zurück oder (None, None).
        """
        result_folder: str | None = None
        result_name: str | None = None

        try:
            files = camera.folder_list_files(folder, context)
            for fname, _ in files:
                if fname.lower().endswith((".jpg", ".jpeg")):
                    # Letzter Fund gewinnt (neuestes Bild zuerst aufgelistet)
                    result_folder = folder
                    result_name = fname
        except Exception as exc:
            print(f"[HwCaptureProvider] Konnte Ordner '{folder}' nicht listen: {exc}")

        try:
            subfolders = camera.folder_list_folders(folder, context)
            for subfolder, _ in subfolders:
                sub_path = folder.rstrip("/") + "/" + subfolder
                f, n = HwCaptureProvider._find_latest_file(camera, context, sub_path)
                if f is not None:
                    result_folder = f
                    result_name = n
        except Exception as exc:
            print(f"[HwCaptureProvider] Konnte Unterordner von '{folder}' nicht listen: {exc}")

        return result_folder, result_name

    # -- GVFS-Blocker entfernen ------------------------------------------------

    @staticmethod
    def _kill_gvfs() -> None:
        """
        gvfs-gphoto2-volume-monitor beenden, der den gphoto2-Zugriff blockiert.
        Wird beim Start und vor jedem Retry-Versuch aufgerufen.
        """
        try:
            result = subprocess.run(
                ["pgrep", "-f", "gvfs-gphoto2"],
                capture_output=True, text=True,
            )
            for pid_str in result.stdout.splitlines():
                pid = int(pid_str.strip())
                os.kill(pid, signal.SIGKILL)
                print(f"[HwCaptureProvider] gvfs-gphoto2 (PID {pid}) beendet.")
        except (ValueError, ProcessLookupError, PermissionError):
            pass
        except Exception as exc:
            print(f"[HwCaptureProvider] Hinweis beim GVFS-Kill: {exc}")


# ------------------------------------------------------------------------------
# Manuelle Schnell-Tests (direkt auf dem Pi ausführen)
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from pathlib import Path

    target = Path("/tmp/hw_capture_test")
    target.mkdir(exist_ok=True)

    print("HwCaptureProvider Schnelltest")
    print(f"Bilder werden gespeichert in: {target}")
    print("Nikon eingeschaltet? USB-Kabel angeschlossen? Optokoppler-Kabel gesteckt? (STRG+C = Abbruch)")
    input("ENTER drücken, um auszulösen...")

    provider = HwCaptureProvider()
    try:
        path = provider.capture(target)
        print(f"\n[OK] Erfolg! Bild gespeichert: {path}")
        print(f"  Dateigröße: {path.stat().st_size / 1024:.1f} KB")
    except Exception as exc:
        print(f"\n[FEHLER] {exc}")
    finally:
        provider.cleanup_gpio()