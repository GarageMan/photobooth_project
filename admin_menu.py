"""
admin_menu.py
=============
Zentrale Definition des Service-/Admin-Menues (AppState.ADMIN_MENU).

Warum ein eigenes Modul und kein Eintrag in layout.py:
Beschriftung, Rasterposition, Farbe und ausgeloestes Event eines
Menuepunkts stehen hier an EINER Stelle. renderer.py (Zeichnen) und
app_with_hw.py (Treffererkennung) leiten beide aus derselben Liste ab
und koennen dadurch nicht auseinanderlaufen - ein neuer Menuepunkt
erfordert nur eine Aenderung in dieser Datei. Bei der sonst ueblichen
Aufteilung (Rechtecke in layout.py, Beschriftungen im Renderer,
Event-Zuordnung in app_with_hw.py) muessten drei Dateien synchron
gehalten werden.

Raster: 2 Spalten x 3 Zeilen. Der destruktivste Punkt ("Alle Bilder
loeschen") liegt bewusst unten rechts, in der Ecke und rot eingefaerbt,
raeumlich weit weg von "Zurueck" unten links.

Etappen: Punkte, deren Funktion noch nicht implementiert ist, stehen auf
enabled=False - sie werden ausgegraut gezeichnet und reagieren nicht auf
Beruehrung. Beim Nachliefern der jeweiligen Etappe wird hier lediglich
enabled auf True gesetzt.
"""

from __future__ import annotations

from dataclasses import dataclass

import pygame

from events import EventType


@dataclass(frozen=True)
class AdminMenuItem:
    key: str                       # interner Schluessel (Rechteck-Zuordnung)
    label: str                     # Beschriftung auf dem Button
    event_type: EventType          # Event, das bei Beruehrung ausgeloest wird
    color: tuple[int, int, int]    # Grundfarbe des Buttons
    column: int                    # 0 = links, 1 = rechts
    row: int                       # 0 = oben, 1 = mitte, 2 = unten
    enabled: bool = True           # False = ausgegraut, nicht antippbar


ADMIN_MENU_ITEMS: tuple[AdminMenuItem, ...] = (
    AdminMenuItem(
        key="status",
        label="Status / Diagnose",
        event_type=EventType.TAP_ADMIN_STATUS,
        color=(0, 100, 150),
        column=0,
        row=0,
        enabled=False,          # Etappe 2
    ),
    AdminMenuItem(
        key="usb_export",
        label="Bilder auf USB-Stick",
        event_type=EventType.TAP_ADMIN_USB_EXPORT,
        color=(0, 130, 110),
        column=1,
        row=0,
        enabled=False,          # Etappe 4
    ),
    AdminMenuItem(
        key="restart_app",
        label="App neu starten",
        event_type=EventType.TAP_ADMIN_RESTART_APP,
        color=(120, 90, 0),
        column=0,
        row=1,
        enabled=False,          # Etappe 2
    ),
    AdminMenuItem(
        key="shutdown",
        label="Herunterfahren",
        event_type=EventType.TAP_ADMIN_SHUTDOWN,
        color=(120, 30, 90),
        column=1,
        row=1,
        enabled=True,           # Etappe 1 - bereits funktionsfaehig
    ),
    AdminMenuItem(
        key="back",
        label="Zurück",
        event_type=EventType.TAP_BACK,
        color=(100, 100, 100),
        column=0,
        row=2,
        enabled=True,           # Etappe 1
    ),
    AdminMenuItem(
        key="delete_all",
        label="Alle Bilder löschen",
        event_type=EventType.TAP_ADMIN_DELETE_ALL,
        color=(150, 0, 0),
        column=1,
        row=2,
        enabled=False,          # Etappe 3
    ),
)

# Rasterkennwerte als Bruchteile der Bildschirmgroesse (wie in layout.py),
# damit das Menue auf dem Pi (1280x720 nach OS-Rotation) und auf dem
# PC-Testfenster gleichermassen proportional sitzt.
#
# Oberkante 0.40: darueber zeichnet render() bereits den Fenstertitel
# (y=60) und den status_text (y=240, Unterkante ca. 272px bei 720px
# Hoehe) - 0.40*720 = 288px laesst dazu etwas Luft.
_MARGIN_X = 0.06
_COLUMN_W = 0.42
_TOP = 0.40
_ROW_H = 0.155
_GAP_Y = 0.025


def build_admin_rects(width: int, height: int) -> dict[str, pygame.Rect]:
    """Berechnet die Button-Rechtecke aller Menuepunkte.

    Wird sowohl vom Renderer (Zeichnen) als auch von app_with_hw
    (Treffererkennung) aufgerufen - beide bekommen damit garantiert
    dieselbe Geometrie. Bewusst bei jedem Aufruf neu berechnet statt
    zwischengespeichert: die Rechnung ist trivial, und ein Cache waere
    eine weitere Stelle, die bei einer Aufloesungsaenderung veralten
    koennte.
    """
    rects: dict[str, pygame.Rect] = {}
    for item in ADMIN_MENU_ITEMS:
        x = _MARGIN_X + item.column * (_COLUMN_W + (1.0 - 2 * _MARGIN_X - 2 * _COLUMN_W))
        y = _TOP + item.row * (_ROW_H + _GAP_Y)
        rects[item.key] = pygame.Rect(
            round(x * width),
            round(y * height),
            round(_COLUMN_W * width),
            round(_ROW_H * height),
        )
    return rects
