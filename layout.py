from __future__ import annotations

from dataclasses import dataclass

import pygame

from states import AppState


@dataclass(frozen=True)
class LayoutRects:
    # Hauptmenue: 4 diagonal versetzte Buttons
    main_photo: pygame.Rect
    main_gallery: pygame.Rect
    main_instructions: pygame.Rect
    main_terms: pygame.Rect
    # Generische Zwei-Button-Reihe (Fotografieren-Menue, Countdown-Menue,
    # Review, Loesch-Bestaetigung - alle nutzen dieselbe Position)
    left: pygame.Rect
    right: pygame.Rect
    # Einzelner Button (Galerie/Attract/QR/Fehler "Zurueck")
    back: pygame.Rect
    # Eigener "Zurueck"/"Verstanden"-Button fuer die beiden scrollbaren
    # Text-Vollbild-Ansichten (INSTRUCTIONS, TERMS) - bewusst NICHT "right"
    # wiederverwendet, weil "right" auch von PHOTO_INTRO/PHOTO_PREVIEW/
    # COUNTDOWN/REVIEW/DELETE_CONFIRM/QR_DISPLAY genutzt wird und diese
    # unveraendert bleiben sollen. Tiefer positioniert als "right", um den
    # sonst ungenutzten Rand am unteren Bildschirmrand fuer mehr Textzeilen
    # nutzbar zu machen (siehe _draw_terms/_draw_instructions).
    text_view_back: pygame.Rect
    # NEU (3.3): Ziffernfeld fuer die versteckte PIN-Eingabe (PIN_ENTRY).
    # Schluessel: "0".."9", "backspace", "submit", "cancel".
    pin_keys: dict[str, pygame.Rect]
    # NEU (6c): Sammelaktionen auf dem USB-Konflikt-Screen. "Ausfuehren"
    # nutzt bewusst KEIN eigenes Rect, sondern das bereits vorhandene
    # "right" (gleiche Position wie "Weiter"/"Export starten" bei den
    # uebrigen USB-Screens) - ein Screen, ein gewohnter Platz fuer die
    # Hauptaktion.
    usb_conflicts_overwrite_all: pygame.Rect
    usb_conflicts_rename_all: pygame.Rect
    # NEU (Sprint 11, Feature 4): kompaktes Icon "QR-Code anfordern" unten
    # rechts auf dem Foto in GALLERY_FULLSCREEN - Alternative zum Doppeltap.
    gallery_qr_icon: pygame.Rect
    # NEU (Sprint 11, Feature 2): je ein "-"/"+"-Buttonpaar fuer ISO (obere
    # Zeile) und Blende (untere Zeile) auf AppState.ADMIN_CAMERA_SETTINGS -
    # der jeweils aktuelle Wert wird vom Renderer MITTIG zwischen beiden
    # Buttons gezeichnet (kein eigenes Rect noetig, reiner Text).
    admin_camera_iso_minus: pygame.Rect
    admin_camera_iso_plus: pygame.Rect
    admin_camera_aperture_minus: pygame.Rect
    admin_camera_aperture_plus: pygame.Rect
    # NEU (Kamera-Menue 2.0, Nutzer-Feedback nach Sprint 11): 2-Seiten-
    # Layout mit Live-Vorschau-Panel links (admin_camera_preview) und einer
    # Spalte von +/--Zeilen rechts. Seite 1 "Belichtung": ISO/Blende (s.o.),
    # Verschlusszeit (reiner Info-Wert, keine Buttons), Belichtungskorrektur,
    # Messfeld. Seite 2 "Sonstiges": Weissabgleich, Bildqualitaet,
    # Bildgroesse, Aufnahmebetrieb. Beide Seiten nutzen dieselben vier
    # Zeilenpositionen (0-3) - der Renderer zeichnet je Frame immer nur die
    # zur aktuellen Seite (model.ui.admin_camera_page) gehoerenden Buttons.
    admin_camera_preview: pygame.Rect
    admin_camera_expcomp_minus: pygame.Rect
    admin_camera_expcomp_plus: pygame.Rect
    admin_camera_metering_minus: pygame.Rect
    admin_camera_metering_plus: pygame.Rect
    admin_camera_wb_minus: pygame.Rect
    admin_camera_wb_plus: pygame.Rect
    admin_camera_quality_minus: pygame.Rect
    admin_camera_quality_plus: pygame.Rect
    admin_camera_imagesize_minus: pygame.Rect
    admin_camera_imagesize_plus: pygame.Rect
    admin_camera_drive_minus: pygame.Rect
    admin_camera_drive_plus: pygame.Rect
    # Ersetzen "Zurueck" auf diesem Screen; page_prev/page_next sind je nach
    # aktueller Seite nur einer davon wirklich erreichbar (siehe
    # app_with_hw._map_click_to_event).
    admin_camera_save: pygame.Rect
    admin_camera_cancel: pygame.Rect
    admin_camera_page_prev: pygame.Rect
    admin_camera_page_next: pygame.Rect
    # NEU (Veranstaltungsdaten): je eine Zeile pro Feld auf der Uebersicht
    # (ADMIN_EVENT_SETTINGS) - Tap auf eine Textzeile oeffnet die
    # Bildschirmtastatur fuer genau dieses Feld, Tap auf eine Schalter-Zeile
    # kippt den Wert direkt. "Speichern"/"Abbrechen" nutzen die vorhandenen
    # rects.left/rects.right.
    admin_event_title_row: pygame.Rect
    admin_event_prefix_row: pygame.Rect
    admin_event_wifi_ssid_row: pygame.Rect
    admin_event_wifi_password_row: pygame.Rect
    admin_event_qr_toggle: pygame.Rect
    admin_event_gallery_toggle: pygame.Rect
    admin_event_wallpaper_button: pygame.Rect
    # NEU (Nutzer-Feedback): "Standardwerte"-Taste - teilt sich die vormals
    # volle Zeile mit admin_event_wallpaper_button (siehe unten).
    admin_event_defaults_button: pygame.Rect
    # NEU (Veranstaltungsdaten): Bildschirmtastatur (QWERTZ) fuer
    # ADMIN_EVENT_TEXT_ENTRY - gemeinsam fuer alle vier Textfelder, analog
    # zu pin_keys, aber mit beliebigem Text statt nur Ziffern. Schluessel:
    # jedes Zeichen der vier Buchstaben-/Ziffernreihen sowie "shift",
    # "backspace", "space", "cancel", "submit".
    keyboard_keys: dict[str, pygame.Rect]


