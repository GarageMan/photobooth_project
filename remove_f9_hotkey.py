#!/usr/bin/env python3
"""
remove_f9_hotkey.py
====================
Entfernt den F9-Debug-Hotkey aus app_with_hw.py (Haertung). Der Hotkey
sprang bei debug_overlay=True direkt in die PIN-Eingabe, ohne die
Geheim-Geste zu benoetigen - eine bewusst wieder entfernte Abkuerzung.

renderer.py wurde bereits geprueft und ist sauber (kein Debug-Rechteck mehr
vorhanden) - dieses Skript fasst nur app_with_hw.py an.

Idempotent: ist der Hotkey schon entfernt, passiert nichts.
Sicherheit: Backup (*.bak), py_compile-Selbstcheck, Rollback bei Fehler.

Aufruf im Projektverzeichnis:

    python3 remove_f9_hotkey.py
"""

from __future__ import annotations

import py_compile
import shutil
import sys
from pathlib import Path

F9_BLOCK = (
    "\n"
    "        if event.type == pygame.KEYDOWN and event.key == pygame.K_F9 and self.config.features.debug_overlay:\n"
    "            # NUR Debug: direkt in die PIN-Eingabe springen, ohne die\n"
    "            # Geheim-Geste ausfuehren zu muessen.\n"
    "            if self.model.state == AppState.MAIN_MENU:\n"
    "                self.dispatch(AppEvent(EventType.SHUTDOWN_GESTURE_DETECTED, source=\"debug_hotkey\"))\n"
    "            return"
)


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("app_with_hw.py")
    if not path.is_file():
        print(f"FEHLER: {path} nicht gefunden.")
        return 1

    text = path.read_text(encoding="utf-8")

    if "K_F9" not in text:
        print("Nichts zu tun - F9-Hotkey ist bereits entfernt.")
        return 0

    if text.count(F9_BLOCK) != 1:
        print("Abbruch: 'K_F9' gefunden, aber der Block passt nicht exakt (1x erwartet).")
        print("Es wurde NICHTS geaendert. Bitte den Block manuell pruefen/entfernen.")
        return 1

    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    path.write_text(text.replace(F9_BLOCK, "", 1), encoding="utf-8")
    print(f"F9-Hotkey entfernt. Backup: {backup}")

    try:
        py_compile.compile(str(path), doraise=True)
    except py_compile.PyCompileError as exc:
        shutil.copy2(backup, path)
        print("\nFEHLER beim py_compile - Rollback ausgefuehrt.")
        print(f"Compiler-Meldung:\n{exc}")
        return 1

    print("OK: py_compile sauber.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
