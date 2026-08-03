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
    # "cancel" bewusst abseits oben links, damit es beim Tippen der Ziffern
    # nicht versehentlich getroffen wird.
    key_w = 0.14
    key_h = 0.135
    gap_x = 0.035
    gap_y = 0.03
    grid_w = 3 * key_w + 2 * gap_x
    grid_x0 = (1.0 - grid_w) / 2.0
    grid_y0 = 0.30

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
    pin_keys["cancel"] = rect(0.03, 0.03, 0.18, 0.09)

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

    # NEU (Sprint 11, Feature 2): ISO-Zeile oben, Blenden-Zeile darunter -
    # "-" aussen links, "+" aussen rechts, der Wert dazwischen (Renderer
    # zeichnet ihn zentriert in die Luecke, siehe
    # renderer._draw_admin_camera_settings).
    #
    # GEAENDERT (Sprint-11-Nachbesserung): Buttons sollen (a) quadratisch
    # sein und (b) weiter vom Bildschirmrand abgerueckt werden. Die
    # Seitenlaenge wird deshalb NICHT wie sonst ueblich getrennt in Breiten-/
    # Hoehenprozentsatz ausgedrueckt (die haben bei einem nicht quadratischen
    # Bildschirm - hier 1280x720 - unterschiedliche Basis und ergaeben ein
    # verzerrtes Rechteck), sondern einmalig in Pixeln aus der Bildschirm-
    # hoehe abgeleitet und fuer x/y gleichermassen verwendet.
    camera_btn_side = round(0.12 * height)
    camera_margin_x = round(0.15 * width)
    camera_iso_y = round(0.40 * height)
    camera_aperture_y = round(0.60 * height)
    admin_camera_iso_minus = pygame.Rect(camera_margin_x, camera_iso_y, camera_btn_side, camera_btn_side)
    admin_camera_iso_plus = pygame.Rect(
        width - camera_margin_x - camera_btn_side, camera_iso_y, camera_btn_side, camera_btn_side,
    )
    admin_camera_aperture_minus = pygame.Rect(camera_margin_x, camera_aperture_y, camera_btn_side, camera_btn_side)
    admin_camera_aperture_plus = pygame.Rect(
        width - camera_margin_x - camera_btn_side, camera_aperture_y, camera_btn_side, camera_btn_side,
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
    if state == AppState.ADMIN_CAMERA_SETTINGS:   # NEU (Sprint 11, Feature 2)
        return {
            "back": rects.back,
            "admin_camera_iso_minus": rects.admin_camera_iso_minus,
            "admin_camera_iso_plus": rects.admin_camera_iso_plus,
            "admin_camera_aperture_minus": rects.admin_camera_aperture_minus,
            "admin_camera_aperture_plus": rects.admin_camera_aperture_plus,
        }
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