# NEU (Nutzer-Feedback): Sonderzeichen-Ebene der Bildschirmtastatur
# (ADMIN_EVENT_TEXT_ENTRY) bei gedruecktem "Umschalt" - deutsche QWERTZ-
# Belegung (Ziffernreihe -> Sonderzeichen) sowie ,.- -> ;:_ (Unterstrich
# wichtig als Trennzeichen im Datei-Praefix). Modul-Ebene statt innerhalb
# von build_layout(), damit sowohl app_with_hw.py (Klick-Routing) als auch
# renderer.py (Label-Anzeige) denselben einen Import nutzen koennen -
# gleiches Prinzip wie admin_menu.ADMIN_MENU_ITEMS, das ebenfalls von
# beiden Dateien importiert wird. ae/oe/ue sind bewusst NICHT enthalten -
# die bleiben bei Umschalt unveraendert klein.
KEYBOARD_SHIFT_MAP: dict[str, str] = {
    "1": "!", "2": "\"", "3": "§", "4": "$", "5": "%",
    "6": "&", "7": "/", "8": "(", "9": ")", "0": "=",
    ",": ";", ".": ":", "-": "_",
}


def build_layout(width: int, height: int) -> LayoutRects:
    # Alle Masse sind Prozentsaetze, damit das Layout bei jeder Aufloesung
    # proportional korrekt bleibt (Pi: 720x1280, PC-Test: 1280x720).
    #
    # Buttons: schmaler in der Breite, hoeher als frueher (Design-Vorgabe),
    # und die Schrift wird im Renderer separat vergroessert.
    def rect(x_pct: float, y_pct: float, w_pct: float, h_pct: float) -> pygame.Rect:
        return pygame.Rect(
            round(x_pct * width),
            round(y_pct * height),
            round(w_pct * width),
            round(h_pct * height),
        )

    # Zwei-Button-Reihe (unten, mittig links/rechts)
    margin_x = 0.10
    button_w = 0.28   # schmaler als vorher (war ~0.296)
    button_h = 0.155  # Etappe 5: einheitlich auf Service-Menue-Hoehe (0.155)
    lower_y = 0.80

    left = rect(margin_x, lower_y, button_w, button_h)
    right = rect(1 - margin_x - button_w, lower_y, button_w, button_h)
    back = rect(margin_x, lower_y, button_w, button_h)

    # Wie "right", aber am unteren Rand verankert - nur fuer
    # INSTRUCTIONS/TERMS. Unterkante bleibt bei 0.975 (ca. 18px Rand
    # bei 720px Hoehe); durch die groessere Buttonhoehe (0.155,
    # Etappe 5) waechst der Button nach OBEN statt tiefer zu rutschen.
    text_view_lower_y = 0.82
    text_view_back = rect(1 - margin_x - button_w, text_view_lower_y, button_w, button_h)

    # Hauptmenue: 4 Buttons diagonal versetzt, in der unteren Bildschirmhaelfte.
    # War frueher 3 Buttons bei diag_w=0.28 (0.06/0.36/0.66) - fuer den 4.
    # Button (Nutzungsbedingungen, rechts unterhalb von "Anleitung") passte
    # kein weiterer 0.28-breiter Button mehr rechts daneben. Statt die
    # Diagonale nur fuer den neuen Button abzubrechen, wurden alle 4 Buttons
    # gleichmaessig schmaler/kompakter gemacht, damit die Diagonale optisch
    # konsistent bleibt.
    #
    # Etappe 5: diag_h von 0.085 auf 0.155 erhoeht (einheitliche
    # Buttonhoehe). Eine Neuberechnung der Diagonale ist NICHT noetig:
    # Zwei Buttons kollidieren nur, wenn sie sich in BEIDEN Achsen
    # ueberlappen. Der horizontale Schritt (diag_x_step=0.22) ist
    # groesser als die Buttonbreite (diag_w=0.20), also bleibt zwischen
    # benachbarten Buttons immer eine Luecke von 0.02*Breite in X -
    # unabhaengig von der Hoehe. Damit ueberlappt garantiert nichts,
    # auch wenn sich die vertikalen Baender jetzt ueberschneiden.
    # diag_y0/diag_y_step bleiben unveraendert, damit die Oberkante
    # (und der Titelbereich darueber) unberuehrt bleibt; die Gruppe
    # waechst nur nach unten (unterster Button endet bei 0.955,
    # ca. 32px Rand bei 720px Hoehe).
    diag_w = 0.20
    diag_h = 0.155
    diag_x_step = 0.22
    diag_y_step = 0.09
    diag_x0 = 0.06
    diag_y0 = 0.53

    main_photo = rect(diag_x0 + 0 * diag_x_step, diag_y0 + 0 * diag_y_step, diag_w, diag_h)
    main_gallery = rect(diag_x0 + 1 * diag_x_step, diag_y0 + 1 * diag_y_step, diag_w, diag_h)
    main_instructions = rect(diag_x0 + 2 * diag_x_step, diag_y0 + 2 * diag_y_step, diag_w, diag_h)
    main_terms = rect(diag_x0 + 3 * diag_x_step, diag_y0 + 3 * diag_y_step, diag_w, diag_h)

    # NEU (3.3): Ziffernfeld fuer PIN_ENTRY.
    # Zentriertes 3x4-Raster: 1-9, dann [backspace] 0 [submit].
    #
    # GEAENDERT (Nutzer-Feedback): "cancel" lag vorher oben links und
    # ueberlappte dort mit der zentrierten Kopfzeile ("Wartungs-PIN
    # eingeben", siehe renderer._draw_pin_entry) - liegt jetzt stattdessen
    # UNTERHALB des Tastenfelds. Die Tasten sind ausserdem quadratisch: die
    # Hoehe (key_h) bleibt unveraendert, die Breite wird daraus in Pixeln
    # abgeleitet (Breite/Hoehe haben bei einem nicht quadratischen
    # Bildschirm wie 1280x720 unterschiedliche Prozent-Basis - eine feste
    # Breite in Prozent ergaebe KEIN Quadrat).
    #
    # GEAENDERT (2. Nutzer-Feedback-Runde): Raster nochmal weiter nach oben
    # gerueckt (grid_y0 kleiner) UND "Abbrechen" auf dieselbe Groesse wie in
    # den uebrigen Menues gebracht (button_h=0.155 statt vorher 0.08). Beides
    # zusammen ist auf 720px Bildschirmhoehe eng - Kopfzeile/PIN-Punkte/
    # Fehlertext (siehe renderer._draw_pin_entry) wurden dafuer ebenfalls
    # kompakter positioniert, sonst haette "Abbrechen" unten aus dem
    # Bildschirm herausgeragt.
    key_h = 0.135
    key_w = (key_h * height) / width
    gap_y = 0.02
    gap_x = (gap_y * height) / width
    grid_w = 3 * key_w + 2 * gap_x
    grid_x0 = (1.0 - grid_w) / 2.0
    grid_y0 = 0.21

    def key_rect(col: int, row: int) -> pygame.Rect:
        return rect(
            grid_x0 + col * (key_w + gap_x),
            grid_y0 + row * (key_h + gap_y),
            key_w,
            key_h,
        )

    pin_keys: dict[str, pygame.Rect] = {}
    for i in range(9):                       # Ziffern 1-9
        pin_keys[str(i + 1)] = key_rect(i % 3, i // 3)
    pin_keys["backspace"] = key_rect(0, 3)   # untere Reihe: <-  0  OK
    pin_keys["0"] = key_rect(1, 3)
    pin_keys["submit"] = key_rect(2, 3)
    # "Abbrechen" unterhalb des Rasters, zentriert, jetzt in derselben
    # Groesse (button_w/button_h von oben) wie die "Zurueck"/"Abbrechen"-
    # Buttons in den uebrigen Menues.
    grid_bottom = grid_y0 + 4 * key_h + 3 * gap_y
    cancel_y = grid_bottom + 0.015
    pin_keys["cancel"] = rect((1.0 - button_w) / 2.0, cancel_y, button_w, button_h)

    # NEU (6c, ueberarbeitet nach Nutzer-Feedback): Kontrollkaesten der
    # "Alle auswaehlen"-Zeile - kompakt statt grosse Buttons, damit sie
    # optisch zu den Kontrollkaesten der einzelnen Dateizeilen passen
    # (siehe renderer._draw_admin_usb_conflicts). Die X-Position (centerx)
    # ist zugleich die EINZIGE Quelle fuer die Spaltenposition ueberhaupt -
    # der Renderer liest sie fuer Spaltenkoepfe UND jede einzelne
    # Dateizeile aus, damit nirgends zwei Stellen synchron gehalten werden
    # muessen.
    usb_conflicts_overwrite_all = rect(0.55, 0.245, 0.10, 0.075)
    usb_conflicts_rename_all = rect(0.77, 0.245, 0.10, 0.075)

    # GEAENDERT (Sprint-11-Nachbesserung): frueher ein kleines eigenes Icon
    # unten rechts - auf Wunsch jetzt exakt so gross wie der "Zurueck"-
    # Button (unten links, rects.back/rects.left), nur horizontal gespiegelt
    # positioniert. Nutzt bewusst dieselben Masse wie "right" statt eines
    # eigenen kleineren Rects.
    gallery_qr_icon = rect(1 - margin_x - button_w, lower_y, button_w, button_h)

    # NEU (Kamera-Menue 2.0): alle Zeilen (ISO/Blende eingeschlossen) nutzen
    # dieselbe quadratische Pixel-Logik, gestaffelt auf der rechten
    # Bildschirmhaelfte, damit links Platz fuer das Live-Vorschau-Panel
    # bleibt (Nutzer-Feedback: Blende ist bei eingebauter Kamera weder zu
    # hoeren noch zu sehen). Seitenlaenge/Position bewusst einmalig in
    # Pixeln aus der Bildschirmhoehe abgeleitet (nicht getrennt in Breiten-/
    # Hoehenprozentsatz, da das bei einem nicht quadratischen Bildschirm -
    # hier 1280x720 - ein verzerrtes Rechteck ergaebe).
    camera_row_btn_side = round(0.085 * height)
    camera_row_x_minus = round(0.50 * width)
    camera_row_x_plus = round(0.955 * width) - camera_row_btn_side
    camera_row_y0 = round(0.14 * height)
    camera_row_step = round(0.105 * height)

    def _camera_row(index: int) -> tuple[pygame.Rect, pygame.Rect]:
        y = camera_row_y0 + index * camera_row_step
        minus = pygame.Rect(camera_row_x_minus, y, camera_row_btn_side, camera_row_btn_side)
        plus = pygame.Rect(camera_row_x_plus, y, camera_row_btn_side, camera_row_btn_side)
        return minus, plus

    # Seite 1 "Belichtung": ISO (0), Blende (1), Verschlusszeit (2, reiner
    # Info-Wert ohne Buttons, siehe renderer._draw_admin_camera_settings),
    # Belichtungskorrektur (3), Messfeld (4).
    admin_camera_iso_minus, admin_camera_iso_plus = _camera_row(0)
    admin_camera_aperture_minus, admin_camera_aperture_plus = _camera_row(1)
    admin_camera_expcomp_minus, admin_camera_expcomp_plus = _camera_row(3)
    admin_camera_metering_minus, admin_camera_metering_plus = _camera_row(4)
    # Seite 2 "Sonstiges" - nutzt dieselben vier Zeilenpositionen wie Seite 1
    # (immer nur eine Seite gleichzeitig sichtbar).
    admin_camera_wb_minus, admin_camera_wb_plus = _camera_row(0)
    admin_camera_quality_minus, admin_camera_quality_plus = _camera_row(1)
    admin_camera_imagesize_minus, admin_camera_imagesize_plus = _camera_row(2)
    admin_camera_drive_minus, admin_camera_drive_plus = _camera_row(3)

    # Live-Vorschau-Panel links - Seitenverhaeltnis grob an die gphoto2-
    # Vorschau (ca. 640x424) angenaehert, exaktes Andocken (Letterboxing)
    # uebernimmt der Renderer.
    admin_camera_preview = pygame.Rect(
        round(0.03 * width), round(0.13 * height), round(0.44 * width), round(0.52 * height),
    )

    # GEAENDERT (Nutzer-Feedback nach Live-Test auf dem Pi): "Seite 2"-Button
    # war zu klein und ausserdem an einer Stelle (0.385/0.530), die keinen
    # Bezug zu Abbrechen/Speichern hatte. Jetzt eine Reihe aus DREI
    # gleichgrossen Buttons (Abbrechen/Speichern/Seiten-Navigation), analog
    # zur sonst ueblichen Zwei-Button-Reihe (margin_x/button_h), nur mit
    # eigener, schmalerer Breite, damit alle drei nebeneinander passen:
    # 3 * admin_camera_row_btn_w + 2 * admin_camera_row_gap ergibt genau die
    # Strecke von margin_x bis (1 - margin_x), wie bei "left"/"right".
    # admin_camera_page_prev/_next teilen sich bewusst dasselbe Rect (immer
    # nur eine Richtung gleichzeitig sichtbar, siehe renderer._draw_buttons).
    admin_camera_row_gap = 0.025
    admin_camera_row_btn_w = (1 - 2 * margin_x - 2 * admin_camera_row_gap) / 3
    admin_camera_cancel = rect(margin_x, lower_y, admin_camera_row_btn_w, button_h)
    admin_camera_save = rect(
        margin_x + admin_camera_row_btn_w + admin_camera_row_gap, lower_y, admin_camera_row_btn_w, button_h,
    )
    admin_camera_page_prev = rect(
        margin_x + 2 * (admin_camera_row_btn_w + admin_camera_row_gap), lower_y, admin_camera_row_btn_w, button_h,
    )
    admin_camera_page_next = admin_camera_page_prev

    # NEU (Veranstaltungsdaten): Uebersichts-Zeilen. Sieben Zeilen (vier
    # Textfelder, zwei Schalter, ein Wallpaper-Button) uebereinander -
    # eigene, schmalere Randbreite als margin_x (0.10), damit auf einer
    # Zeile spuerbar mehr Platz fuer Label+Wert bleibt (Sichtpruefung auf
    # echter Hardware empfohlen, wie beim Kamera-Menue).
    #
    # GEAENDERT (Nutzer-Feedback): event_row_y0 von 0.10 auf 0.21 angehoben -
    # der grosse Titel "Veranstaltungsdaten" wird generisch fest bei y=60px
    # gezeichnet (siehe renderer._title_font_for/render()) und reichte bei
    # 720px Bildschirmhoehe bis ca. 137px runter, ueberlappte also die erste
    # Zeile. Titel bleibt (bewusst NICHT entfernt, Nutzerwunsch), stattdessen
    # ruecken die Zeilen darunter. event_row_h/_step dafuer etwas verkleinert
    # (0.075/0.088 -> 0.068/0.078), sonst waere kein Platz mehr fuer den
    # Hinweistext ("Titel/Praefix/WLAN/..." siehe renderer.
    # _draw_admin_event_settings) zwischen letzter Zeile und der Speichern/
    # Abbrechen-Reihe geblieben. Letzte Zeile (Wallpaper-Button, Index 6)
    # endet damit bei 0.21+6*0.078+0.068=0.746 - der Hinweistext sitzt in der
    # verbleibenden Luecke bis lower_y=0.80 (Sichtpruefung auf echter
    # Hardware empfohlen, wie beim Kamera-Menue).
    event_margin_x = 0.06
    event_row_w = 1 - 2 * event_margin_x
    event_row_h = 0.068
    event_row_y0 = 0.21
    event_row_step = 0.078

    def _event_row(index: int) -> pygame.Rect:
        return rect(event_margin_x, event_row_y0 + index * event_row_step, event_row_w, event_row_h)

    admin_event_title_row = _event_row(0)
    admin_event_prefix_row = _event_row(1)
    admin_event_wifi_ssid_row = _event_row(2)
    admin_event_wifi_password_row = _event_row(3)
    admin_event_qr_toggle = _event_row(4)
    admin_event_gallery_toggle = _event_row(5)
    # GEAENDERT (Nutzer-Feedback): Zeile 6 war bisher ein einzelner voller
    # Button ("Wallpaper von USB laden"). Der jetzt entfallene Hinweistext
    # darunter (siehe renderer._draw_admin_event_settings, ENTFERNT) hat
    # keinen Platz fuer eine ganz neue Zeile hinterlassen (nur ~0.054 bis
    # lower_y=0.80) - die neue "Standardwerte"-Taste teilt sich stattdessen
    # dieselbe Zeile mit "Wallpaper von USB laden" (je Haelfte, gleiches
    # Zwei-Button-Prinzip wie admin_camera_cancel/admin_camera_save).
    _admin_event_row6 = _event_row(6)
    _admin_event_row6_gap = round(0.015 * width)
    _admin_event_row6_half_w = (_admin_event_row6.width - _admin_event_row6_gap) // 2
    admin_event_wallpaper_button = pygame.Rect(
        _admin_event_row6.x, _admin_event_row6.y, _admin_event_row6_half_w, _admin_event_row6.height,
    )
    admin_event_defaults_button = pygame.Rect(
        _admin_event_row6.x + _admin_event_row6_half_w + _admin_event_row6_gap,
        _admin_event_row6.y, _admin_event_row6_half_w, _admin_event_row6.height,
    )
    # ENTFERNT (Nutzer-Feedback): "Anzeigen"/"Verbergen"-Umschalter fuer die
    # WLAN-Passwort-Zeile - das Passwort steht jetzt immer als Klartext da,
    # ein Sichtbarkeits-Umschalter ist nicht mehr noetig (siehe renderer.py).

    # NEU (Veranstaltungsdaten): QWERTZ-Bildschirmtastatur (deutsche
    # Tastenanordnung inkl. ae/oe/ue) fuer ADMIN_EVENT_TEXT_ENTRY.
    #
    # GEAENDERT (Nutzer-Feedback): "Umschalt" wirkt jetzt nicht mehr nur auf
    # Buchstaben a-z, sondern zusaetzlich ueber KEYBOARD_SHIFT_MAP auf die
    # Ziffernreihe (-> Sonderzeichen, deutsche QWERTZ-Belegung) sowie ,.-
    # (-> ;:_ - der Unterstrich wird als Trennzeichen im Datei-Praefix
    # gebraucht). ae/oe/ue bleiben bewusst unveraendert (siehe
    # app_with_hw._map_admin_event_text_entry_click/renderer.py).
    # GEAENDERT (Nutzer-Feedback, Bugfix): kb_y0 stand bisher bei 0.12 -
    # direkt im Bereich der Bildschirm-Ueberschrift (status_text, z.B.
    # "WLAN-Passwort", gezeichnet bei y=60..135px, siehe renderer.render())
    # und ueberlappte dadurch sichtbar die erste Tastaturreihe. Jetzt auf
    # 0.33 verschoben (unterhalb von Titel + Eingabefeld, siehe renderer.
    # _draw_admin_event_text_entry, text_cy=0.26, jetzt mit der hoeheren
    # Monospace-Schrift font_body_admin_mono), kb_key_h dafuer leicht
    # verkleinert (0.10 -> 0.088), damit alle sechs Reihen (vier Buchstaben-/
    # Ziffernreihen + Umschalt/Leertaste/DEL + Abbrechen/Speichern) trotzdem
    # mit Rand unterhalb 1.0 bleiben (Rechnung: 0.33 + 6*0.088 + 5*0.012 =
    # 0.33 + 0.528 + 0.06 = 0.918 - laesst knapp 8% des Bildschirms als
    # Rand am unteren Rand frei).
    kb_key_h = 0.088
    kb_key_w = (kb_key_h * height) / width
    kb_gap_y = 0.012
    kb_gap_x = (kb_gap_y * height) / width
    kb_y0 = 0.33
    _KEYBOARD_ROWS: tuple[tuple[str, ...], ...] = (
        ("1", "2", "3", "4", "5", "6", "7", "8", "9", "0"),
        ("q", "w", "e", "r", "t", "z", "u", "i", "o", "p", "ü"),
        ("a", "s", "d", "f", "g", "h", "j", "k", "l", "ö", "ä"),
        ("y", "x", "c", "v", "b", "n", "m", ",", ".", "-"),
    )

    def _kb_key_rect(row_index: int, col_index: int, row_len: int) -> pygame.Rect:
        row_w = row_len * kb_key_w + (row_len - 1) * kb_gap_x
        x0 = (1.0 - row_w) / 2.0
        y = kb_y0 + row_index * (kb_key_h + kb_gap_y)
        return rect(x0 + col_index * (kb_key_w + kb_gap_x), y, kb_key_w, kb_key_h)

    keyboard_keys: dict[str, pygame.Rect] = {}
    for row_index, row_chars in enumerate(_KEYBOARD_ROWS):
        for col_index, char in enumerate(row_chars):
            keyboard_keys[char] = _kb_key_rect(row_index, col_index, len(row_chars))

    # GEAENDERT (Nutzer-Feedback): statt einer Reihe mit fuenf gleich breiten
    # Tasten jetzt ZWEI Reihen:
    #   Reihe A (direkt unter "yxcvb..."): Umschalt (schmaler) | Leertaste
    #     (dominant, 50% breiter) | DEL - Verhaeltnis 0.8 : 1.5 : 1.0.
    #   Reihe B (darunter): Abbrechen | Speichern - je zur Haelfte, gleiche
    #     Zwei-Button-Aufteilung wie admin_camera_cancel/admin_camera_save,
    #     nur auf event_margin_x statt margin_x bezogen.
    kb_row_a_y = kb_y0 + len(_KEYBOARD_ROWS) * (kb_key_h + kb_gap_y)
    kb_row_b_y = kb_row_a_y + kb_key_h + kb_gap_y
    kb_bottom_gap = 0.015

    kb_row_a_unit = (event_row_w - 2 * kb_bottom_gap) / (0.8 + 1.5 + 1.0)
    kb_shift_w = 0.8 * kb_row_a_unit
    kb_space_w = 1.5 * kb_row_a_unit
    kb_del_w = 1.0 * kb_row_a_unit

    keyboard_keys["shift"] = rect(event_margin_x, kb_row_a_y, kb_shift_w, kb_key_h)
    keyboard_keys["space"] = rect(
        event_margin_x + kb_shift_w + kb_bottom_gap, kb_row_a_y, kb_space_w, kb_key_h,
    )
    keyboard_keys["backspace"] = rect(
        event_margin_x + kb_shift_w + kb_space_w + 2 * kb_bottom_gap, kb_row_a_y, kb_del_w, kb_key_h,
    )

    kb_row_b_w = (event_row_w - kb_bottom_gap) / 2
    keyboard_keys["cancel"] = rect(event_margin_x, kb_row_b_y, kb_row_b_w, kb_key_h)
    keyboard_keys["submit"] = rect(
        event_margin_x + kb_row_b_w + kb_bottom_gap, kb_row_b_y, kb_row_b_w, kb_key_h,
    )

    return LayoutRects(
        main_photo=main_photo,
        main_gallery=main_gallery,
        main_instructions=main_instructions,
        main_terms=main_terms,
        left=left,
        right=right,
        back=back,
        text_view_back=text_view_back,
        pin_keys=pin_keys,
        usb_conflicts_overwrite_all=usb_conflicts_overwrite_all,
        usb_conflicts_rename_all=usb_conflicts_rename_all,
        gallery_qr_icon=gallery_qr_icon,
        admin_camera_iso_minus=admin_camera_iso_minus,
        admin_camera_iso_plus=admin_camera_iso_plus,
        admin_camera_aperture_minus=admin_camera_aperture_minus,
        admin_camera_aperture_plus=admin_camera_aperture_plus,
        admin_camera_preview=admin_camera_preview,
        admin_camera_expcomp_minus=admin_camera_expcomp_minus,
        admin_camera_expcomp_plus=admin_camera_expcomp_plus,
        admin_camera_metering_minus=admin_camera_metering_minus,
        admin_camera_metering_plus=admin_camera_metering_plus,
        admin_camera_wb_minus=admin_camera_wb_minus,
        admin_camera_wb_plus=admin_camera_wb_plus,
        admin_camera_quality_minus=admin_camera_quality_minus,
        admin_camera_quality_plus=admin_camera_quality_plus,
        admin_camera_imagesize_minus=admin_camera_imagesize_minus,
        admin_camera_imagesize_plus=admin_camera_imagesize_plus,
        admin_camera_drive_minus=admin_camera_drive_minus,
        admin_camera_drive_plus=admin_camera_drive_plus,
        admin_camera_save=admin_camera_save,
        admin_camera_cancel=admin_camera_cancel,
        admin_camera_page_prev=admin_camera_page_prev,
        admin_camera_page_next=admin_camera_page_next,
        admin_event_title_row=admin_event_title_row,
        admin_event_prefix_row=admin_event_prefix_row,
        admin_event_wifi_ssid_row=admin_event_wifi_ssid_row,
        admin_event_wifi_password_row=admin_event_wifi_password_row,
        admin_event_qr_toggle=admin_event_qr_toggle,
        admin_event_gallery_toggle=admin_event_gallery_toggle,
        admin_event_wallpaper_button=admin_event_wallpaper_button,
        admin_event_defaults_button=admin_event_defaults_button,
        keyboard_keys=keyboard_keys,
    )


def button_rects_for_state(state: AppState, rects: LayoutRects) -> dict[str, pygame.Rect]:
    if state == AppState.MAIN_MENU:
        return {
            "photo": rects.main_photo,
            "gallery": rects.main_gallery,
            "instructions": rects.main_instructions,
            "terms": rects.main_terms,
        }
    if state == AppState.INSTRUCTIONS:
        # "Zurueck" unten rechts, tiefer als sonst uebliche Einzel-Button-
        # Screens - nutzt den sonst ungenutzten unteren Bildschirmrand fuer
        # mehr Textzeilen (siehe LayoutRects.text_view_back).
        return {"back": rects.text_view_back}
    if state == AppState.TERMS:
        # "Verstanden" an derselben Position wie das "Zurueck" bei
        # INSTRUCTIONS - gleiche Einzel-Button-Konvention.
        return {"back": rects.text_view_back}
    if state == AppState.PHOTO_INTRO:
        return {"photo": rects.left, "cancel": rects.right}
    if state == AppState.PHOTO_PREVIEW:
        # Nur noch "Abbrechen" - der Countdown startet automatisch, es gibt
        # keinen "Countdown starten"-Button mehr an dieser Stelle.
        return {"cancel": rects.right}
    if state == AppState.COUNTDOWN:
        return {"cancel": rects.right}
    if state == AppState.GALLERY_GRID:
        return {"back": rects.back}
    # NEU (Etappe 7): gleiche links/rechts-Zuordnung wie PHOTO_INTRO (auf
    # das der "photo"-Button hier direkt fuehrt) - vertrautes Layout beim
    # Uebergang zwischen den beiden Screens.
    if state == AppState.GALLERY_EMPTY:
        return {"photo": rects.left, "back": rects.right}
    if state == AppState.ATTRACT_GALLERY:
        # Bewusst leer: kein sichtbarer Button, nur Tippen irgendwo/Taster
        # fuehrt zurueck ins Hauptmenue (siehe app_with_hw.py).
        return {}
    if state == AppState.GALLERY_FULLSCREEN:
        # NEU (Sprint 11, Feature 4): "gallery_qr" - Icon "QR-Code
        # anfordern" unten rechts, gleichwertige Alternative zum Doppeltap
        # (siehe app_with_hw._handle_pygame_event).
        return {"back": rects.back, "gallery_qr": rects.gallery_qr_icon}
    # NEU (Sprint 11, Feature 4): eigener Zustand fuer den Foto-QR-Code -
    # nur "Zurueck" (gleiche Position wie bei GALLERY_FULLSCREEN), kein
    # Doppeltap/Icon hier noetig (Anzeige schliesst sich sonst automatisch
    # nach config.timeouts.gallery_qr_seconds).
    if state == AppState.GALLERY_PHOTO_QR:
        return {"back": rects.back}
    if state == AppState.REVIEW:
        return {"save": rects.left, "delete": rects.right}
    if state == AppState.DELETE_CONFIRM:
        return {"confirm_delete": rects.left, "abort_delete": rects.right}
    if state == AppState.QR_DISPLAY:
        return {"cancel": rects.right}
    if state == AppState.ERROR_SCREEN:
        return {"back": rects.back}
    if state == AppState.PIN_ENTRY:          # NEU (3.3)
        return rects.pin_keys
    if state == AppState.ADMIN_STATUS:       # NEU (4.3)
        return {"back": rects.back}
    if state == AppState.ADMIN_CAMERA_SETTINGS:
        # GEAENDERT (Kamera-Menue 2.0): "back" entfaellt (Speichern/Abbrechen
        # ersetzen es). Enthaelt bewusst die Buttons BEIDER Seiten - welche
        # davon auf der aktuell sichtbaren Seite tatsaechlich Sinn ergeben
        # (model.ui.admin_camera_page), filtert app_with_hw._map_click_to_event
        # heraus (gleiches Prinzip wie beim gallery_enabled-Filter).
        return {
            "admin_camera_iso_minus": rects.admin_camera_iso_minus,
            "admin_camera_iso_plus": rects.admin_camera_iso_plus,
            "admin_camera_aperture_minus": rects.admin_camera_aperture_minus,
            "admin_camera_aperture_plus": rects.admin_camera_aperture_plus,
            "admin_camera_expcomp_minus": rects.admin_camera_expcomp_minus,
            "admin_camera_expcomp_plus": rects.admin_camera_expcomp_plus,
            "admin_camera_metering_minus": rects.admin_camera_metering_minus,
            "admin_camera_metering_plus": rects.admin_camera_metering_plus,
            "admin_camera_wb_minus": rects.admin_camera_wb_minus,
            "admin_camera_wb_plus": rects.admin_camera_wb_plus,
            "admin_camera_quality_minus": rects.admin_camera_quality_minus,
            "admin_camera_quality_plus": rects.admin_camera_quality_plus,
            "admin_camera_imagesize_minus": rects.admin_camera_imagesize_minus,
            "admin_camera_imagesize_plus": rects.admin_camera_imagesize_plus,
            "admin_camera_drive_minus": rects.admin_camera_drive_minus,
            "admin_camera_drive_plus": rects.admin_camera_drive_plus,
            "admin_camera_save": rects.admin_camera_save,
            "admin_camera_cancel": rects.admin_camera_cancel,
            "admin_camera_page_prev": rects.admin_camera_page_prev,
            "admin_camera_page_next": rects.admin_camera_page_next,
        }
    if state == AppState.ADMIN_EVENT_SETTINGS:
        return {
            "admin_event_edit_title": rects.admin_event_title_row,
            "admin_event_edit_prefix": rects.admin_event_prefix_row,
            "admin_event_edit_wifi_ssid": rects.admin_event_wifi_ssid_row,
            "admin_event_edit_wifi_password": rects.admin_event_wifi_password_row,
            "admin_event_toggle_qr": rects.admin_event_qr_toggle,
            "admin_event_toggle_gallery": rects.admin_event_gallery_toggle,
            "admin_event_wallpaper": rects.admin_event_wallpaper_button,
            # NEU (Nutzer-Feedback): "Standardwerte"-Taste.
            "admin_event_defaults": rects.admin_event_defaults_button,
            "admin_event_save": rects.left,
            "back": rects.right,
        }
    if state == AppState.ADMIN_EVENT_TEXT_ENTRY:
        # Wird technisch nicht ueber diese generische Tabelle abgefragt
        # (app_with_hw._map_click_to_event special-cased diesen State,
        # analog zu PIN_ENTRY) - nur der Vollstaendigkeit halber mitgefuehrt.
        return rects.keyboard_keys
    if state == AppState.ADMIN_EVENT_WALLPAPER_PICK_LOADING:
        # Laeuft, nicht abbrechbar - bewusst kein Button (analog
        # ADMIN_USB_CHECK). Umbenannt von ADMIN_EVENT_WALLPAPER_IMPORT
        # (Nutzer-Feedback: Stick wird jetzt nur noch nach Bildern
        # DURCHSUCHT, nicht mehr automatisch das erste gefundene Bild
        # kopiert - siehe ADMIN_EVENT_WALLPAPER_PICK).
        return {}
    if state == AppState.ADMIN_EVENT_WALLPAPER_PICK:
        # NEU (Nutzer-Feedback): scrollbare Bilderliste vom Stick - die
        # Zeilen selbst sind dynamisch (siehe renderer._draw_admin_event_
        # wallpaper_pick/app_with_hw._map_click_to_event, gleiches Prinzip
        # wie ADMIN_USB_CONFLICTS), nur "Speichern"/"Abbrechen" sind
        # statisch und nutzen die vorhandenen rects.left/rects.right.
        return {
            "admin_event_wallpaper_pick_save": rects.left,
            "admin_event_wallpaper_pick_cancel": rects.right,
        }
    if state == AppState.ADMIN_EVENT_WALLPAPER_RESULT:
        return {"back": rects.back}
    if state == AppState.ADMIN_EVENT_SAVED:
        return {"admin_event_restart_now": rects.left, "back": rects.right}
    if state == AppState.ADMIN_SHUTDOWN_CONFIRM:  # NEU (Sprint-11-Nachbesserung)
        # Gleiches Prinzip wie ADMIN_DELETE_CONFIRM: "Nein" bewusst LINKS.
        return {"admin_shutdown_abort": rects.left, "admin_shutdown_confirm": rects.right}
    if state == AppState.ADMIN_RESTART_CONFIRM:  # NEU (Nutzer-Feedback)
        # Gleiches Prinzip wie ADMIN_SHUTDOWN_CONFIRM.
        return {"admin_restart_abort": rects.left, "admin_restart_confirm": rects.right}
    if state == AppState.ADMIN_DELETE_CONFIRM:   # NEU (4.4)
        # "Nein" bewusst LINKS (die harmlose Wahl an der Stelle, an der
        # sonst die Standardaktion liegt), "Ja, loeschen" rechts.
        return {"admin_delete_abort": rects.left, "admin_delete_confirm": rects.right}
    if state == AppState.ADMIN_DELETE_DONE:      # NEU (4.4)
        return {"back": rects.back}
    # --- USB-Export (NEU 4.6) ---
    if state == AppState.ADMIN_USB_WAIT:
        # "Weiter" wird immer gezeichnet, ist aber erst wirksam, sobald ein
        # Stick erkannt wurde (Entscheidung faellt in der State Machine).
        return {"cancel": rects.left, "usb_continue": rects.right}
    if state == AppState.ADMIN_USB_READY:
        return {"cancel": rects.left, "usb_continue": rects.right}
    if state == AppState.ADMIN_USB_PROBLEM:
        # NEU (4.7): "usb_clear" steht immer auf dem rechten Button; der
        # Renderer aendert die Beschriftung je nach Problemtyp, die State
        # Machine entscheidet ueber die Wirkung.
        return {"cancel": rects.left, "usb_clear": rects.right}
    if state == AppState.ADMIN_USB_EXPORT_DONE:   # NEU (4.7)
        return {"usb_continue": rects.right}
    if state == AppState.ADMIN_USB_REMOVE:
        return {"back": rects.back}
    # NEU (6c): Sammelaktionen + "Ausfuehren" (auf "right", wie bei den
    # uebrigen USB-Screens). Die Einzelentscheidung je Datei laeuft NICHT
    # ueber diese Rects, sondern ueber renderer.usb_conflict_row_hitboxes
    # (siehe app_with_hw._map_click_to_event) - die Zeilenposition ist erst
    # nach dem Zeichnen (Scroll-Offset!) bekannt.
    if state == AppState.ADMIN_USB_CONFLICTS:
        return {
            "usb_conflicts_overwrite_all": rects.usb_conflicts_overwrite_all,
            "usb_conflicts_rename_all": rects.usb_conflicts_rename_all,
            "usb_conflicts_apply": rects.right,
        }
    # ADMIN_DELETE_RUNNING, ADMIN_USB_CHECK, ADMIN_USB_EJECT, ADMIN_USB_RESOLVE:
    # bewusst leer - laufende Vorgaenge sind nicht abbrechbar.
    return {}