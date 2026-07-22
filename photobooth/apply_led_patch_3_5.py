#!/usr/bin/env python3
"""
apply_led_patch_3_5.py
======================
Schritt 3.5 (LED-Kuer) fuer das versteckte Herunterfahren:

  - LedEffect.PIN_ERROR: bei falscher PIN schnelles Rot/Gelb-Blinken am Ring
    (waehrend pin_error_deadline laeuft), Taster-LED blitzt synchron mit.
  - LedEffect.SHUTDOWN_SEQUENCE: Sonnenuntergang am Ring waehrend des
    Abschieds-Screens, gerendert aus led_shutdown.frame_colors(t).

Betrifft led_service.py, hw_led_provider.py und app_with_hw.py.

Sicherheitsmechanik (wie die bisherigen Patches):
  - Jeder Anker muss GENAU EINMAL vorkommen, sonst Abbruch OHNE Schreiben.
  - Alles-oder-nichts ueber ALLE drei Dateien.
  - Backups (*.bak).
  - py_compile-Selbstcheck; bei Fehler Rollback aller Dateien.

Aufruf im Projektverzeichnis:

    python3 apply_led_patch_3_5.py
    # oder mit Basisverzeichnis:
    python3 apply_led_patch_3_5.py /home/photobox/photobooth
"""

from __future__ import annotations

import py_compile
import shutil
import sys
from pathlib import Path


