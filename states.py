from __future__ import annotations

from enum import Enum, auto


class AppState(Enum):
    BOOT = auto()
    MAIN_MENU = auto()
    ATTRACT_GALLERY = auto()
    GALLERY_GRID = auto()
    # NEU (Etappe 7): GALLERY_GRID wird betreten, aber es gibt noch keine
    # Fotos - eigener Zustand statt eines Leer-Falls INNERHALB von
    # GALLERY_GRID, weil hier andere Buttons ("Jetzt fotografieren" statt
    # Thumbnails) und ein eigener LED-Effekt gelten (siehe led_service.py).
    GALLERY_EMPTY = auto()
    GALLERY_FULLSCREEN = auto()
    # NEU (Sprint 11, Feature 4): aus GALLERY_FULLSCREEN per Doppeltap auf
    # das Foto ODER per Icon "QR-Code anfordern" erreichbar - zeigt den
    # QR-Code des Downloadlinks fuer GENAU dieses eine Foto (nicht mehr
    # nur fuer das zuletzt aufgenommene, siehe qr_service.py). Schliesst
    # sich nach gallery_qr_seconds automatisch oder per "Zurueck" wieder,
    # zurueck zu GALLERY_FULLSCREEN (Foto bleibt dabei ausgewaehlt).
    GALLERY_PHOTO_QR = auto()
    PHOTO_INTRO = auto()
    PHOTO_PREVIEW = auto()
    COUNTDOWN = auto()
    CAPTURE_PENDING = auto()
    REVIEW = auto()
    DELETE_CONFIRM = auto()
    QR_DISPLAY = auto()
    INSTRUCTIONS = auto()
    TERMS = auto()
    ERROR_SCREEN = auto()
    MAINTENANCE = auto()
    # --- Verstecktes Herunterfahren (Schritt 3) ---
    # PIN_ENTRY: Ziffernfeld, erscheint nach erkannter Geheim-Geste im
    # Hauptmenue (siehe admin_service.SecretGestureDetector, umbenannt
    # Sprint 11, vormals shutdown_service.py).
    PIN_ENTRY = auto()
    # NEU (Sprint-11-Nachbesserung, Nutzer-Feedback): Sicherheitsabfrage vor
    # dem eigentlichen Herunterfahren - ein Fehltipp auf "Herunterfahren" im
    # Service-Menue fuehrte bisher SOFORT und unabbrechbar in SHUTDOWN_GOODBYE
    # (siehe dort: "Bewusst NICHT abbrechbar"). Gleiches Sicherheitsprinzip
    # wie ADMIN_DELETE_CONFIRM, nur fuer's Herunterfahren statt Loeschen.
    ADMIN_SHUTDOWN_CONFIRM = auto()
    # SHUTDOWN_GOODBYE: Abschieds-Animation (Wallpaper shutdown_wallpaper.png
    # + LED-Sonnenuntergang led_shutdown.py); danach faehrt der Pi herunter.
    SHUTDOWN_GOODBYE = auto()
    # --- Service-/Admin-Menue (Schritt 4) ---
    # ADMIN_MENU: erscheint nach korrekt eingegebener Wartungs-PIN und
    # buendelt alle Wartungsfunktionen (Status, USB-Export, alle Bilder
    # loeschen, App-Neustart, Herunterfahren). Die PIN schuetzt bewusst
    # das gesamte Menue statt einzelner Punkte - siehe admin_menu.py.
    ADMIN_MENU = auto()
    # NEU (4.3): Diagnose-Unterseite des Service-Menues.
    ADMIN_STATUS = auto()
    # NEU (Sprint 11, Feature 2): ISO/Blende direkt ueber die USB-Verbindung
    # anpassen, ohne die Kamera aus dem Gehaeuse zu nehmen (siehe
    # hw_camera_settings_provider.py). Werte/Auswahllisten werden synchron
    # beim Betreten gelesen (kein Hintergrund-Thread noetig - einzelne
    # gphoto2-Config-Calls sind ueblicherweise deutlich unter einer
    # Sekunde), +/- wandert in der von der Kamera gelieferten choices-Liste.
    ADMIN_CAMERA_SETTINGS = auto()
    # --- Veranstaltungsdaten (letzte Sprint-11-Aufgabe) --------------------
    # Titel/Datei-Praefix/QR-/Galerie-Schalter/Gaeste-WLAN-SSID+Passwort
    # direkt am Touchscreen pflegen statt event_config.json von Hand zu
    # editieren (siehe event_config_service.py). Wirkt (bis auf das
    # Wallpaper) erst nach einem Neustart, da AppConfig beim Start
    # eingefroren wird (siehe config.py::load_event_config).
    ADMIN_EVENT_SETTINGS = auto()
    # Bildschirmtastatur fuer GENAU EIN Textfeld - welches, steht in
    # ui.admin_event_edit_field. Ein gemeinsamer Screen fuer Titel/Praefix/
    # WLAN-SSID/WLAN-Passwort statt vier fast identischer States.
    ADMIN_EVENT_TEXT_ENTRY = auto()
    # GEAENDERT (Nutzer-Feedback): Hintergrund-Thread sucht einen USB-Stick
    # und listet ALLE gefundenen Bilder darauf (statt wie bisher automatisch
    # das alphabetisch erste zu kopieren) - bewusst nicht abbrechbar, analog
    # zu ADMIN_USB_CHECK. Umbenannt von ADMIN_EVENT_WALLPAPER_IMPORT, da
    # hier nichts mehr importiert/kopiert wird, nur gesucht/gelistet.
    ADMIN_EVENT_WALLPAPER_PICK_LOADING = auto()
    # NEU (Nutzer-Feedback): scrollbare Liste der auf dem Stick gefundenen
    # Bilder - Antippen markiert eine Auswahl, "Speichern" kopiert NUR in
    # eine Zwischenablage (noch nicht das echte Hauptmenue-Wallpaper, siehe
    # event_config_service.py), "Abbrechen" verwirft die Auswahl. Der
    # USB-Stick bleibt waehrend dieses gesamten Screens gemountet (analog
    # zum USB-Export-Ablauf, self._wallpaper_pick_stick in app_with_hw.py)
    # und wird erst beim Verlassen (Speichern ODER Abbrechen) wieder
    # ausgehaengt.
    ADMIN_EVENT_WALLPAPER_PICK = auto()
    # Nur noch FEHLER-Anzeige (kein Stick gefunden/Mount fehlgeschlagen/
    # keine Bilder auf dem Stick) - der Erfolgsfall fuehrt seit der
    # Auswahlliste (ADMIN_EVENT_WALLPAPER_PICK) direkt zurueck auf
    # ADMIN_EVENT_SETTINGS statt hierher.
    ADMIN_EVENT_WALLPAPER_RESULT = auto()
    # Nach erfolgreichem Speichern: bietet "Jetzt neu starten" (fuehrt in
    # ADMIN_RESTART_PENDING, exakt derselbe Ablauf wie der bestehende
    # Menuepunkt "App neu starten") oder "Spaeter" (zurueck ins
    # Service-Menue, Werte greifen erst beim naechsten Neustart).
    ADMIN_EVENT_SAVED = auto()
    # NEU (4.3): kurzer, nicht abbrechbarer Zwischenscreen nach "App neu
    # starten" - gibt sichtbares Feedback, bevor die App sich beendet
    # (die Auto-Restart-Schleife in start_fotobox.sh startet sie danach
    # automatisch neu).
    ADMIN_RESTART_PENDING = auto()
    # NEU (4.4): Sicherheitsabfrage vor dem Loeschen aller Bilder.
    # Bewusst ein eigener Zustand statt einer Wiederverwendung von
    # DELETE_CONFIRM - jener loescht nur das eine gerade aufgenommene
    # Foto, hier geht es um den kompletten Bestand inklusive Kamera.
    ADMIN_DELETE_CONFIRM = auto()
    # NEU (4.4): Loeschung laeuft (Hintergrund-Thread), nicht abbrechbar.
    ADMIN_DELETE_RUNNING = auto()
    # NEU (4.4): Ergebnis-Screen mit Zusammenfassung und Protokoll-Hinweis.
    ADMIN_DELETE_DONE = auto()
    # --- USB-Export (Etappe 4a) ---
    # ADMIN_USB_WAIT: zeigt den benoetigten Platz und wartet darauf, dass
    # ein Stick eingesteckt wird (Hintergrund-Suche in app_with_hw).
    ADMIN_USB_WAIT = auto()
    # ADMIN_USB_CHECK: einbinden und messen (Hintergrund-Thread).
    ADMIN_USB_CHECK = auto()
    # ADMIN_USB_READY: Stick geprueft und ausreichend gross/frei.
    ADMIN_USB_READY = auto()
    # ADMIN_USB_PROBLEM: zu klein, zu wenig frei oder nicht beschreibbar.
    ADMIN_USB_PROBLEM = auto()
    # ADMIN_USB_EJECT: sync + umount laeuft (Hintergrund-Thread).
    ADMIN_USB_EJECT = auto()
    # ADMIN_USB_REMOVE: "Stick kann jetzt entfernt werden".
    ADMIN_USB_REMOVE = auto()
    # NEU (4.7): Kopierlauf mit Fortschrittsanzeige.
    ADMIN_USB_COPY = auto()
    # NEU (6b): mindestens eine Datei mit ABWEICHENDEM Inhalt gefunden
    # (Etappe 6a erkennt das inhaltsbasiert per SHA256). Der Ablauf
    # pausiert hier fuer die interaktive Auswahl (ueberschreiben/umbenennen
    # je Datei oder als Sammelaktion), bevor er fortgesetzt wird.
    ADMIN_USB_CONFLICTS = auto()
    # NEU (6b): die auf dem Konflikt-Screen getroffenen Entscheidungen
    # werden angewendet - Hintergrund-Thread, analog zu ADMIN_USB_COPY.
    ADMIN_USB_RESOLVE = auto()
    # NEU (4.7): Ergebnis-Screen nach dem Export.
    ADMIN_USB_EXPORT_DONE = auto()