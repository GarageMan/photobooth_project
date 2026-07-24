"""
test_shutdown_service.py
========================
Offline-Tests (pytest) fuer shutdown_service.py und die Zonen-Aufloesung
in config.py. Keine Hardware, kein pygame, keine echte Wanduhr - alle
Zeitpunkte werden als Parameter injiziert, damit die Tests deterministisch
und schnell sind.

Ausfuehren im Projektverzeichnis:

    python3 -m pytest test_shutdown_service.py -v
"""

from __future__ import annotations

import pytest

import config
import shutdown_service as ss
from shutdown_service import (
    PinLockout,
    PinResult,
    SecretGestureDetector,
    TapKind,
    pin_is_configured,
)


# ===========================================================================
# Hilfsfunktionen
# ===========================================================================

def _make_detector(**overrides) -> SecretGestureDetector:
    # Kleine, gut testbare Ecke oben links; Standard-Muster "3x kurz, 1x lang,
    # 2x kurz"; Long-Press-Schwelle 0.5s; max. Pause 2.0s.
    params = dict(
        corner=(0, 0, 100, 100),
        long_press_seconds=0.5,
        max_gap_seconds=2.0,
    )
    params.update(overrides)
    return SecretGestureDetector(**params)


def _tap(det: SecretGestureDetector, t_down: float, held: float, pos=(10, 10)) -> bool:
    # Simuliert einen Tipp: Beruehrung bei t_down, Loslassen nach held Sekunden.
    # Gibt zurueck, was on_touch_up meldet (True = Geste vollstaendig erkannt).
    det.on_touch_down(pos, t_down)
    return det.on_touch_up(pos, t_down + held)


# ===========================================================================
# SecretGestureDetector - Muster-Erkennung
# ===========================================================================

def test_full_pattern_triggers():
    det = _make_detector()
    results = []
    # 3x kurz
    results.append(_tap(det, 0.0, 0.1))
    results.append(_tap(det, 0.3, 0.1))
    results.append(_tap(det, 0.6, 0.1))
    # 1x lang (0.7s >= 0.5s Schwelle)
    results.append(_tap(det, 0.9, 0.7))
    # 2x kurz
    results.append(_tap(det, 1.8, 0.1))
    results.append(_tap(det, 2.1, 0.1))
    assert results[:-1] == [False] * 5
    assert results[-1] is True


def test_all_short_does_not_trigger():
    det = _make_detector()
    out = [_tap(det, i * 0.3, 0.1) for i in range(6)]
    assert out == [False] * 6


def test_long_in_wrong_position_does_not_trigger():
    det = _make_detector()
    # kurz, LANG, kurz, kurz, kurz, kurz  -> passt nicht auf (S,S,S,L,S,S)
    holds = [0.1, 0.7, 0.1, 0.1, 0.1, 0.1]
    out = [_tap(det, i * 0.3, h) for i, h in enumerate(holds)]
    assert out[-1] is False


def test_long_press_boundary_is_inclusive():
    # Muster der Laenge 1 isoliert die Kurz/Lang-Klassifikation sauber.
    det = _make_detector(pattern=("lang",))
    # knapp unter der Schwelle -> SHORT -> kein Treffer
    assert _tap(det, 0.0, 0.49) is False
    # exakt auf der Schwelle -> LONG (>=) -> Treffer
    assert _tap(det, 1.0, 0.50) is True


def test_short_boundary_just_below_threshold():
    det = _make_detector(pattern=("kurz",))
    # exakt auf der Schwelle gilt bereits als LANG -> matcht "kurz" NICHT
    assert _tap(det, 0.0, 0.50) is False
    # knapp darunter -> SHORT -> Treffer
    assert _tap(det, 1.0, 0.49) is True


def test_taps_outside_corner_are_ignored_and_do_not_reset():
    det = _make_detector()
    # 5 korrekte Tipps in der Ecke: S,S,S,L,S
    assert _tap(det, 0.0, 0.1) is False
    assert _tap(det, 0.3, 0.1) is False
    assert _tap(det, 0.6, 0.1) is False
    assert _tap(det, 0.9, 0.7) is False   # LANG
    assert _tap(det, 1.8, 0.1) is False   # 5. Tipp, Puffer nun voll bis auf 1
    # Tipp AUSSERHALB der Ecke - muss ignoriert werden und die Sequenz
    # unangetastet lassen (auch die Pausen-Uhr nicht weiterstellen).
    assert _tap(det, 2.0, 0.1, pos=(500, 500)) is False
    # 6. korrekter Tipp schliesst die Geste ab
    assert _tap(det, 2.2, 0.1) is True


