"""
Vorlage fuer local_secrets.py - NICHT die echten Zugangsdaten!

Auf dem Pi einmalig kopieren und dort die echten Werte eintragen:

    cp local_secrets_example.py local_secrets.py
    nano local_secrets.py

local_secrets.py steht in .gitignore und wird NIEMALS ins Repository
committet. config.py importiert die Werte von dort mit Fallback auf
einen auffaelligen Platzhalter, falls die Datei fehlt (siehe config.py).
"""

# Aktuelles Gast-WLAN-Passwort (SSID "Fotobox_Gast") - wird den Gaesten
# auf dem Bildschirm angezeigt (renderer.py), damit sie sich verbinden
# und ihre Fotos herunterladen koennen. Bei jedem Passwort-Wechsel am
# TP-Link IMMER auch hier nachziehen, sonst zeigt der Screen ein
# falsches Passwort an.
GUEST_WIFI_PASSWORD = "CHANGE_ME"

# SMTP-Zugangsdaten fuer die automatische Update-Benachrichtigung
# (siehe check_updates.py). Bei Gmail: kein normales Account-Passwort,
# sondern ein "App-Passwort" verwenden (Google-Konto -> Sicherheit ->
# App-Passwoerter). Andere Provider (GMX, Web.de, eigener Mailserver
# etc.) funktionieren ebenso, nur Host/Port anpassen.
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "CHANGE_ME@example.com"
SMTP_PASSWORD = "CHANGE_ME"
NOTIFY_EMAIL_TO = "CHANGE_ME@example.com"
