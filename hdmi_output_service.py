"""
hdmi_output_service.py
=======================
Sprint 14: HDMI-Ausgang (Beamer) - Status lesen, Ein-/Ausschalten,
Galerie-Wiedergabe auf dem zweiten Bildschirm starten/stoppen.

Anzeige-Backend (GEAENDERT nach dem ersten Pi-Test, 2026-11-11):
Der Fotobox-Pi laeuft mit einer X11-Sitzung (lightdm/Xorg/LXDE/openbox -
RealVNC Server setzt X11 voraus), NICHT mit Wayland/labwc. Hauptweg ist
daher `xrandr` (Ausgaenge: DSI-2 = Touch-Display, HDMI-1-1/HDMI-1-2 =
die beiden Micro-HDMI-Ports des Pi 5). `wlr-randr` bleibt als Rueckfall
fuer eine Wayland-Sitzung erhalten (Auswahl: ist WAYLAND_DISPLAY gesetzt).

Ausgabename: config.HdmiConfig.connector_name = "auto" waehlt den ersten
ANGESCHLOSSENEN HDMI-Ausgang (egal an welchem der beiden Ports der Beamer
haengt); ein fester Name (z.B. "HDMI-1-2") erzwingt einen bestimmten Port.

Erweiterter Desktop: beim Einschalten wird der HDMI-Ausgang RECHTS neben
das Touch-Display gelegt (`xrandr --output X --auto --right-of DSI-2`).
Danach wird der Touchscreen per `xinput map-to-output` fest dem
Touch-Display zugeordnet - sonst wuerde sich der Touch-Bereich ueber
BEIDE Bildschirme strecken.

Fix 6 (Live-Test 2026-11-14): die Touch-Zuordnung schlug bisher IMMER fehl
(mehrdeutiger Geraetename, siehe map_touch_to_display). Jetzt mit Praefix
"pointer:", zusaetzlich bei App-Start (ensure_touch_primary), nach "HDMI
aus" und automatisch bei jeder Aenderung der Bildschirmaufteilung
(TouchMappingGuard, z.B. Monitor im laufenden Betrieb eingesteckt).

Warum ein SEPARATER Prozess fuer die Beamer-Wiedergabe: die pygame/SDL-API
unterstuetzt nur EIN Fenster pro Prozess. hdmi_slideshow.py laeuft als
eigener Prozess und setzt sein Fenster (ohne Rahmen) per
SDL_VIDEO_WINDOW_POS genau auf den HDMI-Bildschirm - stuerzt die
Beamer-Wiedergabe ab, laeuft die Fotobox unbeeintraechtigt weiter.

Direkte subprocess-Aufrufe wie admin_usb_service.py (keine injizierte
Kommando-Funktion): die reinen Parser (_parse_xrandr/_parse_wlr_randr)
sind ohne Hardware testbar, alle Hardware-Aufrufe liefern im Fehlerfall
eine Meldung statt einer Ausnahme.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_CMD_TIMEOUT_SEC = 5.0
AUTO_CONNECTOR = "auto"
DEFAULT_TOUCH_OUTPUT = "DSI-2"


@dataclass(frozen=True)
class HdmiOutputStatus:
    available: bool            # Werkzeug (xrandr/wlr-randr) nutzbar?
    connected: bool            # Ausgabegeraet an einem HDMI-Port erkannt?
    powered_on: bool | None    # None = unbekannt (z.B. nicht verbunden)
    resolution: str            # z.B. "1920x1080", "" wenn unbekannt
    error: str                 # Fehlertext fuer die Anzeige, "" wenn keiner
    slideshow_running: bool = False
    connector: str = ""        # aufgeloester Ausgabename, z.B. "HDMI-1-1"
    # (Breite, Hoehe, X, Y) im erweiterten Desktop, None = Ausgang inaktiv.
    geometry: tuple[int, int, int, int] | None = None


# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------

_DEFAULT_XAUTHORITY = "/home/photobox/.Xauthority"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")
    # NEU (Sprint-14-Fix 6): die App laeuft per sudo als root - ohne
    # XAUTHORITY der angemeldeten Desktop-Sitzung koennen xrandr/xinput je
    # nach sudo-Konfiguration den X-Server nicht erreichen. Nur setzen, wenn
    # noch nicht vorhanden und die Datei tatsaechlich existiert.
    if "XAUTHORITY" not in env and os.path.isfile(_DEFAULT_XAUTHORITY):
        env["XAUTHORITY"] = _DEFAULT_XAUTHORITY
    return env


def _run(args: list[str]) -> tuple[str | None, str]:
    """Fuehrt ein Kommando aus. Rueckgabe: (stdout, "") bei Erfolg,
    (None, Fehlermeldung) bei Fehler - nie eine Ausnahme."""
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=_CMD_TIMEOUT_SEC, env=_env(),
        )
    except FileNotFoundError:
        return None, f"{args[0]} ist auf diesem System nicht installiert."
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"{args[0]} fehlgeschlagen: {exc}"
    if result.returncode != 0:
        return None, (result.stderr or f"{args[0]} meldete einen Fehler.").strip()
    return result.stdout, ""


def _use_wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY"))


# --------------------------------------------------------------------------
# Parser (reine Funktionen, ohne Hardware testbar)
# --------------------------------------------------------------------------

_XRANDR_LINE = re.compile(
    r"^(\S+) (connected|disconnected)\b(?: primary)?(?: (\d+)x(\d+)\+(-?\d+)\+(-?\d+))?"
)


def _parse_xrandr(text: str) -> dict[str, dict]:
    """Liest `xrandr` (ohne Argumente): Name -> {"connected": bool,
    "geometry": (w, h, x, y) | None}. geometry ist nur bei AKTIVEN
    Ausgaengen gesetzt (z.B. "HDMI-1-1 connected 1920x1080+1280+0 ...");
    ein angeschlossener, aber abgeschalteter Ausgang hat keine Geometrie."""
    outputs: dict[str, dict] = {}
    for line in text.splitlines():
        match = _XRANDR_LINE.match(line)
        if not match:
            continue
        name, state, w, h, x, y = match.groups()
        geometry = (int(w), int(h), int(x), int(y)) if w else None
        outputs[name] = {"connected": state == "connected", "geometry": geometry}
    return outputs


def _pick_hdmi(outputs: dict[str, dict], connector_name: str) -> str | None:
    """Loest "auto" auf den ersten angeschlossenen HDMI-Ausgang auf; ein
    fester Name wird nur akzeptiert, wenn xrandr ihn kennt."""
    if connector_name != AUTO_CONNECTOR:
        return connector_name if connector_name in outputs else None
    for name, info in outputs.items():
        if name.upper().startswith("HDMI") and info["connected"]:
            return name
    return None


def _parse_wlr_randr(text: str, connector_name: str) -> dict:
    """Rueckfall fuer Wayland: `wlr-randr` listet einen Block je Ausgang,
    eingeleitet mit dem Ausgabenamen unindentiert am Zeilenanfang."""
    block: list[str] = []
    in_block = False
    for line in text.splitlines():
        if line and not line[0].isspace():
            if in_block:
                break
            if line.split()[0] == connector_name:
                in_block = True
                block.append(line)
            continue
        if in_block:
            block.append(line)

    if not block:
        return {"connected": False, "powered_on": None, "resolution": ""}

    powered_on: bool | None = None
    resolution = ""
    for line in block:
        stripped = line.strip()
        if stripped.startswith("Enabled:"):
            powered_on = stripped.split(":", 1)[1].strip().lower() == "yes"
        # Die aktive Aufloesung steht in einer Klammer wie "(current)" oder
        # "(preferred, current)".
        if "(" in stripped and "current" in stripped.rsplit("(", 1)[-1]:
            resolution = stripped.split(",")[0].replace(" px", "").strip()
    return {"connected": True, "powered_on": powered_on, "resolution": resolution}


# --------------------------------------------------------------------------
# Status lesen / ein- und ausschalten
# --------------------------------------------------------------------------

def _error_status(message: str, slideshow_running: bool) -> HdmiOutputStatus:
    return HdmiOutputStatus(
        available=False, connected=False, powered_on=None, resolution="",
        error=message, slideshow_running=slideshow_running,
    )


def read_status(connector_name: str, *, slideshow_running: bool = False) -> HdmiOutputStatus:
    """Liest den aktuellen HDMI-Status synchron aus (xrandr bzw. wlr-randr).
    Dauert typischerweise deutlich unter einer Sekunde - wird von einem
    bewussten Tap im Service-Menue ausgeloest, daher kein Hintergrund-
    Thread noetig (gleiches Prinzip wie admin_diagnostics)."""
    if _use_wayland():
        stdout, error = _run(["wlr-randr"])
        if stdout is None:
            return _error_status(error, slideshow_running)
        name = connector_name if connector_name != AUTO_CONNECTOR else "HDMI-A-1"
        parsed = _parse_wlr_randr(stdout, name)
        return HdmiOutputStatus(
            available=True, connected=parsed["connected"], powered_on=parsed["powered_on"],
            resolution=parsed["resolution"], error="", slideshow_running=slideshow_running,
            connector=name,
        )

    stdout, error = _run(["xrandr"])
    if stdout is None:
        return _error_status(error, slideshow_running)
    outputs = _parse_xrandr(stdout)
    target = _pick_hdmi(outputs, connector_name)
    if target is None:
        return HdmiOutputStatus(
            available=True, connected=False, powered_on=None, resolution="",
            error="", slideshow_running=slideshow_running,
        )
    info = outputs[target]
    geometry = info["geometry"]
    return HdmiOutputStatus(
        available=True,
        connected=info["connected"],
        powered_on=geometry is not None if info["connected"] else None,
        resolution=f"{geometry[0]}x{geometry[1]}" if geometry else "",
        error="",
        slideshow_running=slideshow_running,
        connector=target,
        geometry=geometry,
    )


def map_touch_to_display(touch_output: str = DEFAULT_TOUCH_OUTPUT) -> str:
    """Ordnet den Touchscreen (Goodix) fest dem Touch-Display zu, damit sich
    der Touch-Bereich nicht ueber den erweiterten Desktop streckt.
    Best-Effort: liefert eine Warnung statt eines Fehlers, "" bei Erfolg.
    Unter Wayland ohne Wirkung.

    BUGFIX (Sprint-14-Fix 6, Live-Test 2026-11-14): der Goodix-Controller
    meldet sich bei X ZWEIMAL an - als Zeiger UND als Tastatur (xinput list:
    id 6 "slave pointer", id 10 "slave keyboard"). Der blosse Name war
    deshalb mehrdeutig, xinput brach mit "There are multiple devices
    matching ..." ab und die Zuordnung fand NIE statt. Folge: nach "HDMI an"
    (X-Flaeche 3200x1080) landeten Taps um Faktor 2,5 (x) bzw. 1,5 (y)
    verschoben, rechte Displayhaelfte sogar auf dem Beamer. Das Praefix
    "pointer:" waehlt eindeutig das Zeigergeraet.

    Muss nach JEDER Aenderung der Bildschirmaufteilung erneut laufen (App-
    Start, HDMI an/aus, Monitor ein-/ausgesteckt) - siehe TouchMappingGuard."""
    if _use_wayland():
        return ""
    stdout, error = _run(["xinput", "--list", "--name-only"])
    if stdout is None:
        return f"Touch-Zuordnung nicht moeglich: {error}"
    names = [line.strip() for line in stdout.splitlines() if "goodix" in line.lower()]
    if not names:
        return "Touch-Zuordnung nicht moeglich: kein Goodix-Touchscreen gefunden."
    _, error = _run(["xinput", "map-to-output", f"pointer:{names[0]}", touch_output])
    return f"Touch-Zuordnung fehlgeschlagen: {error}" if error else ""


# Alter interner Name - bleibt als Alias erhalten.
_map_touch_to_display = map_touch_to_display


def ensure_touch_primary(touch_output: str = DEFAULT_TOUCH_OUTPUT) -> str:
    """Setzt das Touch-Display als primaeren Bildschirm (X11). SDL oeffnet
    ein Vollbildfenster auf dem primaeren Bildschirm bzw. Display 0 - ist
    nach dem Einschalten von HDMI der Beamer "primaer", erscheint die
    Fotobox dort statt auf dem Touch-Display (Live-Test). Best-Effort,
    liefert eine Fehlermeldung oder "". Unter Wayland ohne Wirkung.

    GEAENDERT (Sprint-14-Fix 6): ordnet danach auch den Touch dem Touch-
    Display zu - ensure_touch_primary() laeuft bei jedem App-Start, und ein
    beim Booten bereits angeschlossener Monitor erweitert die X-Flaeche
    schon VOR dem App-Start."""
    if _use_wayland():
        return ""
    _, error = _run(["xrandr", "--output", touch_output, "--primary"])
    warning = map_touch_to_display(touch_output)
    return "; ".join(part for part in (error, warning) if part)


def read_layout_signature() -> str | None:
    """Kurzer "Fingerabdruck" der aktuellen Bildschirmaufteilung
    (`xrandr --listmonitors`), z.B. zum Erkennen eines im laufenden Betrieb
    ein- oder ausgesteckten Monitors. None, wenn xrandr nicht erreichbar."""
    if _use_wayland():
        return None
    stdout, _ = _run(["xrandr", "--listmonitors"])
    return stdout.strip() if stdout is not None else None


class TouchMappingGuard:
    """NEU (Sprint-14-Fix 6): ordnet den Touch automatisch neu zu, sobald
    sich die Bildschirmaufteilung aendert - auch wenn die App davon nichts
    mitbekommt (Monitor/Beamer im laufenden Betrieb ein- oder ausgesteckt).

    Laeuft in einem eigenen Daemon-Thread, damit die xrandr-Abfrage (einige
    Millisekunden) nie die pygame-Hauptschleife bzw. den Live View stockt.
    check_once() ist ohne Thread direkt testbar; read_signature/remap sind
    injizierbar (siehe test_hdmi_output_service.TouchMappingGuardTestCase)."""

    def __init__(
        self,
        touch_output: str = DEFAULT_TOUCH_OUTPUT,
        *,
        interval_seconds: float = 5.0,
        read_signature: Callable[[], str | None] = read_layout_signature,
        remap: Callable[[str], str] = map_touch_to_display,
    ) -> None:
        self.touch_output = touch_output
        self.interval_seconds = interval_seconds
        self._read_signature = read_signature
        self._remap = remap
        self._last_signature: str | None = None
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None

    def check_once(self) -> bool:
        """Prueft die Aufteilung einmal; ordnet bei einer Aenderung neu zu.
        Rueckgabe True, wenn neu zugeordnet wurde. Der erste Aufruf merkt
        sich nur den Ausgangszustand (die Zuordnung beim App-Start erledigt
        ensure_touch_primary)."""
        signature = self._read_signature()
        if signature is None:
            return False
        if self._last_signature is None:
            self._last_signature = signature
            return False
        if signature == self._last_signature:
            return False
        self._last_signature = signature
        message = self._remap(self.touch_output)
        print("[HDMI] Bildschirmaufteilung geaendert - Touch neu zugeordnet."
              + (f" Hinweis: {message}" if message else ""))
        return True

    def note_layout_changed(self) -> None:
        """Nach einer Aenderung durch die App selbst (set_power) aufrufen -
        merkt sich die neue Aufteilung, damit der Waechter nicht noch einmal
        unnoetig neu zuordnet."""
        self._last_signature = self._read_signature()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event = threading.Event()
        stop_event = self._stop_event

        def _loop() -> None:
            while not stop_event.wait(self.interval_seconds):
                try:
                    self.check_once()
                except Exception as exc:  # Waechter darf die App nie stoeren
                    print(f"[HDMI] Touch-Waechter: {exc}")

        self.check_once()
        self._thread = threading.Thread(target=_loop, name="touch-mapping-guard", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        self._stop_event = None


def pick_display_index(desktop_sizes: list[tuple[int, int]], wanted: tuple[int, int]) -> int:
    """Index des ersten Bildschirms mit der gewuenschten Groesse (z.B.
    1280x720 des Touch-Displays); sonst 0. Der Beamer hat meist eine andere
    Aufloesung (1920x1080) und wird so nicht versehentlich gewaehlt."""
    for index, size in enumerate(desktop_sizes):
        if tuple(size) == tuple(wanted):
            return index
    return 0


def set_power(
    connector_name: str, on: bool, *, touch_output: str = DEFAULT_TOUCH_OUTPUT,
) -> tuple[bool, str]:
    """Schaltet den HDMI-Ausgang ein oder aus. Liefert (ok, Meldung); die
    Meldung kann auch bei ok=True eine Warnung enthalten (z.B. wenn nur die
    Touch-Zuordnung nicht geklappt hat). Nie eine Ausnahme."""
    if _use_wayland():
        name = connector_name if connector_name != AUTO_CONNECTOR else "HDMI-A-1"
        _, error = _run(["wlr-randr", "--output", name, "--on" if on else "--off"])
        return (error == ""), error

    stdout, error = _run(["xrandr"])
    if stdout is None:
        return False, error
    target = _pick_hdmi(_parse_xrandr(stdout), connector_name)
    if target is None:
        return False, "Kein Ausgabegerät erkannt."
    if on:
        _, error = _run(["xrandr", "--output", target, "--auto", "--right-of", touch_output])
        if error:
            return False, error
        # Touch-Display muss primaer bleiben, sonst oeffnet SDL die Fotobox
        # beim naechsten App-Start auf dem Beamer.
        # ensure_touch_primary ordnet seit Fix 6 auch den Touch neu zu.
        return True, ensure_touch_primary(touch_output)
    _, error = _run(["xrandr", "--output", target, "--off"])
    if error:
        return False, error
    # BUGFIX (Sprint-14-Fix 6): auch nach "HDMI aus" neu zuordnen - die
    # X-Flaeche schrumpft wieder auf 1280x720, die alte Zuordnung (fuer
    # 3200x1080 berechnet) wuerde den Touch sonst auf einen Teilbereich
    # stauchen.
    return True, map_touch_to_display(touch_output)


def ensure_hdmi_off(
    connector_name: str, *, touch_output: str = DEFAULT_TOUCH_OUTPUT,
) -> tuple[bool, str]:
    """NEU (Sprint 14, Nutzer-Feedback 2026-11-14): HDMI-Ausgang gezielt
    abschalten - bei App-Start, App-Neustart und vor dem Herunterfahren.
    Der HDMI-Ausgang soll AUSSCHLIESSLICH ueber das Service-Menue
    eingeschaltet werden; ein beim Booten von X automatisch aktivierter
    Monitor oder ein aus einer frueheren App-Instanz noch aktiver Ausgang
    wird dadurch zuverlaessig abgeschaltet.

    Schaltet nur ab, wenn der Ausgang tatsaechlich AKTIV ist (Geometrie
    gesetzt) - ohne angeschlossenes Geraet oder bei bereits abgeschaltetem
    Ausgang passiert nichts. Rueckgabe (abgeschaltet, Meldung); nie eine
    Ausnahme. Unter Wayland ohne Wirkung."""
    if _use_wayland():
        return False, ""
    status = read_status(connector_name)
    if not status.available:
        return False, status.error
    if not status.connected or status.geometry is None:
        return False, ""
    ok, message = set_power(connector_name, False, touch_output=touch_output)
    return ok, message


# --------------------------------------------------------------------------
# Wiedergabe-Subprozess
# --------------------------------------------------------------------------

def kill_stray_slideshows() -> None:
    """Beendet ALLE laufenden hdmi_slideshow.py-Prozesse, auch solche, die
    diese App-Instanz nicht (mehr) kennt - z.B. nach einem App-Neustart
    (pkill/Start-Schleife) bleibt der Beamer-Subprozess der alten Instanz
    als Waise am Leben (Live-Test: Wiedergabe lief nach "HDMI aus" weiter).
    Best-Effort, nie eine Ausnahme."""
    try:
        subprocess.run(
            ["pkill", "-f", r"python.*hdmi_slideshow\.py"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


class HdmiOutputService:
    """Haelt den laufenden Wiedergabe-Subprozess ueber mehrere Frames
    hinweg (siehe app.PhotoboothApp._hdmi_output_service). `popen_factory`
    ist injizierbar, damit start_slideshow()/stop_slideshow() ohne echten
    Subprozess testbar bleiben (siehe test_hdmi_output_service.py)."""

    def __init__(
        self,
        connector_name: str,
        photo_dir: Path,
        *,
        touch_output: str = DEFAULT_TOUCH_OUTPUT,
        wallpaper_path: Path | None = None,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    ) -> None:
        self.wallpaper_path = wallpaper_path
        self.connector_name = connector_name
        self.photo_dir = photo_dir
        self.touch_output = touch_output
        self._popen_factory = popen_factory
        self._process: subprocess.Popen | None = None
        self._wallpaper_process: subprocess.Popen | None = None
        # Waisen einer frueheren App-Instanz beseitigen (siehe
        # kill_stray_slideshows).
        kill_stray_slideshows()

    def is_slideshow_running(self) -> bool:
        if self._process is None:
            return False
        if self._process.poll() is not None:
            # Prozess ist von selbst beendet (z.B. Absturz) - aufraeumen,
            # damit ein erneuter Start wieder moeglich ist.
            self._process = None
            return False
        return True

    def start_slideshow(self) -> tuple[bool, str]:
        """Startet die Beamer-Wiedergabe. Rueckgabe (ok, Meldung): ohne
        angeschlossenen UND eingeschalteten HDMI-Ausgang gibt es keine
        Zielflaeche - dann (False, Hinweistext) statt eines Fensters am
        falschen Bildschirm."""
        if self.is_slideshow_running():
            return True, ""
        status = read_status(self.connector_name)
        if not status.available:
            return False, status.error
        if not status.connected:
            return False, "Kein Ausgabegerät erkannt."
        if status.geometry is None and not _use_wayland():
            return False, "HDMI-Ausgang ist aus - bitte zuerst einschalten."
        script = Path(__file__).resolve().parent / "hdmi_slideshow.py"
        args = [
            sys.executable, str(script),
            "--connector", status.connector or self.connector_name,
            "--photo-dir", str(self.photo_dir),
        ]
        if status.geometry is not None:
            w, h, x, y = status.geometry
            args += ["--geometry", f"{w}x{h}+{x}+{y}"]
        # Standbild (Wallpaper) beenden, bevor die Galerie startet.
        kill_stray_slideshows()
        self._process = self._popen_factory(args, env=_env())
        return True, ""

    def show_wallpaper(self) -> tuple[bool, str]:
        """Zeigt das Standbild (systemstart_wallpaper.png) auf dem Beamer,
        solange die Galerie-Wiedergabe gestoppt ist. Nur bei eingeschaltetem
        und angeschlossenem Ausgang; laeuft die Galerie, passiert nichts.
        Best-Effort: (ok, Meldung)."""
        if self.wallpaper_path is None or not Path(self.wallpaper_path).is_file():
            return False, "Wallpaper-Datei nicht gefunden."
        if self.is_slideshow_running():
            return True, ""
        status = read_status(self.connector_name)
        if not status.available or not status.connected or status.geometry is None:
            return False, status.error or "HDMI-Ausgang nicht aktiv."
        kill_stray_slideshows()
        script = Path(__file__).resolve().parent / "hdmi_slideshow.py"
        w, h, x, y = status.geometry
        args = [
            sys.executable, str(script),
            "--connector", status.connector or self.connector_name,
            "--photo-dir", str(self.photo_dir),
            "--geometry", f"{w}x{h}+{x}+{y}",
            "--wallpaper", str(self.wallpaper_path),
        ]
        self._wallpaper_process = self._popen_factory(args, env=_env())
        return True, ""

    def stop_slideshow(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=3.0)
            self._process = None
        self._wallpaper_process = None
        # Zusaetzlich unbekannte Waisen beenden (auch wenn kein eigener
        # Prozess bekannt war).
        kill_stray_slideshows()

    def run_test(self, duration_seconds: float) -> str:
        """"HDMI-Test durchfuehren": liest den aktuellen Status erneut aus
        und liefert eine knappe, fuer den Admin verstaendliche Meldung.
        Bewusst KEIN eigenes Testbild/-subprozess: "ist ein Ausgabegeraet
        angeschlossen" laesst sich bereits aus dem Status beantworten."""
        del duration_seconds  # aktuell ungenutzt, siehe Docstring
        status = read_status(self.connector_name, slideshow_running=self.is_slideshow_running())
        if not status.available:
            return f"Test fehlgeschlagen: {status.error}"
        if not status.connected:
            return "Kein Ausgabegerät erkannt."
        aufloesung = status.resolution or "unbekannte Auflösung"
        zustand = "eingeschaltet" if status.powered_on else "ausgeschaltet"
        name = f"{status.connector}: " if status.connector else ""
        return f"Ausgabegerät erkannt ({name}{aufloesung}, {zustand})."
