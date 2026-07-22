"""
Die echten Werte eintragen und dann ausführen:

    cp local_secrets_example.py local_secrets.py
    nano local_secrets.py

local_secrets.py steht in .gitignore und wird NIEMALS ins Repository
committet. config.py importiert die Werte von dort mit Fallback auf
einen auffaelligen Platzhalter, falls die Datei (oder ein einzelner
Wert darin) fehlt (siehe config.py).
"""

# Aktuelles Gast-WLAN-Passwort (SSID "Fotobox_Gast") - wird den Gaesten
# auf dem Bildschirm angezeigt (renderer.py), damit sie sich verbinden
# und ihre Fotos herunterladen koennen. Bei jedem Passwort-Wechsel am
# TP-Link IMMER auch hier nachziehen, sonst zeigt der Screen ein
# falsches Passwort an.
GUEST_WIFI_PASSWORD = "Mina2026"

# Geheim-PIN zum Herunterfahren der Fotobox ueber die versteckte Geste
# im Hauptmenue (siehe shutdown_service.py / config.ShutdownConfig).
# Nur Ziffern verwenden, damit sie sich spaeter ueber ein einfaches
# Touch-Ziffernfeld eingeben laesst. Nicht mit dem Gast-WLAN-Passwort
# o.ae. teilen. Bleibt der Wert der Platzhalter, verweigert die App den
# Shutdown bewusst (siehe shutdown_service.pin_is_configured).
SHUTDOWN_PIN = "170366"

# SMTP-Zugangsdaten fuer die automatische Update-Benachrichtigung
# (siehe check_updates.py). Bei Gmail: kein normales Account-Passwort,
# sondern ein "App-Passwort" verwenden (Google-Konto -> Sicherheit ->
# App-Passwoerter). Andere Provider (GMX, Web.de, eigener Mailserver
# etc.) funktionieren ebenso, nur Host/Port anpassen.
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "1150gsler@gmail.com"
SMTP_PASSWORD = "thar8tosh-le*NAIF"
NOTIFY_EMAIL_TO = "lutz-peter@imail.de"