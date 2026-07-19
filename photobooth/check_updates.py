#!/usr/bin/env python3
"""
check_updates.py - woechentliche Update-Pruefung fuer die Fotobox.

Prueft drei Quellen und verschickt bei Funden eine zusammenfassende
E-Mail:
  1. apt-Paketupdates (Raspberry Pi OS)
  2. Raspberry-Pi-Bootloader/EEPROM-Firmware (rpi-eeprom-update)
  3. TP-Link-Router-Firmware (Web-Scraping der offiziellen
     Download-Seite, bestmoeglich - siehe Hinweise unten)

Aufruf (z.B. per Cronjob, siehe README):
    python3 /home/photobox/photobooth/check_updates.py

Zugangsdaten fuer den Mailversand kommen aus local_secrets.py
(siehe local_secrets_example.py) - NICHT hier im Code hinterlegen.
"""

from __future__ import annotations

import re
import smtplib
import subprocess
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

from local_secrets import (
    NOTIFY_EMAIL_TO,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USER,
)

# TP-Link-Download-Seite fuer den WR802N. Hardware-Version bestaetigt
# ueber die Router-Konfigurationsseite: "TL-WR802N v4 00000004" -> v4.
# Falls der Router mal getauscht wird, hier anpassen:
# https://www.tp-link.com/de/support/download/tl-wr802n/<version>/
TPLINK_DOWNLOAD_URL = "https://www.tp-link.com/de/support/download/tl-wr802n/v4/"

# Speichert die zuletzt gesehene TP-Link-Firmware-Version, um beim
# naechsten Lauf nur bei tatsaechlicher AENDERUNG zu benachrichtigen
# (sonst wuerde jede Woche dieselbe Info erneut verschickt).
STATE_FILE = Path(__file__).resolve().parent / "data" / "last_tplink_fw.txt"

# Zeitstempel des letzten Laufs - wird von der MOTD-Erinnerung
# (/etc/update-motd.d/, siehe README) ausgelesen, um anzuzeigen, wie
# lange der letzte manuelle Update-Check her ist.
LAST_RUN_FILE = Path(__file__).resolve().parent / "data" / "last_check_run.txt"


def check_apt_updates() -> list[str]:
    """Gibt die Liste der aktualisierbaren apt-Pakete zurueck."""
    subprocess.run(["apt-get", "update"], capture_output=True, timeout=120)
    result = subprocess.run(
        ["apt", "list", "--upgradable"], capture_output=True, text=True, timeout=60
    )
    lines = [
        line for line in result.stdout.splitlines() if line and not line.startswith("Listing")
    ]
    return lines


def check_rpi_eeprom() -> str | None:
    """Prueft auf ein verfuegbares Bootloader/EEPROM-Update.

    Bewusst OHNE "-a" aufgerufen - das wuerde ein Update sofort
    einspielen. Hier soll nur geprueft/gemeldet werden.
    """
    try:
        result = subprocess.run(
            ["rpi-eeprom-update"], capture_output=True, text=True, timeout=30
        )
    except FileNotFoundError:
        return "rpi-eeprom-update ist auf diesem System nicht installiert."

    output = result.stdout + result.stderr
    if "update available" in output.lower():
        return output.strip()
    return None


def check_tplink_firmware() -> str | None:
    """Best-effort-Pruefung der TP-Link-Firmwareseite auf eine neue
    Versionsnummer. Kein offizielles API - reines Web-Scraping, daher
    fehleranfaellig, falls TP-Link die Seitenstruktur aendert. Bei
    Fehlern wird das im Rueckgabewert vermerkt statt das Skript
    abstuerzen zu lassen.
    """
    try:
        import urllib.request

        req = urllib.request.Request(
            TPLINK_DOWNLOAD_URL, headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except Exception as exc:  # bewusst breit - Netzwerk-/Parsingfehler nicht fatal
        return f"TP-Link-Firmwareseite konnte nicht geprueft werden ({exc})."

    # Sucht nach einem Versionsmuster wie "Firmware V4_170219" o.ae.
    # Muster ggf. anpassen, falls TP-Link das Seitenformat aendert.
    match = re.search(r"[Ff]irmware[^0-9]{0,20}([0-9]{4,8})", html)
    if not match:
        return (
            "Konnte keine Versionsnummer auf der TP-Link-Seite finden - "
            "Seitenstruktur hat sich vermutlich geaendert, bitte manuell "
            f"pruefen: {TPLINK_DOWNLOAD_URL}"
        )

    current_version = match.group(1)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    last_version = STATE_FILE.read_text().strip() if STATE_FILE.exists() else None

    if last_version == current_version:
        return None  # keine Aenderung seit dem letzten Lauf

    STATE_FILE.write_text(current_version)
    if last_version is None:
        return None  # erster Lauf - nur Baseline speichern, noch nichts melden
    return (
        f"Neue TP-Link-Firmwareversion entdeckt: {current_version} "
        f"(zuvor bekannt: {last_version}). Bitte pruefen: {TPLINK_DOWNLOAD_URL}"
    )


def send_email(subject: str, body: str) -> None:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = NOTIFY_EMAIL_TO

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)


def main() -> None:
    sections: list[str] = []

    # Zeitstempel IMMER schreiben, auch wenn nichts gefunden wird oder ein
    # Teilcheck fehlschlaegt - die MOTD-Erinnerung soll zeigen, wann
    # zuletzt ueberhaupt geprueft wurde, nicht nur wann etwas gefunden wurde.
    LAST_RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_RUN_FILE.write_text(datetime.now().isoformat(timespec="seconds"))

    apt_updates = check_apt_updates()
    if apt_updates:
        sections.append(
            "apt-Paketupdates verfuegbar ({} Paket(e)):\n{}".format(
                len(apt_updates), "\n".join(f"  - {line}" for line in apt_updates)
            )
        )

    eeprom_result = check_rpi_eeprom()
    if eeprom_result:
        sections.append(f"Raspberry-Pi-Bootloader/EEPROM-Update:\n{eeprom_result}")

    tplink_result = check_tplink_firmware()
    if tplink_result:
        sections.append(f"TP-Link-Router:\n{tplink_result}")

    if not sections:
        print("Keine Updates gefunden - keine E-Mail verschickt.")
        return

    body = (
        "Automatische Update-Pruefung der Fotobox hat folgende "
        "Punkte gefunden:\n\n" + "\n\n".join(sections)
    )
    send_email("Fotobox: Updates verfuegbar", body)
    print("E-Mail mit Update-Hinweisen verschickt.")


if __name__ == "__main__":
    main()