def test_too_long_gap_resets_sequence():
    det = _make_detector()
    for t in (0.0, 0.3, 0.6):
        _tap(det, t, 0.1)
    _tap(det, 0.9, 0.7)   # LANG
    _tap(det, 1.8, 0.1)   # 5 Tipps im Puffer: S,S,S,L,S
    # Grosse Pause (> max_gap_seconds) vor dem 6. Tipp -> Puffer geleert,
    # danach steht nur noch ein einzelner SHORT drin -> kein Treffer.
    assert _tap(det, 5.0, 0.1) is False


def test_detector_retriggers_after_success():
    det = _make_detector()

    def run_full(base: float) -> bool:
        r = False
        for i, h in enumerate([0.1, 0.1, 0.1, 0.7, 0.1, 0.1]):
            r = _tap(det, base + i * 0.3, h)
        return r

    assert run_full(0.0) is True
    # reset() nach Erfolg muss eine erneute Erkennung erlauben
    assert run_full(10.0) is True


def test_reset_clears_partial_sequence():
    det = _make_detector()
    _tap(det, 0.0, 0.1)
    _tap(det, 0.3, 0.1)
    det.reset()
    # Nach reset zaehlt eine neue, vollstaendige Geste ganz von vorn.
    out = [_tap(det, 1.0 + i * 0.3, h) for i, h in enumerate([0.1, 0.1, 0.1, 0.7, 0.1, 0.1])]
    assert out[-1] is True


def test_pattern_normalization_case_insensitive():
    # "LANG"/"Kurz" in beliebiger Gross-/Kleinschreibung wird korrekt normiert.
    det = _make_detector(pattern=("Kurz", "LANG"))
    assert _tap(det, 0.0, 0.1) is False   # SHORT
    assert _tap(det, 0.5, 0.7) is True    # LONG -> Muster (SHORT, LONG) komplett


# ===========================================================================
# SecretGestureDetector.from_config - Pixel-Aufloesung
# ===========================================================================

def test_from_config_resolves_corner_to_pixels():
    sc = config.ShutdownConfig(
        gesture_corner_fraction=(0.88, 0.15, 0.12, 0.16),
        long_press_seconds=0.6,
        gesture_max_gap_seconds=2.0,
        gesture_pattern=("kurz", "lang"),
    )
    det = SecretGestureDetector.from_config(sc, screen_width=1280, screen_height=720)
    # round(0.88*1280)=1126, round(0.15*720)=108, round(0.12*1280)=154, round(0.16*720)=115
    assert det.corner == (1126, 108, 154, 115)
    # Uebrige Parameter muessen durchgereicht sein
    assert det.long_press_seconds == 0.6
    assert det.max_gap_seconds == 2.0
    assert det.pattern == ("kurz", "lang")


# ===========================================================================
# pin_is_configured
# ===========================================================================

@pytest.mark.parametrize("pin,expected", [
    ("1234", True),
    ("0", True),
    ("", False),
    (ss._PIN_PLACEHOLDER, False),
])
def test_pin_is_configured(pin, expected):
    assert pin_is_configured(pin) is expected


# ===========================================================================
# PinLockout - Zaehlung, Sperre, Persistenz
# ===========================================================================

def _lock(tmp_path, **overrides) -> PinLockout:
    params = dict(
        lockout_path=tmp_path / "shutdown_lockout.json",
        max_attempts=3,
        lockout_seconds=1800,
    )
    params.update(overrides)
    return PinLockout(**params)


def test_correct_pin_accepted(tmp_path):
    lock = _lock(tmp_path)
    assert lock.check("1234", "1234", now_wall=100) == PinResult.ACCEPTED
    assert lock.attempts_left() == 3


