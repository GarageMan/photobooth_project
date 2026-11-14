"""
attract_slideshow.py
=====================
Reine Auswahl-/Timing-Logik der "fliegenden Galerie" (Attract-Modus), aus
renderer._draw_attract_gallery() herausgezogen (Sprint 14).

Warum ein eigenes Modul statt der Logik direkt in renderer.py zu belassen:
Sprint 14 fuehrt eine ZWEITE Wiedergabe derselben Fotos ein - auf einem
per HDMI angeschlossenen Beamer, unabhaengig vom Touch-Display (siehe
hdmi_slideshow.py). Der Nutzer hat ausdruecklich verlangt, dass diese
zweite Wiedergabe "genau so zufaellig verlaeuft wie die aktuell
existierende Galerie-Wiedergabe im Standby der Fotobox" - das ist nur
garantiert, wenn BEIDE Stellen exakt denselben Code aufrufen, statt die
Zufallslogik ein zweites Mal (womoeglich leicht abweichend) nachzubauen.

Reine Berechnung, kein pygame/PIL noetig - sowohl renderer.py (zeichnet auf
das Touch-Display) als auch hdmi_slideshow.py (eigener Prozess, zeichnet auf
den HDMI-Ausgang) importieren nur dieses eine Modul fuer die Auswahl, jede
Seite kuemmert sich selbst um das eigentliche Laden/Skalieren/Blitten der
Bilder.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# Gleiche Werte wie bisher in renderer._draw_attract_gallery() - siehe
# dortige Erklaerung (Feedback: gelegentliches, aber nicht zu haeufiges
# Einstreuen der Werbung).
DEFAULT_SLOT_SECONDS = 5.0
DEFAULT_FLY_SECONDS = 0.6
DEFAULT_AD_PROBABILITY = 0.2

_DIRECTIONS: tuple[str, ...] = ("left", "right", "top", "bottom")


@dataclass(frozen=True)
class AttractSlot:
    """Ergebnis von compute_attract_slot() fuer einen Zeitpunkt `now`."""
    slot: int
    # Wieviele Sekunden sind wir bereits in diesem Slot (0.0 .. slot_seconds).
    t: float
    # True: statt eines Kundenfotos soll das Systemstart-Wallpaper als
    # "Werbung" gezeigt werden.
    show_ad: bool
    # Einflugrichtung, gueltig fuer den GESAMTEN Slot (kein Flackern).
    direction: str
    # Index in die photos-Liste des Aufrufers - nur gueltig/aussagekraeftig,
    # wenn photo_count > 0 UND show_ad False ist.
    photo_index: int
    # Fortschritt der Einflug-Animation: 0.0 (gerade erst reingeflogen) bis
    # 1.0 (eingerastet, steht still).
    eased: float


def compute_attract_slot(
    now: float,
    photo_count: int,
    slot_seconds: float = DEFAULT_SLOT_SECONDS,
    fly_seconds: float = DEFAULT_FLY_SECONDS,
    ad_probability: float = DEFAULT_AD_PROBABILITY,
) -> AttractSlot:
    """Berechnet, was im Attract-Modus zum Zeitpunkt `now` zu sehen sein
    soll - deterministisch aus `now` selbst (kein versteckter Zustand), auf
    denselben Slot (5s-Fenster) angewendet immer dasselbe Ergebnis. `now`
    ist bewusst ein einfacher float (z.B. time.monotonic()) statt eines
    Objekts - hdmi_slideshow.py laeuft als eigener Prozess mit eigener Uhr,
    ein gemeinsames Zeit-Objekt ueber Prozessgrenzen hinweg waere unnoetig
    kompliziert. Beide Prozesse laufen dadurch zeitlich unabhaengig
    (bewusst NICHT synchron zueinander) - das ist fuer den Zweck (Beamer im
    Hintergrund einer Veranstaltung) irrelevant, wichtig ist nur, dass JEDE
    einzelne Wiedergabe fuer sich genommen genau nach diesem Muster
    verlaeuft.
    """
    slot = int(now // slot_seconds)
    t = now % slot_seconds
    slot_rng = random.Random(slot)
    show_ad = slot_rng.random() < ad_probability
    direction = slot_rng.choice(_DIRECTIONS)
    photo_index = (slot % photo_count) if photo_count > 0 else 0

    if t < fly_seconds:
        progress = t / fly_seconds
        eased = 1 - (1 - progress) ** 3  # ease-out: schnell rein, sanft einrasten
    else:
        eased = 1.0

    return AttractSlot(
        slot=slot, t=t, show_ad=show_ad, direction=direction,
        photo_index=photo_index, eased=eased,
    )


def fly_in_offset(direction: str, eased: float, width: int, height: int) -> tuple[int, int]:
    """Blit-Versatz (x, y) fuer die Einflug-Animation - gleiche Rechnung wie
    bisher in renderer._draw_attract_gallery()."""
    x, y = 0, 0
    if direction == "left":
        x = round(-width * (1 - eased))
    elif direction == "right":
        x = round(width * (1 - eased))
    elif direction == "top":
        y = round(-height * (1 - eased))
    else:
        y = round(height * (1 - eased))
    return x, y
