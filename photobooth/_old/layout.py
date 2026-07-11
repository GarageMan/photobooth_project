from __future__ import annotations

from dataclasses import dataclass

import pygame

from states import AppState


@dataclass(frozen=True)
class LayoutRects:
    # Hauptmenue: 3 diagonal versetzte Buttons
    main_photo: pygame.Rect
    main_gallery: pygame.Rect
    main_instructions: pygame.Rect
    # Generische Zwei-Button-Reihe (Fotografieren-Menue, Countdown-Menue,
    # Review, Loesch-Bestaetigung - alle nutzen dieselbe Position)
    left: pygame.Rect
    right: pygame.Rect
    # Einzelner Button (Galerie/Attract/QR/Fehler "Zurueck")
    back: pygame.Rect


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

    # Hauptmenue: 3 Buttons diagonal versetzt, in der unteren Bildschirmhaelfte
    diag_w = 0.28
    diag_h = 0.095
    main_photo = rect(0.06, 0.53, diag_w, diag_h)
    main_gallery = rect(0.36, 0.63, diag_w, diag_h)
    main_instructions = rect(0.66, 0.73, diag_w, diag_h)

    return LayoutRects(
        main_photo=main_photo,
        main_gallery=main_gallery,
        main_instructions=main_instructions,
        left=left,
        right=right,
        back=back,
    )


def button_rects_for_state(state: AppState, rects: LayoutRects) -> dict[str, pygame.Rect]:
    if state == AppState.MAIN_MENU:
        return {"photo": rects.main_photo, "gallery": rects.main_gallery, "instructions": rects.main_instructions}
    if state == AppState.INSTRUCTIONS:
        # "Zurueck" unten rechts (statt unten links wie die anderen
        # Einzel-Button-Screens) - Design-Vorgabe fuer diese Ansicht.
        return {"back": rects.right}
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
    return {}