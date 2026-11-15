"""Gemeinsame Test-Vorbereitung (Sprint 15, Fix 1).

Wird von pytest VOR allen Testmodulen geladen. Die Tests sollen immer mit
den Standardwerten laufen - unabhaengig davon, welche Veranstaltung gerade
in ~/photobooth/event_config.json eingetragen ist. Deshalb zeigt
PHOTOBOOTH_EVENT_CONFIG auf eine nicht vorhandene Datei in einem
Temp-Verzeichnis: config.load_event_config liefert dann {} (Standardwerte),
und eventuelle Schreibzugriffe landen dort statt in der Live-Datei.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="photobooth_test_"))
os.environ["PHOTOBOOTH_EVENT_CONFIG"] = str(_TMP_DIR / "event_config.json")
