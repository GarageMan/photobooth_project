"""
ECHTE Zugangsdaten - NICHT ins Repository committen (siehe .gitignore).
Auf dem Pi unter /home/photobox/photobooth/local_secrets.py ablegen.
"""

# Aktuelles Gast-WLAN-Passwort (SSID "Fotobox_Gast").
GUEST_WIFI_PASSWORD = "Mina2026"

# SMTP-Zugangsdaten fuer check_updates.py - bitte ausfuellen, bevor der
# Cronjob eingerichtet wird (siehe README, Abschnitt "Sicherheit").
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "CHANGE_ME@example.com"
SMTP_PASSWORD = "CHANGE_ME"
NOTIFY_EMAIL_TO = "CHANGE_ME@example.com"