FILES: list[tuple[str, str, list[tuple[str, str, str]]]] = [
    (
        "led_service.py",
        "SHUTDOWN_SEQUENCE",
        [
            (
                "LS1) Zwei neue LedEffect-Werte",
                "    ERROR = auto()\n"
                "\n"
                "\n"
                "@dataclass\n"
                "class LedService:",
                "    ERROR = auto()\n"
                "    # NEU (3.5): Abschieds-Sonnenuntergang am Ring (SHUTDOWN_GOODBYE);\n"
                "    # rendert led_shutdown.frame_colors(t). Danach faehrt der Pi herunter.\n"
                "    SHUTDOWN_SEQUENCE = auto()\n"
                "    # NEU (3.5): Rot/Gelb-Fehlerblitz am Ring nach falscher PIN (transient,\n"
                "    # solange pin_error_deadline laeuft).\n"
                "    PIN_ERROR = auto()\n"
                "\n"
                "\n"
                "@dataclass\n"
                "class LedService:",
            ),
        ],
    ),
    (
        "hw_led_provider.py",
        "import led_shutdown",
        [
            (
                "HW1) Import led_shutdown",
                "from led_service import LedEffect",
                "from led_service import LedEffect\n"
                "import led_shutdown  # NEU (3.5): Sonnenuntergang-Frames fuer SHUTDOWN_SEQUENCE",
            ),
            (
                "HW2) Feld _effect_since",
                "    _current_effect: LedEffect = field(default=LedEffect.OFF, init=False)",
                "    _current_effect: LedEffect = field(default=LedEffect.OFF, init=False)\n"
                "    _effect_since: float = field(default=0.0, init=False)  # NEU (3.5): Startzeit des aktuellen Effekts",
            ),
            (
                "HW3) set_effect merkt sich die Startzeit",
                "    def set_effect(self, effect: LedEffect) -> None:\n"
                "        \"\"\"Thread-sicher den gewünschten Effekt setzen.\"\"\"\n"
                "        with self._lock:\n"
                "            self._current_effect = effect",
                "    def set_effect(self, effect: LedEffect) -> None:\n"
                "        \"\"\"Thread-sicher den gewünschten Effekt setzen.\"\"\"\n"
                "        with self._lock:\n"
                "            if effect != self._current_effect:  # NEU (3.5): Startzeit merken\n"
                "                self._effect_since = time.monotonic()\n"
                "            self._current_effect = effect",
            ),
            (
                "HW4) Worker liest zusaetzlich die Startzeit",
                "        while self._running:\n"
                "            with self._lock:\n"
                "                effect = self._current_effect",
                "        while self._running:\n"
                "            with self._lock:\n"
                "                effect = self._current_effect\n"
                "                effect_since = self._effect_since  # NEU (3.5)",
            ),
            (
                "HW5) Render-Zweige PIN_ERROR + SHUTDOWN_SEQUENCE",
                "            elif effect == LedEffect.ERROR:\n"
                "                # Schnelles rotes Blinken (5 Hz)\n"
                "                on = int(now * 10) % 2 == 0\n"
                "                self._fill((255, 0, 0) if on else (30, 0, 0))\n"
                "                time.sleep(0.03)\n"
                "\n"
                "            else:\n"
                "                time.sleep(0.1)",
                "            elif effect == LedEffect.ERROR:\n"
                "                # Schnelles rotes Blinken (5 Hz)\n"
                "                on = int(now * 10) % 2 == 0\n"
                "                self._fill((255, 0, 0) if on else (30, 0, 0))\n"
                "                time.sleep(0.03)\n"
                "\n"
                "            elif effect == LedEffect.PIN_ERROR:\n"
                "                # NEU (3.5): falsche PIN - schnelles Rot/Gelb-Wechselblinken\n"
                "                # (~6 Hz, spiegelt config.shutdown.error_*_rgb / _flash_hz).\n"
                "                phase = int(now * 6.0) % 2\n"
                "                self._fill((200, 0, 0) if phase == 0 else (220, 160, 0))\n"
                "                time.sleep(0.02)\n"
                "\n"
                "            elif effect == LedEffect.SHUTDOWN_SEQUENCE:\n"
                "                # NEU (3.5): Sonnenuntergang aus led_shutdown.frame_colors(t),\n"
                "                # t = Zeit seit Effektbeginn. Nach TOTAL_SECONDS alles aus; die\n"
                "                # App loest zeitgleich das eigentliche poweroff aus.\n"
                "                t = now - effect_since\n"
                "                for i, color in enumerate(led_shutdown.frame_colors(t)):\n"
                "                    self._pixels[i] = color\n"
                "                self._pixels.show()\n"
                "                time.sleep(0.02)\n"
                "\n"
                "            else:\n"
                "                time.sleep(0.1)",
            ),
            (
                "HW6) Test-Hooks im __main__-Block",
                '        "error":            LedEffect.ERROR,\n'
                '        "off":              LedEffect.OFF,\n'
                "    }",
                '        "error":            LedEffect.ERROR,\n'
                '        "pin_error":        LedEffect.PIN_ERROR,         # NEU (3.5)\n'
                '        "shutdown_seq":     LedEffect.SHUTDOWN_SEQUENCE,  # NEU (3.5)\n'
                '        "off":              LedEffect.OFF,\n'
                "    }",
            ),
        ],
    ),
    (
        "app_with_hw.py",
        "LedEffect.SHUTDOWN_SEQUENCE",
        [
            (
                "APP1) _sync_led: PIN_ENTRY (Fehler-Optik) + SHUTDOWN_GOODBYE",
                "        elif state == AppState.ERROR_SCREEN:\n"
                "            effect = LedEffect.ERROR\n"
                "        else:\n"
                "            effect = LedEffect.OFF",
                "        elif state == AppState.ERROR_SCREEN:\n"
                "            effect = LedEffect.ERROR\n"
                "        elif state == AppState.PIN_ENTRY:\n"
                "            # NEU (3.5): nur waehrend der Fehler-Optik rot/gelb, sonst dunkel.\n"
                "            deadline = self.model.timers.pin_error_deadline\n"
                "            if deadline is not None and now < deadline:\n"
                "                effect = LedEffect.PIN_ERROR\n"
                "            else:\n"
                "                effect = LedEffect.OFF\n"
                "        elif state == AppState.SHUTDOWN_GOODBYE:\n"
                "            effect = LedEffect.SHUTDOWN_SEQUENCE  # NEU (3.5): Sonnenuntergang\n"
                "        else:\n"
                "            effect = LedEffect.OFF",
            ),
            (
                "APP2) _sync_button_led: Taster-Blitz bei falscher PIN",
                "        if state in {\n"
                "            AppState.CAPTURE_PENDING, AppState.REVIEW, AppState.QR_DISPLAY,\n"
                "            AppState.DELETE_CONFIRM, AppState.ERROR_SCREEN,\n"
                "            AppState.BOOT, AppState.MAINTENANCE,\n"
                "            AppState.PIN_ENTRY, AppState.SHUTDOWN_GOODBYE,   # NEU (3.4)\n"
                "        }:\n"
                "            self._button_provider.set_led(False)\n"
                "            return",
                "        if state == AppState.PIN_ENTRY:\n"
                "            # NEU (3.5): bei falscher PIN Taster-LED synchron zum Ring blitzen,\n"
                "            # sonst aus.\n"
                "            deadline = self.model.timers.pin_error_deadline\n"
                "            if deadline is not None and now < deadline:\n"
                "                hz = self.config.shutdown.error_button_flash_hz\n"
                "                self._button_provider.set_led(int(now * hz) % 2 == 0)\n"
                "            else:\n"
                "                self._button_provider.set_led(False)\n"
                "            return\n"
                "\n"
                "        if state in {\n"
                "            AppState.CAPTURE_PENDING, AppState.REVIEW, AppState.QR_DISPLAY,\n"
                "            AppState.DELETE_CONFIRM, AppState.ERROR_SCREEN,\n"
                "            AppState.BOOT, AppState.MAINTENANCE,\n"
                "            AppState.SHUTDOWN_GOODBYE,   # (PIN_ENTRY jetzt oben separat, 3.5)\n"
                "        }:\n"
                "            self._button_provider.set_led(False)\n"
                "            return",
            ),
        ],
    ),
]