def test_single_wrong_pin_rejected_and_counts_down(tmp_path):
    lock = _lock(tmp_path)
    assert lock.check("0000", "1234", now_wall=100) == PinResult.REJECTED
    assert lock.attempts_left() == 2


def test_third_wrong_pin_locks(tmp_path):
    lock = _lock(tmp_path)
    assert lock.check("0000", "1234", now_wall=100) == PinResult.REJECTED
    assert lock.check("0000", "1234", now_wall=101) == PinResult.REJECTED
    assert lock.check("0000", "1234", now_wall=102) == PinResult.REJECTED_NOW_LOCKED
    assert lock.is_locked(now_wall=103) is True
    # Nach dem Sperren wird der Fehlversuchs-Zaehler zurueckgesetzt.
    assert lock.attempts_left() == 3


def test_locked_rejects_even_correct_pin(tmp_path):
    lock = _lock(tmp_path)
    for t in (100, 101, 102):
        lock.check("0000", "1234", now_wall=t)
    # locked_until = 102 + 1800 = 1902; auch die richtige PIN prallt jetzt ab.
    assert lock.check("1234", "1234", now_wall=1500) == PinResult.LOCKED


def test_lockout_expires_and_counter_is_fresh(tmp_path):
    lock = _lock(tmp_path)
    for t in (100, 101, 102):
        lock.check("0000", "1234", now_wall=t)
    locked_until = 102 + 1800
    assert lock.is_locked(now_wall=locked_until - 1) is True
    assert lock.is_locked(now_wall=locked_until + 1) is False
    # Nach Ablauf wieder normal nutzbar, mit frischem Kontingent.
    assert lock.check("0000", "1234", now_wall=locked_until + 1) == PinResult.REJECTED
    assert lock.attempts_left() == 2


def test_remaining_seconds(tmp_path):
    lock = _lock(tmp_path)
    for t in (100, 101, 102):
        lock.check("0000", "1234", now_wall=t)
    assert lock.remaining_seconds(now_wall=102) == pytest.approx(1800)
    assert lock.remaining_seconds(now_wall=902) == pytest.approx(1000)
    assert lock.remaining_seconds(now_wall=5000) == 0.0


def test_not_configured_does_not_count_as_attempt(tmp_path):
    lock = _lock(tmp_path)
    r = lock.check("1234", ss._PIN_PLACEHOLDER, now_wall=100)
    assert r == PinResult.NOT_CONFIGURED
    assert lock.attempts_left() == 3
    assert lock.is_locked(now_wall=100) is False


def test_register_success_clears_partial_counter(tmp_path):
    lock = _lock(tmp_path)
    lock.check("0000", "1234", now_wall=100)   # 1 Fehlversuch
    assert lock.attempts_left() == 2
    lock.register_success()
    assert lock.attempts_left() == 3


def test_clear_resets_lock_and_counter(tmp_path):
    lock = _lock(tmp_path)
    for t in (100, 101, 102):
        lock.check("0000", "1234", now_wall=t)
    assert lock.is_locked(now_wall=103) is True
    lock.clear()
    assert lock.is_locked(now_wall=103) is False
    assert lock.attempts_left() == 3


# ---------------------------------------------------------------------------
# Persistenz ueber einen Neustart hinweg (neue Instanz aus derselben Datei)
# ---------------------------------------------------------------------------

def test_lockout_survives_restart(tmp_path):
    path = tmp_path / "lock.json"
    a = PinLockout(lockout_path=path, max_attempts=3, lockout_seconds=1800)
    for t in (1000, 1001, 1002):
        a.check("0000", "1234", now_wall=t)
    assert a.is_locked(now_wall=1003) is True
    # "Reboot": frische Instanz liest den Sperr-Ablauf aus der Datei.
    b = PinLockout(lockout_path=path, max_attempts=3, lockout_seconds=1800)
    assert b.is_locked(now_wall=1003) is True
    assert b.remaining_seconds(now_wall=1002) == pytest.approx(1800)
    assert b.is_locked(now_wall=1002 + 1800 + 1) is False


