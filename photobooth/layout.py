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
    button_h = 0.09   # deutlich hoeher als vorher (war ~0.047)
    lower_y = 0.80

    left = rect(margin_x, lower_y, button_w, button_h)
    right = rect(1 - margin_x - button_w, lower_y, button_w, button_h)
    back = rect(margin_x, lower_y, button_w, button_h)

    # Wie "right", aber tiefer (0.885 statt 0.80 lower_y) - nur fuer
    # INSTRUCTIONS/TERMS. Bildschirmunterkante bleibt bei button_h=0.09
    # noch mit ca. 1.6% (≈11px bei 720px Hoehe) Rand erhalten, also nicht
    # bis an den allerletzten Pixel.
    text_view_lower_y = 0.885
    text_view_back = rect(1 - margin_x - button_w, text_view_lower_y, button_w, button_h)

    # Hauptmenue: 4 Buttons diagonal versetzt, in der unteren Bildschirmhaelfte.
    # War frueher 3 Buttons bei diag_w=0.28 (0.06/0.36/0.66) - fuer den 4.
    # Button (Nutzungsbedingungen, rechts unterhalb von "Anleitung") passte
    # kein weiterer 0.28-breiter Button mehr rechts daneben. Statt die
    # Diagonale nur fuer den neuen Button abzubrechen, wurden alle 4 Buttons
    # gleichmaessig schmaler/kompakter gemacht, damit die Diagonale optisch
    # konsistent bleibt.
    diag_w = 0.20
    diag_h = 0.085
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
    if state == AppState.ATTRACT_GALLERY:
        # Bewusst leer: kein sichtbarer Button, nur Tippen irgendwo/Taster
        # fuehrt zurueck ins Hauptmenue (siehe app_with_hw.py).
        return {}
    if state == AppState.GALLERY_FULLSCREEN:
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
    return {}