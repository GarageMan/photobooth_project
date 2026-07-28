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
    # Hauptmenue (siehe shutdown_service.SecretGestureDetector).
    PIN_ENTRY = auto()
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