def test_failure_counter_survives_restart_no_reset_bypass(tmp_path):
    # Kerntest gegen die Umgehung "einfach neu starten": Der Fehlversuchs-
    # Zaehler darf durch einen Neustart NICHT auf 0 zurueckfallen.
    path = tmp_path / "lock.json"
    a = PinLockout(lockout_path=path, max_attempts=3, lockout_seconds=1800)
    assert a.check("0000", "1234", now_wall=100) == PinResult.REJECTED
    assert a.check("0000", "1234", now_wall=101) == PinResult.REJECTED
    assert a.attempts_left() == 1
    # "Reboot" mitten in der Fehlversuchs-Serie
    b = PinLockout(lockout_path=path, max_attempts=3, lockout_seconds=1800)
    assert b.attempts_left() == 1
    # Der naechste Fehlversuch (der insgesamt dritte) sperrt sofort.
    assert b.check("0000", "1234", now_wall=102) == PinResult.REJECTED_NOW_LOCKED


def test_success_persists_reset(tmp_path):
    path = tmp_path / "lock.json"
    a = PinLockout(lockout_path=path, max_attempts=3, lockout_seconds=1800)
    a.check("0000", "1234", now_wall=100)   # 1 Fehlversuch, persistiert
    a.register_success()
    b = PinLockout(lockout_path=path, max_attempts=3, lockout_seconds=1800)
    assert b.attempts_left() == 3


def test_corrupt_file_starts_clean(tmp_path):
    path = tmp_path / "lock.json"
    path.write_text("{ das ist kein gueltiges json", encoding="utf-8")
    lock = PinLockout(lockout_path=path, max_attempts=3, lockout_seconds=1800)
    assert lock.attempts_left() == 3
    assert lock.is_locked(now_wall=100) is False


def test_missing_file_starts_clean(tmp_path):
    lock = PinLockout(lockout_path=tmp_path / "gibt_es_nicht.json")
    assert lock.attempts_left() == lock.max_attempts
    assert lock.is_locked(now_wall=100) is False


def test_from_config_builds_lockout(tmp_path):
    sc = config.ShutdownConfig(
        lockout_file=tmp_path / "x.json",
        max_pin_attempts=5,
        lockout_seconds=60,
    )
    lock = PinLockout.from_config(sc)
    assert lock.lockout_path == tmp_path / "x.json"
    assert lock.max_attempts == 5
    assert lock.lockout_seconds == 60


# ===========================================================================
# config - Zonen-Aufloesung
# ===========================================================================

def test_all_four_zones_defined():
    assert set(config._GESTURE_ZONE_FRACTIONS) == {"links", "rechts", "oben", "unten"}


@pytest.mark.parametrize("name,expected", [
    ("oben",   (0.40, 0.00, 0.20, 0.12)),
    ("unten",  (0.40, 0.88, 0.20, 0.12)),
    ("links",  (0.00, 0.15, 0.12, 0.16)),
    ("rechts", (0.88, 0.15, 0.12, 0.16)),
])
def test_resolve_known_zone(name, expected):
    assert config._resolve_gesture_zone(name) == expected


def test_resolve_zone_case_and_whitespace_insensitive():
    assert config._resolve_gesture_zone("  RECHTS ") == config._resolve_gesture_zone("rechts")


def test_resolve_unknown_zone_falls_back_to_rechts():
    assert config._resolve_gesture_zone("diagonal") == config._resolve_gesture_zone("rechts")


def test_zones_do_not_overlap_main_menu_buttons():
    # Die vier diagonalen Hauptmenue-Buttons als Bruchteil-Rechtecke,
    # gespiegelt aus layout.build_layout (diag_x0=0.06, x_step=0.22, w=0.20;
    # diag_y0=0.53, y_step=0.09, h=0.085). Keine Geste-Zone darf einen davon
    # ueberlappen, sonst wuerde ein Tipp doppelt interpretiert.
    diag_x0, x_step, w = 0.06, 0.22, 0.20
    diag_y0, y_step, h = 0.53, 0.09, 0.085
    buttons = [
        (diag_x0 + i * x_step, diag_y0 + i * y_step, w, h) for i in range(4)
    ]

    def overlaps(a, b) -> bool:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah

    for zone_name, zone in config._GESTURE_ZONE_FRACTIONS.items():
        for btn in buttons:
            assert not overlaps(zone, btn), f"Zone '{zone_name}' ueberlappt einen Menue-Button"