def main() -> int:
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")

    loaded: list[tuple[Path, str, list[tuple[str, str, str]]]] = []
    problems: list[str] = []
    for name, marker, edits in FILES:
        path = base / name
        if not path.is_file():
            problems.append(f"  {path} nicht gefunden.")
            continue
        text = path.read_text(encoding="utf-8")
        if marker in text:
            problems.append(f"  {name}: enthaelt bereits '{marker}' - schon gepatcht.")
            continue
        for desc, old, _new in edits:
            count = text.count(old)
            if count != 1:
                problems.append(f"  {name} [{desc}] Anker {count}x gefunden (erwartet 1x).")
        loaded.append((path, text, edits))

    if problems:
        print("Abbruch - es wurde NICHTS geaendert:")
        print("\n".join(problems))
        print("\nMoegliche Ursache: eine Datei wurde seit der Session veraendert.")
        return 1

    backups: list[tuple[Path, Path]] = []
    for path, text, edits in loaded:
        patched = text
        for _desc, old, new in edits:
            patched = patched.replace(old, new, 1)
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        backups.append((path, backup))
        path.write_text(patched, encoding="utf-8")
        print(f"Gepatcht: {path}  (Backup: {backup})")

    for path, _backup in backups:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            print(f"\nFEHLER beim py_compile von {path} - rolle ALLE Dateien zurueck.")
            for p, b in backups:
                shutil.copy2(b, p)
            print("Alle Dateien sind wieder auf dem alten Stand (aus den Backups).")
            print(f"Compiler-Meldung:\n{exc}")
            return 1

    print("\nOK: alle drei Dateien gepatcht und py_compile sauber.")
    print("Ring-Effekte einzeln testbar (App vorher beenden):")
    print("  sudo python3 hw_led_provider.py pin_error")
    print("  sudo python3 hw_led_provider.py shutdown_seq")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())