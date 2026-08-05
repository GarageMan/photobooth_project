from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class EventType(Enum):
    APP_STARTED = auto()
    TICK = auto()
    TAP_PHOTO = auto()
    TAP_GALLERY = auto()
    TAP_INSTRUCTIONS = auto()
    TAP_TERMS = auto()
    TAP_BACK = auto()
    TAP_CANCEL = auto()
    TAP_SAVE = auto()
    TAP_DELETE = auto()
    TAP_CONFIRM_DELETE = auto()
    TAP_ABORT_DELETE = auto()
    TAP_FULLSCREEN_PHOTO = auto()
    # NEU (Sprint 11, Feature 4): Doppeltap auf das Foto oder Icon "QR-Code
    # anfordern" in GALLERY_FULLSCREEN -> AppState.GALLERY_PHOTO_QR.
    TAP_GALLERY_QR = auto()
    # NEU (Sprint 11, Feature 4): gallery_qr_deadline abgelaufen -> zurueck
    # zu GALLERY_FULLSCREEN (analog zu QR_TIMEOUT).
    GALLERY_QR_TIMEOUT = auto()
    BUTTON_PRESS = auto()
    SWIPE_LEFT = auto()
    SWIPE_RIGHT = auto()
    SWIPE_UP = auto()
    SWIPE_DOWN = auto()
    IDLE_TIMEOUT = auto()
    WARNING_TIMEOUT = auto()
    DELETE_TIMEOUT = auto()
    QR_TIMEOUT = auto()
    COUNTDOWN_FINISHED = auto()
    CAPTURE_REQUESTED = auto()
    CAPTURE_OK = auto()
    CAPTURE_FAILED = auto()
    PREVIEW_READY = auto()
    PREVIEW_FAILED = auto()
    ERROR_ACKNOWLEDGED = auto()
    # --- Verstecktes Herunterfahren (Schritt 3) ---
    # Geheim-Geste im Hauptmenue erkannt -> Wechsel nach PIN_ENTRY.
    SHUTDOWN_GESTURE_DETECTED = auto()
    # Ziffernfeld-Eingaben. PIN_DIGIT traegt die getippte Ziffer im payload
    # als {"digit": "0".."9"}; der State-/App-Layer haengt sie an den
    # Eingabepuffer an.
    PIN_DIGIT = auto()
    PIN_BACKSPACE = auto()      # letzte Ziffer loeschen
    PIN_SUBMIT = auto()         # aktuelle Eingabe pruefen (PinLockout.check)
    PIN_ENTRY_CANCEL = auto()   # Eingabe abbrechen -> zurueck ins Hauptmenue
    # Abschieds-Animation (SHUTDOWN_GOODBYE) abgelaufen -> App loest das
    # eigentliche Poweroff aus. Analog zu den uebrigen *_TIMEOUT-Events.
    SHUTDOWN_TIMEOUT = auto()
    # --- Service-/Admin-Menue (Schritt 4) ---
    # Antippen der einzelnen Menuepunkte. Welcher Button welches Event
    # ausloest, steht in admin_menu.py (ADMIN_MENU_ITEMS), nicht hier.
    # "Zurueck" nutzt bewusst das bestehende TAP_BACK.
    TAP_ADMIN_STATUS = auto()
    TAP_ADMIN_USB_EXPORT = auto()
    TAP_ADMIN_DELETE_ALL = auto()
    TAP_ADMIN_RESTART_APP = auto()
    TAP_ADMIN_SHUTDOWN = auto()
    # NEU (Sprint 11, Feature 2): Kamera-Einstellungen (ISO/Blende) direkt
    # ueber USB, ohne die Kamera aus dem Gehaeuse zu nehmen.
    TAP_ADMIN_CAMERA_SETTINGS = auto()
    # Aktuelle Werte + gueltige Auswahllisten sind ermittelt (synchron
    # gelesen, siehe app_with_hw._read_admin_camera_settings); payload
    # traegt die Snapshot-Felder (iso, aperture, iso_choices,
    # aperture_choices, available, error).
    ADMIN_CAMERA_SETTINGS_READY = auto()
    # +/- durch die von der Kamera gelieferte choices-Liste wandern.
    TAP_ADMIN_CAMERA_ISO_UP = auto()
    TAP_ADMIN_CAMERA_ISO_DOWN = auto()
    TAP_ADMIN_CAMERA_APERTURE_UP = auto()
    TAP_ADMIN_CAMERA_APERTURE_DOWN = auto()
    # NEU (Kamera-Menue 2.0, Nutzer-Feedback): weitere per USB einstellbare
    # Werte, gleiches +/- Prinzip wie ISO/Blende.
    TAP_ADMIN_CAMERA_EXPCOMP_UP = auto()
    TAP_ADMIN_CAMERA_EXPCOMP_DOWN = auto()
    TAP_ADMIN_CAMERA_METERING_UP = auto()
    TAP_ADMIN_CAMERA_METERING_DOWN = auto()
    TAP_ADMIN_CAMERA_WB_UP = auto()
    TAP_ADMIN_CAMERA_WB_DOWN = auto()
    TAP_ADMIN_CAMERA_QUALITY_UP = auto()
    TAP_ADMIN_CAMERA_QUALITY_DOWN = auto()
    TAP_ADMIN_CAMERA_IMAGESIZE_UP = auto()
    TAP_ADMIN_CAMERA_IMAGESIZE_DOWN = auto()
    TAP_ADMIN_CAMERA_DRIVE_UP = auto()
    TAP_ADMIN_CAMERA_DRIVE_DOWN = auto()
    # NEU (Kamera-Menue 2.0): Seite 1 (Belichtung: ISO/Blende/Verschlusszeit/
    # Belichtungskorrektur/Messfeld) <-> Seite 2 (Sonstiges: Weissabgleich/
    # Bildqualitaet/Bildgroesse/Aufnahmebetrieb).
    TAP_ADMIN_CAMERA_PAGE_NEXT = auto()
    TAP_ADMIN_CAMERA_PAGE_PREV = auto()
    # NEU (Kamera-Menue 2.0): ersetzt das bisherige "Zurueck" (TAP_BACK) auf
    # diesem Screen - Speichern bestaetigt den aktuellen (schon live
    # gesetzten) Stand, Abbrechen sendet die Werte zurueck, mit denen der
    # Screen betreten wurde (siehe state_machine._handle_admin_camera_settings).
    TAP_ADMIN_CAMERA_SAVE = auto()
    TAP_ADMIN_CAMERA_CANCEL = auto()
    # --- Veranstaltungsdaten (letzte Sprint-11-Aufgabe) --------------------
    TAP_ADMIN_EVENT_SETTINGS = auto()          # Menuepunkt in ADMIN_MENU
    # Aktuelle Werte sind synchron ermittelt (siehe
    # app_with_hw._collect_admin_event_settings); payload: title, prefix,
    # wifi_ssid, wifi_password, qr_enabled, gallery_enabled.
    ADMIN_EVENT_SETTINGS_READY = auto()
    # payload: {"field": "title"|"prefix"|"wifi_ssid"|"wifi_password"} -
    # oeffnet ADMIN_EVENT_TEXT_ENTRY fuer genau dieses Feld.
    TAP_ADMIN_EVENT_FIELD_EDIT = auto()
    # payload: {"field": "qr"|"gallery"} - kippt den jeweiligen Schalter.
    TAP_ADMIN_EVENT_TOGGLE = auto()
    # Zeigt/verbirgt das Gaeste-WLAN-Passwort im Klartext - gilt fuer die
    # Uebersicht UND den Tastatur-Screen (ein gemeinsames Flag).
    TAP_ADMIN_EVENT_TOGGLE_PASSWORD_VISIBLE = auto()
    TAP_ADMIN_EVENT_WALLPAPER_IMPORT = auto()
    # Hintergrund-Thread (Stick suchen/einbinden/Bild kopieren/aushaengen)
    # ist fertig; payload: ok, lines.
    ADMIN_EVENT_WALLPAPER_IMPORT_FINISHED = auto()
    TAP_ADMIN_EVENT_SAVE = auto()
    # Ergebnis des synchronen JSON-Schreibens; payload: ok, message.
    ADMIN_EVENT_SAVE_RESULT = auto()
    TAP_ADMIN_EVENT_RESTART_NOW = auto()
    # Tastatur-Eingaben - gleiches Prinzip wie PIN_DIGIT/PIN_BACKSPACE/
    # PIN_SUBMIT/PIN_ENTRY_CANCEL, aber fuer beliebigen Text statt nur
    # Ziffern, dazu eine Umschalt-Taste fuer Gross-/Kleinschreibung.
    TEXT_ENTRY_CHAR = auto()        # payload: {"char": "a"}
    TEXT_ENTRY_BACKSPACE = auto()
    TEXT_ENTRY_SHIFT = auto()
    TEXT_ENTRY_SUBMIT = auto()      # "OK" - schreibt den Puffer ins Zielfeld
    TEXT_ENTRY_CANCEL = auto()      # verwirft den Puffer
    # NEU (4.3): Diagnosezeilen sind fertig ermittelt (app_with_hw sammelt
    # sie synchron nach TAP_ADMIN_STATUS und liefert sie im payload zurueck).
    ADMIN_STATUS_READY = auto()
    # NEU (4.3): der kurze Anzeige-Timer in ADMIN_RESTART_PENDING ist
    # abgelaufen - loest die "restart_app"-Action aus.
    ADMIN_RESTART_TIMEOUT = auto()
    # NEU (4.4): Ja/Nein der Sicherheitsabfrage vor dem Loeschen. Bewusst
    # eigene Events statt TAP_CONFIRM_DELETE/TAP_ABORT_DELETE - jene
    # gehoeren zum Loeschen eines EINZELNEN Fotos im Review-Ablauf und
    # duerfen sich mit dem Loeschen des Gesamtbestands nicht vermischen.
    TAP_ADMIN_DELETE_CONFIRM = auto()
    TAP_ADMIN_DELETE_ABORT = auto()
    # NEU (4.4): Hintergrund-Thread ist fertig; payload enthaelt unter
    # "lines" die Zusammenfassung fuer den Abschluss-Screen.
    ADMIN_DELETE_FINISHED = auto()
    # NEU (Sprint-11-Nachbesserung, Nutzer-Feedback): Ja/Nein der
    # Sicherheitsabfrage vor dem Herunterfahren - gleiches Prinzip wie
    # TAP_ADMIN_DELETE_CONFIRM/_ABORT, eigene Events statt Wiederverwendung,
    # damit sich die beiden Sicherheitsabfragen nicht vermischen.
    TAP_ADMIN_SHUTDOWN_CONFIRM = auto()
    TAP_ADMIN_SHUTDOWN_ABORT = auto()
    # --- USB-Export (Etappe 4a) ---
    # Platzbedarf ist berechnet; payload["lines"] enthaelt die Anzeige.
    ADMIN_USB_INFO_READY = auto()
    # Ein Stick wurde gefunden; payload["name"] beschreibt ihn.
    ADMIN_USB_DETECTED = auto()
    # "Weiter" auf dem Warte- bzw. Bereit-Bildschirm.
    TAP_ADMIN_USB_CONTINUE = auto()
    # Pruefung abgeschlossen; payload: ok, too_small, not_enough_free, lines.
    ADMIN_USB_CHECK_DONE = auto()
    # sync + umount abgeschlossen; payload["lines"].
    ADMIN_USB_EJECTED = auto()
    # NEU (4.7): Hintergrund-Thread (Kopieren+Verifikation) ist fertig.
    # NEU (6b): payload traegt zusaetzlich "conflicts" - ein Tupel
    # offener ExportConflict-Eintraege (leer, wenn keine Konflikte
    # gefunden wurden; dann bleibt der bisherige Ablauf unveraendert).
    ADMIN_USB_EXPORT_FINISHED = auto()
    # NEU (4.7): "Stick leeren" im Problem-Screen (nur bei not_enough_free).
    TAP_ADMIN_USB_CLEAR = auto()
    # --- USB-Export Konfliktbehebung (Etappe 6b) ---
    # Einzelne Entscheidung fuer eine Datei aendern.
    # payload: {"name": str, "decision": "overwrite"|"rename"}.
    TAP_ADMIN_USB_CONFLICT_DECISION = auto()
    # Sammelaktionen: alle noch offenen Konflikte auf dieselbe Entscheidung
    # setzen (ersetzt keine bereits einzeln geaenderten Eintraege NICHT
    # gesondert - wirkt gleichermassen auf alle, das ist bewusst einfach
    # gehalten: "alles ueberschreiben"/"alles umbenennen" meint wirklich
    # alles).
    TAP_ADMIN_USB_CONFLICTS_OVERWRITE_ALL = auto()
    TAP_ADMIN_USB_CONFLICTS_RENAME_ALL = auto()
    # "Ausfuehren": wendet die aktuellen Entscheidungen an (startet den
    # Hintergrund-Thread fuer admin_usb_export.apply_conflict_resolutions).
    TAP_ADMIN_USB_CONFLICTS_APPLY = auto()
    # Hintergrund-Thread (Phase 2 - Konfliktaufloesung) ist fertig.
    # payload: "lines", "ok" - gleiche Form wie ADMIN_USB_EXPORT_FINISHED.
    ADMIN_USB_RESOLVE_FINISHED = auto()


@dataclass(slots=True, frozen=True)
class AppEvent:
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "app"
