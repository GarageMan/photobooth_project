"""
hw_camera_settings_provider.py
===============================
NEU (Sprint 11, Feature 2): ISO und Blende direkt ueber die bestehende
USB/gphoto2-Verbindung lesen und aendern - ohne die Kamera aus dem Gehaeuse
zu nehmen, Kabel zu loesen und sie danach wieder korrekt auszurichten
(bisheriger, fehleranfaelliger Weg im Live-Betrieb).

Recherche (github.com/gphoto/libgphoto2, camlibs/ptp2/cameras/nikon-d3300.txt):
Die D3300 deklariert sowohl "Exposure Index" (ISO, PTP-Property 0x500f) als
auch "F-Number" (Blende, 0x5007) als read-write. Der gueltige Blenden-
Wertebereich haengt vom montierten Objektiv ab und wird deshalb bewusst NIE
hartkodiert, sondern bei jedem Aufruf live von der Kamera gelesen
(get_config()/choices).

Bekannte Einschraenkung (gphoto/gphoto2 Issue #491): manche Kameras lehnen
Aenderungen ab, waehrend parallel eine Live-Vorschau/ein Capture-Loop laeuft.
Im Service-Menue laeuft nie eine Live-Vorschau (die ist auf PHOTO_PREVIEW/
COUNTDOWN beschraenkt), daher hier unkritisch - trotzdem nur auf echter
Hardware endgueltig verifizierbar, diese Sandbox hat keine Kamera.

Gemeinsamer Kamera-Zugriff:
  Wie HwCaptureProvider/HwGphoto2PreviewProvider wird das von app_with_hw.py
  erzeugte gemeinsame `camera_lock` verwendet - gphoto2 erlaubt immer nur
  eine aktive Verbindung gleichzeitig.

Voraussetzung an der Kamera: ein Modus, der eine manuelle Blendenwahl
zulaesst (M oder A) - in P/S/Auto verweigert die Nikon meist eine externe
Blendenaenderung (gleiche Einschraenkung wie am Kameramenue selbst).
GEAENDERT (Sprint-11-Nachbesserung): Lutz nutzt bewusst eine Zeitautomatik
(Modus A) statt M - er gibt die Blende vor, die Kamera berechnet die
Belichtungszeit selbst. Der ISO-Wert laesst sich unabhaengig vom Modus
setzen.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

try:
    import gphoto2 as gp
    _GP_AVAILABLE = True
except ImportError:
    _GP_AVAILABLE = False
    gp = None  # type: ignore


# Kanonische libgphoto2-Widget-Namen zuerst, dann bekannte Alternativnamen
# (unterscheiden sich je nach Kameramodell/libgphoto2-Version) - _read_widget/
# _set_widget probieren sie der Reihe nach durch, bis einer existiert.
_ISO_CONFIG_NAMES = ("iso",)
_APERTURE_CONFIG_NAMES = ("f-number", "aperture")


@dataclass
class CameraSettingsSnapshot:
    """Ergebnis von read_current() - eine Momentaufnahme, kein Live-Objekt.
    `available=False` deckt sowohl "gphoto2 fehlt" als auch "Kamera nicht
    erreichbar" als auch "Kamera liefert weder ISO noch Blende" ab; `error`
    traegt dann einen fuer Lutz verstaendlichen Grund (siehe renderer.py)."""

    available: bool
    iso: str = ""
    iso_choices: tuple[str, ...] = ()
    aperture: str = ""
    aperture_choices: tuple[str, ...] = ()
    error: str | None = None


def _read_widget(config, names: tuple[str, ...]) -> tuple[str | None, tuple[str, ...]]:
    for name in names:
        try:
            widget = config.get_child_by_name(name)
        except Exception:
            continue
        try:
            value = widget.get_value()
            count = widget.count_choices()
            choices = tuple(widget.get_choice(i) for i in range(count)) if count else ()
            return str(value), choices
        except Exception:
            continue
    return None, ()


def read_current(camera_lock: threading.Lock) -> CameraSettingsSnapshot:
    """Liest ISO + Blende inkl. der von der Kamera erlaubten Auswahllisten.
    Laeuft synchron (kein Hintergrund-Thread) - ein einzelner gphoto2-
    get_config()-Aufruf ist ueblicherweise deutlich unter einer Sekunde,
    siehe Umsetzungsplan. Wirft nie eine Exception nach aussen - jeder
    Fehlerfall landet als `error` im Snapshot, damit der Service-Menue-
    Screen ihn einfach anzeigen kann statt abzustuerzen."""
    if not _GP_AVAILABLE:
        return CameraSettingsSnapshot(
            available=False,
            error="gphoto2 ist auf diesem System nicht installiert.",
        )
    with camera_lock:
        context = gp.Context()
        camera = gp.Camera()
        try:
            camera.init(context)
            config = camera.get_config(context)
            iso_value, iso_choices = _read_widget(config, _ISO_CONFIG_NAMES)
            aperture_value, aperture_choices = _read_widget(config, _APERTURE_CONFIG_NAMES)
            if iso_value is None and aperture_value is None:
                return CameraSettingsSnapshot(
                    available=False,
                    error=(
                        "ISO/Blende sind über die Kamera-Schnittstelle nicht "
                        "erreichbar. Steht die Kamera im Modus A oder M?"
                    ),
                )
            return CameraSettingsSnapshot(
                available=True,
                iso=iso_value or "",
                iso_choices=iso_choices,
                aperture=aperture_value or "",
                aperture_choices=aperture_choices,
            )
        except Exception as exc:
            return CameraSettingsSnapshot(
                available=False,
                error=f"Kamera nicht erreichbar: {exc}",
            )
        finally:
            try:
                camera.exit(context)
            except Exception:
                pass


def _set_widget(camera_lock: threading.Lock, names: tuple[str, ...], value: str) -> tuple[bool, str | None]:
    if not _GP_AVAILABLE:
        return False, "gphoto2 ist auf diesem System nicht installiert."
    with camera_lock:
        context = gp.Context()
        camera = gp.Camera()
        try:
            camera.init(context)
            config = camera.get_config(context)
            for name in names:
                try:
                    widget = config.get_child_by_name(name)
                except Exception:
                    continue
                widget.set_value(value)
                camera.set_config(config, context)
                return True, None
            return False, "Diese Einstellung wurde auf der Kamera nicht gefunden."
        except Exception as exc:
            return False, f"Konnte Einstellung nicht setzen: {exc}"
        finally:
            try:
                camera.exit(context)
            except Exception:
                pass


def set_iso(camera_lock: threading.Lock, value: str) -> tuple[bool, str | None]:
    """Setzt den ISO-Wert. `value` muss einer der zuvor per read_current()
    gelieferten iso_choices sein (siehe state_machine._step_choice)."""
    return _set_widget(camera_lock, _ISO_CONFIG_NAMES, value)


def set_aperture(camera_lock: threading.Lock, value: str) -> tuple[bool, str | None]:
    """Setzt die Blende. `value` muss einer der zuvor per read_current()
    gelieferten aperture_choices sein."""
    return _set_widget(camera_lock, _APERTURE_CONFIG_NAMES, value)


# ------------------------------------------------------------------------------
# Manueller Schnell-Test (direkt auf dem Pi ausführen)
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    lock = threading.Lock()
    print("hw_camera_settings_provider.py Schnelltest")
    print("Kamera eingeschaltet, per USB verbunden, Modus A oder M? (STRG+C = Abbruch)")
    input("ENTER drücken, um ISO/Blende zu lesen...")

    snapshot = read_current(lock)
    print(f"available={snapshot.available} error={snapshot.error}")
    print(f"ISO aktuell: {snapshot.iso!r}  Auswahl: {snapshot.iso_choices}")
    print(f"Blende aktuell: {snapshot.aperture!r}  Auswahl: {snapshot.aperture_choices}")

    if snapshot.available and snapshot.iso_choices:
        candidate = snapshot.iso_choices[0]
        answer = input(f"ISO probeweise auf {candidate!r} setzen? (j/N) ")
        if answer.strip().lower() == "j":
            ok, error = set_iso(lock, candidate)
            print(f"set_iso({candidate!r}) -> ok={ok} error={error}")
