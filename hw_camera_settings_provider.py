"""
hw_camera_settings_provider.py
===============================
ISO, Blende und weitere Aufnahme-Einstellungen direkt ueber die bestehende
USB/gphoto2-Verbindung lesen und aendern - ohne die Kamera aus dem Gehaeuse
zu nehmen, Kabel zu loesen und sie danach wieder korrekt auszurichten
(bisheriger, fehleranfaelliger Weg im Live-Betrieb).

Recherche (github.com/gphoto/libgphoto2, camlibs/ptp2/cameras/nikon-d3300.txt):
Die D3300 deklariert "Exposure Index" (ISO, PTP-Property 0x500f), "F-Number"
(Blende, 0x5007), "Exposure Metering Mode", "White Balance", "Image Quality",
"Image Size", "Exposure Compensation" und "Still Capture Mode" (Aufnahme-
betrieb/Drive Mode) allesamt als read/write; "Shutter Speed" ist ebenfalls
gelistet, wird hier aber nur LESEND verwendet (im Modus A berechnet die
Kamera sie automatisch passend zur gewaehlten Blende - das ist die einzige
Form von "aktueller Belichtung", die sich ueber PTP auslesen laesst; ein
echter Belichtungsmesser-/EV-Wert wird von der D3300 nicht angeboten, siehe
github.com/gphoto/gphoto2 Issue #78). Der gueltige Blenden-Wertebereich
haengt vom montierten Objektiv ab und wird deshalb bewusst NIE hartkodiert,
sondern bei jedem Aufruf live von der Kamera gelesen (get_config()/choices).

GEAENDERT (Kamera-Menue 2.0, Nutzer-Feedback nach Sprint 11): Die Konfig-
Namen fuer "iso", "f-number"/"aperture", "shutterspeed", "whitebalance",
"imagequality" und "imagesize" sind ueber die gphoto2-Projektdokumentation
mehrfach belegt. Fuer "exposurecompensation" ebenfalls.

BESTAETIGT (echte `gphoto2 --list-config`-Ausgabe von Lutz' D3300, Sprint-11-
Nachbesserung): Belichtungsmessfeld heisst am echten Geraet
"exposuremetermode" (nicht "meteringmode" - das existiert bei dieser
Kamera/libgphoto2-Version nicht; zu verwechseln waere auch "focusmetermode",
das ist etwas anderes - AF-Messfeld, nicht Belichtungsmessung), Aufnahme-
betrieb heisst "capturemode" (passt direkt). Beide Namen standen als
Kandidaten bereits in den untenstehenden Tupeln und wurden von _read_widget/
_set_widget automatisch gefunden - keine Code-Aenderung noetig, die Tupel
wurden nur so umsortiert, dass der bestaetigte Name zuerst probiert wird
(vermeidet einen unnoetigen fehlschlagenden Versuch bei jedem Aufruf). Die
uebrigen Kandidaten bleiben als Fallback stehen (andere Firmware-/libgphoto2-
Versionen koennten abweichen). `--list-config` zeigt nur EXISTENZ, keine
Lese-/Schreibrechte oder Wertelisten - die Funktionen melden trotzdem sauber
ueber `error`/`available=False`, falls ein Property doch nicht schreibbar
sein sollte, statt abzustuerzen.

Bekannte Einschraenkung (gphoto/gphoto2 Issue #491): manche Kameras lehnen
Aenderungen ab, waehrend parallel eine Live-Vorschau/ein Capture-Loop laeuft.
Das Kamera-Menue nutzt inzwischen bewusst eine laufende Live-Vorschau (Nutzer-
Feedback: Blende ist bei eingebauter Kamera weder zu hoeren noch zu sehen) -
um trotzdem nur EINE PTP-Sitzung offen zu haben (mehrere gleichzeitige
Sitzungen zur selben Kamera funktionieren ueblicherweise gar nicht), teilen
sich Vorschau und Einstellungs-Aenderungen jetzt dieselbe bereits offene
Kamera-Sitzung: Jede Funktion hier akzeptiert optional ein bereits offenes
`camera`/`context`-Paar (siehe hw_gphoto2_preview_provider.run_with_camera)
und ueberspringt dann ihre eigene init()/exit()-Sitzung. Ist keine Vorschau
aktiv (camera=None), wird wie bisher eine eigene, kurze Sitzung geoeffnet
und wieder geschlossen. Auch dieses Zusammenspiel ist nur am echten Geraet
endgueltig verifizierbar.

Gemeinsamer Kamera-Zugriff:
  Wie HwCaptureProvider/HwGphoto2PreviewProvider wird das von app.py
  erzeugte gemeinsame `camera_lock` verwendet - gphoto2 erlaubt immer nur
  eine aktive Verbindung gleichzeitig.

Voraussetzung an der Kamera: ein Modus, der eine manuelle Blendenwahl
zulaesst (M oder A) - in P/S/Auto verweigert die Nikon meist eine externe
Blendenaenderung (gleiche Einschraenkung wie am Kameramenue selbst).
Lutz nutzt bewusst eine Zeitautomatik (Modus A) statt M - er gibt die
Blende vor, die Kamera berechnet die Belichtungszeit selbst. Der ISO-Wert
laesst sich unabhaengig vom Modus setzen.
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
_SHUTTER_CONFIG_NAMES = ("shutterspeed",)
_EXPCOMP_CONFIG_NAMES = ("exposurecompensation",)
_WB_CONFIG_NAMES = ("whitebalance",)
_QUALITY_CONFIG_NAMES = ("imagequality", "compressionsetting")
_IMAGESIZE_CONFIG_NAMES = ("imagesize",)
# BESTAETIGT an Lutz' D3300 (gphoto2 --list-config, Sprint-11-Nachbesserung,
# siehe Modul-Docstring) - bestaetigter Name jeweils zuerst, Rest als Fallback
# fuer abweichende Firmware-/libgphoto2-Versionen.
_METERING_CONFIG_NAMES = ("exposuremetermode", "meteringmode", "expmetermode")
_DRIVE_CONFIG_NAMES = ("capturemode", "drivemode", "burstnumber")

# NEU (Sprint-11-Nachbesserung, per gphoto2 --get-config bestaetigt): die
# D3300 bietet unter "capturemode" auch "Timer", "Quick Response Remote",
# "Delayed Remote" und "Quiet Release" an - im Fotobox-Betrieb (Ausloesung
# ausschliesslich per GPIO) ergeben diese Modi keinen Sinn und koennten bei
# versehentlicher Auswahl den Ablauf stoeren (z.B. Timer-Verzoegerung vor der
# eigentlichen Aufnahme). Auf Wunsch von Lutz wird die im Menue anwaehlbare
# Liste deshalb auf die beiden sinnvollen Modi eingeschraenkt - der
# tatsaechliche Kamera-Wert wird trotzdem unveraendert angezeigt, falls er
# (z.B. durch manuelle Aenderung am Kameragehaeuse) ausserhalb dieser Liste
# liegt; _step_choice() in state_machine.py springt in dem Fall beim ersten
# Tastendruck sauber auf "Single Shot" (Index 0).
_DRIVE_CHOICES_ALLOWED = ("Single Shot", "Burst")


def _filter_drive_choices(choices: tuple[str, ...]) -> tuple[str, ...]:
    filtered = tuple(c for c in choices if c in _DRIVE_CHOICES_ALLOWED)
    # Defensiv: falls eine abweichende Firmware/libgphoto2-Version andere
    # Gross-/Kleinschreibung oder Bezeichnungen liefert und die Filterung
    # dadurch alles entfernt, lieber die volle (unbekannte) Liste zeigen als
    # den Aufnahmebetrieb komplett unbedienbar zu machen.
    return filtered or choices


@dataclass
class CameraSettingsSnapshot:
    """Ergebnis von read_current() - eine Momentaufnahme, kein Live-Objekt.
    `available=False` deckt sowohl "gphoto2 fehlt" als auch "Kamera nicht
    erreichbar" als auch "Kamera liefert weder ISO noch Blende" ab; `error`
    traegt dann einen fuer Lutz verstaendlichen Grund (siehe renderer.py).
    Alle Felder ausser `shutter` haben ein *_choices-Pendant und sind ueber
    set_<name>() aenderbar; `shutter` ist reiner Info-Wert (siehe Modul-
    Docstring)."""

    available: bool
    iso: str = ""
    iso_choices: tuple[str, ...] = ()
    aperture: str = ""
    aperture_choices: tuple[str, ...] = ()
    shutter: str = ""
    expcomp: str = ""
    expcomp_choices: tuple[str, ...] = ()
    metering: str = ""
    metering_choices: tuple[str, ...] = ()
    white_balance: str = ""
    white_balance_choices: tuple[str, ...] = ()
    quality: str = ""
    quality_choices: tuple[str, ...] = ()
    image_size: str = ""
    image_size_choices: tuple[str, ...] = ()
    drive_mode: str = ""
    drive_mode_choices: tuple[str, ...] = ()
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


def _read_current_impl(camera, context) -> CameraSettingsSnapshot:
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
    shutter_value, _ = _read_widget(config, _SHUTTER_CONFIG_NAMES)
    expcomp_value, expcomp_choices = _read_widget(config, _EXPCOMP_CONFIG_NAMES)
    metering_value, metering_choices = _read_widget(config, _METERING_CONFIG_NAMES)
    wb_value, wb_choices = _read_widget(config, _WB_CONFIG_NAMES)
    quality_value, quality_choices = _read_widget(config, _QUALITY_CONFIG_NAMES)
    imagesize_value, imagesize_choices = _read_widget(config, _IMAGESIZE_CONFIG_NAMES)
    drive_value, drive_choices = _read_widget(config, _DRIVE_CONFIG_NAMES)
    drive_choices = _filter_drive_choices(drive_choices)
    return CameraSettingsSnapshot(
        available=True,
        iso=iso_value or "",
        iso_choices=iso_choices,
        aperture=aperture_value or "",
        aperture_choices=aperture_choices,
        shutter=shutter_value or "",
        expcomp=expcomp_value or "",
        expcomp_choices=expcomp_choices,
        metering=metering_value or "",
        metering_choices=metering_choices,
        white_balance=wb_value or "",
        white_balance_choices=wb_choices,
        quality=quality_value or "",
        quality_choices=quality_choices,
        image_size=imagesize_value or "",
        image_size_choices=imagesize_choices,
        drive_mode=drive_value or "",
        drive_mode_choices=drive_choices,
    )


def read_current(camera_lock: threading.Lock, camera=None, context=None) -> CameraSettingsSnapshot:
    """Liest ISO/Blende + alle weiteren Werte inkl. der von der Kamera
    erlaubten Auswahllisten. Wirft nie eine Exception nach aussen - jeder
    Fehlerfall landet als `error` im Snapshot, damit der Service-Menue-
    Screen ihn einfach anzeigen kann statt abzustuerzen.

    `camera`/`context`: optional bereits offene Sitzung (z.B. waehrend die
    Live-Vorschau laeuft, siehe Modul-Docstring) - wird dann direkt
    verwendet, KEIN eigenes init()/exit(). Ohne Angabe (Normalfall ohne
    laufende Vorschau) wird wie bisher eine eigene, kurze Sitzung
    geoeffnet und wieder geschlossen."""
    if not _GP_AVAILABLE:
        return CameraSettingsSnapshot(
            available=False,
            error="gphoto2 ist auf diesem System nicht installiert.",
        )
    if camera is not None and context is not None:
        try:
            return _read_current_impl(camera, context)
        except Exception as exc:
            return CameraSettingsSnapshot(available=False, error=f"Kamera nicht erreichbar: {exc}")
    with camera_lock:
        context = gp.Context()
        camera = gp.Camera()
        try:
            camera.init(context)
            return _read_current_impl(camera, context)
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


def _set_widget_impl(camera, context, names: tuple[str, ...], value: str) -> tuple[bool, str | None]:
    try:
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


def _set_widget(
    camera_lock: threading.Lock, names: tuple[str, ...], value: str, camera=None, context=None
) -> tuple[bool, str | None]:
    if not _GP_AVAILABLE:
        return False, "gphoto2 ist auf diesem System nicht installiert."
    if camera is not None and context is not None:
        return _set_widget_impl(camera, context, names, value)
    with camera_lock:
        context = gp.Context()
        camera = gp.Camera()
        try:
            camera.init(context)
            return _set_widget_impl(camera, context, names, value)
        except Exception as exc:
            return False, f"Konnte Einstellung nicht setzen: {exc}"
        finally:
            try:
                camera.exit(context)
            except Exception:
                pass


def set_iso(camera_lock: threading.Lock, value: str, camera=None, context=None) -> tuple[bool, str | None]:
    """Setzt den ISO-Wert. `value` muss einer der zuvor per read_current()
    gelieferten iso_choices sein (siehe state_machine._step_choice)."""
    return _set_widget(camera_lock, _ISO_CONFIG_NAMES, value, camera, context)


def set_aperture(camera_lock: threading.Lock, value: str, camera=None, context=None) -> tuple[bool, str | None]:
    """Setzt die Blende. `value` muss einer der zuvor per read_current()
    gelieferten aperture_choices sein."""
    return _set_widget(camera_lock, _APERTURE_CONFIG_NAMES, value, camera, context)


def set_expcomp(camera_lock: threading.Lock, value: str, camera=None, context=None) -> tuple[bool, str | None]:
    """Setzt die Belichtungskorrektur (EV)."""
    return _set_widget(camera_lock, _EXPCOMP_CONFIG_NAMES, value, camera, context)


def set_metering(camera_lock: threading.Lock, value: str, camera=None, context=None) -> tuple[bool, str | None]:
    """Setzt das Belichtungsmessfeld (Center Weighted/Multi Spot/Center
    Spot bei der D3300 - Konfig-Name nicht einzeln verifiziert, siehe
    Modul-Docstring)."""
    return _set_widget(camera_lock, _METERING_CONFIG_NAMES, value, camera, context)


def set_white_balance(camera_lock: threading.Lock, value: str, camera=None, context=None) -> tuple[bool, str | None]:
    """Setzt den Weißabgleich."""
    return _set_widget(camera_lock, _WB_CONFIG_NAMES, value, camera, context)


def set_quality(camera_lock: threading.Lock, value: str, camera=None, context=None) -> tuple[bool, str | None]:
    """Setzt die Bildqualität (z.B. JPEG Fine statt RAW)."""
    return _set_widget(camera_lock, _QUALITY_CONFIG_NAMES, value, camera, context)


def set_image_size(camera_lock: threading.Lock, value: str, camera=None, context=None) -> tuple[bool, str | None]:
    """Setzt die Bildgröße/Auflösung."""
    return _set_widget(camera_lock, _IMAGESIZE_CONFIG_NAMES, value, camera, context)


def set_drive_mode(camera_lock: threading.Lock, value: str, camera=None, context=None) -> tuple[bool, str | None]:
    """Setzt den Aufnahmebetrieb (Einzelbild/Serie/Timer/... - Konfig-Name
    nicht einzeln verifiziert, siehe Modul-Docstring)."""
    return _set_widget(camera_lock, _DRIVE_CONFIG_NAMES, value, camera, context)


# ------------------------------------------------------------------------------
# Vollbild-Zoom zum Scharfstellen (Sprint 12)
# ------------------------------------------------------------------------------
# Lutz meinte mit "Zoom aus der Kamera heraus" NICHT den (bei der D3300 ohnehin
# rein mechanischen, von Hand bedienten) Objektiv-Zoom, sondern die
# "Lupenfunktion" des Nikon Live View: derselbe Zoom, den man am Kameragehaeuse
# selbst durch Druecken der Vergroesserungs-Taste waehrend des Live View
# bekommt - vergroessert einen Bildausschnitt des Live-Bilds direkt auf der
# Kamera, exakt zum praezisen manuellen Scharfstellen gedacht.
#
# Per libgphoto2-Quelle verifiziert (github.com/gphoto/libgphoto2):
# camlibs/ptp2/ptp.h definiert PTP_DPC_NIKON_LiveViewImageZoomRatio (0xD1A3),
# camlibs/ptp2/config.c bietet dafuer je nach Kameramodell/-generation einen
# freundlichen Konfig-Namen "liveviewimagezoomratio" an (Kommentar dort:
# "varies between nikons" - zwei unterschiedliche Wertelisten, z.B. bis "100%"
# oder bis "200%"). Fuer Lutz' D3300 KONKRET bestaetigt die im Projekt bereits
# zitierte Referenzdatei camlibs/ptp2/cameras/nikon-d3300.txt (Sprint-11-
# Nachbesserung), dass die D3300 diese Eigenschaft zwar besitzt (Zeile 79:
# "Live View Image Zoom Ratio(0xd1a3):(readwrite)"), libgphoto2 sie bei DIESEM
# Modell aber nicht auf den freundlichen Namen abbildet, sondern nur unter dem
# generischen Rohwert-Namen "d1a3" als MENU-Widget mit den Werten "0".."5"
# (0 = "Entire Display"/kein Zoom, 5 = staerkster Zoom, den die Kamera bietet -
# exakt "das, was die Kamera maximal kann"). "d1a3" deshalb als ERSTER
# Kandidat, "liveviewimagezoomratio" als Fallback fuer evtl. andere/zukuenftige
# Kameras an der Fotobox. Nutzt (wie alle anderen Einstellungen hier) die
# vorhandene _read_widget/_set_widget-Infrastruktur unveraendert - die D3300
# liefert das Property als gewoehnliches MENU (Auswahlliste), kein RANGE-
# Widget, trotz des "Range [0-5, step 1]"-Hinweises im rohen PTP-Formular
# (das beschreibt nur, wie die Kamera die PTP-Property intern deklariert, hier
# aber wandelt gphoto2 sie in die uebliche Auswahlliste um).
#
# LIVE-TEST-ERGEBNIS (Rueckmeldung Lutz, 2026-09-17): Der Hardware-Zoom
# ueber "d1a3" wird zwar von der D3300 angenommen (kein Fehler beim
# set_config), aber (a) das Setzen dauert am echten Geraet ca. 10 Sekunden,
# waehrend derer die komplette App-Bedienung (auch "Beenden") einfriert -
# der synchrone gphoto2-Aufruf blockiert den Haupt-/Event-Thread
# (app._set_admin_camera_zoom laeuft direkt im pygame-Event-Loop, siehe
# app.py) - und (b) selbst nach dieser Wartezeit aendert sich der von
# capture_preview() gelieferte Live-View-Bildausschnitt sichtbar NICHT.
# Damit hat sich der im Modul-Docstring oben offen gelassene Verdacht
# bestaetigt: Die Nikon-Zoom-Lupe (PTP 0xD1A3) wirkt bei der D3300
# offenbar nur auf die kamera-eigene Anzeige, nicht auf den per PTP
# abgegriffenen Live-View-Stream.
#
# Konsequenz: _ZOOM_CONFIG_NAMES bleibt deshalb bewusst LEER. _read_widget()
# findet dann nie einen Kandidaten, read_zoom() liefert dadurch immer
# hardware=False, und set_zoom() wird von app.py folglich nie mehr
# aufgerufen (siehe app._set_admin_camera_zoom: fruehzeitiger Return bei
# hardware=False) - das beseitigt zugleich den 10-Sekunden-Hänger. Das
# Feature laeuft jetzt ausschliesslich ueber den Software-Zoom-Fallback
# (SW_ZOOM_CHOICES unten, reiner Crop+Skalierung im Renderer, keine
# Kamera-Anfrage noetig). Die Konstante bleibt als Tupel (statt komplett
# entfernt) erhalten, falls ein zukuenftiges Kameramodell an der Fotobox
# die Eigenschaft doch ueber den Live-View-Stream sichtbar macht - dann
# hier die Kandidaten wieder eintragen. Rest dieses Abschnitts (technische
# PTP-Herleitung) bewusst nicht geloescht, dokumentiert die Recherche fuer
# einen moeglichen spaeteren erneuten Versuch.
_ZOOM_CONFIG_NAMES: tuple[str, ...] = ()

# Fallback, falls eine (andere/zukuenftige) Kamera an der Fotobox gar keinen
# Live-View-Zoom anbietet: rein rechnerischer Zoom im Renderer (Crop +
# Skalierung des bereits uebertragenen Vorschau-Frames, siehe renderer.
# _draw_admin_camera_zoom) - keine weitere Kamera-Anfrage noetig, funktioniert
# mit jeder Kamera. "100%" = Originalgroesse des Livebilds (Lutz' Vorgabe:
# "-" geht maximal bis hierhin zurueck). Obergrenze bewusst bei 400% gekappt -
# staerker vergroessert wird das ohnehin nur ca. 640x424 grosse Vorschaubild
# zu unscharf, um beim Scharfstellen noch zu helfen (der eigentliche Zweck
# dieses Features).
SW_ZOOM_CHOICES = ("100%", "150%", "200%", "300%", "400%")


@dataclass
class CameraZoomSnapshot:
    """Ergebnis von read_zoom() - eigenstaendig statt Teil von
    CameraSettingsSnapshot (Zoom ist kein Belichtungswert, gehoert nicht zur
    normalen Kamera-Einstellungen-Seite). `hardware=True` nur, wenn die
    Kamera selbst einen der _ZOOM_CONFIG_NAMES-Kandidaten anbietet - dann
    tragen `value`/`choices` deren Rohwerte. Sonst bereits mit dem Software-
    Zoom-Fallback befuellt (SW_ZOOM_CHOICES), damit Aufrufer nicht zwischen
    beiden Faellen unterscheiden muessen: dieselbe generalisierte
    _step_admin_camera_field()-Logik wie bei ISO/Blende/etc. steppt in
    beiden Faellen einfach durch `choices`."""

    available: bool
    hardware: bool
    value: str = ""
    choices: tuple[str, ...] = ()
    error: str | None = None


def _sw_zoom_fallback(error: str | None = None) -> "CameraZoomSnapshot":
    return CameraZoomSnapshot(
        available=True, hardware=False, value=SW_ZOOM_CHOICES[0], choices=SW_ZOOM_CHOICES, error=error,
    )


def _read_zoom_impl(camera, context) -> CameraZoomSnapshot:
    config = camera.get_config(context)
    value, choices = _read_widget(config, _ZOOM_CONFIG_NAMES)
    if value is None:
        # Kamera bietet keinen der bekannten Zoom-Konfig-Namen (bei der
        # D3300 nicht erwartet, siehe Modul-Docstring oben) - Software-
        # Zoom-Fallback statt "nicht verfuegbar", damit das Feature trotzdem
        # nutzbar bleibt.
        return _sw_zoom_fallback()
    return CameraZoomSnapshot(available=True, hardware=True, value=value, choices=choices)


def read_zoom(camera_lock: threading.Lock, camera=None, context=None) -> CameraZoomSnapshot:
    """Ermittelt, ob die Kamera selbst eine Live-View-Zoom-Lupe anbietet
    (siehe _ZOOM_CONFIG_NAMES) - sonst Software-Zoom-Fallback
    (SW_ZOOM_CHOICES). Wirft nie eine Exception nach aussen, gleiches
    Prinzip wie read_current(). `camera`/`context`: siehe read_current()."""
    if not _GP_AVAILABLE:
        return _sw_zoom_fallback()
    if camera is not None and context is not None:
        try:
            return _read_zoom_impl(camera, context)
        except Exception as exc:
            return _sw_zoom_fallback(error=f"Kamera-Zoom nicht lesbar: {exc}")
    with camera_lock:
        context = gp.Context()
        camera = gp.Camera()
        try:
            camera.init(context)
            return _read_zoom_impl(camera, context)
        except Exception as exc:
            return _sw_zoom_fallback(error=f"Kamera nicht erreichbar: {exc}")
        finally:
            try:
                camera.exit(context)
            except Exception:
                pass


def set_zoom(camera_lock: threading.Lock, value: str, camera=None, context=None) -> tuple[bool, str | None]:
    """Setzt den Kamera-Zoom (Live-View-Zoom-Lupe). Wird von app.py NUR
    aufgerufen, wenn read_zoom() zuvor hardware=True gemeldet hat - beim
    Software-Zoom-Fallback (SW_ZOOM_CHOICES) macht diese Funktion nichts,
    der Zoom-Effekt entsteht dort rein im Renderer (Crop des bereits
    vorhandenen Vorschau-Frames), siehe app._set_admin_camera_zoom."""
    return _set_widget(camera_lock, _ZOOM_CONFIG_NAMES, value, camera, context)


def format_zoom_label(hardware: bool, value: str, choices: tuple[str, ...]) -> str:
    """Menschenlesbare Beschriftung fuer den aktuellen Zoom-Wert.
    Software-Fallback-Werte (z.B. "150%") sind bereits fertig lesbar. Bei
    echtem Kamera-Zoom sind die Rohwerte kryptische Zahlen ("0".."5", siehe
    Modul-Docstring) - hier stattdessen "Kein Zoom (Originalansicht)" fuer
    den ersten (unveraenderten) Wert bzw. "Zoomstufe X von N" fuer die
    uebrigen, aus der tatsaechlichen Laenge von `choices` abgeleitet statt
    fest codierter Prozentangaben (die laut libgphoto2-Quelle je nach
    Kameramodell variieren, siehe Modul-Docstring)."""
    if not hardware:
        return value or SW_ZOOM_CHOICES[0]
    if not choices:
        return value
    try:
        index = choices.index(value)
    except ValueError:
        return value
    if index == 0:
        return "Kein Zoom (Originalansicht)"
    return f"Zoomstufe {index} von {len(choices) - 1}"


# ------------------------------------------------------------------------------
# Manueller Schnell-Test (direkt auf dem Pi ausführen)
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    lock = threading.Lock()
    print("hw_camera_settings_provider.py Schnelltest")
    print("Kamera eingeschaltet, per USB verbunden, Modus A oder M? (STRG+C = Abbruch)")
    input("ENTER drücken, um alle Werte zu lesen...")

    snapshot = read_current(lock)
    print(f"available={snapshot.available} error={snapshot.error}")
    print(f"ISO aktuell: {snapshot.iso!r}  Auswahl: {snapshot.iso_choices}")
    print(f"Blende aktuell: {snapshot.aperture!r}  Auswahl: {snapshot.aperture_choices}")
    print(f"Verschlusszeit aktuell (nur Info): {snapshot.shutter!r}")
    print(f"Belichtungskorrektur: {snapshot.expcomp!r}  Auswahl: {snapshot.expcomp_choices}")
    print(f"Messfeld: {snapshot.metering!r}  Auswahl: {snapshot.metering_choices}")
    print(f"Weißabgleich: {snapshot.white_balance!r}  Auswahl: {snapshot.white_balance_choices}")
    print(f"Bildqualität: {snapshot.quality!r}  Auswahl: {snapshot.quality_choices}")
    print(f"Bildgröße: {snapshot.image_size!r}  Auswahl: {snapshot.image_size_choices}")
    print(f"Aufnahmebetrieb: {snapshot.drive_mode!r}  Auswahl: {snapshot.drive_mode_choices}")

    if snapshot.available and snapshot.iso_choices:
        candidate = snapshot.iso_choices[0]
        answer = input(f"ISO probeweise auf {candidate!r} setzen? (j/N) ")
        if answer.strip().lower() == "j":
            ok, error = set_iso(lock, candidate)
            print(f"set_iso({candidate!r}) -> ok={ok} error={error}")

    # NEU (Sprint 12): eigener Schnelltest fuer die Zoom-Lupe - separat vom
    # obigen ISO-Test, da hier vor allem interessiert, ob die D3300
    # ueberhaupt als hardware=True erkannt wird (siehe Modul-Docstring,
    # "d1a3" ist bislang nur per Referenzdump verifiziert, nicht live).
    print()
    input("ENTER drücken, um den Zoom-Status zu lesen...")
    zoom = read_zoom(lock)
    print(f"hardware={zoom.hardware} error={zoom.error}")
    print(f"Zoom aktuell: {zoom.value!r}  Auswahl: {zoom.choices}")
    print(f"Anzeige-Text: {format_zoom_label(zoom.hardware, zoom.value, zoom.choices)!r}")
    if zoom.hardware and len(zoom.choices) > 1:
        candidate = zoom.choices[1]
        answer = input(f"Zoom probeweise auf {candidate!r} setzen (Stufe 1)? (j/N) ")
        if answer.strip().lower() == "j":
            ok, error = set_zoom(lock, candidate)
            print(f"set_zoom({candidate!r}) -> ok={ok} error={error}")
            print("Bitte am Live-Vorschaubild pruefen, ob der Zoom sichtbar wird "
                  "(siehe hw_gphoto2_preview_provider.py-Schnelltest).")
            input("ENTER druecken, um wieder auf 'kein Zoom' zurueckzusetzen...")
            ok, error = set_zoom(lock, zoom.choices[0])
            print(f"set_zoom({zoom.choices[0]!r}) -> ok={ok} error={error}")
