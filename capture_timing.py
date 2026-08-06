"""
capture_timing.py
==================
persistiert eine laufend aktualisierte
Schaetzung der tatsaechlichen Bilduebertragungsdauer (Ausloesen inkl.
GPIO-Puls + gphoto2-Download), damit die Uebertragungs-Animation
(Datei-Symbol in renderer.py + LED-Punkt in hw_led_provider.py) halbwegs
synchron zur echten Dauer laeuft - "koennen wir ja einfach mal die
Uebertragungszeit stoppen" (Refinement-Feedback).

Bewusst eine eigene, winzige Datei statt Teil von config.py: das ist
Laufzeitdaten (wie data/shutdown_lockout.json), keine Konfiguration, und
aendert sich mit jeder Aufnahme. Liegt unter data/ (siehe .gitignore),
wird also nie versioniert.
"""

from __future__ import annotations

import json
from pathlib import Path

# Grenzen fuer die Schaetzung - verhindert, dass ein einzelner Ausreisser
# (z.B. ein haengender USB-Bus) die Animation absurd lang oder absurd kurz
# werden laesst.
_MIN_SECONDS = 1.5
_MAX_SECONDS = 20.0

# Gleitender Mittelwert (Exponentially Weighted Moving Average): reagiert
# zuegig auf echte Aenderungen (z.B. groessere Fotos, langsamere Karte),
# ohne dass ein einzelner Ausreisser die Schaetzung sofort komplett verzerrt.
_EMA_ALPHA = 0.3


def _clamp(value: float) -> float:
    return max(_MIN_SECONDS, min(_MAX_SECONDS, value))


def load_expected_duration(path: Path, default: float) -> float:
    """Liest die zuletzt gespeicherte Schaetzung. Gibt bei fehlender oder
    beschaedigter Datei den uebergebenen Default zurueck (nie eine
    Exception) - gleiches Prinzip wie config.load_event_config()."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        value = float(data.get("expected_duration_seconds", default))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return _clamp(default)
    return _clamp(value)


def record_duration(path: Path, measured_seconds: float, previous_estimate: float) -> float:
    """Aktualisiert die Schaetzung nach einer echten Messung (EMA) und
    speichert sie persistent. Gibt den neuen Schaetzwert zurueck - Fehler
    beim Schreiben werden protokolliert, aber die App laeuft mit dem neuen
    Wert (nur eben nicht persistiert) unveraendert weiter."""
    measured = _clamp(measured_seconds)
    updated = _clamp(_EMA_ALPHA * measured + (1 - _EMA_ALPHA) * previous_estimate)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"expected_duration_seconds": updated}, fh)
    except OSError as exc:
        print(f"[CaptureTiming] Konnte {path} nicht schreiben: {exc}")
    return updated
