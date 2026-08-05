"""
Vorlage fuer local_secrets.py - NICHT die echten Zugangsdaten!

Auf dem Pi einmalig kopieren und dort die echten Werte eintragen:

    cp local_secrets_example.py local_secrets.py
    nano local_secrets.py

local_secrets.py steht in .gitignore und wird NIEMALS ins Repository
committet. config.py importiert die Werte von dort mit Fallback auf
einen auffaelligen Platzhalter bzw. sinnvolle Standards, falls die Datei
(oder ein einzelner Wert darin) fehlt (siehe config.py).
"""

# Aktuelles Gast-WLAN-Passwort (SSID "Fotobox_Gast") - wird den Gaesten
# auf dem Bildschirm angezeigt (renderer.py), damit sie sich verbinden
# und ihre Fotos herunterladen koennen. Bei jedem Passwort-Wechsel am
# TP-Link IMMER auch hier nachziehen, sonst zeigt der Screen ein
# falsches Passwort an.
GUEST_WIFI_PASSWORD = "CHANGE_ME"

# Geheim-PIN fuer den Zugang zum Service-Menue (Status/Diagnose,
# USB-Export, Bilder loeschen, Kamera-Einstellungen, Herunterfahren, ...)
# ueber die versteckte Geste im Hauptmenue (siehe admin_service.py /
# config.ShutdownConfig). Nur Ziffern verwenden, damit sie sich ueber ein
# einfaches Touch-Ziffernfeld eingeben laesst. Nicht mit dem Gast-WLAN-
# Passwort o.ae. teilen. Bleibt der Wert der Platzhalter, verweigert die
# App den Zugang zum Service-Menue bewusst (siehe
# admin_service.pin_is_configured).
#
# GEAENDERT (Sprint 11): vormals SHUTDOWN_PIN - die PIN schuetzt den
# gesamten Service-Bereich, nicht (mehr ausschliesslich) das
# Herunterfahren, das urspruenglich der einzige Menuepunkt dahinter war.
# Liegt auf dem Pi noch eine local_secrets.py mit dem alten Namen
# SHUTDOWN_PIN: config.py liest diesen weiterhin als Fallback, eine
# Umbenennung hier ist also nicht zwingend eilig, aber empfohlen.
SERVICE_MENU_PIN = "CHANGE_ME"

# --- Geheim-Geste, die die PIN-Eingabe aufruft (siehe admin_service.py) ---
# Bewusst hier in der NICHT versionierten Datei, damit weder Muster noch
# Position der versteckten Geste im oeffentlichen Repo stehen. Fehlt einer
# dieser drei Werte, greift der jeweilige Standard aus config.py - nur
# SERVICE_MENU_PIN oben ist zwingend.
#
# GEAENDERT (Sprint 11): die folgenden drei Variablen hiessen vormals
# SHUTDOWN_GESTURE_ZONE/_PATTERN/SHUTDOWN_LONG_PRESS_SECONDS - siehe
# Begruendung beim PIN oben. Auch hier gilt derselbe Fallback auf die
# alten Namen in config.py, falls noch nicht nachgezogen.

# Zone (unsichtbarer Bereich im Hauptmenue), in dem die Geste erkannt
# wird. Genau einer von: "links", "rechts", "oben", "unten".
SERVICE_MENU_GESTURE_ZONE = "rechts"

# Muster ("Anzahl"): Reihenfolge aus "kurz"/"lang"-Tipps. Laenge frei
# waehlbar - der Detector passt sich automatisch an. Standard entspricht
# bspw. "3x kurz, 1x lang, 2x kurz".
SERVICE_MENU_GESTURE_PATTERN = ("kurz", "kurz", "kurz", "lang", "kurz", "kurz")

# Dauer: ab dieser Haltezeit (Sekunden) zaehlt ein Tipp als "lang".
# Kleiner = empfindlicher, groesser = muss bewusst laenger gehalten werden.
SERVICE_MENU_LONG_PRESS_SECONDS = 0.6

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