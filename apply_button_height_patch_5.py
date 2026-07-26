#!/usr/bin/env python3
"""
apply_button_height_patch_5.py  (v2)
====================================
Etappe 5 - Buttonhoehen vereinheitlichen.

Setzt in layout.py alle relevanten Buttonhoehen auf die Hoehe der
Service-Menue-Buttons (0.155 = 112px bei 720px Bildschirmhoehe, siehe
admin_menu.py _ROW_H):
  - Zwei-Button-Reihe + Einzel-Button (button_h): 0.09  -> 0.155
    (betrifft left / right / back)
  - Text-Ansichten (text_view_back): bleibt am unteren Rand verankert
    (Unterkante 0.975), waechst durch die groessere Hoehe nach OBEN;
    text_view_lower_y 0.885 -> 0.82
  - Hauptmenue-Diagonale (diag_h): 0.085 -> 0.155

Das PIN-Ziffernfeld (pin_keys) bleibt bewusst UNVERAENDERT.

Anker-basiert und all-or-nothing:
  1. jeden Anker exakt 1x pruefen (sonst Abbruch, Datei UNVERAENDERT)
  2. Backup .bak anlegen
  3. Ersetzen, in Tempdatei schreiben, Syntax-Selbsttest
  4. bei Erfolg atomar an Ort und Stelle verschieben, sonst verwerfen

v2-Aenderung: Der Syntax-Selbsttest nutzt das eingebaute compile() rein
im Speicher statt py_compile. py_compile legt eine .pyc in __pycache__/
ab - gehoert dieses Verzeichnis root (nach frueheren sudo-Laeufen),
scheitert das mit "Permission denied", obwohl der Patch selbst in
Ordnung ist. compile() schreibt nichts auf die Platte und umgeht das.
(Root-eigene Dateien im Projekt trotzdem gelegentlich aufraeumen:
 sudo chown -R photobox:photobox ~/photobooth)

Zeilenenden (CRLF/LF) werden automatisch erkannt und beim Schreiben
erhalten.

Aufruf:
    python3 apply_button_height_patch_5.py [pfad/zu/layout.py]
Ohne Argument wird layout.py neben diesem Skript erwartet.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path


# (Beschreibung, ALT, NEU, erwartete Trefferzahl)
# ALT/NEU sind mit "\n" geschrieben; die tatsaechlichen Zeilenenden der
# Zieldatei werden zur Laufzeit eingesetzt (siehe nl()).
REPLACEMENTS: list[tuple[str, str, str, int]] = [
    (
        "button_h (left/right/back) 0.09 -> 0.155",
        "    button_h = 0.09   # deutlich hoeher als vorher (war ~0.047)",
        "    button_h = 0.155  # Etappe 5: einheitlich auf Service-Menue-Hoehe (0.155)",
        1,
    ),
    (
        "diag_h (Hauptmenue-Diagonale) 0.085 -> 0.155 inkl. Begruendung",
        (
            "    # konsistent bleibt.\n"
            "    diag_w = 0.20\n"
            "    diag_h = 0.085"
        ),
        (
            "    # konsistent bleibt.\n"
            "    #\n"
            "    # Etappe 5: diag_h von 0.085 auf 0.155 erhoeht (einheitliche\n"
            "    # Buttonhoehe). Eine Neuberechnung der Diagonale ist NICHT noetig:\n"
            "    # Zwei Buttons kollidieren nur, wenn sie sich in BEIDEN Achsen\n"
            "    # ueberlappen. Der horizontale Schritt (diag_x_step=0.22) ist\n"
            "    # groesser als die Buttonbreite (diag_w=0.20), also bleibt zwischen\n"
            "    # benachbarten Buttons immer eine Luecke von 0.02*Breite in X -\n"
            "    # unabhaengig von der Hoehe. Damit ueberlappt garantiert nichts,\n"
            "    # auch wenn sich die vertikalen Baender jetzt ueberschneiden.\n"
            "    # diag_y0/diag_y_step bleiben unveraendert, damit die Oberkante\n"
            "    # (und der Titelbereich darueber) unberuehrt bleibt; die Gruppe\n"
            "    # waechst nur nach unten (unterster Button endet bei 0.955,\n"
            "    # ca. 32px Rand bei 720px Hoehe).\n"
            "    diag_w = 0.20\n"
            "    diag_h = 0.155"
        ),
        1,
    ),
    (
        "text_view_back am unteren Rand verankern (0.885 -> 0.82)",
        (
            '    # Wie "right", aber tiefer (0.885 statt 0.80 lower_y) - nur fuer\n'
            "    # INSTRUCTIONS/TERMS. Bildschirmunterkante bleibt bei button_h=0.09\n"
            "    # noch mit ca. 1.6% (\u224811px bei 720px Hoehe) Rand erhalten, also nicht\n"
            "    # bis an den allerletzten Pixel.\n"
            "    text_view_lower_y = 0.885"
        ),
        (
            '    # Wie "right", aber am unteren Rand verankert - nur fuer\n'
            "    # INSTRUCTIONS/TERMS. Unterkante bleibt bei 0.975 (ca. 18px Rand\n"
            "    # bei 720px Hoehe); durch die groessere Buttonhoehe (0.155,\n"
            "    # Etappe 5) waechst der Button nach OBEN statt tiefer zu rutschen.\n"
            "    text_view_lower_y = 0.82"
        ),
        1,
    ),
]


def _detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def apply(path: Path) -> int:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    newline = _detect_newline(text)

    def nl(s: str) -> str:
        # Nur "\n" ersetzen; evtl. schon vorhandene "\r\n" nicht verdoppeln.
        return s.replace("\r\n", "\n").replace("\n", newline)

    print(f"Zieldatei : {path}")
    print(f"Zeilenende: {'CRLF' if newline == chr(13) + chr(10) else 'LF'}\n")

    # 1) Anker pruefen
    problems = []
    for desc, old, new, expected in REPLACEMENTS:
        count = text.count(nl(old))
        marker = "OK   " if count == expected else "FEHLER"
        print(f"[{marker}] {count}x (erwartet {expected}): {desc}")
        if count != expected:
            problems.append(desc)
    if problems:
        print("\nAbbruch: mindestens ein Anker nicht exakt gefunden. Datei UNVERAENDERT.")
        return 1

    # 2) Ersetzen (im Speicher)
    patched = text
    for desc, old, new, expected in REPLACEMENTS:
        patched = patched.replace(nl(old), nl(new))
    if patched == text:
        print("\nKeine Aenderung berechnet - Abbruch.")
        return 1

    # 2b) Syntax-Selbsttest rein im Speicher (schreibt KEINE .pyc,
    #     unabhaengig von __pycache__-Berechtigungen).
    try:
        compile(patched, str(path), "exec")
    except SyntaxError as exc:
        print(f"\nSyntaxfehler im Ergebnis, Datei UNVERAENDERT:\n  {exc}")
        return 1

    # 3) Backup
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    print(f"\nBackup angelegt: {backup}")

    # 4) In Tempdatei im selben Verzeichnis schreiben (fuer atomares move)
    fd, tmp_name = tempfile.mkstemp(suffix=".py", dir=str(path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_bytes(patched.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        print(f"Fehler beim Schreiben, Datei UNVERAENDERT: {exc}")
        return 1

    # 5) Atomar an Ort und Stelle
    shutil.move(str(tmp), str(path))

    # 5b) Gegenprobe: geschriebene Datei erneut einlesen und im Speicher
    #     kompilieren (auch hier ohne .pyc).
    try:
        compile(path.read_bytes().decode("utf-8"), str(path), "exec")
    except SyntaxError as exc:
        print(f"\nGegenprobe fehlgeschlagen! Rollback empfohlen:\n  mv '{backup}' '{path}'\n  {exc}")
        return 1

    print("\nOK: layout.py gepatcht, Syntax sauber.")
    print(f"Rollback bei Bedarf:  mv '{backup}' '{path}'")
    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("layout.py")
    if not target.is_file():
        print(f"Datei nicht gefunden: {target}")
        sys.exit(1)
    sys.exit(apply(target))
