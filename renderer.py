from __future__ import annotations

import math
import random
import time
from collections import OrderedDict
from dataclasses import dataclass

import pygame
from PIL import Image as PILImage

from config import AppConfig
from layout import KEYBOARD_SHIFT_MAP, LayoutRects, build_layout
from models import AppModel
from states import AppState
from admin_menu import ADMIN_MENU_ITEMS, build_admin_rects  # NEU (4.1)


@dataclass
class Renderer:
    config: AppConfig
    screen: pygame.Surface

    def __post_init__(self) -> None:
        self.layout: LayoutRects = build_layout(self.config.screen.width, self.config.screen.height)
        self.font_title = pygame.font.Font(None, 100)
        # NEU (Lesbarkeit): um 50% vergroessert (vorher 42) - betrifft alle
        # Meldungen/Hinweise fuer GAESTE, die ueber model.ui.status_text/
        # error_text bzw. als Hinweistext gezeichnet werden ("Du willst dich
        # fotografieren lassen?", "Bitte auf die Markierung stellen!",
        # "Möchtest du dieses Foto speichern?" usw.), sowie die Anleitung/
        # Nutzungsbedingungen und den QR-Hinweistext, die denselben Font
        # teilen. Buttons, Titel und die Countdown-Ziffer bleiben bewusst
        # unveraendert. NEU (Feedback): das PIN-geschuetzte Service-Menue
        # (nur fuer Lutz, keine Gaeste) nutzt bewusst NICHT diesen Font,
        # sondern die unveraenderten font_body_admin/font_status_admin
        # weiter unten.
        self.font_body = pygame.font.Font(None, 63)
        # Gleiche Groesse wie font_body, nur fett - fuer Ueberschriften
        # innerhalb laengerer Textblöcke (aktuell: _draw_terms). Bewusst
        # dieselbe Punktgroesse, damit die feste Zeilenhoehe (line_height in
        # _draw_terms/_draw_instructions, aus font_body.get_linesize()
        # berechnet) fuer alle Zeilen gueltig bleibt, unabhaengig davon, ob
        # eine einzelne Zeile fett oder normal gerendert wird.
        self.font_body_bold = pygame.font.Font(None, 63)
        self.font_body_bold.set_bold(True)
        self.font_small = pygame.font.Font(None, 32)
        self.font_button = pygame.font.Font(None, 50)
        # Etwa doppelt so gross wie font_body (frueher 42, jetzt 63) -
        # ausschliesslich fuer den Willkommenstext im Hauptmenue
        # ("Willkommen an der Fotobox!") und andere besonders prominente
        # Statuszeilen fuer GAESTE (z.B. Speicheralarm), damit diese auf
        # den ersten Blick auffallen. NEU (Lesbarkeit): ebenfalls um 50%
        # vergroessert (vorher 84), damit das Groessenverhaeltnis zu
        # font_body erhalten bleibt.
        self.font_status_main_menu = pygame.font.Font(None, 126)
        # NEU (Feedback): "Noch keine Fotos vorhanden!" (GALLERY_EMPTY) war
        # bei font_status_main_menu (126) zu dominant - eigene, um 25%
        # kleinere Schrift nur fuer diese Ueberschrift (126 * 0.75 ≈ 95),
        # ohne die anderen font_status_main_menu-Texte zu beeinflussen.
        self.font_gallery_empty_title = pygame.font.Font(None, 95)
        # NEU (Feedback): das PIN-geschuetzte Service-/Admin-Menue (Status/
        # Diagnose, USB-Export, Loeschen, ...) wird nur von Lutz selbst
        # gelesen, nicht von Gaesten - bleibt daher bewusst bei den
        # urspruenglichen (kleineren) Groessen statt der 50%-Vergroesserung
        # von font_body/font_status_main_menu. Gleiche Werte wie vor dem
        # Lesbarkeits-Update (42/84).
        self.font_body_admin = pygame.font.Font(None, 42)
        self.font_status_admin = pygame.font.Font(None, 84)
        # NEU (Nutzer-Feedback): dicktengleiche Schrift fuer den Eingabe-
        # Puffer der Bildschirmtastatur (zwischen den beiden Linien, siehe
        # _draw_admin_event_text_entry) - macht z.B. WLAN-Passwoerter besser
        # lesbar/abzaehlbar, da jedes Zeichen gleich breit ist (wie
        # Consolas/Terminal auf einem PC). pygame.font.Font(None, ...) laedt
        # immer die proportionale Standardschrift, daher hier stattdessen
        # ueber match_font() nach einer auf dem System installierten
        # Monospace-Schrift gesucht - mit mehreren Kandidaten, da sich Name/
        # Verfuegbarkeit zwischen dieser Sandbox und dem Raspberry Pi
        # unterscheiden koennen. match_font() wirft nie, sondern liefert
        # bestenfalls None - dann faellt dieser Font wie gehabt auf die
        # eingebaute Standardschrift zurueck (nie ein Absturz).
        _mono_path = None
        for _mono_name in (
            "dejavusansmono", "liberationmono", "freemono", "notosansmono",
            "consolas", "couriernew", "menlo",
        ):
            _mono_path = pygame.font.match_font(_mono_name)
            if _mono_path:
                break
        self.font_body_admin_mono = pygame.font.Font(_mono_path, 42)
        # Grosse Ziffer fuer den Cinema-Countdown - bewusst proportional zur
        # Bildschirmhoehe (nicht fix), damit sie auf jeder Aufloesung den
        # Kreis dominant ausfuellt statt "verloren" zu wirken.
        self.font_countdown_digit = pygame.font.Font(None, round(self.config.screen.height * 0.5))
        # Zwei getrennte LRU-Caches statt einem unbegrenzten dict: Thumbnails
        # (Galerie-Grid) und Vollbild-Ansichten (Review/Fullscreen/Attract)
        # haben unterschiedliche Groessen und damit unterschiedlichen
        # Speicherbedarf pro Eintrag - je eigenes Limit aus config.gallery,
        # sonst waechst der RAM-Verbrauch bei einem Event mit vielen Fotos
        # unbegrenzt weiter.
        self._thumbnail_cache: OrderedDict[str, pygame.Surface] = OrderedDict()
        self._fullscreen_cache: OrderedDict[str, pygame.Surface] = OrderedDict()
        # Pool aller "bitte_laecheln_01.png" .. "_15.png"-Varianten, einmalig
        # geladen und skaliert (None = noch nicht geladen). Aus diesem Pool
        # wird bei jedem neuen Countdown-Durchlauf zufaellig ein Bild fuer
        # _current_countdown_image gezogen (siehe render()).
        self._countdown_image_pool: list[pygame.Surface] | None = None
        self._current_countdown_image: pygame.Surface | None = None
        # NEU (Sprint-11-Nachbesserung): Pools aller "file_icon_01.png" ..
        # "_19.png"-Varianten (echte Fotos statt des handgezeichneten
        # Symbols) fuer die Datei-Animationen (Aufnahme-Uebertragung,
        # USB-Export, Pruefsummen-Vergleich, Loesch-Schredder) - gleiches
        # Lade-/Cache-Prinzip wie _countdown_image_pool, siehe
        # _load_file_icon_pool(). Ein Eintrag pro angefragter Zielgroesse
        # (_FILE_ICON_SIZE fuer die kleinen "fliegenden" Symbole,
        # _FILE_ICON_COMPARE_SIZE fuer die groessere Vergleichsanimation).
        self._file_icon_pools: dict[tuple[int, int], list[pygame.Surface]] = {}
        # Merkt sich pro laufender Aufnahme-Uebertragung (_draw_
        # capture_transfer_animation), welches Symbol aus dem Pool gezogen
        # wurde, damit es waehrend EINER Uebertragung stabil bleibt statt
        # bei jedem Frame zu wechseln - siehe _capture_transfer_icon_key().
        self._capture_transfer_counter: int = 0
        self._capture_transfer_progress_seen: float | None = None
        # NEU (Sprint-11-Nachbesserung): Zeitpunkt (time.time()), zu dem der
        # USB-Export-Abschluss-Screen betreten wurde - fuer die kurze
        # "Pop"-Einblendung des Ergebnis-Symbols, siehe
        # _draw_admin_usb_result_badge() und render().
        self._admin_usb_done_entered_at: float | None = None
        self._main_menu_background: pygame.Surface | bool | None = None
        self._boot_background: pygame.Surface | bool | None = None
        self._shutdown_background: pygame.Surface | bool | None = None  # NEU (3.3)
        self.gallery_thumbnail_hitboxes: list[tuple[pygame.Rect, int]] = []
        # Scroll-Position der Anleitung (in Pixeln). Lebt bewusst nur hier im
        # Renderer (reine Anzeige-Angelegenheit), nicht im AppModel/State
        # Machine - aehnlich wie gallery_thumbnail_hitboxes.
        self.instructions_scroll_offset: int = 0
        # Scroll-Position der Nutzungsbedingungen-Ansicht - analog zu
        # instructions_scroll_offset, aber bewusst ein eigenes Feld, damit
        # ein Wechsel zwischen "Anleitung" und "Nutzungsbedingungen" die
        # jeweils andere Scroll-Position nicht zuruecksetzt/vermischt.
        self.terms_scroll_offset: int = 0
        # NEU (6c): Scroll-Position der USB-Konfliktliste - gleiches Prinzip
        # wie instructions_scroll_offset/terms_scroll_offset (reine Anzeige-
        # Angelegenheit, lebt nur hier im Renderer).
        self.usb_conflicts_scroll_offset: int = 0
        # NEU (6c): Trefferflaechen der aktuell sichtbaren Konfliktzeilen -
        # (Rect, Dateiname, "overwrite"|"rename"). Wird bei jedem Aufruf von
        # _draw_admin_usb_conflicts() neu befuellt (Positionen haengen vom
        # Scroll-Offset ab, sind also erst nach dem Zeichnen bekannt) und in
        # app_with_hw._map_click_to_event gegen den Tap geprueft - gleiches
        # Muster wie gallery_thumbnail_hitboxes.
        self.usb_conflict_row_hitboxes: list[tuple[pygame.Rect, str, str]] = []
        # NEU (Nutzer-Feedback): Scroll-Position + Trefferflaechen der
        # Wallpaper-Auswahlliste - gleiches Prinzip wie
        # usb_conflicts_scroll_offset/usb_conflict_row_hitboxes oben.
        self.wallpaper_pick_scroll_offset: int = 0
        self.wallpaper_pick_row_hitboxes: list[tuple[pygame.Rect, str]] = []
        # NEU (Nutzer-Feedback, Bugfix): Scroll-Position der Diagnose-Zeilen
        # (ADMIN_STATUS) - gleiches Prinzip wie usb_conflicts_scroll_offset.
        # Wurde noetig, weil die Zeilenanzahl inzwischen (Speicherplatz-
        # Alarm, Gaeste-WLAN, ...) ueber die urspruenglich angenommenen
        # "fuenf kurze Zeilen" hinausgewachsen ist und ohne Scrollen unten
        # aus dem Bild lief (siehe _draw_admin_status).
        self.admin_status_scroll_offset: int = 0
        self._last_rendered_state: AppState | None = None
        # NEU (Nutzer-Feedback, Screenshot "Dateien mit abweichendem Inhalt
        # gef[unden]" ragte rechts ueber den Bildschirmrand hinaus): Cache
        # fuer verkleinerte Varianten von font_title, siehe _title_font_for().
        self._title_font_cache: dict[tuple[str, int], pygame.font.Font] = {}

    def render(
        self,
        model: AppModel,
        fps: float,
        preview_frame: pygame.Surface | None = None,
        qr_surface: pygame.Surface | None = None,
        capture_progress: float | None = None,
    ) -> None:
        # Scroll-Position der Anleitung zuruecksetzen, sobald man neu in
        # diesen State wechselt (nicht bei jedem Frame innerhalb des States).
        if model.state == AppState.INSTRUCTIONS and self._last_rendered_state != AppState.INSTRUCTIONS:
            self.instructions_scroll_offset = 0
        if model.state == AppState.TERMS and self._last_rendered_state != AppState.TERMS:
            self.terms_scroll_offset = 0
        # NEU (6c): gleiches Prinzip - jeder neue Konflikt-Screen (z.B. nach
        # einem erneuten Export) beginnt oben, nicht an einer alten Position.
        if model.state == AppState.ADMIN_USB_CONFLICTS and self._last_rendered_state != AppState.ADMIN_USB_CONFLICTS:
            self.usb_conflicts_scroll_offset = 0
        # NEU (Nutzer-Feedback): gleiches Prinzip fuer die Wallpaper-
        # Auswahlliste.
        if model.state == AppState.ADMIN_EVENT_WALLPAPER_PICK and self._last_rendered_state != AppState.ADMIN_EVENT_WALLPAPER_PICK:
            self.wallpaper_pick_scroll_offset = 0
        # NEU (Nutzer-Feedback, Bugfix): jeder neue Diagnose-Aufruf beginnt
        # oben, nicht an einer alten Scroll-Position.
        if model.state == AppState.ADMIN_STATUS and self._last_rendered_state != AppState.ADMIN_STATUS:
            self.admin_status_scroll_offset = 0
        # Neuer Countdown-Durchlauf (State-Wechsel IN COUNTDOWN hinein) -
        # zufaellig ein neues "bitte laecheln"-Bild fuer diesen Durchlauf
        # ziehen, damit es bei jedem Foto wechselt statt immer gleich zu sein.
        if model.state == AppState.COUNTDOWN and self._last_rendered_state != AppState.COUNTDOWN:
            self._select_random_countdown_image()
        # NEU (Sprint-11-Nachbesserung): Startzeitpunkt fuer die kurze
        # "Pop"-Einblendung des Erfolg/Fehler-Symbols auf dem USB-Export-
        # Abschluss-Screen - siehe _draw_admin_usb_result_badge().
        if model.state == AppState.ADMIN_USB_EXPORT_DONE and self._last_rendered_state != AppState.ADMIN_USB_EXPORT_DONE:
            self._admin_usb_done_entered_at = time.time()
        self._last_rendered_state = model.state

        self.screen.fill(self._background_color(model.state))

        if model.state == AppState.MAIN_MENU:
            self._draw_main_menu_background()

        if model.state == AppState.BOOT:
            self._draw_boot_background()

        # GEAENDERT (Kamera-Menue 2.0): auf ADMIN_CAMERA_SETTINGS wird das
        # Live-Bild NICHT vollflaechig gezeichnet (dort wuerde es die
        # Einstell-Zeilen ueberdecken), sondern in einem kleineren Panel
        # innerhalb von _draw_admin_camera_settings() weiter unten.
        if preview_frame is not None and model.state != AppState.ADMIN_CAMERA_SETTINGS:
            self._draw_preview_frame(preview_frame)

        if model.state == AppState.COUNTDOWN:
            value = model.ui.countdown_value
            if value == 1:
                # Liveview aus, "bitte laecheln" einblenden, keine Text-Ausgabe mehr.
                self._draw_countdown_image()
            elif value in (5, 4, 3, 2):
                # Liveview bleibt sichtbar (s.o.), Cinema-Countdown-Grafik davor.
                self._draw_cinema_countdown(value)

        if model.state == AppState.ATTRACT_GALLERY:
            self._draw_attract_gallery(model)

        if model.state == AppState.REVIEW:
            self._draw_review_photo(model)

        if model.state == AppState.GALLERY_FULLSCREEN:
            self._draw_gallery_fullscreen(model)

        # NEU (Sprint 11, Feature 4): zeigt das Foto weiterhin im Hintergrund
        # (gleiche Zeichenmethode wie GALLERY_FULLSCREEN) und legt die
        # QR-Karte fuer genau dieses Foto darueber.
        if model.state == AppState.GALLERY_PHOTO_QR:
            self._draw_gallery_fullscreen(model)
            self._draw_gallery_photo_qr(qr_surface)

        if model.state == AppState.QR_DISPLAY:
            self._draw_save_confirmation(model)

        # NEU (Sprint 11, Feature 1): Datei-Symbol-Animation waehrend der
        # eigentlichen Uebertragung (capture_progress ist nur waehrend
        # dieser Phase gesetzt - siehe app_with_hw._capture_progress_fraction).
        # Davor (Ausloeseimpuls, kurzer Vorlauf) zeigt CAPTURE_PENDING
        # bewusst noch keine Animation, siehe _draw_capture_transfer_animation.
        if model.state == AppState.CAPTURE_PENDING and capture_progress is not None:
            self._draw_capture_transfer_animation(capture_progress)

        if model.state == AppState.INSTRUCTIONS:
            self._draw_instructions()

        if model.state == AppState.TERMS:
            self._draw_terms()

        if model.state == AppState.PIN_ENTRY:            # NEU (3.3)
            self._draw_pin_entry(model)

        if model.state == AppState.SHUTDOWN_GOODBYE:      # NEU (3.3)
            self._draw_shutdown_goodbye(model)

        if model.state == AppState.ADMIN_STATUS:          # NEU (4.3)
            self._draw_admin_status(model)

        if model.state == AppState.ADMIN_CAMERA_SETTINGS:  # NEU (Sprint 11, Feature 2)
            self._draw_admin_camera_settings(model, preview_frame)

        if model.state == AppState.ADMIN_EVENT_SETTINGS:      # NEU (Veranstaltungsdaten)
            self._draw_admin_event_settings(model)

        if model.state == AppState.ADMIN_EVENT_TEXT_ENTRY:    # NEU (Veranstaltungsdaten)
            self._draw_admin_event_text_entry(model)

        if model.state == AppState.ADMIN_EVENT_WALLPAPER_PICK_LOADING:  # NEU (Veranstaltungsdaten)
            self._draw_admin_event_wallpaper_pick_loading(model)

        if model.state == AppState.ADMIN_EVENT_WALLPAPER_PICK:  # NEU (Nutzer-Feedback)
            self._draw_admin_event_wallpaper_pick(model)

        if model.state == AppState.ADMIN_EVENT_WALLPAPER_RESULT:  # NEU (Veranstaltungsdaten)
            self._draw_admin_event_wallpaper_result(model)

        if model.state == AppState.ADMIN_EVENT_SAVED:         # NEU (Veranstaltungsdaten)
            self._draw_admin_event_saved(model)

        if model.state == AppState.ADMIN_RESTART_PENDING:  # NEU (4.3)
            self._draw_admin_restart_pending(model)

        if model.state == AppState.ADMIN_SHUTDOWN_CONFIRM:  # NEU (Sprint-11-Nachbesserung)
            self._draw_admin_shutdown_confirm(model)

        if model.state == AppState.ADMIN_RESTART_CONFIRM:  # NEU (Nutzer-Feedback)
            self._draw_admin_restart_confirm(model)

        if model.state == AppState.ADMIN_DELETE_CONFIRM:   # NEU (4.4)
            self._draw_admin_delete_confirm(model)

        if model.state == AppState.ADMIN_DELETE_RUNNING:   # NEU (4.4)
            self._draw_admin_delete_running(model)

        if model.state == AppState.ADMIN_DELETE_DONE:      # NEU (4.4)
            self._draw_admin_delete_done(model)

        if model.state in {                                # NEU (4.6)
            AppState.ADMIN_USB_WAIT, AppState.ADMIN_USB_READY,
            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_REMOVE,
        }:
            self._draw_admin_usb_lines(model)

        if model.state in {AppState.ADMIN_USB_CHECK, AppState.ADMIN_USB_EJECT}:  # NEU (4.6)
            self._draw_admin_usb_busy(model)

        if model.state == AppState.ADMIN_USB_COPY:           # NEU (4.7)
            self._draw_admin_usb_copy(model)

        # NEU (6b/6c): Aufloesungslauf (Phase 2) zeigt denselben Fortschritts-
        # balken wie der Kopierlauf - app_with_hw.py fuellt dieselben UI-
        # Felder (admin_usb_export_progress/admin_usb_progress_fraction),
        # daher genuegt die Wiederverwendung derselben Zeichenfunktion statt
        # einer Kopie.
        if model.state == AppState.ADMIN_USB_RESOLVE:
            self._draw_admin_usb_copy(model)

        if model.state == AppState.ADMIN_USB_CONFLICTS:       # NEU (6c)
            self._draw_admin_usb_conflicts(model)

        if model.state == AppState.ADMIN_USB_EXPORT_DONE:    # NEU (4.7)
            self._draw_admin_usb_lines(model)
            # NEU (Sprint-11-Nachbesserung #2): Haken/Kreuz-Ergebnissymbol,
            # siehe _draw_admin_usb_result_badge().
            self._draw_admin_usb_result_badge(model)

        # Bei Ziffer 1 (Liveview aus, "bitte laecheln") soll GAR KEIN Text
        # mehr zu sehen sein - weder Titel noch Statuszeile.
        hide_all_text = model.state == AppState.COUNTDOWN and model.ui.countdown_value == 1

        # Titel wird in der Anleitung, den Nutzungsbedingungen und bei
        # Ziffer 1 bewusst weggelassen (eigene scrollbare Textansichten,
        # die den vollen Bildschirm brauchen).
        text_screens = {
            AppState.INSTRUCTIONS, AppState.TERMS, AppState.PIN_ENTRY, AppState.SHUTDOWN_GOODBYE,
            # NEU (Sprint 11, Feature 3): zeigt seit diesem Umbau keinen
            # QR-Code mehr, nur noch einen selbst gezeichneten Hinweistext
            # (siehe _draw_save_confirmation) - Titel/Statuszeile werden
            # dort selbst gezeichnet, der generische Block soll nicht
            # zusaetzlich darueberzeichnen.
            AppState.QR_DISPLAY,
            # NEU (Sprint-11-Nachbesserung): GALLERY_PHOTO_QR zeichnet seine
            # eigene Ueberschrift ("QR-Code für dieses Foto", siehe
            # _draw_gallery_photo_qr) - der generische Fotobox-Titel oben
            # links wuerde sich sonst genau damit ueberlappen (Feedback-
            # Screenshot zeigte "150 Jahre-Feier" ueber "QR-Code für dieses
            # Foto").
            AppState.GALLERY_PHOTO_QR,
            AppState.ADMIN_MENU, AppState.ADMIN_STATUS, AppState.ADMIN_RESTART_PENDING,  # NEU (4.3)
            # GEAENDERT (Kamera-Menue 2.0): zeichnet die Ueberschrift
            # komplett selbst (siehe _draw_admin_camera_settings, gleiches
            # Prinzip wie GALLERY_PHOTO_QR oben) - der generische Titel
            # "Kamera-Einstellungen" hier ueberlappte sonst mit dem neuen
            # Live-Vorschau-Panel und der Seiten-Ueberschrift (per
            # Screenshot-Eigenpruefung gefunden).
            AppState.ADMIN_CAMERA_SETTINGS,
            AppState.ADMIN_SHUTDOWN_CONFIRM,  # NEU (Sprint-11-Nachbesserung)
            AppState.ADMIN_RESTART_CONFIRM,  # NEU (Nutzer-Feedback)
            AppState.ADMIN_DELETE_CONFIRM, AppState.ADMIN_DELETE_RUNNING,                # NEU (4.4)
            AppState.ADMIN_DELETE_DONE,                                                  # NEU (4.4)
            AppState.ADMIN_USB_WAIT, AppState.ADMIN_USB_CHECK, AppState.ADMIN_USB_READY, # NEU (4.6)
            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_EJECT, AppState.ADMIN_USB_REMOVE,
            AppState.ADMIN_USB_COPY, AppState.ADMIN_USB_EXPORT_DONE,   # NEU (4.7)
            AppState.ADMIN_USB_CONFLICTS, AppState.ADMIN_USB_RESOLVE,  # NEU (6c)
            # NEU (Veranstaltungsdaten): zeichnen ihre Ueberschrift ueber den
            # generischen status_text-Mechanismus weiter unten (gleiches
            # Prinzip wie die USB-Screens) statt eines eigenen Titel-Blocks.
            AppState.ADMIN_EVENT_SETTINGS, AppState.ADMIN_EVENT_TEXT_ENTRY,
            AppState.ADMIN_EVENT_WALLPAPER_PICK_LOADING, AppState.ADMIN_EVENT_WALLPAPER_PICK,
            AppState.ADMIN_EVENT_WALLPAPER_RESULT,
            AppState.ADMIN_EVENT_SAVED,
        }

        if model.state not in text_screens and not hide_all_text:
            self._draw_shadowed_text(self.config.screen.title, self.font_title, (255, 255, 255), (60, 60))
        elif model.state == AppState.ADMIN_MENU:
            # NEU (4.2): statt des Fotobox-Titels an gleicher Position/
            # Schrift/Farbe der Menuename - der Titel ist hier nicht der
            # passende Kontext.
            self._draw_shadowed_text("Service-Menü", self.font_title, (255, 255, 255), (60, 60))
        elif model.state == AppState.ADMIN_STATUS:
            # NEU (4.3): eigener Titel statt des Fotobox-Titels, wie ADMIN_MENU.
            self._draw_shadowed_text("Status / Diagnose", self.font_title, (255, 255, 255), (60, 60))
        elif model.state == AppState.ADMIN_DELETE_DONE:
            # NEU (4.4): Ergebnis der Loeschung.
            self._draw_shadowed_text("Löschen abgeschlossen", self.font_title, (255, 255, 255), (60, 60))
        elif model.state == AppState.ADMIN_USB_EXPORT_DONE:
            # NEU (4.7): eigener Titel statt des generischen status_text.
            self._draw_shadowed_text("Export abgeschlossen", self.font_title, (255, 255, 255), (60, 60))
        elif model.state in {
            AppState.ADMIN_USB_WAIT, AppState.ADMIN_USB_READY,
            AppState.ADMIN_USB_PROBLEM, AppState.ADMIN_USB_REMOVE,
            AppState.ADMIN_USB_CONFLICTS,   # NEU (6c): "Dateien mit abweichendem Inhalt gefunden"
            # NEU (Veranstaltungsdaten): der jeweilige Schrittname steht in
            # ui.status_text (siehe state_machine._go_admin_event_settings/
            # _go_admin_event_text_entry/_go_admin_event_wallpaper_result/
            # ADMIN_EVENT_SAVE_RESULT) - gleiches Prinzip wie bei den
            # USB-Screens oben.
            AppState.ADMIN_EVENT_SETTINGS, AppState.ADMIN_EVENT_TEXT_ENTRY,
            AppState.ADMIN_EVENT_WALLPAPER_PICK_LOADING, AppState.ADMIN_EVENT_WALLPAPER_PICK,
            AppState.ADMIN_EVENT_WALLPAPER_RESULT,
            AppState.ADMIN_EVENT_SAVED,
        }:
            # NEU (4.6): der jeweilige Schrittname steht in ui.status_text -
            # eine Ueberschrift fuer alle Screens, kein Sonderfall je Zustand.
            # NEU (Nutzer-Feedback): laengere Varianten (z.B. "Dateien mit
            # abweichendem Inhalt gefunden") ragten bei voller Titelgroesse
            # rechts ueber den Bildschirmrand hinaus - siehe
            # _title_font_for(). 60px Rand links, 40px Sicherheitsabstand
            # rechts.
            title_max_width = self.config.screen.width - 60 - 40
            title_font = self._title_font_for(model.ui.status_text, title_max_width)
            self._draw_shadowed_text(model.ui.status_text, title_font, (255, 255, 255), (60, 60))
        # ADMIN_RESTART_PENDING/ADMIN_USB_RESOLVE zeigen bewusst gar keinen
        # Titel - nur die grosse zentrierte Statuszeile.

        if self.config.features.debug_overlay:
            self._draw_text(f"Zustand: {model.state.name}", self.font_body, (220, 220, 220), (60, 180))

        # NEU (Feedback): PHOTO_PREVIEW ist nur ca. 1-2s sichtbar (danach
        # startet automatisch der Cinema-Countdown, siehe state_machine.py/
        # preview_auto_start_seconds) - der sonst kleine, linksbuendige
        # Statustext ("Bitte auf die Markierung stellen!") war auf diesem
        # fast leeren Zwischenbild schlecht lesbar. Eigener Sonderfall statt
        # des generischen Blocks unten: doppelt so gross (font_body*2 =
        # font_status_main_menu) und mittig auf dem Bildschirm statt oben
        # links.
        if model.state == AppState.PHOTO_PREVIEW and not hide_all_text:
            self._blit_center(
                model.ui.status_text, self.font_status_main_menu, (255, 220, 120),
                self.config.screen.height // 2,
            )
        elif model.state == AppState.CAPTURE_PENDING and not hide_all_text:
            # NEU (Sprint 11, Feature 1): Gaeste haben das grosse weisse
            # LED-Blitzen (Ausloesemoment) faelschlich fuer den eigentlichen
            # Aufnahmemoment gehalten und dachten, erst das nachfolgende
            # gruene Leuchten sei die Aufnahme - dabei laeuft da nur noch
            # die Uebertragung. Gleicher Sonderfall wie PHOTO_PREVIEW (grosse,
            # zentrierte Schrift statt der sonst kleinen, linksbuendigen
            # Statuszeile), oben statt mittig positioniert, damit darunter
            # noch Platz fuer die Uebertragungs-Animation bleibt (siehe
            # _draw_capture_transfer_animation).
            lines = model.ui.status_text.split("\n")
            # NEU (Sprint-11-Nachbesserung): beide Zeilen MUESSEN dieselbe
            # Schriftgroesse haben. _blit_center() verkleinert pro Aufruf
            # unabhaengig, falls eine Zeile zu breit fuer den Bildschirm
            # waere (siehe _fit_text_font) - bei der ersten, laengeren Zeile
            # ("Foto wird von der Kamera heruntergeladen") griff das bisher,
            # bei der kurzen zweiten Zeile ("und verarbeitet...") nicht, was
            # zu zwei sichtbar unterschiedlichen Schriftgroessen fuehrte.
            # Fix: einmalig ueber die LAENGSTE Zeile fitten und diese eine
            # Schriftgroesse fuer alle Zeilen verwenden.
            max_width = self.config.screen.width - 80
            fitted_font = self.font_status_main_menu
            for line in lines:
                fitted_font = self._fit_text_font(line, fitted_font, max_width)
            line_height = fitted_font.get_linesize()
            # Zone unterhalb des Fotobox-Titels (der hier - anders als bei
            # PHOTO_PREVIEW - bewusst weiter oben stehen bleibt) und
            # oberhalb der Uebertragungs-Animation (siehe
            # _draw_capture_transfer_animation), damit sich beide nie
            # ueberlappen koennen.
            zone_top = round(self.config.screen.height * 0.26)
            zone_bottom = round(self.config.screen.height * 0.52)
            total_height = len(lines) * line_height
            top = zone_top + max(0, (zone_bottom - zone_top - total_height) // 2)
            for i, line in enumerate(lines):
                surf = fitted_font.render(line, True, (150, 255, 150))
                rect = surf.get_rect(
                    center=(self.config.screen.width // 2, top + i * line_height + line_height // 2)
                )
                self.screen.blit(surf, rect)
        elif model.state not in text_screens and not hide_all_text:
            # Im Hauptmenue liegt der Text auf dem Hintergrundbild - Anthrazit statt
            # dem sonst ueblichen Amber, da Amber auf dem Bild schlecht lesbar war.
            status_color = (40, 40, 45) if model.state == AppState.MAIN_MENU else (255, 220, 120)
            status_font = self.font_status_main_menu if model.state == AppState.MAIN_MENU else self.font_body
            if model.state == AppState.MAIN_MENU:
                # NEU (Lesbarkeit): der Willkommenstext ("Lass dich zur
                # Erinnerung an die Veranstaltung fotografieren!") ist bei
                # der um 50% vergroesserten Schrift nur noch knapp schmaler
                # als der Bildschirm - eine automatische Verkleinerung
                # (gleiche Technik wie bei Button-Labels in _draw_button())
                # verhindert ein Abschneiden am rechten Rand, falls die
                # Systemschrift auf der eingesetzten Hardware geringfuegig
                # breiter rendert als hier getestet. Die Kartenpolsterung
                # (2x card_padding_x) wird beim Fitting mit beruecksichtigt,
                # damit die fertige Karte nicht ueber den Bildschirmrand
                # hinausragt.
                card_padding_x = 40
                status_font = self._fit_text_font(
                    model.ui.status_text, status_font, self.config.screen.width - 60 - 40 - 2 * card_padding_x,
                )
                # NEU (Nutzer-Feedback, Lesbarkeit): der Begruessungstext lag
                # bisher direkt auf dem (teils unruhigen) Eventfoto im
                # Hintergrund - eine weisse, abgerundete Karte mit Schatten
                # (siehe _draw_text_card, gleiche Optik wie Titel/Buttons)
                # sorgt jetzt fuer verlaesslichen Kontrast unabhaengig vom
                # jeweiligen Foto.
                self._draw_text_card(
                    model.ui.status_text, status_font, status_color,
                    (self.config.screen.width // 2, 270), padding_x=card_padding_x,
                )
            else:
                self._draw_text(model.ui.status_text, status_font, status_color, (60, 240))

        if model.ui.error_text and model.state not in text_screens:
            self._draw_text(model.ui.error_text, self.font_body, (255, 120, 120), (60, 320))

        # NEU (Speicherplatz-Alarm): laufender Y-Offset, damit sich der
        # Event-Konfig-Hinweis und die Speicherplatz-Warnung (koennen
        # gleichzeitig zutreffen) nicht gegenseitig ueberdecken, sondern
        # sauber untereinander stehen.
        banner_y = 0
        if model.state == AppState.MAIN_MENU and self.config.needs_event_setup:
            # NEU (Etappe 8, Feedback): dezenter Hinweisstreifen ganz oben -
            # eigene halbtransparente Leiste statt einer weiteren Textzeile
            # im ohnehin schon belegten status_text/error_text-Bereich, und
            # unabhaengig vom Hintergrundbild lesbar. Gedacht fuer
            # Entwickler/neue GitHub-Nutzer, die die Box frisch aufsetzen -
            # verschwindet automatisch, sobald data/event_config.json echte
            # Werte enthaelt. Bewusst kein Alarm-Look (kein Rot, kein
            # Blinken) und keine Bedienungssperre - die Box funktioniert mit
            # Standardwerten technisch einwandfrei, nur eben noch nicht
            # event-spezifisch konfiguriert.
            banner_height = 34
            banner = pygame.Surface((self.config.screen.width, banner_height), pygame.SRCALPHA)
            banner.fill((0, 0, 0, 165))
            self.screen.blit(banner, (0, banner_y))
            self._draw_text(
                "Hinweis: Standardkonfiguration aktiv - data/event_config.json anpassen",
                self.font_small, (255, 210, 120), (16, banner_y + 6),
            )
            banner_y += banner_height

        if model.state == AppState.MAIN_MENU and model.ui.storage_alarm_level == 1:
            # NEU (Speicherplatz-Alarm) Stufe 1: farbiger Hinweistext, wie
            # gewuenscht NUR im Hauptmenue (nicht in GALLERY_EMPTY - dort
            # ist noch kein Foto gemacht worden, das Problem ist dort noch
            # nicht dringend). Kein Blinken, keine Sperre - die greift erst
            # ab Stufe 2 (siehe state_machine.py).
            banner_height = 34
            banner = pygame.Surface((self.config.screen.width, banner_height), pygame.SRCALPHA)
            banner.fill((60, 30, 0, 180))
            self.screen.blit(banner, (0, banner_y))
            self._draw_text(
                f"Speicherplatz wird knapp: noch {model.ui.storage_free_percent:.0f}% frei",
                self.font_small, (255, 180, 80), (16, banner_y + 6),
            )
            banner_y += banner_height

        if model.state == AppState.GALLERY_GRID:
            self._draw_gallery_grid(model)

        if model.state == AppState.GALLERY_EMPTY:            # NEU (Etappe 7)
            self._draw_gallery_empty(model)

        self._draw_buttons(model.state)
        self._draw_footer(model, fps)

        if model.state in {AppState.MAIN_MENU, AppState.GALLERY_EMPTY} and model.ui.storage_alarm_level >= 2:
            # NEU (Speicherplatz-Alarm) Stufe 2: sehr auffaellig, wie
            # gewuenscht - blinkender roter Rahmen um den ganzen Bildschirm
            # plus deutlicher Text. Bewusst NUR auf diesen beiden Screens
            # (dort, wo die Aufnahme-Sperre in state_machine.py tatsaechlich
            # greift), nicht global. Bewusst als LETZTES gezeichnet (nach
            # Buttons/Footer), damit der Alarm garantiert ueber allem
            # anderen liegt und nie versehentlich verdeckt werden kann.
            self._draw_storage_critical_overlay()

        pygame.display.flip()

    def _draw_preview_frame(self, preview_frame: pygame.Surface) -> None:
        """Kamera-Livebild als Hintergrund einblenden, skaliert auf Bildschirmgröße."""
        target_size = (self.config.screen.width, self.config.screen.height)
        if preview_frame.get_size() != target_size:
            preview_frame = pygame.transform.smoothscale(preview_frame, target_size)
        self.screen.blit(preview_frame, (0, 0))

    def _draw_gallery_grid(self, model: AppModel) -> None:
        photos = model.session.photos
        width = self.config.screen.width
        height = self.config.screen.height
        self.gallery_thumbnail_hitboxes: list[tuple[pygame.Rect, int]] = []

        if not photos:
            hint = f"Keine Fotos gefunden in: {self.config.photo_dir}"
            self._draw_text(hint, self.font_body, (200, 200, 200), (60, round(0.30 * height)))
            return

        columns = max(1, self.config.gallery.grid_columns)
        margin = round(0.06 * width)
        gap = round(0.03 * width)
        top = round(0.30 * height)
        bottom = round(0.77 * height)

        available_w = width - 2 * margin - (columns - 1) * gap
        cell_w = max(20, available_w // columns)
        thumb_w, thumb_h = self.config.gallery.thumbnail_size
        cell_h = max(20, round(cell_w * (thumb_h / thumb_w)))

        total_rows = max(1, (len(photos) + columns - 1) // columns)
        scroll_row = min(model.ui.gallery_scroll_offset, max(0, total_rows - 1))
        start_index = scroll_row * columns
        visible_photos = photos[start_index:]

        x, y, col = margin, top, 0
        for offset, path in enumerate(visible_photos):
            if y + cell_h > bottom:
                break
            cell_rect = pygame.Rect(x, y, cell_w, cell_h)
            surface = self._get_thumbnail_surface(path, (cell_w, cell_h))
            if surface is not None:
                self.screen.blit(surface, (x, y))
            else:
                pygame.draw.rect(self.screen, (60, 60, 60), (x, y, cell_w, cell_h))
                pygame.draw.rect(self.screen, (150, 60, 60), (x, y, cell_w, cell_h), width=2)
            self.gallery_thumbnail_hitboxes.append((cell_rect, start_index + offset))
            col += 1
            x += cell_w + gap
            if col >= columns:
                col = 0
                x = margin
                y += cell_h + gap

    def _draw_gallery_empty(self, model: AppModel) -> None:
        """NEU (Etappe 7): GALLERY_GRID wurde angetippt, aber es gibt noch
        keine Fotos. Ersetzt den vorherigen technischen Pfad-Hinweis
        ("Keine Fotos gefunden in: /home/...") durch eine einladende,
        gastfreundliche Nachricht mit direktem Weg zum ersten Foto."""
        height = self.config.screen.height
        first_cy = round(0.42 * height)
        # NEU (Lesbarkeit): Abstand zur zweiten Zeile wird aus den
        # tatsaechlichen Zeilenhoehen beider Schriftarten berechnet statt
        # eines festen Pixelwerts - ein fixer Abstand ginge von einer
        # bestimmten Schriftgroesse aus und wuerde bei Aenderungen daran
        # (siehe font_gallery_empty_title) leicht wieder zur Ueberlappung
        # der beiden Zeilen fuehren.
        # NEU (Feedback): Abstand von 20 auf 60px vergroessert - wirkte zu
        # gedrungen/eng an der Ueberschrift dran.
        second_cy = first_cy + self.font_gallery_empty_title.get_linesize() // 2 + self.font_body.get_linesize() // 2 + 60
        self._blit_center(
            "Noch keine Fotos vorhanden!", self.font_gallery_empty_title, (210, 235, 225),
            first_cy,
        )
        self._blit_center(
            "Sei die/der Erste - mach jetzt ein Foto!", self.font_body, (190, 190, 195),
            second_cy,
        )

    def _draw_storage_critical_overlay(self) -> None:
        """NEU (Speicherplatz-Alarm) Stufe 2: sehr auffaelliges Warnsignal -
        dicker, blinkender roter Rahmen um den gesamten Bildschirm plus
        deutlicher Text. Blinkt unabhaengig vom LED-Ring (eigener
        Zeittakt hier), aber in aehnlicher Frequenz wie LedEffect.ERROR
        (5 Hz) - Bildschirm und Ring wirken dadurch synchron, ohne dass
        beide Systeme sich denselben Takt teilen muessten."""
        width, height = self.config.screen.width, self.config.screen.height
        on = int(time.time() * 5) % 2 == 0
        if on:
            border_width = 14
            color = (220, 0, 0)
            pygame.draw.rect(self.screen, color, pygame.Rect(0, 0, width, border_width))
            pygame.draw.rect(self.screen, color, pygame.Rect(0, height - border_width, width, border_width))
            pygame.draw.rect(self.screen, color, pygame.Rect(0, 0, border_width, height))
            pygame.draw.rect(self.screen, color, pygame.Rect(width - border_width, 0, border_width, height))
        text_color = (255, 60, 60) if on else (120, 20, 20)
        self._blit_center(
            "ACHTUNG: Bitte Techniker rufen!", self.font_status_main_menu, text_color,
            round(0.70 * height),
        )

    def _get_thumbnail_surface(self, path: str, size: tuple[int, int]) -> pygame.Surface | None:
        target_w, target_h = size
        is_fullscreen = size == (self.config.screen.width, self.config.screen.height)
        cache = self._fullscreen_cache if is_fullscreen else self._thumbnail_cache
        max_items = (
            self.config.gallery.max_fullscreen_cache_items
            if is_fullscreen
            else self.config.gallery.max_thumbnail_cache_items
        )

        cache_key = f"{path}:{size[0]}x{size[1]}"
        cached = cache.get(cache_key)
        if cached is not None:
            cache.move_to_end(cache_key)  # als zuletzt benutzt markieren
            return cached
        try:
            with PILImage.open(path) as im:
                # draft() nutzt die in JPEG eingebaute Stufen-Skalierung von
                # libjpeg (1/2, 1/4, 1/8) und dekodiert direkt in niedriger
                # Aufloesung, statt erst das komplette ~24-Megapixel-Foto der
                # D3300 zu dekodieren und danach zu verkleinern. Das ist der
                # Grund, warum das erste Anzeigen jedes Fotos in der Galerie
                # spuerbar geruckelt hat.
                im.draft("RGB", (target_w * 2, target_h * 2))
                im = im.convert("RGB")
                img_w, img_h = im.size
                scale = min(target_w / img_w, target_h / img_h)
                scaled_w = max(1, round(img_w * scale))
                scaled_h = max(1, round(img_h * scale))
                im = im.resize((scaled_w, scaled_h), PILImage.BILINEAR)
                scaled_image = pygame.image.fromstring(im.tobytes(), im.size, "RGB")
        except (FileNotFoundError, OSError):
            return None
        canvas = pygame.Surface(size)
        canvas.fill((25, 25, 30))
        canvas.blit(scaled_image, ((target_w - scaled_w) // 2, (target_h - scaled_h) // 2))
        cache[cache_key] = canvas
        if len(cache) > max_items:
            cache.popitem(last=False)  # aeltesten (am laengsten ungenutzten) Eintrag verwerfen
        return canvas

    def _draw_cinema_countdown(self, value: int) -> None:
        """
        Vom klassischen Kino-Countdown-Vorspann inspirierte, eigenstaendig
        gezeichnete Grafik (Kreis, Fadenkreuz, rotierender Wisch-Zeiger,
        grosse zentrierte Ziffer) - kein Bild/Video-Asset, alles per pygame
        gezeichnet. Wird halbtransparent ueber dem weiterlaufenden Liveview
        eingeblendet (nur fuer die Ziffern 5, 4, 3, 2 - bei 1 uebernimmt
        _draw_countdown_image() mit einem zufaellig gewaehlten "bitte
        laecheln"-Bild aus dem Pool).

        Farbwahl bewusst schwarz auf hellgrau (nicht reinweiss): grenzt sich
        so vom reinweissen Blitzen bei Ziffer 1 ab und bleibt auch auf einem
        hellen Liveview-Hintergrund gut lesbar.
        """
        width, height = self.config.screen.width, self.config.screen.height
        # NEU (Feedback): von 0.44 auf 0.40 angehoben - schafft mehr Abstand
        # zwischen Kreis-Unterkante und dem "Abbrechen"-Button (layout.right,
        # y=0.80*height) fuer den Hinweistext darunter, der sonst mit dem
        # Button kollidierte.
        cx, cy = width // 2, round(height * 0.40)
        radius = round(min(width, height) * 0.30)

        pad = 12
        size = radius * 2 + pad * 2
        overlay = pygame.Surface((size, size), pygame.SRCALPHA)
        ocx, ocy = size // 2, size // 2

        # Heller Kreisgrund - hellgrau statt reinweiss (siehe Docstring),
        # leicht transparent, damit das Liveview minimal durchscheint.
        pygame.draw.circle(overlay, (222, 222, 222, 235), (ocx, ocy), radius)
        pygame.draw.circle(overlay, (30, 30, 30, 255), (ocx, ocy), radius, width=5)

        # Rotierender Wisch-Zeiger (klassische Kino-Countdown-Optik) - eine
        # volle Umdrehung pro Sekunde (360 Grad/s), laeuft kontinuierlich
        # mit. War vorher 90 Grad/s (= 4s pro Umdrehung); 360 Grad/s ist
        # die 4-fache Geschwindigkeit, also 1s pro Umdrehung.
        angle = (time.monotonic() * 360.0) % 360.0
        wedge_points = [(ocx, ocy)]
        for step in range(0, 46, 5):
            rad = math.radians(angle + step)
            wedge_points.append((ocx + radius * math.sin(rad), ocy - radius * math.cos(rad)))
        pygame.draw.polygon(overlay, (255, 255, 255, 110), wedge_points)
        pygame.draw.circle(overlay, (30, 30, 30, 255), (ocx, ocy), radius, width=5)

        # Fadenkreuz durch die Kreismitte
        pygame.draw.line(overlay, (30, 30, 30, 255), (ocx - radius, ocy), (ocx + radius, ocy), 3)
        pygame.draw.line(overlay, (30, 30, 30, 255), (ocx, ocy - radius), (ocx, ocy + radius), 3)

        # Grosse, zentrierte Ziffer - schwarz auf hellgrauem Kreisgrund
        digit_surf = self.font_countdown_digit.render(str(value), True, (15, 15, 15))
        digit_rect = digit_surf.get_rect(center=(ocx, ocy))
        overlay.blit(digit_surf, digit_rect)

        self.screen.blit(overlay, (cx - ocx, cy - ocy))

        # NEU (Feedback): Auf dem laufenden Kamera-Liveview (wechselnder,
        # teils heller Hintergrund) war reines Weiss schlecht lesbar und
        # kollidierte ausserdem mit dem "Abbrechen"-Button darunter
        # (layout.right). Jetzt mit schwarzer Kontur (wie Untertitel -
        # bleibt unabhaengig von der Liveview-Helligkeit lesbar) und mittig
        # im tatsaechlich verfuegbaren Abstand zwischen Kreis-Unterkante und
        # Button platziert statt eines festen Pixel-Versatzes.
        hint = "Bitte auf die Markierung stellen."
        gap_top = cy + radius
        button_top = self.layout.right.y
        hint_cy = (gap_top + button_top) // 2
        self._draw_text_outlined(hint, self.font_body, (255, 255, 255), (10, 10, 10), (width // 2, hint_cy))

    def _draw_countdown_image(self) -> None:
        if self._current_countdown_image is not None:
            self.screen.blit(self._current_countdown_image, (0, 0))

    def _select_random_countdown_image(self) -> None:
        """Zieht zufaellig ein Bild aus dem Pool fuer den jetzt startenden
        Countdown-Durchlauf. Wird einmal pro Durchlauf aufgerufen (siehe
        render()), nicht bei jedem Frame - der Wechsel soll erst beim
        naechsten Foto wieder stattfinden, nicht waehrend Ziffer 5..1."""
        pool = self._load_countdown_image_pool()
        self._current_countdown_image = random.choice(pool) if pool else None

    def _load_countdown_image_pool(self) -> list[pygame.Surface]:
        """Laedt und skaliert alle 15 "bitte_laecheln_XX.png"-Varianten
        (bitte_laecheln_01.png .. bitte_laecheln_15.png) einmalig und
        haelt sie fertig skaliert im Speicher - danach nur noch Auswahl
        per random.choice(), kein wiederholtes Laden/Skalieren."""
        if self._countdown_image_pool is not None:
            return self._countdown_image_pool

        target_w, target_h = self.config.screen.width, self.config.screen.height
        bg_color = self._background_color(AppState.COUNTDOWN)
        pool: list[pygame.Surface] = []
        for i in range(1, 16):
            path = self.config.assets_dir / f"bitte_laecheln_{i:02d}.png"
            try:
                raw = pygame.image.load(str(path)).convert_alpha()
            except (pygame.error, FileNotFoundError):
                print(f"[Renderer] Countdown-Bild nicht gefunden: {path}")
                continue

            img_w, img_h = raw.get_size()
            scale = min(target_w / img_w, target_h / img_h)
            scaled = pygame.transform.smoothscale(
                raw, (max(1, round(img_w * scale)), max(1, round(img_h * scale)))
            )
            canvas = pygame.Surface((target_w, target_h))
            canvas.fill(bg_color)
            canvas.blit(scaled, ((target_w - scaled.get_width()) // 2, (target_h - scaled.get_height()) // 2))
            pool.append(canvas)

        if not pool:
            print("[Renderer] Kein einziges bitte_laecheln_XX.png gefunden - es wird kein Bild angezeigt.")

        self._countdown_image_pool = pool
        return pool

    def _draw_main_menu_background(self) -> None:
        """Jubilaeums-Wallpaper als Hintergrund des Hauptmenues.

        Bild wird im "cover"-Modus skaliert (Bildschirm wird komplett
        ausgefuellt, ueberstehender Rand wird beschnitten) statt wie beim
        Countdown-Bild eingepasst mit Rand - hier soll es als vollflaechiger
        Hintergrund wirken, nicht als einzelnes zentriertes Motiv.
        Darueber liegt ein leichter dunkler Verlauf oben, damit Titel und
        Statuszeile (weisser Text) auf dem hellen Motiv lesbar bleiben.
        """
        image = self._get_main_menu_background()
        if image is None:
            return
        self.screen.blit(image, (0, 0))

        width = self.config.screen.width
        fade_height = round(self.config.screen.height * 0.30)
        overlay = pygame.Surface((width, fade_height), pygame.SRCALPHA)
        max_alpha = 130
        for y in range(fade_height):
            alpha = round(max_alpha * (1 - y / fade_height))
            pygame.draw.line(overlay, (0, 0, 0, alpha), (0, y), (width, y))
        self.screen.blit(overlay, (0, 0))

    def _get_main_menu_background(self) -> pygame.Surface | None:
        if self._main_menu_background is False:
            return None
        if self._main_menu_background is not None:
            return self._main_menu_background  # type: ignore[return-value]

        path = self.config.assets_dir / "hauptmenu_wallpaper.png"
        try:
            raw = pygame.image.load(str(path)).convert()
        except (pygame.error, FileNotFoundError):
            print(f"[Renderer] Hauptmenue-Hintergrundbild nicht gefunden: {path}")
            self._main_menu_background = False
            return None

        target_w, target_h = self.config.screen.width, self.config.screen.height
        img_w, img_h = raw.get_size()
        # "Cover"-Skalierung: groesserer der beiden Skalierungsfaktoren, damit
        # keine Raender frei bleiben - ueberstehender Teil wird beschnitten.
        scale = max(target_w / img_w, target_h / img_h)
        scaled_w, scaled_h = max(1, round(img_w * scale)), max(1, round(img_h * scale))
        scaled = pygame.transform.smoothscale(raw, (scaled_w, scaled_h))

        canvas = pygame.Surface((target_w, target_h))
        offset_x = (target_w - scaled_w) // 2
        offset_y = (target_h - scaled_h) // 2
        canvas.blit(scaled, (offset_x, offset_y))
        self._main_menu_background = canvas
        return canvas

    def invalidate_main_menu_background(self) -> None:
        """NEU (Veranstaltungsdaten): wirft den gecachten Hauptmenue-
        Hintergrund weg, damit ein frisch von USB importiertes Wallpaper
        ohne Neustart der App erscheint (naechster _draw_main_menu_background-
        Aufruf laedt automatisch neu von Platte, siehe
        _get_main_menu_background). Wird von app_with_hw._emit_due_timers
        nach einem erfolgreichen Wallpaper-Import aufgerufen. Alle anderen
        Veranstaltungsdaten (Titel/Praefix/WLAN/Schalter) wirken bewusst
        erst nach einem Neustart - AppConfig ist ein frozen Dataclass ohne
        Live-Reload, siehe config.py."""
        self._main_menu_background = None

    def _draw_boot_background(self) -> None:
        """Wallpaper waehrend des Systemstarts (AppState.BOOT).

        Analog zum Hauptmenue-Hintergrund im "cover"-Modus skaliert
        (Bildschirm komplett ausgefuellt, ueberstehender Rand
        beschnitten), mit demselben leichten dunklen Verlauf oben, damit
        Titel und Statuszeile ("System startet...") lesbar bleiben. Der
        BOOT-State dauert mindestens config.timeouts.boot_seconds (siehe
        config.py) - so lange bleibt dieses Bild sichtbar.
        """
        image = self._get_boot_background()
        if image is None:
            return
        self.screen.blit(image, (0, 0))

        width = self.config.screen.width
        fade_height = round(self.config.screen.height * 0.30)
        overlay = pygame.Surface((width, fade_height), pygame.SRCALPHA)
        max_alpha = 130
        for y in range(fade_height):
            alpha = round(max_alpha * (1 - y / fade_height))
            pygame.draw.line(overlay, (0, 0, 0, alpha), (0, y), (width, y))
        self.screen.blit(overlay, (0, 0))

    def _get_boot_background(self) -> pygame.Surface | None:
        if self._boot_background is False:
            return None
        if self._boot_background is not None:
            return self._boot_background  # type: ignore[return-value]

        path = self.config.assets_dir / "systemstart_wallpaper.png"
        try:
            raw = pygame.image.load(str(path)).convert()
        except (pygame.error, FileNotFoundError):
            print(f"[Renderer] Systemstart-Hintergrundbild nicht gefunden: {path}")
            self._boot_background = False
            return None

        target_w, target_h = self.config.screen.width, self.config.screen.height
        img_w, img_h = raw.get_size()
        # "Cover"-Skalierung: groesserer der beiden Skalierungsfaktoren, damit
        # keine Raender frei bleiben - ueberstehender Teil wird beschnitten.
        scale = max(target_w / img_w, target_h / img_h)
        scaled_w, scaled_h = max(1, round(img_w * scale)), max(1, round(img_h * scale))
        scaled = pygame.transform.smoothscale(raw, (scaled_w, scaled_h))

        canvas = pygame.Surface((target_w, target_h))
        offset_x = (target_w - scaled_w) // 2
        offset_y = (target_h - scaled_h) // 2
        canvas.blit(scaled, (offset_x, offset_y))
        self._boot_background = canvas
        return canvas

    def _draw_review_photo(self, model: AppModel) -> None:
        path = model.session.current_photo_path
        if not path:
            return
        width, height = self.config.screen.width, self.config.screen.height
        image = self._get_thumbnail_surface(path, (width, height))
        if image is not None:
            self.screen.blit(image, (0, 0))

    def _draw_gallery_fullscreen(self, model: AppModel) -> None:
        index = model.ui.selected_gallery_index
        photos = model.session.photos
        if index is None or not (0 <= index < len(photos)):
            return
        width, height = self.config.screen.width, self.config.screen.height
        image = self._get_thumbnail_surface(photos[index], (width, height))
        if image is not None:
            self.screen.blit(image, (0, 0))

    def _draw_attract_gallery(self, model: AppModel) -> None:
        photos = model.session.photos
        width, height = self.config.screen.width, self.config.screen.height
        if not photos:
            # NEU (Feedback): solange noch keine echten Fotos existieren,
            # die drei Beispielbilder als Fallback einfliegen lassen, statt
            # nur den Hinweistext zu zeigen - macht den Attract-Modus schon
            # vor dem ersten echten Foto lebendig. Die Beispielbilder sind
            # bewusst NICHT Teil von session.photos (siehe
            # config.gallery.excluded_filenames) - sie zaehlen nicht als
            # "echte" Fotos und duerfen die GALLERY_EMPTY-Erkennung
            # (state_machine.py) nicht verhindern; hier im Attract-Modus
            # werden sie als reiner Anzeige-Fallback direkt herangezogen.
            example_paths = tuple(
                str(self.config.photo_dir / name)
                for name in self.config.gallery.example_fly_in_filenames
                if (self.config.photo_dir / name).exists()
            )
            if not example_paths:
                self._draw_text("Noch keine Fotos vorhanden.", self.font_body, (200, 200, 200), (60, round(0.4 * height)))
                return
            photos = example_paths

        slot_seconds = 5.0
        fly_seconds = 0.6
        # Wahrscheinlichkeit pro Slot, dass statt eines Kundenfotos das
        # Systemstart-Wallpaper als "Werbung" eingeflogen wird - sorgt fuer
        # ein gelegentliches, aber nicht zu haeufiges Einstreuen.
        ad_probability = 0.2
        now = time.monotonic()
        slot = int(now // slot_seconds)
        t = now % slot_seconds

        # Ein auf den Slot geseedeter RNG: sowohl die Werbung-ja/nein-
        # Entscheidung als auch die Flugrichtung bleiben so fuer den
        # gesamten Slot stabil (kein Neu-Wuerfeln/Flackern bei jedem
        # Frame), aendern sich aber deterministisch mit jedem neuen Slot -
        # exakt wie bisher schon bei der Flugrichtung gehandhabt.
        slot_rng = random.Random(slot)
        show_ad = slot_rng.random() < ad_probability
        direction = slot_rng.choice(("left", "right", "top", "bottom"))

        image = None
        if show_ad:
            image = self._get_boot_background()
            if image is None:
                show_ad = False  # Fallback: kein Wallpaper vorhanden - normales Foto zeigen
        if not show_ad:
            index = slot % len(photos)
            image = self._get_thumbnail_surface(photos[index], (width, height))
        if image is None:
            return

        if t < fly_seconds:
            progress = t / fly_seconds
            eased = 1 - (1 - progress) ** 3  # ease-out: schnell rein, sanft einrasten
        else:
            eased = 1.0

        x, y = 0, 0
        if direction == "left":
            x = round(-width * (1 - eased))
        elif direction == "right":
            x = round(width * (1 - eased))
        elif direction == "top":
            y = round(-height * (1 - eased))
        else:
            y = round(height * (1 - eased))

        self.screen.blit(image, (x, y))

    def _draw_capture_transfer_animation(self, progress: float) -> None:
        """NEU (Sprint 11, Feature 1): waehrend der Bilduebertragung wandert
        ein kleines Datei-Symbol vom Kamera-Symbol (links) zum Raspi-
        Speicher-Symbol (rechts) - synchron zum wandernden LED-Punkt
        (hw_led_provider._render_capture_transfer, dieselbe `progress`-
        Herkunft: app_with_hw._capture_progress_fraction, gespeist aus der
        in capture_timing.py persistierten Zeitschaetzung).

        Kamera- und Speicher-Symbol sind weiterhin mit pygame-Bordmitteln
        gezeichnet (keine neuen Bild-Assets noetig) - gleicher Stil wie die
        Cinema-Countdown-Grafik (_draw_cinema_countdown) und der
        Speicheralarm-Rahmen (_draw_storage_critical_overlay). Das
        fliegende Datei-Symbol selbst nutzt seit der Sprint-11-
        Nachbesserung eines der echten file_icon_XX.png-Fotos, siehe
        _draw_file_icon()."""
        progress = max(0.0, min(1.0, progress))
        width, height = self.config.screen.width, self.config.screen.height
        cy = round(height * 0.66)
        camera_x = round(width * 0.14)
        storage_x = round(width * 0.86)

        # Duenne gepunktete Verbindungslinie zwischen den beiden Symbolen.
        dot_gap = 18
        x = camera_x + 40
        while x < storage_x - 40:
            pygame.draw.circle(self.screen, (90, 140, 90), (x, cy), 2)
            x += dot_gap

        self._draw_camera_icon((camera_x, cy))
        self._draw_storage_icon((storage_x, cy))

        # Smoothstep statt linear - sanftes Anlaufen/Abbremsen statt
        # eines abrupten Starts/Stopps.
        eased = progress * progress * (3.0 - 2.0 * progress)
        file_x = round(camera_x + eased * (storage_x - camera_x))
        # Leichter Bogen nach oben waehrend der Bewegung ("huepft" ein
        # Stueck), steht an beiden Enden still auf der Mittellinie.
        bounce = round(-22 * math.sin(eased * math.pi))
        key = self._capture_transfer_icon_key(progress)
        self._draw_file_icon((file_x, cy + bounce), key=key)

    def _draw_camera_icon(self, center: tuple[int, int]) -> None:
        """Stark vereinfachtes Kamera-Symbol (Gehaeuse + Objektiv-Kreis)."""
        cx, cy = center
        body = pygame.Rect(0, 0, 64, 44)
        body.center = (cx, cy + 4)
        pygame.draw.rect(self.screen, (210, 215, 220), body, border_radius=6)
        # Sucher-Buckel oben
        bump = pygame.Rect(0, 0, 22, 12)
        bump.midbottom = (cx - 10, body.top + 2)
        pygame.draw.rect(self.screen, (210, 215, 220), bump, border_radius=3)
        # Objektiv
        pygame.draw.circle(self.screen, (40, 40, 45), (cx + 6, cy + 4), 15)
        pygame.draw.circle(self.screen, (140, 190, 230), (cx + 6, cy + 4), 9)

    def _draw_storage_icon(self, center: tuple[int, int]) -> None:
        """Stark vereinfachtes Speicherkarten-Symbol (microSD-Silhouette mit
        abgeschraegter Ecke + Kontakt-Striche) fuer den Raspi-Speicher."""
        cx, cy = center
        w, h = 46, 60
        rect = pygame.Rect(0, 0, w, h)
        rect.center = (cx, cy)
        points = [
            (rect.left + 14, rect.top), (rect.right, rect.top),
            (rect.right, rect.bottom), (rect.left, rect.bottom),
            (rect.left, rect.top + 14),
        ]
        pygame.draw.polygon(self.screen, (230, 200, 90), points)
        pygame.draw.polygon(self.screen, (120, 100, 30), points, width=2)
        for i in range(4):
            contact_x = rect.left + 8 + i * 9
            pygame.draw.line(
                self.screen, (120, 100, 30),
                (contact_x, rect.top + 20), (contact_x, rect.top + 34), 2,
            )

    def _draw_usb_stick_icon(self, center: tuple[int, int]) -> None:
        """Stark vereinfachtes USB-Stick-Symbol (silberner Stecker oben,
        oranger Koerper darunter) - Farb-/Formsprache angelehnt an das vom
        Nutzer im Refinement mitgeschickte Beispielbild, aber im selben
        reduzierten Icon-Stil wie die uebrigen handgezeichneten Symbole
        dieser Datei (_draw_storage_icon, _draw_camera_icon).

        GEAENDERT (Nutzer-Feedback): schmaler und weniger rund als die
        erste Fassung (Koerper 46px breit, border_radius 12) - wirkte im
        Vergleich zum Beispielbild zu "breit"/blobby. Koerper und Stecker
        sind jetzt schlanker (34px breit) und weniger stark abgerundet
        (border_radius 8/3), naeher an den Proportionen des Beispielbilds."""
        cx, cy = center
        body = pygame.Rect(0, 0, 34, 58)
        body.center = (cx, cy + 12)
        connector = pygame.Rect(0, 0, 16, 22)
        connector.midbottom = (cx, body.top + 6)

        pygame.draw.rect(self.screen, (250, 165, 15), body, border_radius=8)
        pygame.draw.rect(self.screen, (200, 120, 0), body, width=2, border_radius=8)

        pygame.draw.rect(self.screen, (210, 215, 220), connector, border_radius=2)
        pygame.draw.rect(self.screen, (140, 145, 150), connector, width=2, border_radius=2)
        for dx in (-4, 4):
            contact = pygame.Rect(0, 0, 3, 7)
            contact.center = (cx + dx, connector.top + 7)
            pygame.draw.rect(self.screen, (60, 60, 65), contact)

    # NEU (Sprint-11-Nachbesserung): Groesse, in der die echten
    # file_icon_XX.png-Fotos auf dem Bildschirm dargestellt werden - naeher
    # am Original-Seitenverhaeltnis (330:439 ≈ 0.75) als das alte
    # handgezeichnete Symbol (34x42) und etwas groesser, weil Portrait +
    # "JPG"-Schriftzug bei sehr kleiner Darstellung sonst kaum noch lesbar
    # waeren.
    _FILE_ICON_SIZE = (46, 61)
    # NEU (Sprint-11-Nachbesserung): deutlich groessere Darstellung fuer die
    # Pruefsummen-Vergleichsanimation (_draw_admin_usb_verify_animation) -
    # dort stehen nur zwei Symbole im Mittelpunkt der Aufmerksamkeit statt
    # mehrerer kleiner "fliegender" Symbole, daher duerfen/sollen sie
    # deutlich groesser sein. Gleiches Seitenverhaeltnis wie _FILE_ICON_SIZE.
    _FILE_ICON_COMPARE_SIZE = (96, 128)

    def _draw_file_icon(
        self,
        center: tuple[int, int],
        key: object = None,
        alpha: int = 255,
        size: tuple[int, int] | None = None,
    ) -> None:
        """Zeichnet ein Bilddatei-Symbol bei `center` - bevorzugt eines der
        19 vom Nutzer bereitgestellten Fotos (assets/file_icon_01.png ..
        _19.png, siehe _load_file_icon_pool()) statt des urspruenglichen
        handgezeichneten Platzhalters. `key` waehlt dabei STABIL statt rein
        zufaellig aus (z.B. Lane+Zyklus-Nummer einer Animation) - derselbe
        `key` liefert innerhalb eines Aufrufs immer dasselbe Bild, damit ein
        einzelnes "fliegendes" Symbol waehrend seiner gesamten Bewegung
        nicht bei jedem Frame das Bild wechselt. Ohne `key` (None) wird pro
        Aufruf zufaellig gezogen. Ist kein einziges file_icon_XX.png
        vorhanden (z.B. Testumgebung ohne assets/), faellt die Methode auf
        das alte handgezeichnete Symbol zurueck (_draw_file_icon_vector).

        `alpha` (0..255) blendet das Symbol zusaetzlich ein-/aus - genutzt
        von _draw_admin_usb_transfer_animation, damit ein ankommendes
        Symbol beim USB-Stick nicht sichtbar "einfriert", sondern kurz vor
        dem Ziel weich verblasst statt starr darauf liegen zu bleiben.

        `size` waehlt einen alternativen, groesser/kleiner vorskalierten
        Bild-Pool (siehe _load_file_icon_pool) - Standard ist
        _FILE_ICON_SIZE, die Pruefsummen-Vergleichsanimation nutzt das
        deutlich groessere _FILE_ICON_COMPARE_SIZE."""
        pool = self._load_file_icon_pool(size)
        if not pool:
            self._draw_file_icon_vector(center, alpha=alpha)
            return
        index = (hash(key) % len(pool)) if key is not None else random.randrange(len(pool))
        icon = pool[index]
        alpha = max(0, min(255, alpha))
        if alpha >= 255:
            self.screen.blit(icon, icon.get_rect(center=center))
            return
        faded = icon.copy()
        faded.set_alpha(alpha)
        self.screen.blit(faded, faded.get_rect(center=center))

    def _load_file_icon_pool(self, size: tuple[int, int] | None = None) -> list[pygame.Surface]:
        """Laedt und skaliert alle 19 moeglichen "file_icon_XX.png"-Fotos
        (file_icon_01.png .. file_icon_19.png) einmalig pro angefragter
        Zielgroesse und haelt sie fertig skaliert im Speicher (Cache-Dict
        `_file_icon_pools`, ein Eintrag je `size`) - gleiches Prinzip wie
        _load_countdown_image_pool() fuer die "bitte_laecheln"-Bilder, nur
        mit mehreren parallelen Groessen (siehe _FILE_ICON_SIZE vs.
        _FILE_ICON_COMPARE_SIZE). Skaliert wird immer aus der Original-
        Datei (nicht aus einem bereits verkleinerten Pool), damit auch die
        groessere Vergleichsanimation scharf bleibt.

        Fehlende Nummern werden stillschweigend uebersprungen (kein
        Fehler) - Stand Sprint-11-Nachbesserung liegen alle 19 Nummern vor."""
        target_w, target_h = size or self._FILE_ICON_SIZE
        cache_key = (target_w, target_h)
        cached = self._file_icon_pools.get(cache_key)
        if cached is not None:
            return cached

        pool: list[pygame.Surface] = []
        for i in range(1, 20):
            path = self.config.assets_dir / f"file_icon_{i:02d}.png"
            try:
                raw = pygame.image.load(str(path)).convert_alpha()
            except (pygame.error, FileNotFoundError):
                continue
            scaled = pygame.transform.smoothscale(raw, (target_w, target_h))
            pool.append(scaled)

        if not pool:
            print("[Renderer] Kein einziges file_icon_XX.png gefunden - Fallback auf das handgezeichnete Symbol.")

        self._file_icon_pools[cache_key] = pool
        return pool

    def _capture_transfer_icon_key(self, progress: float) -> int:
        """Liefert einen ueber die gesamte Dauer EINER Bilduebertragung
        stabilen Schluessel fuer die Zufallsauswahl in _draw_file_icon()
        (siehe _draw_capture_transfer_animation). Eine neue Uebertragung
        wird daran erkannt, dass `progress` gegenueber dem letzten Aufruf
        gesunken ist (oder dies der erste Aufruf ist) - dann wird ein
        Zaehler erhoeht, der bis zum Ende dieser Uebertragung stabil
        bleibt, danach fuer die naechste Uebertragung wieder wechselt."""
        if self._capture_transfer_progress_seen is None or progress < self._capture_transfer_progress_seen - 0.01:
            self._capture_transfer_counter += 1
        self._capture_transfer_progress_seen = progress
        return self._capture_transfer_counter

    def _draw_file_icon_vector(self, center: tuple[int, int], alpha: int = 255) -> None:
        """Urspruengliches, handgezeichnetes Bilddatei-Symbol (Blatt mit
        umgeknickter Ecke + kleines Berg-/Sonne-Piktogramm). Seit der
        Sprint-11-Nachbesserung (echte file_icon_XX.png-Fotos, siehe
        _draw_file_icon) nur noch Fallback fuer den Fall, dass im
        assets/-Ordner kein einziges dieser Fotos gefunden wird."""
        cx, cy = center
        w, h = 34, 42
        fold = 10
        alpha = max(0, min(255, alpha))

        if alpha >= 255:
            target: pygame.Surface = self.screen
            tcx, tcy = cx, cy
            surf = None
        else:
            pad = 6
            surf = pygame.Surface((w + pad * 2, h + pad * 2), pygame.SRCALPHA)
            target = surf
            tcx, tcy = w // 2 + pad, h // 2 + pad

        rect = pygame.Rect(0, 0, w, h)
        rect.center = (tcx, tcy)
        points = [
            (rect.left, rect.top), (rect.right - fold, rect.top),
            (rect.right, rect.top + fold), (rect.right, rect.bottom),
            (rect.left, rect.bottom),
        ]
        pygame.draw.polygon(target, (255, 255, 255), points)
        pygame.draw.polygon(target, (60, 60, 65), points, width=2)
        pygame.draw.line(
            target, (60, 60, 65),
            (rect.right - fold, rect.top), (rect.right - fold, rect.top + fold), 2,
        )
        pygame.draw.line(
            target, (60, 60, 65),
            (rect.right - fold, rect.top + fold), (rect.right, rect.top + fold), 2,
        )
        # kleines Bild-Piktogramm (Sonne + Berg), analog zum Beispielbild
        pygame.draw.circle(target, (255, 200, 60), (rect.left + 10, rect.top + 16), 3)
        mountain = [
            (rect.left + 5, rect.bottom - 6), (rect.left + 14, rect.bottom - 18),
            (rect.left + 20, rect.bottom - 10), (rect.left + 26, rect.bottom - 20),
            (rect.right - 4, rect.bottom - 6),
        ]
        pygame.draw.lines(target, (60, 60, 65), False, mountain, 2)

        if surf is not None:
            surf.set_alpha(alpha)
            self.screen.blit(surf, (cx - (w // 2 + pad), cy - (h // 2 + pad)))

    def _draw_qr_card(
        self,
        qr_surface: pygame.Surface | None,
        center: tuple[int, int],
        max_size: tuple[int, int],
    ) -> None:
        """Zeichnet eine weiße Karte mit dem QR-Code zentriert auf `center`,
        so gross wie innerhalb von `max_size` (Breite, Hoehe) moeglich -
        deutlich bessere Scanbarkeit auf dunklem Hintergrund und robuster
        gegen schraege Blickwinkel als der QR-Code direkt auf dem
        Fotobox-Hintergrund. Gemeinsamer Helfer fuer AppState.GALLERY_PHOTO_QR
        (Feature 4) - fruehrer war das derselbe Code wie in der inzwischen
        entfernten QR-Kartenanzeige von AppState.QR_DISPLAY (siehe
        _draw_save_confirmation, seit Sprint 11 kein QR-Bild mehr dort)."""
        if qr_surface is None:
            self._blit_center(
                "QR-Code konnte nicht erzeugt werden.", self.font_body, (255, 120, 120), center[1],
            )
            return
        # GEAENDERT (Nutzer-Feedback, 2. Runde): 12px reichen aus - fester
        # Wert statt der zuvor proportionalen Berechnung (die war schon
        # kleiner als der urspruengliche feste Wert von 24px, wirkte aber
        # trotzdem noch breit). Grund war primaer NICHT dieser Kartenrand,
        # sondern die in das QR-Bild selbst eingebackene Ruhezone der
        # qrcode-Bibliothek (border=4 Module) - die wurde separat in
        # qr_service.py auf 1 Modul reduziert, siehe dortigen Kommentar.
        card_total = max(80, min(max_size[0], max_size[1]))
        card_padding = 12
        target_size = card_total - 2 * card_padding
        scaled = pygame.transform.smoothscale(qr_surface, (target_size, target_size))
        card_side = target_size + 2 * card_padding

        # NEU (Nutzer-Feedback): Schlagschatten wie bei den Buttons - gleiche
        # Optik/Technik wie in _draw_button (Anthrazit statt Schwarz, leicht
        # nach rechts unten versetzt, ueber eine SRCALPHA-Zwischenflaeche fuer
        # echte Transparenz). Bewusst OHNE border_radius, damit der Schatten
        # exakt zur eckigen Karte passt (kein rundes Schatten-Eck hinter
        # einer eckigen Kartenecke).
        shadow_surface = pygame.Surface((card_side, card_side), pygame.SRCALPHA)
        pygame.draw.rect(shadow_surface, (*self._SHADOW_COLOR, self._SHADOW_ALPHA), shadow_surface.get_rect())
        shadow_rect = shadow_surface.get_rect(center=center)
        self.screen.blit(
            shadow_surface, (shadow_rect.x + self._SHADOW_OFFSET, shadow_rect.y + self._SHADOW_OFFSET)
        )

        card = pygame.Surface((card_side, card_side))
        card.fill((255, 255, 255))
        card.blit(scaled, (card_padding, card_padding))
        card_rect = card.get_rect(center=center)
        self.screen.blit(card, card_rect)

    def _draw_gallery_photo_qr(self, qr_surface: pygame.Surface | None) -> None:
        """NEU (Sprint 11, Feature 4): legt ueber das im Hintergrund weiter
        sichtbare Foto (siehe render(), ruft vorher bereits
        _draw_gallery_fullscreen()) eine abgedunkelte Flaeche plus die
        QR-Karte fuer GENAU dieses Foto (app_with_hw._generate_gallery_qr
        erzeugt qr_surface passend zum aktuell ausgewaehlten Foto). Schliesst
        sich automatisch nach config.timeouts.gallery_qr_seconds oder per
        "Zurück" (siehe state_machine._handle_gallery_photo_qr)."""
        width, height = self.config.screen.width, self.config.screen.height

        # Dezente Abdunkelung, damit die weisse QR-Karte auf jedem Foto
        # (auch auf hellen Bildern) gut lesbar bleibt - gleiche Technik wie
        # der halbtransparente Banner-Hinweis im Hauptmenue.
        overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 120))
        self.screen.blit(overlay, (0, 0))

        # GEAENDERT (Sprint-11-Nachbesserung): komplett neu vermessen, nach
        # Feedback-Screenshots mit zwei Ueberlappungen - (1) die Ueberschrift
        # kollidierte mit dem generischen Fotobox-Titel oben links (behoben
        # durch Aufnahme in text_screens, siehe render()) und (2) der
        # "Zurück"-Button (rects.back, unten LINKS, Bereich ca. 0.80-0.955
        # der Bildschirmhoehe) ueberlappte mit dem Hinweistext darunter. Die
        # Karte war deshalb zunaechst kleiner (0.34 statt 0.5 der
        # Bildschirmhoehe) - auf Nutzer-Feedback hin wieder etwas vergroessert
        # (0.38), bei gleichzeitig schmalerer weisser Umrandung (siehe
        # _draw_qr_card), sodass weiterhin alle drei Elemente in klar
        # getrennten Zonen bleiben: Ueberschrift oben, Karte in der Mitte,
        # Hinweistext knapp darunter mit Abstand zum "Zurück"-Button.
        self._blit_center("QR-Code für dieses Foto", self.font_status_main_menu, (255, 255, 255), round(0.14 * height))

        center = (width // 2, round(height * 0.44))
        card_side = round(height * 0.38)
        max_size = (card_side, card_side)
        self._draw_qr_card(qr_surface, center, max_size)

        self._blit_center(
            "Mit dem Handy scannen, um dieses Bild herunterzuladen.",
            self.font_body, (220, 220, 220), round(height * 0.70),
        )

    def _draw_save_confirmation(self, model: AppModel) -> None:
        """NEU (Sprint 11, Feature 3): AppState.QR_DISPLAY zeigt seit diesem
        Umbau KEINEN QR-Code mehr direkt nach dem Speichern (siehe
        state_machine._SAVE_CONFIRMATION_TEXT fuer die Begruendung) -
        stattdessen nur noch einen zentrierten, umgebrochenen Hinweistext in
        Gaeste-Schriftgroesse. Der Zustand ist bewusst Teil von
        `text_screens` (siehe render()), damit weder der normale
        Fotobox-Titel noch der generische Statuszeilen-Block darueber
        gezeichnet werden - diese Methode uebernimmt Titel und Text
        vollstaendig selbst."""
        width, height = self.config.screen.width, self.config.screen.height
        self._blit_center("Foto gespeichert!", self.font_title, (255, 255, 255), round(0.16 * height))

        max_text_w = width - 160
        lines = self._wrap_text(model.ui.status_text, self.font_body, max_text_w)
        line_height = self.font_body.get_linesize()
        total_height = len(lines) * line_height
        # Vertikal zentriert im Bereich zwischen Titel und dem "Zurück"-
        # Button unten rechts (layout.right) - analog zur Bereichsableitung
        # in _draw_instructions/_draw_terms.
        area_top = round(0.30 * height)
        area_bottom = self.layout.right.y - 24
        top = area_top + max(0, (area_bottom - area_top - total_height) // 2)
        y = top
        for line in lines:
            self._blit_center(line, self.font_body, (230, 230, 230), y + line_height // 2)
            y += line_height

    def _wrap_text(self, text: str, font: pygame.font.Font, max_width: int) -> list[str]:
        """Greedy Wortumbruch: haengt Woerter zeilenweise an, bis die Zeile
        breiter als max_width waere, dann beginnt eine neue Zeile. Reicht
        fuer die kurzen, statischen Hinweistexte hier voellig aus - kein
        Bedarf fuer Silbentrennung o.ae."""
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and font.size(candidate)[0] > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines

    def _blit_center(self, text: str, font: pygame.font.Font, color: tuple[int, int, int], cy: int) -> None:
        """Einzeiligen Text horizontal zentriert auf Hoehe cy zeichnen
        (das uebliche _draw_text ist linksbuendig ab (x, y)).

        NEU (Lesbarkeit): faengt automatisch ab, falls text bei der
        uebergebenen Fontgroesse breiter waere als der Bildschirm (abzueglich
        Rand) - mit den um 50% vergroesserten Schriften (font_body/
        font_status_main_menu) reichen manche der bisher unkritisch kurzen
        Statuszeilen sonst ueber den Bildschirmrand hinaus (z.B. laengere
        Zeilen der Loesch-Sicherheitsabfrage oder Warnmeldungen). Gleiche
        Verkleinerungs-Technik wie bei Button-Labels, siehe _fit_text_font().
        """
        max_width = self.config.screen.width - 80
        fitted_font = self._fit_text_font(text, font, max_width)
        surf = fitted_font.render(text, True, color)
        rect = surf.get_rect(center=(self.config.screen.width // 2, cy))
        self.screen.blit(surf, rect)

    def _draw_text_outlined(
        self,
        text: str,
        font: pygame.font.Font,
        color: tuple[int, int, int],
        outline_color: tuple[int, int, int],
        center: tuple[int, int],
    ) -> None:
        """Einzeiligen Text mittig auf `center` zeichnen, mit einer festen
        Kontur in `outline_color` (Untertitel-Optik) - bleibt dadurch lesbar
        unabhaengig davon, wie hell/dunkel der Hintergrund gerade ist (z.B.
        das laufende Kamera-Liveview beim Cinema-Countdown, siehe
        _draw_cinema_countdown()). Rendert die Kontur mehrfach leicht
        versetzt UNTER dem eigentlichen Text - deutlich guenstiger als ein
        echtes Outline-Rendering, aber fuer kurze Hinweistexte ausreichend."""
        x, y = center
        offsets = ((-2, -2), (-2, 2), (2, -2), (2, 2), (-2, 0), (2, 0), (0, -2), (0, 2))
        outline_surf = font.render(text, True, outline_color)
        for dx, dy in offsets:
            rect = outline_surf.get_rect(center=(x + dx, y + dy))
            self.screen.blit(outline_surf, rect)
        main_surf = font.render(text, True, color)
        rect = main_surf.get_rect(center=(x, y))
        self.screen.blit(main_surf, rect)

    def _draw_pin_entry(self, model: AppModel) -> None:
        """Verstecktes Ziffernfeld fuer die Wartungs-PIN (AppState.PIN_ENTRY).

        Erreichbar nur ueber die Geheim-Geste im Hauptmenue (siehe
        admin_service / app_with_hw, admin_service umbenannt Sprint 11,
        vormals shutdown_service.py). Die eingegebene PIN wird maskiert
        (ein Kreis je Ziffer), das Raster kommt aus layout.pin_keys.
        """
        width, height = self.config.screen.width, self.config.screen.height

        # GEAENDERT (2. Nutzer-Feedback-Runde): Kopfzeile/PIN-Punkte/
        # Fehlertext kompakter nach oben gerueckt (0.07/0.17/0.235 ->
        # 0.055/0.132/0.178), damit unterhalb genug Platz fuer das nach oben
        # gerueckte Tastenfeld PLUS ein "Abbrechen" in Standardgroesse bleibt
        # (siehe layout.py, grid_y0/pin_keys["cancel"]) - beides zusammen
        # passt sonst nicht auf 720px Bildschirmhoehe.
        # Kopfzeile (vom State gesetzt: "Wartungs-PIN eingeben").
        header = model.ui.status_text or "Wartungs-PIN eingeben"
        self._blit_center(header, self.font_title, (255, 255, 255), round(0.055 * height))

        # Maskierte Anzeige: ein gefuellter Kreis pro eingegebener Ziffer.
        n = len(model.ui.pin_entry)
        if n:
            radius = 14
            spacing = 44
            total_w = (n - 1) * spacing
            cx0 = width // 2 - total_w // 2
            cy = round(0.132 * height)
            for i in range(n):
                pygame.draw.circle(self.screen, (240, 240, 240), (cx0 + i * spacing, cy), radius)

        # Fehlermeldung mittig unter der PIN-Anzeige. NEU (Feedback): PIN-
        # Eingabe ist Teil des Service-Menues (nur Lutz) - font_body_admin
        # statt der fuer Gaeste vergroesserten font_body.
        if model.ui.error_text:
            self._blit_center(model.ui.error_text, self.font_body_admin, (255, 120, 120), round(0.178 * height))

        # Ziffernfeld aus layout.pin_keys. Ziffern-Schluessel sind bereits
        # "0".."9"; Sondertasten bekommen sprechende Beschriftungen/Farben.
        # GEAENDERT (Nutzer-Feedback): "Löschen" -> "DEL", passt auf der
        # jetzt quadratischen, schmaleren Taste ohne staerkere automatische
        # Verkleinerung als die uebrigen Tasten.
        labels = {"backspace": "DEL", "submit": "OK", "cancel": "Abbrechen"}
        colors = {"backspace": (120, 90, 0), "submit": (0, 130, 0), "cancel": (100, 100, 100)}
        # NEU (Nutzer-Feedback): Ziffern +50% groesser (Standard-Startgroesse
        # waere 50, siehe _draw_button) - die Tasten sind seit der
        # Layout-Ueberarbeitung quadratisch und schmaler als vorher, die
        # Verkleinerungsschleife in _draw_button greift bei Bedarf trotzdem
        # weiterhin. "Löschen"/"OK"/"Abbrechen" bleiben bei der Standardgroesse.
        digit_font_size = 75
        for name, rect in self.layout.pin_keys.items():
            font_size = digit_font_size if name.isdigit() else None
            self._draw_button(labels.get(name, name), rect, colors.get(name, (55, 65, 85)), font_size=font_size)

        # Fehler-Optik: schnelles Rot/Gelb-Blinken am Bildschirmrand als
        # Bildschirm-Echo zur LED-/Taster-Fehleranzeige, solange
        # pin_error_deadline laeuft (gleiche Uhr wie die App: time.monotonic).
        deadline = model.timers.pin_error_deadline
        if deadline is not None:
            now = time.monotonic()
            if now < deadline:
                phase = int(now * self.config.shutdown.error_button_flash_hz) % 2
                border = (self.config.shutdown.error_ring_color_rgb if phase == 0
                          else self.config.shutdown.error_accent_color_rgb)
                pygame.draw.rect(self.screen, border, self.screen.get_rect(), width=12)

    def _draw_shutdown_goodbye(self, model: AppModel) -> None:
        """Abschieds-Screen (AppState.SHUTDOWN_GOODBYE): nur das Wallpaper
        (der Text steckt im Bild selbst). Danach faehrt die App den Pi herunter."""
        image = self._get_shutdown_background()
        if image is not None:
            self.screen.blit(image, (0, 0))

    def _get_shutdown_background(self) -> pygame.Surface | None:
        # Gleiche Cache-/Cover-Skalier-Logik wie _get_boot_background /
        # _get_main_menu_background.
        if self._shutdown_background is False:
            return None
        if self._shutdown_background is not None:
            return self._shutdown_background  # type: ignore[return-value]

        path = self.config.assets_dir / "shutdown_wallpaper.png"
        try:
            raw = pygame.image.load(str(path)).convert()
        except (pygame.error, FileNotFoundError):
            print(f"[Renderer] Shutdown-Hintergrundbild nicht gefunden: {path}")
            self._shutdown_background = False
            return None

        target_w, target_h = self.config.screen.width, self.config.screen.height
        img_w, img_h = raw.get_size()
        scale = max(target_w / img_w, target_h / img_h)
        scaled_w, scaled_h = max(1, round(img_w * scale)), max(1, round(img_h * scale))
        scaled = pygame.transform.smoothscale(raw, (scaled_w, scaled_h))

        canvas = pygame.Surface((target_w, target_h))
        offset_x = (target_w - scaled_w) // 2
        offset_y = (target_h - scaled_h) // 2
        canvas.blit(scaled, (offset_x, offset_y))
        self._shutdown_background = canvas
        return canvas


    def _draw_instructions(self) -> None:
        """Scrollbarer Anleitungstext, ohne Titel darueber (siehe render()).

        Die Liste `lines` darf beliebig erweitert werden - die Ansicht
        scrollt automatisch, sobald der Text nicht mehr komplett in den
        sichtbaren Bereich passt (Wischen hoch/runter, siehe app_with_hw.py).
        """
        width, height = self.config.screen.width, self.config.screen.height
        # GEAENDERT (Sprint-11-Nachbesserung): der bisherige Punkt 5 (QR-Code
        # direkt nach dem Speichern scannen) beschrieb ein Verhalten, das es
        # seit Sprint 11 Feature 3 gar nicht mehr gibt (kein sofortiges
        # QR-Bild mehr, siehe state_machine._SAVE_CONFIRMATION_TEXT_*) - er
        # entfaellt daher ersatzlos, unabhaengig von qr_codes_enabled. Der
        # bisherige Punkt 6 (Galerie) ruckt zu Punkt 5 nach und bekommt den
        # QR-Hinweissatz nur noch angehaengt, wenn QR-Codes fuer diese
        # Veranstaltung ueberhaupt aktiv sind (config.qr_codes_enabled,
        # siehe event_config.json) - dynamisch umgebrochen statt als feste
        # Zeilenliste, da der Satz je nach Einstellung unterschiedlich lang
        # ist (siehe _wrap_text).
        #
        # NEU (Sprint 11): der ganze Punkt 5 (Galerie) entfaellt jetzt
        # ersatzlos, wenn die Galerie-Funktion fuer diese Veranstaltung
        # deaktiviert ist (config.gallery_enabled) - dann gibt es nichts,
        # worauf sich der Text noch beziehen wuerde (auch der QR-Hinweis
        # darin, da der QR-Download ausschliesslich aus der Galerie-
        # Vollansicht heraus erreichbar ist, siehe config.GALLERY_ENABLED).
        gallery_lines: list[str] = []
        if self.config.gallery_enabled:
            gallery_point_text = (
                "In der \"Galerie\" siehst du alle bisherigen Fotos. "
                "Hoch/runter Wischen zum Blättern durch die Galerie, ein Foto "
                "antippen für die Vollansicht, dort links/rechts Wischen"
            )
            if self.config.qr_codes_enabled:
                gallery_point_text += (
                    " und die Möglichkeit den QR-Code zum Download anzeigen "
                    "zu lassen. Alternativ verbindest du dich direkt mit dem "
                    f"WLAN \"{self.config.network.guest_wifi_ssid}\" "
                    f"(Passwort: {self.config.network.guest_wifi_password})."
                )
            else:
                gallery_point_text += "."
            # "5. "/"    " sind beide exakt gleich breit (siehe Messung) - ein
            # einheitlicher Puffer reicht fuer Erst- und Folgezeilen.
            prefix_w = self.font_body.size("5. ")[0]
            max_text_w = width - 60 - 40 - prefix_w
            gallery_lines = [
                ("5. " if i == 0 else "    ") + wrapped
                for i, wrapped in enumerate(self._wrap_text(gallery_point_text, self.font_body, max_text_w))
            ]

        lines = [
            "Bitte nutze die Fotobox nur, wenn du den",
            "Nutzungsbedingungen zustimmst.",
            "",
            "1. \"Fotografieren\" drücken oder die Foto-Taste betätigen",
            "",
            "2. \"Countdown starten\" drücken, wenn du bereit für die",
            "    Aufnahme bist (oder \"Abrechen\").",
            "    Der Countdown bis zur Auslösung der Aufnahme",
            "    beträgt 5 Sekunden.",
            "",
            "3. Auf die Markierung stellen und lächeln!",
            "",
            "4. Nach der Aufnahme: Foto speichern oder löschen.",
        ]
        if gallery_lines:
            lines.append("")
            lines.extend(gallery_lines)
        lines.append("")
        lines.append("Viel Spaß! Bei Fragen bitte an Lutz wenden.")

        left = 60
        top = round(0.06 * height)
        # Statt einer eigenen, unabhaengigen Prozentzahl (frueher: fix
        # 0.78*height) direkt von der tatsaechlichen Button-Position
        # abgeleitet - 10px Sicherheitsabstand. So bleiben Textbereich und
        # Button immer synchron, auch wenn text_view_back in layout.py
        # kuenftig nochmal verschoben wird.
        bottom = self.layout.text_view_back.y - 10
        line_height = self.font_body.get_linesize()

        viewport = pygame.Rect(0, top, width, bottom - top)
        total_height = len(lines) * line_height
        max_scroll = max(0, total_height - viewport.height)
        self.instructions_scroll_offset = max(0, min(self.instructions_scroll_offset, max_scroll))

        previous_clip = self.screen.get_clip()
        self.screen.set_clip(viewport)
        y = top - self.instructions_scroll_offset
        for line in lines:
            if y + line_height >= top and y <= bottom:
                self._draw_text(line, self.font_body, (230, 230, 230), (left, y))
            y += line_height
        self.screen.set_clip(previous_clip)

    @staticmethod
    def _heading(text: str) -> tuple[str, bool]:
        """Markiert eine Zeile in der `lines`-Liste von _draw_terms als fett
        darzustellende Überschrift. Beispiel: self._heading("Deine Rechte:")
        statt einfach "Deine Rechte:". Normale Zeilen bleiben einfache
        Strings - kein "\\"-artiges Steuerzeichen mitten im Text noetig."""
        return (text, True)

    def _draw_terms(self) -> None:
        """Scrollbare Nutzungsbedingungen, analog zu _draw_instructions().

        Der Inhalt ist bewusst als einfache Zeilenliste hier im Code
        gepflegt (wie bei _draw_instructions) statt in einer externen
        Text-/HTML-Datei geladen zu werden:
        - Die Fotobox-App hat sonst nirgends einen Rich-Text-/HTML-Renderer;
          das haette einen zusaetzlichen Parser noetig gemacht, nur um am
          Ende wieder auf denselben pygame-Text-Zeilen zu landen.
        - Der Text soll beim Start zuverlaessig da sein, auch ohne
          Netzwerk/USB-Stick/Datei-Handling zur Laufzeit - eine fehlende
          oder beschaedigte externe Datei wuerde sonst zu einem leeren oder
          fehlerhaften Rechts-Hinweis fuehren, was bei einem Datenschutz-
          Text besonders unguenstig ist.
        Aenderungen an den Nutzungsbedingungen (z.B. nach einer neuen
        Veranstaltung) macht man hier direkt in der Liste `lines`.
        """
        width, height = self.config.screen.width, self.config.screen.height
        # GEAENDERT (Sprint-11-Nachbesserung): der ganze Abschnitt zum
        # QR-Download entfaellt, wenn QR-Codes fuer diese Veranstaltung
        # deaktiviert sind (config.qr_codes_enabled, siehe event_config.json)
        # - ohne die Funktion gibt es auch nichts, worauf dieser Passus sich
        # noch beziehen wuerde.
        #
        # NEU (Sprint 11): der QR-Download ist ausschliesslich aus der
        # Galerie-Vollansicht heraus erreichbar (Sprint 11, Feature 4) -
        # ohne Galerie-Funktion (config.gallery_enabled) ist er also
        # ebenfalls unerreichbar, unabhaengig von qr_codes_enabled. Beide
        # Schalter muessen daher UND-verknuepft aktiv sein, damit dieser
        # Passus angezeigt wird.
        qr_download_section = [
            self._heading("Lokaler Download (WLAN)"),
            "",
            f"Über das WLAN \"{self.config.network.guest_wifi_ssid}\" (Passwort:",
            f"{self.config.network.guest_wifi_password}) kannst du dein Foto",
            "nach der Aufnahme per QR-Code herunterladen.",
            "Da es sich um ein Veranstaltungsnetzwerk handelt",
            "sind die Bilddateien dabei theoretisch für andere",
            "angemeldete Nutzer einsehbar.",
            "Lade keine Bilder herunter, wenn du damit nicht",
            "einverstanden bist.",
            "",
        ] if (self.config.qr_codes_enabled and self.config.gallery_enabled) else []
        # NEU (Sprint 11): diese Aussage stimmt nur, wenn die Galerie fuer
        # diese Veranstaltung tatsaechlich aktiv ist (config.gallery_enabled)
        # - ohne Galerie-Funktion koennen andere Gaeste die Fotos gerade
        # NICHT auf dem Display einsehen, das muss der rechtlich relevante
        # Text korrekt widerspiegeln statt etwas Falsches zu behaupten.
        gallery_visibility_lines = [
            "Während der Veranstaltung sind deine Fotos auf dem",
            "Display von anderen Nutzern der Fotobox einsehbar.",
        ] if self.config.gallery_enabled else [
            "Eine Galerie-Funktion ist bei dieser Veranstaltung nicht",
            "aktiv - andere Gäste können deine Fotos auf dem Display",
            "der Fotobox nicht einsehen.",
        ]
        lines = [
            self._heading("Nutzungsbedingungen zur Fotobox"),
            "",
            "Mit der Nutzung dieser Fotobox (z. B. durch Betätigen",
            "des Auslösers) erklärst du dich damit einverstanden,",
            "dass Fotografien von dir angefertigt werden.",
            "Die Nutzung ist freiwillig.",
            "",
            self._heading("Verwendungszweck & Speicherung"),
            "",
            "Die Fotos dienen als Erinnerung für Familie, Freunde",
            "und Verwandte sowie den Gastgeber.",
            "Sie werden zunächst lokal auf der Fotobox gespeichert",
            "und anschließend vom Gastgeber in einem privaten Kreis",
            "weiterverarbeitet.",
            *gallery_visibility_lines,
            "Eine Weitergabe an unbeteiligte Dritte, eine",
            "Veröffentlichung bspw. im Internet oder eine",
            "kommerzielle Nutzung findet nicht statt.",
            "",
            *qr_download_section,
            self._heading("Deine Rechte"),
            "",
            "Du kannst der Speicherung deines Bildes direkt nach",
            "der Aufnahme über die \"Löschen\"-Taste widersprechen.",
            "Außerdem hast du jederzeit das Recht auf Auskunft,",
            "Berichtigung, Löschung, Einschränkung der Verarbeitung,",
            "Datenübertragbarkeit und Widerspruch.",
            "Wende dich dazu einfach an den unten genannten",
            "Verantwortlichen bzw. an die unten genannte Ver-",
            "Verantwortliche.",
            "Eine erteilte Einwilligung kannst du jederzeit mit",
            "Wirkung für die Zukunft widerrufen.",
            "",
            "Alle gespeicherten Fotos werden unwiderruflich innerhalb",
            "von zwei (2) Tagen nach der Veranstaltung von der",
            "Fotobox gelöscht.",
            "",
            "Kinder & Jugendliche nutzen die Fotobox bitte nur",
            "in Begleitung bzw. mit Zustimmung einer",
            "erziehungsberechtigten Person.",
            "",
            self._heading("Verantwortlich für den Betrieb"),
            "",
            "Lutz Buchholz",
            "Dechant-Fein-Str. 24",
            "51375 Leverkusen",
            "lutz-peter@imail.de", 
            "0163 8506144",
        ]

        left = 60
        top = round(0.06 * height)
        # Siehe Kommentar in _draw_instructions() - dieselbe Ableitung aus
        # der tatsaechlichen Button-Position statt einer separaten,
        # unabhaengigen Prozentzahl.
        bottom = self.layout.text_view_back.y - 10
        line_height = self.font_body.get_linesize()

        viewport = pygame.Rect(0, top, width, bottom - top)
        total_height = len(lines) * line_height
        max_scroll = max(0, total_height - viewport.height)
        self.terms_scroll_offset = max(0, min(self.terms_scroll_offset, max_scroll))

        previous_clip = self.screen.get_clip()
        self.screen.set_clip(viewport)
        y = top - self.terms_scroll_offset
        for line in lines:
            if isinstance(line, tuple):
                text, is_bold = line
                font = self.font_body_bold if is_bold else self.font_body
                # Ueberschriften zusaetzlich farblich abgesetzt (warmes
                # Amber statt normalem Hellgrau) - Kontrast gegen den TERMS-
                # Hintergrund (20,20,35) liegt bei 13.66:1 und erfuellt damit
                # deutlich auch WCAG AAA (>= 7:1), wichtig fuer aeltere
                # Gaeste mit schwaecherer Sehkraft. Dieselbe Farbe wird auch
                # fuer status_text verwendet - schafft Wiedererkennung als
                # "hervorgehoben".
                color = (255, 220, 120) if is_bold else (230, 230, 230)
            else:
                text, font, color = line, self.font_body, (230, 230, 230)
            if y + line_height >= top and y <= bottom:
                self._draw_text(text, font, color, (left, y))
            y += line_height
        self.screen.set_clip(previous_clip)

    def _draw_buttons(self, state: AppState) -> None:
        if state == AppState.MAIN_MENU:
            self._draw_button("Fotografieren", self.layout.main_photo, (0, 150, 0))
            # NEU (Sprint 11): kein "Galerie"-Button ohne Galerie-Funktion
            # fuer diese Veranstaltung (config.gallery_enabled, siehe
            # event_config.json) - die anderen drei Buttons behalten
            # bewusst ihre bisherigen Positionen (keine Neuanordnung),
            # gleiches Vorgehen wie beim optionalen QR-Icon in
            # GALLERY_FULLSCREEN.
            if self.config.gallery_enabled:
                self._draw_button("Galerie", self.layout.main_gallery, (0, 100, 150))
            self._draw_button("Anleitung", self.layout.main_instructions, (120, 90, 0))
            self._draw_button("Bedingungen", self.layout.main_terms, (120, 30, 90))
        elif state == AppState.ATTRACT_GALLERY:
            pass  # bewusst kein Button - Tippen/Taster fuehrt zurueck
        elif state == AppState.INSTRUCTIONS:
            self._draw_button("Zurück", self.layout.text_view_back, (100, 100, 100))
        elif state == AppState.TERMS:
            self._draw_button("Verstanden", self.layout.text_view_back, (0, 130, 110))
        elif state == AppState.PHOTO_INTRO:
            self._draw_button("Countdown starten", self.layout.left, (0, 150, 0))
            self._draw_button("Zurück", self.layout.right, (100, 100, 100))
        elif state == AppState.PHOTO_PREVIEW:
            # Kein "Countdown starten"-Button mehr - der Countdown startet
            # automatisch (siehe state_machine.py::_go_preview).
            self._draw_button("Abbrechen", self.layout.right, (100, 100, 100))
        elif state == AppState.COUNTDOWN:
            self._draw_button("Abbrechen", self.layout.right, (100, 100, 100))
        elif state == AppState.GALLERY_GRID:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
        elif state == AppState.GALLERY_EMPTY:              # NEU (Etappe 7)
            self._draw_button("Jetzt fotografieren", self.layout.left, (0, 150, 0))
            self._draw_button("Zurück", self.layout.right, (100, 100, 100))
        elif state == AppState.GALLERY_FULLSCREEN:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
            # NEU (Sprint 11, Feature 4): gleichwertige Alternative zum
            # Doppeltap auf das Foto (siehe app_with_hw._handle_pygame_event).
            # GEAENDERT (Sprint-11-Nachbesserung): kein Button ohne
            # QR-Funktion (config.qr_codes_enabled) - siehe auch
            # app_with_hw._map_click_to_event (Treffer-Rechteck entfaellt
            # dort ebenfalls).
            if self.config.qr_codes_enabled:
                self._draw_button("QR-Code anfordern", self.layout.gallery_qr_icon, (0, 100, 150))
        elif state == AppState.GALLERY_PHOTO_QR:
            # NEU (Sprint 11, Feature 4): gleiche Position/Optik wie bei
            # GALLERY_FULLSCREEN - schliesst die QR-Karte wieder vorzeitig,
            # ohne auf den 30s-Timeout (gallery_qr_seconds) warten zu muessen.
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
        elif state == AppState.REVIEW:
            self._draw_button("Speichern", self.layout.left, (0, 150, 0))
            self._draw_button("Löschen", self.layout.right, (150, 0, 0))
        elif state == AppState.DELETE_CONFIRM:
            self._draw_button("Wirklich löschen", self.layout.left, (150, 0, 0))
            self._draw_button("Abbrechen", self.layout.right, (100, 100, 100))
        elif state == AppState.QR_DISPLAY:
            self._draw_button("Zurück", self.layout.right, (100, 100, 100))
        elif state == AppState.ERROR_SCREEN:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
        elif state == AppState.ADMIN_MENU:
            self._draw_admin_menu_buttons()
        elif state == AppState.ADMIN_STATUS:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
        elif state == AppState.ADMIN_CAMERA_SETTINGS:
            # GEAENDERT (Kamera-Menue 2.0): "Zurück" ersetzt durch Speichern/
            # Abbrechen (siehe state_machine._handle_admin_camera_settings);
            # dazwischen die Seiten-Navigation (nur die jeweils passende
            # Richtung wird gezeichnet). +/- nur, wenn die Kamera tatsaechlich
            # erreichbar ist - sonst zeigt _draw_admin_camera_settings()
            # bereits die Fehlermeldung, funktionslose Buttons wuerden nur
            # verwirren.
            self._draw_button("Abbrechen", self.layout.admin_camera_cancel, (120, 60, 60))
            self._draw_button("Speichern", self.layout.admin_camera_save, (60, 120, 70))
            # GEAENDERT (Nutzer-Feedback nach Live-Test): Button jetzt genau
            # so gross wie Abbrechen/Speichern (siehe layout.py), rechts
            # daneben statt in einer schmalen Luecke - dadurch reicht die
            # normale automatische Schriftverkleinerung von _draw_button
            # (kein fester font_size=32 mehr noetig, der zusammen mit dem
            # frueher zu kleinen Rect fuer das "kaputte" Pfeil-Icon sorgte).
            # Einfache ASCII-Pfeile "<"/">" statt Unicode-Pfeilen, die in der
            # Pi-Schriftart als Kaestchen dargestellt wurden.
            if self._admin_camera_page == 0:
                self._draw_button("Seite 2 >", self.layout.admin_camera_page_next, (70, 70, 90))
            else:
                self._draw_button("< Seite 1", self.layout.admin_camera_page_prev, (70, 70, 90))
            if self._admin_camera_available and self._admin_camera_loaded:
                # NEU (Sprint-11-Nachbesserung): Beschriftung "+"/"-"/"<"/">"
                # um 100% vergroessert (Standard waere 50) - besser lesbar/
                # treffsicherer auf den jetzt quadratischen, groesseren
                # Buttons (siehe layout.py).
                camera_btn_font_size = 100
                # GEAENDERT (Nutzer-Feedback nach Live-Test): "+"/"-" passt
                # nur bei echten Zahlenwerten (ISO, Blende, Belichtungs-
                # korrektur, Bildgroesse - dort ist eine Richtung "mehr"/
                # "weniger"). Bei Auswahl aus benannten Kategorien ohne
                # Groessenordnung (Messfeld, Weissabgleich, Bildqualitaet,
                # Aufnahmebetrieb) sind "<"/">" (durchblaettern) passender.
                if self._admin_camera_page == 0:
                    row_pairs = (
                        (self.layout.admin_camera_iso_minus, self.layout.admin_camera_iso_plus, "-", "+"),
                        (self.layout.admin_camera_aperture_minus, self.layout.admin_camera_aperture_plus, "-", "+"),
                        # Verschlusszeit (Zeile 2) ist reiner Info-Wert, keine Buttons.
                        (self.layout.admin_camera_expcomp_minus, self.layout.admin_camera_expcomp_plus, "-", "+"),
                        (self.layout.admin_camera_metering_minus, self.layout.admin_camera_metering_plus, "<", ">"),
                    )
                else:
                    row_pairs = (
                        (self.layout.admin_camera_wb_minus, self.layout.admin_camera_wb_plus, "<", ">"),
                        (self.layout.admin_camera_quality_minus, self.layout.admin_camera_quality_plus, "<", ">"),
                        (self.layout.admin_camera_imagesize_minus, self.layout.admin_camera_imagesize_plus, "-", "+"),
                        (self.layout.admin_camera_drive_minus, self.layout.admin_camera_drive_plus, "<", ">"),
                    )
                for minus_rect, plus_rect, minus_label, plus_label in row_pairs:
                    self._draw_button(minus_label, minus_rect, (70, 70, 75), font_size=camera_btn_font_size)
                    self._draw_button(plus_label, plus_rect, (70, 70, 75), font_size=camera_btn_font_size)
        elif state == AppState.ADMIN_EVENT_SETTINGS:
            self._draw_button("Speichern", self.layout.left, (0, 150, 0))
            self._draw_button("Abbrechen", self.layout.right, (100, 100, 100))
            # GEAENDERT (Nutzer-Feedback): Schrift verkleinert, damit sie zur
            # uebrigen (kompakteren) Schriftgroesse dieses Screens passt
            # (font_body_admin, siehe _draw_admin_event_settings) statt der
            # vorherigen grossen Standard-Buttonschrift.
            self._draw_button(
                "Wallpaper von USB laden", self.layout.admin_event_wallpaper_button, (0, 90, 130), font_size=30,
            )
            # NEU (Nutzer-Feedback): "Standardwerte"-Taste - teilt sich die
            # Zeile mit "Wallpaper von USB laden" (siehe layout.py), gleiche
            # Schriftgroesse fuer ein einheitliches Bild.
            self._draw_button(
                "Standardwerte", self.layout.admin_event_defaults_button, (90, 70, 0), font_size=30,
            )
            # ENTFERNT (Nutzer-Feedback): "Anzeigen"/"Verbergen"-Button - das
            # WLAN-Passwort steht jetzt immer als Klartext da.
        elif state == AppState.ADMIN_EVENT_WALLPAPER_PICK:
            # NEU (Nutzer-Feedback): Auswahlliste - "Speichern" kopiert die
            # markierte Zeile NUR in eine Zwischenablage (siehe
            # event_config_service.WALLPAPER_PENDING_FILENAME), noch nicht
            # in das echte Hauptmenue-Wallpaper. Kein eigener "deaktiviert"-
            # Look ohne Auswahl - der Tap ist dann einfach wirkungslos
            # (state_machine._handle_admin_event_wallpaper_pick).
            self._draw_button("Speichern", self.layout.left, (0, 150, 0))
            self._draw_button("Abbrechen", self.layout.right, (100, 100, 100))
        elif state == AppState.ADMIN_EVENT_WALLPAPER_RESULT:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
        elif state == AppState.ADMIN_EVENT_SAVED:
            self._draw_button("Jetzt neu starten", self.layout.left, (0, 150, 0))
            self._draw_button("Später", self.layout.right, (100, 100, 100))
        # ADMIN_EVENT_TEXT_ENTRY zeichnet die Tastatur komplett selbst (siehe
        # _draw_admin_event_text_entry, analog zu PIN_ENTRY).
        # ADMIN_EVENT_WALLPAPER_PICK_LOADING: bewusst kein Button - laeuft,
        # nicht abbrechbar (analog ADMIN_USB_CHECK).
        elif state == AppState.ADMIN_SHUTDOWN_CONFIRM:
            # NEU (Sprint-11-Nachbesserung): gleiche Farbgebung/Anordnung wie
            # ADMIN_DELETE_CONFIRM - "Nein" links neutral, "Ja" rechts rot.
            self._draw_button("Nein, abbrechen", self.layout.left, (70, 70, 75))
            self._draw_button("Ja, herunterfahren", self.layout.right, (160, 0, 0))
        elif state == AppState.ADMIN_RESTART_CONFIRM:
            # NEU (Nutzer-Feedback): "Nein" links neutral wie ueberall sonst,
            # "Ja" rechts in gedaempftem Orange statt Rot - ein Neustart ist
            # deutlich weniger "gefaehrlich" als Herunterfahren/Loeschen.
            self._draw_button("Nein, abbrechen", self.layout.left, (70, 70, 75))
            self._draw_button("Ja, neu starten", self.layout.right, (170, 100, 0))
        elif state == AppState.ADMIN_DELETE_CONFIRM:
            # NEU (4.4): "Nein" links neutral-grau, "Ja" rechts deutlich rot -
            # die gefaehrliche Wahl soll nicht wie die naheliegende aussehen.
            self._draw_button("Nein, abbrechen", self.layout.left, (70, 70, 75))
            self._draw_button("Ja, alles löschen", self.layout.right, (160, 0, 0))
        elif state == AppState.ADMIN_DELETE_DONE:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
        elif state == AppState.ADMIN_USB_WAIT:
            # NEU (4.6): "Weiter" bleibt ausgegraut, solange kein Stick
            # erkannt wurde - dieselbe Bedingung, die auch die State
            # Machine prueft (_handle_admin_usb_wait).
            ready = self._usb_continue_enabled
            self._draw_button("Abbrechen", self.layout.left, (100, 100, 100))
            self._draw_button("Weiter", self.layout.right, (0, 130, 110) if ready else (55, 55, 60))
        elif state == AppState.ADMIN_USB_READY:
            self._draw_button("Abbrechen", self.layout.left, (100, 100, 100))
            self._draw_button("Export starten", self.layout.right, (0, 130, 110))
        elif state == AppState.ADMIN_USB_PROBLEM:
            # NEU (4.7): bei not_enough_free wird "Stick leeren" angeboten.
            # Bei too_small hilft Aufraeumen nicht - dort ersetzt der
            # rechte Button die Wirkung von "Weiter".
            if self._usb_not_enough_free:
                self._draw_button("Abbrechen", self.layout.left, (100, 100, 100))
                self._draw_button("Stick leeren", self.layout.right, (180, 80, 0))
            else:
                self._draw_button("Weiter", self.layout.right, (120, 90, 0))
        elif state == AppState.ADMIN_USB_EXPORT_DONE:
            self._draw_button("Weiter", self.layout.right, (0, 130, 110))
        elif state == AppState.ADMIN_USB_REMOVE:
            self._draw_button("Zurück", self.layout.back, (100, 100, 100))
        elif state == AppState.ADMIN_USB_CONFLICTS:            # NEU (6c)
            # Sammelaktionen ("Alle auswaehlen"-Zeile) und Dateizeilen samt
            # Kontrollkaestchen zeichnet _draw_admin_usb_conflicts() bereits
            # vollstaendig mit - hier nur die eine echte Haupt-Aktion.
            self._draw_button("Ausführen", self.layout.right, (0, 130, 110))
        # ADMIN_RESTART_PENDING / ADMIN_DELETE_RUNNING / ADMIN_USB_RESOLVE:
        # bewusst kein Button - nicht abbrechbar.

    def _draw_admin_menu_buttons(self) -> None:
        # Beschriftung, Farbe und Position kommen vollstaendig aus
        # admin_menu.ADMIN_MENU_ITEMS - hier wird nichts dupliziert, damit
        # Zeichnung und Treffererkennung (app_with_hw._map_admin_menu_click)
        # nicht auseinanderlaufen koennen.
        # Noch nicht implementierte Punkte (enabled=False) werden dunkelgrau
        # gezeichnet, damit sichtbar ist, dass sie noch nichts tun.
        rects = build_admin_rects(self.config.screen.width, self.config.screen.height)
        for item in ADMIN_MENU_ITEMS:
            color = item.color if item.enabled else (55, 55, 60)
            self._draw_button(item.label, rects[item.key], color)

    def _draw_admin_status(self, model: AppModel) -> None:
        # GEAENDERT (Nutzer-Feedback, Bugfix): urspruenglich als "fuenf kurze
        # Zeilen, kein Scrollen noetig" angelegt - die Diagnose ist seither
        # um mehrere Zeilen gewachsen (Speicherplatz-Alarm, Gaeste-WLAN, ...)
        # und lief dadurch unten aus dem sichtbaren Bereich (die letzte
        # Zeile lag teils hinter dem "Zurueck"-Button bzw. komplett ausserhalb
        # des Bildschirms). Jetzt scrollbar - gleiches Clip-/Scroll-Muster
        # wie _draw_admin_usb_conflicts (Swipe-Handling siehe
        # app_with_hw._handle_pygame_event, ADMIN_STATUS-Zweig).
        # NEU (Feedback): Service-Menue, nur fuer Lutz - font_body_admin
        # (urspruengliche Groesse) statt der fuer Gaeste vergroesserten
        # font_body.
        width, height = self.config.screen.width, self.config.screen.height
        top = round(0.22 * height)
        bottom = round(0.77 * height)
        line_height = self.font_body_admin.get_linesize() + 14

        if not model.ui.admin_status_lines:
            self._draw_text("Ermittle Status ...", self.font_body_admin, (200, 200, 200), (60, top))
            return

        lines = model.ui.admin_status_lines
        viewport = pygame.Rect(0, top, width, bottom - top)
        total_height = len(lines) * line_height
        max_scroll = max(0, total_height - viewport.height)
        self.admin_status_scroll_offset = max(0, min(self.admin_status_scroll_offset, max_scroll))

        previous_clip = self.screen.get_clip()
        self.screen.set_clip(viewport)
        y = top - self.admin_status_scroll_offset
        for line in lines:
            if y + line_height >= top and y <= bottom:
                self._draw_text(line, self.font_body_admin, (230, 230, 230), (60, y))
            y += line_height
        self.screen.set_clip(previous_clip)

    def _draw_admin_camera_settings(self, model: AppModel, preview_frame: pygame.Surface | None) -> None:
        """GEAENDERT (Kamera-Menue 2.0, Nutzer-Feedback nach Sprint 11):
        2-Seiten-Layout - Live-Vorschau-Panel links (Blende ist bei
        eingebauter Kamera weder zu hoeren noch zu sehen), rechts eine
        Spalte von bis zu 5 Zeilen "Label: Wert" zwischen "-"/"+"-Buttons
        (die zeichnet _draw_buttons(), wie bei allen anderen Screens - hier
        nur die Werte/Texte + das Live-Bild). Seite 1 "Belichtung": ISO,
        Blende, Verschlusszeit (Info), Belichtungskorrektur, Messfeld.
        Seite 2 "Sonstiges": Weissabgleich, Bildqualitaet, Bildgroesse,
        Aufnahmebetrieb. Zeichnet stattdessen eine Fehlermeldung, falls die
        Kamera nicht erreichbar ist oder weder ISO noch Blende liefert
        (siehe hw_camera_settings_provider.read_current)."""
        ui = model.ui
        width = self.config.screen.width
        height = self.config.screen.height
        # NEU (Sprint 11, Feature 2): merkt sich fuer _draw_buttons (wird
        # danach aufgerufen, siehe render()), ob die +/- Buttons ueberhaupt
        # sinnvoll sind - gleiches Prinzip wie _usb_continue_enabled.
        self._admin_camera_available = ui.admin_camera_available
        # NEU (Kamera-Menue 2.0): merkt sich fuer _draw_buttons, welche Seite
        # gerade sichtbar ist (0=Belichtung, 1=Sonstiges).
        self._admin_camera_page = ui.admin_camera_page
        # BUGFIX (Kamera-Menue 2.0, Eigenpruefung): bis zum Beweis des
        # Gegenteils (Werte tatsaechlich eingetroffen, siehe unten) gilt der
        # Screen als "noch nicht geladen" - verhindert, dass _draw_buttons()
        # waehrend "Lese Kamera-Einstellungen ..." bereits funktionslose +/-
        # Buttons ohne zugehoerigen Text zeichnet.
        self._admin_camera_loaded = False

        if not ui.admin_camera_available:
            message = ui.admin_camera_error or "Kamera-Einstellungen nicht verfügbar."
            self._blit_center(message, self.font_body_admin, (255, 160, 120), round(height * 0.5))
            return

        if not ui.admin_camera_iso_choices and not ui.admin_camera_aperture_choices:
            # Kurzes Zeitfenster zwischen Betreten des Screens (Werte noch
            # leer) und dem Eintreffen von ADMIN_CAMERA_SETTINGS_READY -
            # gleiches Prinzip wie "Ermittle Status ..." bei ADMIN_STATUS.
            self._draw_text("Lese Kamera-Einstellungen ...", self.font_body_admin, (200, 200, 200), (60, round(0.35 * height)))
            return

        self._admin_camera_loaded = True
        self._draw_admin_camera_preview_panel(preview_frame)

        # BUGFIX (Sprint-11-Nachbesserung, weiterhin gueltig): der von der
        # Kamera gelieferte Blenden-Rohwert enthaelt je nach Modell/
        # libgphoto2-Version teils schon ein "f/"-Praefix und teils nicht -
        # ein evtl. vorhandenes Praefix wird zuerst abgeschnitten, danach
        # genau EIN "f/" ergaenzt.
        raw_aperture = ui.admin_camera_aperture
        aperture_value = raw_aperture[2:] if raw_aperture.lower().startswith("f/") else raw_aperture

        row_font = self.font_body_admin
        row_color = (230, 230, 230)
        x0 = self.layout.admin_camera_iso_minus.right + 14
        x1 = self.layout.admin_camera_iso_plus.left - 14

        if ui.admin_camera_page == 0:
            rows = (
                (f"ISO: {ui.admin_camera_iso}" if ui.admin_camera_iso else "ISO: -", self.layout.admin_camera_iso_minus.centery),
                (
                    f"Blende: f/{aperture_value}" if aperture_value else "Blende: -",
                    self.layout.admin_camera_aperture_minus.centery,
                ),
                (
                    f"Verschlusszeit: {ui.admin_camera_shutter} (automatisch)" if ui.admin_camera_shutter else "Verschlusszeit: -",
                    self.layout.admin_camera_expcomp_minus.centery - round(0.105 * height),
                ),
                (
                    f"Belichtungskorrektur: {ui.admin_camera_expcomp} EV" if ui.admin_camera_expcomp else "Belichtungskorrektur: -",
                    self.layout.admin_camera_expcomp_minus.centery,
                ),
                (
                    f"Messfeld: {ui.admin_camera_metering}" if ui.admin_camera_metering else "Messfeld: -",
                    self.layout.admin_camera_metering_minus.centery,
                ),
            )
        else:
            rows = (
                (f"Weißabgleich: {ui.admin_camera_wb}" if ui.admin_camera_wb else "Weißabgleich: -", self.layout.admin_camera_wb_minus.centery),
                (
                    f"Bildqualität: {ui.admin_camera_quality}" if ui.admin_camera_quality else "Bildqualität: -",
                    self.layout.admin_camera_quality_minus.centery,
                ),
                (
                    f"Bildgröße: {ui.admin_camera_imagesize}" if ui.admin_camera_imagesize else "Bildgröße: -",
                    self.layout.admin_camera_imagesize_minus.centery,
                ),
                (
                    f"Aufnahmebetrieb: {ui.admin_camera_drive}" if ui.admin_camera_drive else "Aufnahmebetrieb: -",
                    self.layout.admin_camera_drive_minus.centery,
                ),
            )

        # GEAENDERT (Nutzer-Feedback nach Live-Test): nur noch der einfache
        # Bildschirmname statt "- Seite x/y - <Name>" (steht bereits in den
        # Zeilen selbst, war als Titel redundant/unklar) - dafuer deutlich
        # groesser (font_body_admin statt font_small) und damit besser lesbar.
        self._blit_center("Kamera-Einstellungen", self.font_body_admin, (150, 190, 220), round(0.07 * height))
        for label, cy in rows:
            self._draw_centered_in_range(label, row_font, row_color, x0, x1, cy)

        # BUGFIX (Kamera-Menue 2.0, Eigenpruefung): Hinweis bezieht sich nur
        # auf die Blende (Seite 1 "Belichtung") - erschien vorher faelschlich
        # auch auf Seite 2 "Sonstiges", wo es keinen Bezug zum Inhalt hatte.
        # GEAENDERT (Nutzer-Feedback nach Live-Test): weiter nach oben
        # gerueckt, in die Mitte der Luecke zwischen Live-Bild/unterster
        # Einstell-Zeile (enden beide bei ca. 0.65) und der Buttonreihe
        # (beginnt bei lower_y=0.80) - vorher sass er zu dicht an den Buttons.
        if ui.admin_camera_page == 0:
            hint = "Blende hängt vom montierten Objektiv ab. Kamera sollte im Modus A (Zeitautomatik) stehen."
            self._blit_center(hint, self.font_small, (170, 170, 170), round(height * 0.725))

    def _draw_admin_event_settings(self, model: AppModel) -> None:
        """NEU (Veranstaltungsdaten): Uebersichts-/Bearbeitungs-Screen fuer
        Titel/Praefix/WLAN-SSID/WLAN-Passwort (je ein eigenes Tap-Ziel, siehe
        layout.admin_event_*_row/app_with_hw._map_click_to_event - oeffnet
        die Tastatur fuer genau dieses Feld) sowie QR-/Galerie-Schalter (Tap
        kippt direkt, kein Tastatur-Umweg). Die Rects sind reine Tap-Ziele
        ohne eigene Optik aus layout.py - die sichtbare "Karte" je Zeile wird
        hier gezeichnet, analog zu _draw_button() aber links ausgerichtet
        (Label+Wert passen sonst nicht nebeneinander). Der Button "Wallpaper
        von USB laden" sowie "Speichern"/"Abbrechen" werden bereits von
        _draw_buttons() gezeichnet, hier nicht nochmal."""
        ui = model.ui

        # GEAENDERT (Nutzer-Feedback): WLAN-Passwort steht jetzt immer im
        # Klartext da - kein Masken-/Sichtbarkeits-Umschalter mehr.
        rows = (
            (self.layout.admin_event_title_row, "Titel", ui.admin_event_title or "-"),
            (self.layout.admin_event_prefix_row, "Datei-Präfix", ui.admin_event_prefix or "-"),
            (self.layout.admin_event_wifi_ssid_row, "WLAN-SSID", ui.admin_event_wifi_ssid or "-"),
            (self.layout.admin_event_wifi_password_row, "WLAN-Passwort", ui.admin_event_wifi_password or "-"),
        )
        for rect, label, value in rows:
            pygame.draw.rect(self.screen, (45, 50, 60), rect, border_radius=10)
            pygame.draw.rect(self.screen, (255, 255, 255), rect, width=2, border_radius=10)
            text = self._truncate_text(f"{label}: {value}", self.font_body_admin, rect.width - 24)
            self._draw_text(
                text, self.font_body_admin, (230, 230, 230),
                (rect.x + 16, rect.centery - self.font_body_admin.get_linesize() // 2),
            )

        toggles = (
            (self.layout.admin_event_qr_toggle, "QR-Code", ui.admin_event_qr_enabled),
            (self.layout.admin_event_gallery_toggle, "Galerie", ui.admin_event_gallery_enabled),
        )
        for rect, label, enabled in toggles:
            color = (20, 90, 30) if enabled else (75, 30, 30)
            pygame.draw.rect(self.screen, color, rect, border_radius=10)
            pygame.draw.rect(self.screen, (255, 255, 255), rect, width=2, border_radius=10)
            text = f"{label}: {'an' if enabled else 'aus'}"
            self._draw_text(
                text, self.font_body_admin, (230, 230, 230),
                (rect.x + 16, rect.centery - self.font_body_admin.get_linesize() // 2),
            )

        # ENTFERNT (Nutzer-Feedback): der Hinweis "Titel/Praefix/WLAN/
        # Schalter wirken erst nach einem Neustart der App." stand hier
        # redundant zur Speichern-Bestaetigungsseite (_draw_admin_event_saved)
        # - dort ist er jetzt die einzige, dafuer deutlich groessere Stelle.

    def _draw_admin_event_text_entry(self, model: AppModel) -> None:
        """NEU (Veranstaltungsdaten): eine gemeinsame QWERTZ-Tastatur fuer
        alle vier Textfelder (Titel/Praefix/WLAN-SSID/WLAN-Passwort) - welches
        Feld gerade bearbeitet wird, steht in ui.admin_event_edit_field (die
        Kopfzeile mit dem Feldnamen kommt bereits ueber status_text, siehe
        render()). Analog zu _draw_pin_entry(), aber mit freiem Text statt
        maskierten Ziffern.

        GEAENDERT (Nutzer-Feedback): "Umschalt" wirkt jetzt nicht mehr nur
        auf Buchstaben a-z, sondern zusaetzlich ueber KEYBOARD_SHIFT_MAP auf
        die Ziffernreihe/,.-  (siehe layout.py). WLAN-Passwort wird nicht
        mehr maskiert (immer Klartext). Ein Cursor-Strich zeigt die
        Schreibposition (immer am Ende - kein mittiges Editieren
        vorgesehen)."""
        ui = model.ui
        width, height = self.config.screen.width, self.config.screen.height

        buffer = ui.admin_event_text_buffer
        display_text = buffer + "|"
        # GEAENDERT (Nutzer-Feedback, Bugfix): text_cy sass bisher bei 0.075
        # (54px) - direkt im Bereich der Bildschirm-Ueberschrift (status_text,
        # z.B. "WLAN-Passwort", gezeichnet bei y=60..135, siehe render()) und
        # ueberlappte dadurch sowohl den Titel als auch die erste
        # Tastaturreihe. 0.26 laesst nach dem Titel (Ende ~0.19) genug Luft;
        # kb_y0 in layout.py wurde passend mit nach unten verschoben.
        text_cy = round(0.26 * height)
        # NEU (Nutzer-Feedback): Linie jetzt auch OBERHALB des Eingabetexts,
        # nicht mehr nur darunter - rahmt das Eingabefeld symmetrisch ein.
        # GEAENDERT (Nutzer-Feedback): Puffertext nutzt jetzt font_body_admin_
        # mono (dicktengleich) statt font_body_admin - der Zeilenabstand
        # richtet sich daher nach dessen (groesserer) Zeilenhoehe, sonst
        # wuerden die Linien den jetzt hoeheren Text knapp schneiden.
        half_line_gap = self.font_body_admin_mono.get_linesize() // 2 + 10
        line_y_above = text_cy - half_line_gap
        line_y_below = text_cy + half_line_gap
        for line_y in (line_y_above, line_y_below):
            pygame.draw.line(
                self.screen, (120, 120, 130), (round(0.15 * width), line_y), (round(0.85 * width), line_y), 2,
            )
        self._blit_center(display_text, self.font_body_admin_mono, (255, 255, 255), text_cy)

        if model.ui.error_text:
            # GEAENDERT (Nutzer-Feedback, Bugfix): ebenfalls von 0.105 (im
            # Titelbereich) nach unten verschoben - zwischen Titel und
            # Eingabefeld statt darueber.
            self._blit_center(model.ui.error_text, self.font_small, (255, 120, 120), round(0.21 * height))

        labels = {"backspace": "DEL", "submit": "Speichern", "cancel": "Abbrechen", "shift": "Umschalt", "space": "Leertaste"}
        colors = {
            "backspace": (120, 90, 0),
            "submit": (0, 130, 0),
            "cancel": (100, 100, 100),
            # NEU: aktiver Umschalt-Zustand optisch hervorgehoben, analog zu
            # den QR-/Galerie-Schaltern auf der Uebersicht.
            "shift": (0, 90, 130) if ui.admin_event_keyboard_shift else (70, 70, 75),
            "space": (70, 70, 75),
        }
        key_font_size = 34
        for name, rect in self.layout.keyboard_keys.items():
            if name in labels:
                label = labels[name]
            elif ui.admin_event_keyboard_shift and name in KEYBOARD_SHIFT_MAP:
                # NEU (Nutzer-Feedback): Sonderzeichen-Ebene (Ziffern/,.-).
                label = KEYBOARD_SHIFT_MAP[name]
            elif ui.admin_event_keyboard_shift and name.isalpha():
                # GEAENDERT (Nutzer-Feedback, Bugfix): frueher isascii()-
                # Zusatzbedingung liess ae/oe/ue bewusst klein - laut
                # Feedback sollen sie sich wie a-z verhalten. str.upper()
                # wandelt sie korrekt in Ae/Oe/Ue um, muss also nur noch mit
                # app_with_hw._map_admin_event_text_entry_click (tatsaechliche
                # Zeichenausgabe) im Einklang gehalten werden.
                label = name.upper()
            else:
                label = name
            self._draw_button(label, rect, colors.get(name, (55, 65, 85)), font_size=key_font_size)

    def _draw_admin_event_wallpaper_pick_loading(self, model: AppModel) -> None:
        """GEAENDERT (Nutzer-Feedback): Hintergrund-Thread laeuft (Stick
        suchen/mounten/Bilder AUFLISTEN, noch nichts kopiert), nicht
        abbrechbar - analog zu _draw_admin_usb_busy(). Umbenannt von
        _draw_admin_event_wallpaper_import."""
        self._blit_center(
            model.ui.status_text or "USB-Stick wird durchsucht ...",
            self.font_status_admin, (200, 235, 225),
            round(0.35 * self.config.screen.height),
        )
        # NEU (Nutzer-Feedback): Aufforderung, ueberhaupt erst einen Stick
        # einzustecken - vorher fehlte hier jede Anweisung dazu.
        self._blit_center(
            "Bitte einen USB-Stick mit Bilddateien (.png/.jpg) in den",
            self.font_body_admin, (170, 200, 195), round(0.48 * self.config.screen.height),
        )
        self._blit_center(
            "USB-Port rechts am Gehäuse einstecken.",
            self.font_body_admin, (170, 200, 195), round(0.53 * self.config.screen.height),
        )

    def _draw_admin_event_wallpaper_pick(self, model: AppModel) -> None:
        """NEU (Nutzer-Feedback): scrollbare Liste der auf dem Stick
        gefundenen Bilder - Antippen markiert eine Zeile (gruen hervorgehoben,
        gleiche Farbe wie die aktiven QR-/Galerie-Schalter auf der
        Uebersicht), "Speichern"/"Abbrechen" zeichnet bereits _draw_buttons().
        Gleiches Scroll-/Clip-/Hitbox-Muster wie _draw_admin_usb_conflicts:
        Zeilenposition haengt vom Scroll-Offset ab, daher dynamisch pro Frame
        neu berechnet statt aus layout.py, mit Hitboxen in Bildschirm-
        koordinaten fuer app_with_hw._map_click_to_event."""
        width, height = self.config.screen.width, self.config.screen.height
        candidates = model.ui.admin_event_wallpaper_candidates
        selected = model.ui.admin_event_wallpaper_selected
        self.wallpaper_pick_row_hitboxes = []

        top = round(0.22 * height)
        bottom = round(0.77 * height)
        row_h = round(0.075 * height)
        gap = round(0.012 * height)
        row_w = round((1 - 2 * 0.06) * width)
        row_x = round(0.06 * width)

        viewport = pygame.Rect(0, top, width, bottom - top)
        total_height = len(candidates) * (row_h + gap)
        max_scroll = max(0, total_height - viewport.height)
        self.wallpaper_pick_scroll_offset = max(0, min(self.wallpaper_pick_scroll_offset, max_scroll))

        previous_clip = self.screen.get_clip()
        self.screen.set_clip(viewport)
        y = top - self.wallpaper_pick_scroll_offset
        for name in candidates:
            if y + row_h >= top and y <= bottom:
                row_rect = pygame.Rect(row_x, y, row_w, row_h)
                is_selected = name == selected
                fill = (20, 90, 30) if is_selected else (45, 50, 60)
                pygame.draw.rect(self.screen, fill, row_rect, border_radius=10)
                pygame.draw.rect(self.screen, (255, 255, 255), row_rect, width=2, border_radius=10)
                text = self._truncate_text(name, self.font_body_admin, row_rect.width - 24)
                self._draw_text(
                    text, self.font_body_admin, (230, 230, 230),
                    (row_rect.x + 16, row_rect.centery - self.font_body_admin.get_linesize() // 2),
                )
                self.wallpaper_pick_row_hitboxes.append((row_rect, name))
            y += row_h + gap
        self.screen.set_clip(previous_clip)

    def _draw_admin_event_wallpaper_result(self, model: AppModel) -> None:
        """NEU (Veranstaltungsdaten): Ergebnis-Zeilen nach dem Wallpaper-
        Import, gruen/rot je nach admin_event_wallpaper_ok - analog zu
        _draw_admin_usb_lines(), hier zusaetzlich farblich nach Erfolg/
        Fehler statt einheitlichem Grau (der Erfolg/Fehler steht bereits als
        Kopfzeile ueber status_text, siehe render())."""
        ui = model.ui
        height = self.config.screen.height
        color = (150, 230, 170) if ui.admin_event_wallpaper_ok else (255, 150, 150)
        y = round(0.30 * height)
        line_height = self.font_body_admin.get_linesize() + 14
        for line in ui.admin_event_wallpaper_lines:
            self._blit_center(line, self.font_body_admin, color, y)
            y += line_height

    def _draw_admin_event_saved(self, model: AppModel) -> None:
        """NEU (Veranstaltungsdaten): Bestaetigung nach "Speichern" - Erfolg/
        Fehler-Meldung (gruen/rot) plus Hinweis auf den noetigen Neustart.
        GEAENDERT (Nutzer-Feedback, Bugfix): der Hinweistext gilt jetzt OHNE
        Ausnahme fuer alle vier Punkte (Titel/Praefix/WLAN/Schalter) - das
        Wallpaper wird seit dem Deferred-Save-Bugfix nicht mehr sofort beim
        Auswaehlen uebernommen, sondern erst hier, synchron als Teil dieses
        "Speichern"-Vorgangs (siehe app_with_hw._save_admin_event_settings /
        event_config_service.promote_pending_wallpaper), ist also kein
        Sonderfall mehr. Farbe passend zum leuchtenden Orange in
        _draw_admin_event_settings. "Jetzt neu starten"/"Später" werden
        bereits von _draw_buttons() gezeichnet.

        GEAENDERT (Nutzer-Feedback): der Dateiname (z.B. "event_config.json
        gespeichert.") steckte bisher in ui.admin_event_save_message - fuer
        den Admin keine relevante Information, siehe
        app_with_hw._save_admin_event_settings (ersetzt die Meldung dort im
        Erfolgsfall durch ein einfaches "Gespeichert."). Ausserdem: der
        Neustart-Hinweis ist jetzt die EINZIGE Stelle, an der er noch
        erscheint (auf der Uebersicht ADMIN_EVENT_SETTINGS wurde er als
        redundant entfernt) - deshalb deutlich groesser (font_body_admin
        statt font_small), damit er nicht uebersehen wird."""
        ui = model.ui
        height = self.config.screen.height
        color = (150, 230, 170) if ui.admin_event_save_ok else (255, 150, 150)
        self._blit_center(
            ui.admin_event_save_message or "Gespeichert.", self.font_body_admin, color, round(0.35 * height),
        )
        if ui.admin_event_save_ok:
            self._blit_center(
                "Titel/Präfix/WLAN/Schalter wirken erst nach einem Neustart der App.",
                self.font_body_admin, (255, 165, 0), round(0.48 * height),
            )

    def _draw_centered_in_range(
        self, text: str, font: pygame.font.Font, color: tuple[int, int, int], x0: int, x1: int, cy: int
    ) -> None:
        """Wie _blit_center(), aber horizontal zentriert innerhalb [x0, x1]
        statt ueber die gesamte Bildschirmbreite - noetig, seit die
        Einstell-Zeilen (Kamera-Menue 2.0) nur die rechte Bildschirmhaelfte
        belegen (links: Live-Vorschau-Panel, siehe
        _draw_admin_camera_preview_panel)."""
        max_width = max(10, x1 - x0)
        fitted_font = self._fit_text_font(text, font, max_width, floor=22)
        surface = fitted_font.render(text, True, color)
        rect = surface.get_rect(center=((x0 + x1) // 2, cy))
        self.screen.blit(surface, rect)

    def _draw_admin_camera_preview_panel(self, preview_frame: pygame.Surface | None) -> None:
        """NEU (Kamera-Menue 2.0, Nutzer-Feedback): Live-Bild in einem
        Panel links im Kamera-Einstellungen-Screen (statt wie bei
        PHOTO_PREVIEW/COUNTDOWN vollflaechig, siehe render()) - Blenden-
        Aenderungen sind bei eingebauter Kamera sonst weder zu hoeren noch
        zu sehen. Erhaelt das Seitenverhaeltnis des Kamerabilds per
        Letterboxing (grauer Rahmen bleibt sichtbar, kein Verzerren)."""
        panel = self.layout.admin_camera_preview
        pygame.draw.rect(self.screen, (10, 12, 16), panel, border_radius=10)
        if preview_frame is not None:
            frame_w, frame_h = preview_frame.get_size()
            if frame_w and frame_h:
                scale = min(panel.width / frame_w, panel.height / frame_h)
                target_w = max(1, round(frame_w * scale))
                target_h = max(1, round(frame_h * scale))
                scaled = pygame.transform.smoothscale(preview_frame, (target_w, target_h))
                dest = scaled.get_rect(center=panel.center)
                self.screen.blit(scaled, dest)
        else:
            # BUGFIX (Kamera-Menue 2.0, Eigenpruefung): _blit_center()
            # zentriert ueber die GESAMTE Bildschirmbreite, nicht nur das
            # Panel - der Platzhaltertext ueberlappte dadurch die rechte
            # Werte-Spalte (z.B. "Verschlusszeit"). Wie die Werte-Zeilen
            # jetzt auf den Panel-Bereich beschraenkt via
            # _draw_centered_in_range().
            self._draw_centered_in_range(
                "Live-Bild wird geladen …", self.font_small, (150, 150, 150), panel.left + 10, panel.right - 10, panel.centery,
            )
        pygame.draw.rect(self.screen, (90, 90, 95), panel, width=2, border_radius=10)

    # NEU (4.6): merkt sich fuer _draw_buttons, ob "Weiter" aktiv sein
    # darf. Wird in render() aus dem Modell gesetzt - _draw_buttons
    # bekommt nur den Zustand uebergeben, nicht das Modell.
    _usb_continue_enabled: bool = False

    # NEU (4.7): merkt sich, ob "Stick leeren" angeboten werden darf.
    _usb_not_enough_free: bool = False

    # NEU (Sprint 11, Feature 2): merkt sich, ob die +/- Buttons fuer ISO/
    # Blende gezeichnet werden duerfen (Kamera erreichbar).
    _admin_camera_available: bool = True
    # NEU (Kamera-Menue 2.0, Eigenpruefung): merkt sich, ob die Werte schon
    # eingetroffen sind (ADMIN_CAMERA_SETTINGS_READY) - waehrend des kurzen
    # "Lese Kamera-Einstellungen ..."-Zwischenzustands wurden vorher bereits
    # funktionslose +/- Buttons ohne zugehoerigen Text gezeichnet.
    _admin_camera_loaded: bool = True
    # NEU (Kamera-Menue 2.0): merkt sich, welche der beiden Seiten gerade
    # sichtbar ist (0=Belichtung, 1=Sonstiges) - _draw_buttons() braucht das,
    # um nur die Buttons der aktuellen Seite zu zeichnen.
    _admin_camera_page: int = 0

    def _draw_admin_usb_copy(self, model: AppModel) -> None:
        # GEAENDERT (4.8): Fortschrittsbalken statt durchlaufender
        # Dateinamen. Die Namen wechselten zu schnell zum Mitlesen; ein
        # Balken beantwortet die eigentliche Frage ("wie lange noch?")
        # deutlich besser.
        # NEU (Feedback): Service-Menue, nur fuer Lutz - font_status_admin
        # (urspruengliche Groesse) statt der fuer Gaeste vergroesserten
        # font_status_main_menu.
        height = self.config.screen.height
        text = model.ui.admin_usb_export_progress or "Export wird vorbereitet ..."
        self._blit_center(text, self.font_status_admin, (200, 235, 225), round(0.32 * height))
        self._draw_progress_bar(model.ui.admin_usb_progress_fraction, round(0.48 * height))
        # NEU (Sprint-11-Nachbesserung): zusaetzlich zum Fortschrittsbalken
        # eine bildliche Animation. ADMIN_USB_RESOLVE zeichnet mit derselben
        # Methode (siehe render()) - die Animation laeuft dort automatisch
        # mit.
        #
        # NEU (Sprint-11-Nachbesserung #2): der Fortschrittsbalken belegt
        # die erste Haelfte (0.0-0.5) mit dem eigentlichen Kopieren/
        # Aufloesen und die zweite Haelfte (0.5-1.0) mit der SHA256-
        # Pruefsummen-Kontrolle (siehe app_with_hw._emit_due_timers,
        # phase == "copy"/"resolve" vs. "verify" - dieselbe 0.5-Schwelle).
        # Waehrend des Kopierens bleibt die bisherige "fliegende" Animation
        # (Speicher -> Stick), waehrend der Pruefung zeigt eine eigene
        # Animation zwei Dateien im direkten Vergleich (siehe
        # _draw_admin_usb_verify_animation).
        if model.ui.admin_usb_progress_fraction < 0.5:
            self._draw_admin_usb_transfer_animation()
        else:
            self._draw_admin_usb_verify_animation()

    def _draw_admin_usb_transfer_animation(self) -> None:
        """NEU (Sprint-11-Nachbesserung): begleitet den USB-Export (und die
        Aufloesung gleichnamiger Dateikonflikte, ADMIN_USB_RESOLVE - nutzt
        dieselbe Zeichenmethode _draw_admin_usb_copy, siehe render())
        zusaetzlich zum Fortschrittsbalken mit einer Animation, analog zur
        Uebertragungs-Animation nach der Aufnahme
        (_draw_capture_transfer_animation): mehrere Datei-Symbole "fliegen"
        kontinuierlich vom Raspi-Speicher-Symbol (links, _draw_storage_icon)
        zum USB-Stick-Symbol (rechts, _draw_usb_stick_icon).

        Wie schon bei der Shredder-Animation
        (_draw_admin_delete_shredder_animation) rein zeitbasiert und endlos
        wiederholend (time.time()), OHNE direkten Bezug zu einzelnen
        tatsaechlich kopierten Dateien - das uebernimmt weiterhin
        ausschliesslich der Fortschrittsbalken/die Prozentzahl darunter."""
        width, height = self.config.screen.width, self.config.screen.height
        cy = round(height * 0.75)
        storage_x = round(width * 0.14)
        usb_x = round(width * 0.86)

        # Duenne gepunktete Verbindungslinie zwischen den beiden Symbolen -
        # gleiche Optik wie bei _draw_capture_transfer_animation.
        dot_gap = 18
        x = storage_x + 40
        while x < usb_x - 40:
            pygame.draw.circle(self.screen, (90, 140, 90), (x, cy), 2)
            x += dot_gap

        self._draw_storage_icon((storage_x, cy))
        self._draw_usb_stick_icon((usb_x, cy))

        cycle_seconds = 1.4
        lanes = 3
        # GEAENDERT (Nutzer-Feedback, Fotobeleg vom laufenden Pi): Smoothstep
        # bremst symmetrisch an BEIDEN Enden ab - bei einer ueber den vollen
        # Zyklus (0..1) laufenden Phase blieb das Datei-Symbol dadurch recht
        # lange direkt auf dem USB-Symbol "stehen", bevor die Phase zurueck
        # auf 0 sprang. Das sah wie ein einzelnes, verschmolzenes Symbol aus
        # (irrtuemlich als "zu breites USB-Symbol" wahrgenommen).
        #
        # NACHGEBESSERT (Nutzer-Feedback #2, Fotobeleg vom laufenden Pi):
        # der erste Fix (visible_fraction=0.82, Strecke auf 88% gedeckelt)
        # hat ueberkorrigiert - das Symbol blieb sichtbar VOR dem Ziel
        # stehen ("3 Punkte" Luecke zum USB-Stick). Jetzt legt das Symbol
        # die volle Strecke bis zum USB-Symbol zurueck (kein
        # travel_fraction-Deckel mehr) und wird stattdessen kurz vor/beim
        # Erreichen des Ziels weich ausgeblendet (siehe fade_from unten) -
        # es "landet" sichtbar auf dem Stick statt kurz davor zu verharren,
        # verschwindet dabei aber weich statt starr liegen zu bleiben.
        visible_fraction = 0.94
        now = time.time()
        for lane in range(lanes):
            cycle_count = int((now / cycle_seconds) + lane / lanes)
            phase = ((now / cycle_seconds) + lane / lanes) % 1.0
            if phase >= visible_fraction:
                continue
            local = phase / visible_fraction
            # Smoothstep statt linear - sanftes Anlaufen/Abbremsen, gleiche
            # Technik wie bei _draw_capture_transfer_animation. Laeuft jetzt
            # bis local=1.0 auf die volle Strecke (eased=1.0 -> file_x ==
            # usb_x), siehe Kommentar oben.
            eased = local * local * (3.0 - 2.0 * local)
            file_x = round(storage_x + eased * (usb_x - storage_x))
            bounce = round(-18 * math.sin(eased * math.pi))
            # Weiches Ausblenden im letzten Viertel der Sichtbarkeitsdauer,
            # nicht schon beim Erreichen des Ziels - dadurch ist das Symbol
            # tatsaechlich (kurz) auf dem USB-Stick sichtbar, verschwindet
            # dann aber weich statt dort einzufrieren.
            fade_from = 0.75
            if local > fade_from:
                fade_local = (local - fade_from) / (1.0 - fade_from)
                alpha = round(255 * (1.0 - fade_local))
            else:
                alpha = 255
            self._draw_file_icon((file_x, cy + bounce), key=(lane, cycle_count), alpha=alpha)

    def _draw_admin_usb_verify_animation(self) -> None:
        """NEU (Sprint-11-Nachbesserung #2): begleitet die SHA256-Pruef-
        summen-Kontrolle (zweite Haelfte von ADMIN_USB_COPY/ADMIN_USB_
        RESOLVE, siehe _draw_admin_usb_copy und admin_usb_export.py -
        Phase "verify") mit einer eigenen Animation statt der "fliegenden"
        Symbole aus der Kopier-Phase: zwei GROESSERE, unterschiedliche
        Datei-Symbole (siehe _FILE_ICON_COMPARE_SIZE) schieben sich von
        aussen kommend zusammen, ueberlappen sich kurz vollstaendig in der
        Bildschirmmitte ("werden verglichen") und schieben sich wieder
        auseinander - endlos wiederholend, rein zeitbasiert (time.time()),
        gleiches Prinzip wie die uebrigen Animationen dieser Datei. Das
        tatsaechliche Ergebnis (Haken/Kreuz) erscheint danach auf dem
        Abschluss-Screen, siehe _draw_admin_usb_result_badge() -
        waehrend der Pruefung selbst ist ja noch offen, ob am Ende alle
        Dateien uebereinstimmen."""
        width, height = self.config.screen.width, self.config.screen.height
        cx = width // 2
        cy = round(height * 0.75)

        cycle_seconds = 1.8
        now = time.time()
        cycle_count = int(now / cycle_seconds)
        phase = (now / cycle_seconds) % 1.0

        # Dreieckskurve (1 -> 0 -> 1 pro Zyklus), zusaetzlich mit Smoothstep
        # geglaettet: bei phase=0 stehen die Symbole am weitesten auseinander,
        # bei phase=0.5 liegen sie deckungsgleich uebereinander ("Vergleich"),
        # bei phase=1 (=0 des naechsten Zyklus) wieder auseinander.
        triangle = abs(1.0 - 2.0 * phase)
        eased = triangle * triangle * (3.0 - 2.0 * triangle)
        max_offset = round(width * 0.09)
        offset = round(eased * max_offset)

        pool = self._load_file_icon_pool(self._FILE_ICON_COMPARE_SIZE)
        index_a = hash(("usbverify-a", cycle_count)) % len(pool) if pool else 0
        index_b = hash(("usbverify-b", cycle_count)) % len(pool) if pool else 0
        # Zwei UNTERSCHIEDLICHE Symbole (siehe Nutzer-Wunsch "zwei
        # unterschiedliche Bilddateien-Symbole") - bei zufaelligem
        # Zusammentreffen auf denselben Index einfach den Nachbarn nehmen.
        if len(pool) > 1 and index_a == index_b:
            index_b = (index_b + 1) % len(pool)

        key_a = ("usbverify-a", cycle_count, index_a)
        key_b = ("usbverify-b", cycle_count, index_b)
        self._draw_file_icon((cx - offset, cy), key=key_a, size=self._FILE_ICON_COMPARE_SIZE)
        self._draw_file_icon((cx + offset, cy), key=key_b, size=self._FILE_ICON_COMPARE_SIZE)

        hint = "Dateien werden verglichen (Prüfsumme) ..."
        self._blit_center(hint, self.font_small, (170, 200, 190), round(height * 0.62))

    def _draw_admin_usb_result_badge(self, model: AppModel) -> None:
        """NEU (Sprint-11-Nachbesserung #2): grosses Ergebnis-Symbol
        (gruener Haken bei Erfolg, rotes Kreuz bei Pruefsummenfehlern/
        anderen Fehlern) auf dem Abschluss-Screen des USB-Exports
        (ADMIN_USB_EXPORT_DONE) - der Abschluss des in
        _draw_admin_usb_verify_animation begonnenen "Dateien werden
        verglichen"-Bildes: dort war das Ergebnis noch offen, hier steht
        es fest.

        BEWUSST als gezeichnete Form statt Unicode-Haken (U+2713): siehe
        Kommentar in admin_usb_export.py::ExportResult.summary_lines() -
        die Pygame-Schriftart auf dem Pi kennt dieses Zeichen nicht und
        zeichnet ersatzweise ein leeres Kaestchen.

        `model.ui.admin_usb_offer_delete` ist deckungsgleich mit
        ExportResult.ok (siehe state_machine._go_admin_usb_export_done:
        `admin_usb_offer_delete=ok`) und wird hier als Erfolgs-/Fehler-
        Signal wiederverwendet, statt ein eigenes UI-Feld nur fuer dieses
        Symbol anzulegen."""
        ok = model.ui.admin_usb_offer_delete
        width = self.config.screen.width
        center = (width - 130, 130)

        # Kurze "Pop"-Einblendung beim Betreten des Screens (Startzeitpunkt
        # siehe render()) - waechst binnen ca. 0.35s mit leichtem
        # Ueberschwingen auf volle Groesse, danach stabil stehen bleibend.
        duration = 0.35
        elapsed = duration
        if self._admin_usb_done_entered_at is not None:
            elapsed = time.time() - self._admin_usb_done_entered_at
        t = max(0.0, min(1.0, elapsed / duration))
        scale = math.sin(t * math.pi * 0.5) * (1.0 + 0.15 * math.sin(t * math.pi))
        radius = round(52 * scale)
        if radius <= 1:
            return

        color = (40, 170, 90) if ok else (200, 60, 60)
        ring = (25, 100, 55) if ok else (130, 35, 35)
        pygame.draw.circle(self.screen, color, center, radius)
        pygame.draw.circle(self.screen, ring, center, radius, width=max(2, round(radius * 0.08)))

        cx, cy = center
        s = radius / 52.0
        line_width = max(4, round(8 * s))
        if ok:
            points = [
                (round(cx - 24 * s), round(cy + 2 * s)),
                (round(cx - 8 * s), round(cy + 18 * s)),
                (round(cx + 26 * s), round(cy - 20 * s)),
            ]
            pygame.draw.lines(self.screen, (255, 255, 255), False, points, line_width)
        else:
            pygame.draw.line(
                self.screen, (255, 255, 255),
                (round(cx - 20 * s), round(cy - 20 * s)), (round(cx + 20 * s), round(cy + 20 * s)), line_width,
            )
            pygame.draw.line(
                self.screen, (255, 255, 255),
                (round(cx - 20 * s), round(cy + 20 * s)), (round(cx + 20 * s), round(cy - 20 * s)), line_width,
            )

    def _draw_progress_bar(
        self,
        fraction: float,
        y: int,
        color: tuple[int, int, int] = (0, 185, 110),
        track: tuple[int, int, int] = (22, 52, 48),
        border: tuple[int, int, int] = (90, 145, 135),
    ) -> None:
        """NEU (4.8): waagerechter Fortschrittsbalken, mittig, mit
        Prozentangabe darunter.

        GEAENDERT (4.9): Farben sind jetzt Parameter - der Loeschlauf nutzt
        denselben Balken in Rot. Ein gruener Balken waehrend einer
        unwiderruflichen Loeschung waere das falsche Signal.
        """
        width, height = self.config.screen.width, self.config.screen.height
        fraction = max(0.0, min(1.0, float(fraction)))

        bar_w = round(0.70 * width)
        bar_h = round(0.070 * height)
        bar_x = (width - bar_w) // 2
        radius = bar_h // 2

        outer = pygame.Rect(bar_x, y, bar_w, bar_h)
        # Hintergrund (leerer Teil)
        pygame.draw.rect(self.screen, track, outer, border_radius=radius)
        # Gefuellter Teil - Mindestbreite, damit bei 1 % nicht nichts zu sehen ist
        if fraction > 0.0:
            fill_w = max(bar_h, round(bar_w * fraction))
            inner = pygame.Rect(bar_x, y, fill_w, bar_h)
            pygame.draw.rect(self.screen, color, inner, border_radius=radius)
        # Rahmen
        pygame.draw.rect(self.screen, border, outer, width=3, border_radius=radius)

        # NEU (Feedback): wird nur von _draw_admin_usb_copy/
        # _draw_admin_delete_running aufgerufen (beide Service-Menue, nur
        # Lutz) - font_body_admin statt der fuer Gaeste vergroesserten
        # font_body.
        self._blit_center(
            f"{round(fraction * 100)} %", self.font_body_admin, (220, 240, 235), y + bar_h + 20,
        )

    def _draw_admin_usb_lines(self, model: AppModel) -> None:
        # NEU (4.6): Zeilenliste wie bei Diagnose und Loesch-Ergebnis.
        self._usb_continue_enabled = model.ui.admin_usb_device_ready
        self._usb_not_enough_free = model.ui.admin_usb_not_enough_free
        # NEU (Feedback): Service-Menue, nur fuer Lutz - font_body_admin
        # (urspruengliche Groesse) statt der fuer Gaeste vergroesserten
        # font_body.
        height = self.config.screen.height
        y = round(0.22 * height)
        line_height = self.font_body_admin.get_linesize() + 14
        for line in model.ui.admin_usb_lines:
            self._draw_text(line, self.font_body_admin, (230, 230, 230), (60, y))
            y += line_height

    def _draw_admin_usb_busy(self, model: AppModel) -> None:
        # NEU (4.6): laufender Vorgang - zentrierter Hinweis, kein Button.
        # NEU (Feedback): Service-Menue, nur fuer Lutz - font_status_admin
        # statt der fuer Gaeste vergroesserten font_status_main_menu.
        self._blit_center(
            model.ui.status_text or "Bitte warten ...",
            self.font_status_admin, (200, 235, 225),
            round(0.45 * self.config.screen.height),
        )

    def _truncate_text(self, text: str, font: pygame.font.Font, max_width: int) -> str:
        """Kuerzt text mit angehaengtem '...', falls er in max_width (Pixel)
        nicht hineinpasst. Lineares Abschneiden reicht hier - Dateinamen
        sind kurz genug, dass die Performance keine Rolle spielt."""
        if font.size(text)[0] <= max_width:
            return text
        ellipsis = "..."
        truncated = text
        while truncated and font.size(truncated + ellipsis)[0] > max_width:
            truncated = truncated[:-1]
        return (truncated + ellipsis) if truncated else ellipsis

    @staticmethod
    def _fit_text_font(text: str, font: pygame.font.Font, max_width: int, floor: int = 32) -> pygame.font.Font:
        """Verkleinert die Schrift schrittweise, falls text breiter waere
        als max_width - gleiche Technik wie das automatische Verkleinern
        langer Button-Labels in _draw_button(). Anders als _truncate_text()
        wird hier nichts abgeschnitten (der Text bleibt vollstaendig
        lesbar), sondern die ganze Zeile etwas kleiner dargestellt.

        Die pygame-Standardschrift (Font(None, size)) hat kein direktes
        "gib mir die Punktgroesse zurueck" - als Startpunkt fuer die
        Verkleinerungsschleife wird die Groesse daher aus der Zeilenhoehe
        zurueckgerechnet (linesize entspricht bei dieser Schrift ueber alle
        getesteten Groessen hinweg konstant ca. 75% der Punktgroesse).
        Bricht bei `floor` ab, damit der Text im Zweifel lieber knapp zu
        breit als unleserlich klein wird.
        """
        if font.size(text)[0] <= max_width:
            return font
        size = max(floor, round(font.get_linesize() / 0.75))
        fitted = font
        while fitted.size(text)[0] > max_width and size > floor:
            size -= 4
            fitted = pygame.font.Font(None, size)
        return fitted

    def _draw_conflict_checkbox(self, hitbox: pygame.Rect, checked: bool) -> None:
        """NEU (6c, ueberarbeitet nach Nutzer-Feedback): echte Kontrollkasten-
        Optik statt grosser Buttons - ein kleines Quadrat, zentriert im
        (fuer Touch bewusst groesseren) hitbox-Rect. Gefuellt und weiss
        umrandet, wenn ausgewaehlt, sonst nur ein duenner, gedaempfter
        Rahmen. Eine einzige Auswahlfarbe fuer beide Spalten (vorher zwei
        verschiedene Farben je Spalte) - liest sich naeher an einem
        echten Kontrollkaestchen."""
        size = round(min(hitbox.width, hitbox.height) * 0.55)
        box = pygame.Rect(0, 0, size, size)
        box.center = hitbox.center
        if checked:
            pygame.draw.rect(self.screen, (0, 130, 110), box, border_radius=6)
            pygame.draw.rect(self.screen, (255, 255, 255), box, width=2, border_radius=6)
        else:
            pygame.draw.rect(self.screen, (40, 40, 45), box, border_radius=6)
            pygame.draw.rect(self.screen, (120, 120, 125), box, width=2, border_radius=6)

    def _draw_admin_usb_conflicts(self, model: AppModel) -> None:
        """NEU (6c, ueberarbeitet nach Nutzer-Feedback): scrollbare Liste
        offener Namenskonflikte im Kontrollkasten-Look (zwei Spalten
        "Ueberschreiben"/"Umbenennen"), mit einer festen "Alle auswaehlen"-
        Zeile ganz oben fuer die Sammelaktion.

        GEAENDERT: die vorherige Fassung zeichnete einen Hinweistext
        ("N Dateien ...") auf derselben Bildschirmzeile wie die (damals
        noch grossen) Sammelaktions-Buttons - beide ueberlappten sich
        sichtbar. Der Hinweistext entfaellt jetzt ersatzlos (der Titel
        "Dateien mit abweichendem Inhalt gefunden" plus die sichtbaren
        Zeilen sagen bereits genug), stattdessen klare eigene Zeilen fuer
        Spaltenkoepfe und "Alle auswaehlen".

        Die X-Position beider Spalten kommt bewusst aus layout.py
        (usb_conflicts_overwrite_all/_rename_all .centerx) statt hier neu
        berechnet zu werden - dieselbe Spalte fuer Kopf, "Alle auswaehlen"
        und jede einzelne Dateizeile, ohne Duplizierung.

        Scrollen wie bei INSTRUCTIONS/TERMS ueber einen rein im Renderer
        gehaltenen Offset (reine Anzeigesache). Tipp-Erkennung je
        Dateizeile ueber usb_conflict_row_hitboxes (siehe
        app_with_hw._map_click_to_event) - die "Alle auswaehlen"-Zeile
        scrollt dagegen NICHT mit und nutzt daher die statischen Rects aus
        layout.py, genau wie jeder andere Button im Programm.
        """
        width, height = self.config.screen.width, self.config.screen.height
        conflicts = model.ui.admin_usb_conflicts
        self.usb_conflict_row_hitboxes = []

        overwrite_x = self.layout.usb_conflicts_overwrite_all.centerx
        rename_x = self.layout.usb_conflicts_rename_all.centerx

        # -- Spaltenkoepfe -------------------------------------------------
        # NEU (Feedback): urspruenglich 0.185 - sass fast auf dem Titel.
        # +16px Luft zwischen Titel-Unterkante und dieser Zeile.
        header_y = round(0.185 * height) + 16
        for text, cx in (("Überschreiben", overwrite_x), ("Umbenennen", rename_x)):
            surf = self.font_small.render(text, True, (200, 200, 205))
            rect = surf.get_rect(center=(cx, header_y))
            self.screen.blit(surf, rect)

        # -- "Alle auswaehlen"-Zeile (fest, scrollt nicht mit) --------------
        # NEU (Feedback): das Haekchen dieser Zeile ist kein eigener,
        # persistenter Zustand, sondern wird bei jedem Zeichnen live aus
        # den TATSAECHLICHEN Entscheidungen abgeleitet - sind gerade ALLE
        # offenen Konflikte auf "overwrite" gesetzt, ist das Haekchen dort
        # aktiv, analog fuer "rename". Bei gemischten Entscheidungen (z.B.
        # weil eine einzelne Zeile danach wieder umgestellt wurde) bleiben
        # beide leer - das ist korrekt und entspricht dem Verhalten einer
        # echten Checkbox-Gruppe (kein State-Machine-Feld noetig).
        # NEU (Feedback): Service-Menue, nur fuer Lutz - font_body_admin
        # (urspruengliche Groesse) statt der fuer Gaeste vergroesserten
        # font_body (gilt fuer den gesamten restlichen USB-Konflikt-Screen).
        all_row = self.layout.usb_conflicts_overwrite_all
        label_y = all_row.y + (all_row.height - self.font_body_admin.get_linesize()) // 2
        self._draw_text("Alle auswählen", self.font_body_admin, (210, 210, 215), (60, label_y))
        all_overwrite = bool(conflicts) and all(c.decision == "overwrite" for c in conflicts)
        all_rename = bool(conflicts) and all(c.decision == "rename" for c in conflicts)
        self._draw_conflict_checkbox(self.layout.usb_conflicts_overwrite_all, all_overwrite)
        self._draw_conflict_checkbox(self.layout.usb_conflicts_rename_all, all_rename)

        # -- Scrollbare Liste ------------------------------------------------
        top = round(0.335 * height)
        bottom = round(0.77 * height)
        row_h = round(0.075 * height)
        gap = round(0.012 * height)

        if not conflicts:
            # Sollte praktisch nie sichtbar werden (der Screen wird nur bei
            # mindestens einem Konflikt betreten), ist aber kein Fehlerfall.
            self._blit_center(
                "Keine offenen Konflikte mehr.", self.font_body_admin, (200, 200, 200), (top + bottom) // 2,
            )
            return

        viewport = pygame.Rect(0, top, width, bottom - top)
        total_height = len(conflicts) * (row_h + gap)
        max_scroll = max(0, total_height - viewport.height)
        self.usb_conflicts_scroll_offset = max(0, min(self.usb_conflicts_scroll_offset, max_scroll))

        checkbox_hitbox_w = round(0.09 * width)
        name_max_w = overwrite_x - checkbox_hitbox_w // 2 - 60 - round(0.02 * width)

        previous_clip = self.screen.get_clip()
        self.screen.set_clip(viewport)
        y = top - self.usb_conflicts_scroll_offset

        for conflict in conflicts:
            if y + row_h >= top and y <= bottom:
                name_y = y + (row_h - self.font_body_admin.get_linesize()) // 2
                label = self._truncate_text(conflict.name, self.font_body_admin, name_max_w)
                self._draw_text(label, self.font_body_admin, (230, 230, 230), (60, name_y))

                overwrite_rect = pygame.Rect(0, 0, checkbox_hitbox_w, row_h)
                overwrite_rect.center = (overwrite_x, y + row_h // 2)
                rename_rect = pygame.Rect(0, 0, checkbox_hitbox_w, row_h)
                rename_rect.center = (rename_x, y + row_h // 2)

                self._draw_conflict_checkbox(overwrite_rect, conflict.decision == "overwrite")
                self._draw_conflict_checkbox(rename_rect, conflict.decision == "rename")

                # Hitboxen in Bildschirm-Koordinaten (nicht relativ zum
                # Clip) - app_with_hw.py prueft sie gegen die tatsaechliche
                # Tap-Position.
                self.usb_conflict_row_hitboxes.append((overwrite_rect, conflict.name, "overwrite"))
                self.usb_conflict_row_hitboxes.append((rename_rect, conflict.name, "rename"))
            y += row_h + gap

        self.screen.set_clip(previous_clip)

    def _draw_admin_delete_confirm(self, model: AppModel) -> None:
        # NEU (4.4): Warntext gross und zentriert. status_text enthaelt
        # bereits Zeilenumbrueche (siehe state_machine._go_admin_delete_confirm),
        # daher zeilenweise zentriert setzen statt in einem Rutsch.
        # NEU (Feedback): Service-Menue, nur fuer Lutz - font_status_admin/
        # font_body_admin (urspruengliche Groesse) statt der fuer Gaeste
        # vergroesserten font_status_main_menu/font_body. Ausserdem etwas
        # weiter oben begonnen (0.30 -> 0.22, wie die anderen Admin-
        # Listenscreens), damit zu den Buttons darunter mehr Luft bleibt.
        height = self.config.screen.height
        lines = (model.ui.status_text or "").split("\n")
        y = round(0.22 * height)
        line_height = self.font_status_admin.get_linesize() + 10
        for line in lines:
            self._blit_center(line, self.font_status_admin, (255, 210, 210), y)
            y += line_height
        # Zusaetzlicher Hinweis, was "alles" konkret umfasst - beugt der
        # Fehlannahme vor, es gehe nur um die Bilder auf dem Bildschirm.
        # GEAENDERT (Sprint-11-Nachbesserung): "QR-Download" nur erwaehnen,
        # wenn die Funktion fuer diese Veranstaltung ueberhaupt aktiv ist
        # (config.qr_codes_enabled) - ohne QR-Funktion landet ohnehin nichts
        # im Web-Verzeichnis, das dieser Hinweis meinen koennte.
        # NEU (Sprint 11): der QR-Download ist ausschliesslich aus der
        # Galerie-Vollansicht heraus erreichbar - ohne Galerie-Funktion
        # (config.gallery_enabled) landet ebenfalls nichts im Web-
        # Verzeichnis, unabhaengig von qr_codes_enabled. Beide Schalter
        # muessen daher UND-verknuepft aktiv sein.
        hint = (
            "Betrifft Fotobox, QR-Download und Kamera-Speicherkarte."
            if (self.config.qr_codes_enabled and self.config.gallery_enabled)
            else "Betrifft Fotobox und Kamera-Speicherkarte."
        )
        self._blit_center(hint, self.font_body_admin, (230, 170, 170), y + 16)

    def _draw_admin_shutdown_confirm(self, model: AppModel) -> None:
        # NEU (Sprint-11-Nachbesserung, Nutzer-Feedback): gleiche Gestaltung
        # wie _draw_admin_delete_confirm - grosser, zentrierter Warntext,
        # etwas weiter oben begonnen, damit zu den Ja/Nein-Buttons darunter
        # genug Luft bleibt.
        height = self.config.screen.height
        self._blit_center(model.ui.status_text or "Wirklich herunterfahren?", self.font_status_admin, (255, 210, 210), round(0.30 * height))
        # GEAENDERT (Nutzer-Feedback): praeziserer Hinweis, WIE die Fotobox
        # wieder gestartet wird (Hauptschalter im Gehaeuse) statt der
        # bisherigen, vageren Formulierung "nur direkt am Gehaeuse".
        hint = "Die Fotobox kann nach dem Herunterfahren über den Hauptschalter im Gehäuse neu gestartet werden."
        self._blit_center(hint, self.font_body_admin, (230, 170, 170), round(0.30 * height) + self.font_status_admin.get_linesize() + 16)

    def _draw_admin_restart_confirm(self, model: AppModel) -> None:
        # NEU (Nutzer-Feedback): gleiche Gestaltung wie
        # _draw_admin_shutdown_confirm, mit einem sachlicheren Hinweistext -
        # ein App-Neustart ist (anders als Herunterfahren) folgenlos und
        # laeuft automatisch durch (siehe start_fotobox.sh).
        height = self.config.screen.height
        self._blit_center(
            model.ui.status_text or "Wirklich neu starten?", self.font_status_admin, (255, 225, 180), round(0.30 * height),
        )
        hint = "Die App beendet sich kurz und startet automatisch von selbst wieder."
        self._blit_center(
            hint, self.font_body_admin, (220, 195, 160), round(0.30 * height) + self.font_status_admin.get_linesize() + 16,
        )

    def _draw_admin_delete_running(self, model: AppModel) -> None:
        # GEAENDERT (4.9): Fortschrittsbalken wie beim Export, aber in Rot.
        # Kein Button - die Loeschung ist nicht abbrechbar.
        # NEU (Feedback): Service-Menue, nur fuer Lutz - font_status_admin
        # statt der fuer Gaeste vergroesserten font_status_main_menu.
        height = self.config.screen.height
        self._blit_center(
            model.ui.admin_delete_progress or "Bilder werden gelöscht ...",
            self.font_status_admin, (255, 210, 210), round(0.32 * height),
        )
        self._draw_progress_bar(
            model.ui.admin_delete_fraction, round(0.48 * height),
            color=(200, 45, 45), track=(58, 20, 20), border=(150, 95, 95),
        )
        # NEU (Sprint-11-Nachbesserung): zusaetzlich zum Fortschrittsbalken
        # eine bildliche Animation, siehe _draw_admin_delete_shredder_animation.
        self._draw_admin_delete_shredder_animation()

    def _draw_admin_delete_shredder_animation(self) -> None:
        """NEU (Sprint-11-Nachbesserung): begleitet ADMIN_DELETE_RUNNING
        zusaetzlich zum Fortschrittsbalken mit einer Animation - kleine
        Bilddatei-Symbole (dasselbe Icon wie bei der Uebertragungs-
        Animation, siehe _draw_file_icon) fallen von oben in der
        horizontalen Bildschirmmitte in einen Shredder darunter (ebenfalls
        horizontal mittig) und kommen unten als Schnipsel heraus.

        Rein zeitbasiert und endlos wiederholend (time.time(), gleiche
        Technik wie das blinkende Warnsymbol weiter oben in dieser Datei) -
        es gibt bewusst KEINEN direkten Bezug zu einzelnen tatsaechlich
        geloeschten Dateien (deren genaue Anzahl/Zeitpunkt kennt der
        Renderer nicht, nur die Bruchzahl admin_delete_fraction) - das
        uebernimmt weiterhin ausschliesslich der Fortschrittsbalken.
        Mehrere ueberlappende "Spuren" (lanes) sorgen fuer einen
        kontinuierlichen statt einen einzelnen, isolierten Durchlauf."""
        width, height = self.config.screen.width, self.config.screen.height
        cx = width // 2

        # Vertikale Zonen (von oben nach unten): Fortschrittsbalken +
        # Prozentzahl (siehe _draw_admin_delete_running, endet bei ca. 0.60),
        # Fallstrecke des Datei-Symbols (0.66 bis knapp vor den Schlitz),
        # Shredder-Koerper (Oberkante 0.78), Schnipsel-Fallstrecke darunter.
        shredder_rect = pygame.Rect(0, 0, round(width * 0.26), round(height * 0.05))
        shredder_rect.midtop = (cx, round(height * 0.78))

        drop_start_y = round(height * 0.66)
        slot_y = shredder_rect.top + 4
        shred_end_y = round(height * 0.95)

        self._draw_shredder_body(shredder_rect)

        cycle_seconds = 1.6
        lanes = 3
        now = time.time()
        for lane in range(lanes):
            cycle_count = int((now / cycle_seconds) + lane / lanes)
            phase = ((now / cycle_seconds) + lane / lanes) % 1.0
            if phase < 0.45:
                # Datei-Symbol faellt von oben in den Einzugsschlitz -
                # leichtes Beschleunigen statt linearer Bewegung, damit es
                # sich eher wie "hineinfallen" anfuehlt.
                local = phase / 0.45
                eased = local * local
                y = round(drop_start_y + eased * (slot_y - drop_start_y))
                self._draw_file_icon((cx, y), key=(lane, cycle_count))
            else:
                local = (phase - 0.45) / 0.55
                self._draw_shred_strips(cx, shredder_rect.bottom, shred_end_y, local, lane)

    def _draw_shredder_body(self, rect: pygame.Rect) -> None:
        """Stark vereinfachtes Shredder-Symbol (Vorbild: das im Refinement
        angehaengte Beispielbild) - dunkler Geraetekoerper mit einem
        Einzugsschlitz oben und gezackten "Zaehnen" am unteren Rand, aus
        denen die Papierschnipsel fallen (siehe _draw_shred_strips). Die
        Zaehne wackeln leicht (Sinuskurve ueber time.time()), um den
        laufenden "Mahl"-Vorgang anzudeuten."""
        pygame.draw.rect(self.screen, (70, 70, 78), rect, border_radius=8)
        pygame.draw.rect(self.screen, (200, 200, 205), rect, width=2, border_radius=8)

        slot = pygame.Rect(0, 0, rect.width - 30, 8)
        slot.center = (rect.centerx, rect.top + 14)
        pygame.draw.rect(self.screen, (25, 25, 28), slot, border_radius=4)

        jitter = round(2 * math.sin(time.time() * 20))
        tooth_count = max(3, round(rect.width / 14))
        tooth_w = rect.width / tooth_count
        for i in range(tooth_count):
            base_left = round(rect.left + i * tooth_w)
            base_right = round(rect.left + (i + 1) * tooth_w)
            tip_x = round((base_left + base_right) / 2)
            points = [
                (base_left, rect.bottom), (base_right, rect.bottom),
                (tip_x, rect.bottom + 10 + jitter),
            ]
            pygame.draw.polygon(self.screen, (70, 70, 78), points)
            pygame.draw.polygon(self.screen, (200, 200, 205), points, width=1)

    def _draw_shred_strips(self, cx: int, start_y: int, end_y: int, local: float, seed: int) -> None:
        """Zeichnet die Papierschnipsel, die unten aus dem Shredder fallen -
        mehrere kleine, leicht gestreute Streifen, die beim Fallen weiter
        auseinanderdriften und ausblassen. `local` ist der Fortschritt
        (0..1) innerhalb dieser Phase (siehe
        _draw_admin_delete_shredder_animation), `seed` unterscheidet die
        einzelnen "Spuren" (lanes) - feste Zufallswerte pro Spur statt
        echten Zufalls pro Frame, damit die Bewegung ruckelfrei bleibt
        (nur `local`, nicht die Zufallswerte selbst, aendert sich pro
        Frame)."""
        local = max(0.0, min(1.0, local))
        alpha = round(255 * (1.0 - local))
        if alpha <= 10:
            return
        rng = random.Random(seed * 97 + 13)
        fall_y = start_y + local * (end_y - start_y)
        spread = 1.0 + local * 2.4
        for i in range(6):
            dx = ((i - 2.5) * 9 + rng.uniform(-3, 3)) * spread
            x = round(cx + dx)
            y = round(fall_y + rng.uniform(-6, 6) + i * 2)
            strip_w, strip_h = 5, 14
            strip_surf = pygame.Surface((strip_w, strip_h), pygame.SRCALPHA)
            strip_surf.fill((235, 235, 235, alpha))
            pygame.draw.rect(strip_surf, (90, 90, 95, alpha), strip_surf.get_rect(), width=1)
            rotated = pygame.transform.rotate(strip_surf, dx * 4)
            strip_rect = rotated.get_rect(center=(x, y))
            self.screen.blit(rotated, strip_rect)

    def _draw_admin_delete_done(self, model: AppModel) -> None:
        # NEU (4.4): Zusammenfassung als Zeilenliste, gleiche Optik wie
        # die Diagnoseseite (_draw_admin_status).
        # NEU (Feedback): Service-Menue, nur fuer Lutz - font_body_admin
        # statt der fuer Gaeste vergroesserten font_body.
        height = self.config.screen.height
        y = round(0.22 * height)
        line_height = self.font_body_admin.get_linesize() + 14
        for line in model.ui.admin_delete_lines:
            self._draw_text(line, self.font_body_admin, (230, 230, 230), (60, y))
            y += line_height

    def _draw_admin_restart_pending(self, model: AppModel) -> None:
        # NEU (4.3): grosse, zentrierte Statuszeile - bewusst kein Titel,
        # kein Button (nicht abbrechbar, siehe state_machine.py).
        # NEU (Feedback): Service-Menue, nur fuer Lutz - font_status_admin
        # statt der fuer Gaeste vergroesserten font_status_main_menu.
        self._blit_center(
            model.ui.status_text or "App wird neu gestartet ...",
            self.font_status_admin,
            (255, 220, 120),
            round(0.45 * self.config.screen.height),
        )

    def _draw_button(
        self, label: str, rect: pygame.Rect, color: tuple[int, int, int], font_size: int | None = None,
    ) -> None:
        # Leichter Schatten nach rechts unten fuer einen dezenten 3D-Effekt.
        # Braucht eine separate SRCALPHA-Zwischenflaeche, weil self.screen
        # selbst keinen Alphakanal hat - echte Transparenz beim Zeichnen
        # geht nur ueber eine solche Flaeche, die per blit() ueberblendet
        # wird (gleiche Technik wie beim halbtransparenten Kreis in
        # _draw_cinema_countdown).
        #
        # Farbwahl bewusst Richtung Anthrazit (60,63,68) statt reinem
        # Schwarz: die meisten Bildschirm-Hintergruende dieser App liegen
        # selbst schon im sehr dunklen Bereich (z.B. (20,20,30)) - ein
        # schwarzer Schatten waere darauf kaum zu erkennen. Anthrazit ist
        # dort tatsaechlich heller als der Hintergrund und bleibt dadurch
        # als Tiefenkontur sichtbar, auch fuer Nutzer mit schwaecherem
        # Kontrastsehen.
        shadow_surface = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
        pygame.draw.rect(shadow_surface, (*self._SHADOW_COLOR, self._SHADOW_ALPHA), shadow_surface.get_rect(), border_radius=14)
        self.screen.blit(shadow_surface, (rect.x + self._SHADOW_OFFSET, rect.y + self._SHADOW_OFFSET))

        pygame.draw.rect(self.screen, color, rect, border_radius=14)
        pygame.draw.rect(self.screen, (255, 255, 255), rect, width=2, border_radius=14)

        # Schrift so gross wie moeglich, aber automatisch verkleinert, falls
        # ein langes Label (z.B. "Wirklich löschen") sonst ueberlaufen wuerde.
        # NEU (Sprint-11-Nachbesserung): optionaler `font_size`-Parameter,
        # damit einzelne Buttons (z.B. die ISO/Blende "+"/"-"-Buttons im
        # Service-Menue) bewusst groesser starten koennen als der sonst
        # uebliche Standard (50) - die Verkleinerungsschleife darunter
        # greift trotzdem weiterhin, falls das Label selbst dafuer zu breit
        # waere.
        max_w = rect.width - 24
        size = font_size if font_size is not None else 50
        font = self.font_button if font_size is None else pygame.font.Font(None, size)
        text_surface = font.render(label, True, (255, 255, 255))
        while text_surface.get_width() > max_w and size > 24:
            size -= 4
            font = pygame.font.Font(None, size)
            text_surface = font.render(label, True, (255, 255, 255))
        # BUGFIX (Nutzer-Feedback nach Live-Test, Screenshot-Vergleich): bei
        # diesem Font sitzt das sichtbare Zeichen von "+"/"<"/">" spuerbar
        # UNTERHALB der Flaechenmitte ihrer Render-Oberflaeche (anders als
        # bei "-", das schon nahezu mittig sitzt) - eine reine
        # Flaechen-Zentrierung (wie sonst ueberall) wirkt bei diesen drei
        # Zeichen dadurch sichtbar zu tief. Nur fuer genau diese drei
        # Ein-Zeichen-Labels (ausschliesslich als +/-/</>-Tasten im Kamera-
        # Menue verwendet) wird deshalb die tatsaechliche Tinte (sichtbare
        # Pixel, nicht die Render-Flaeche) auf die Button-Mitte ausgerichtet.
        if label in ("+", "<", ">"):
            ink_rect = text_surface.get_bounding_rect()
            topleft = (rect.centerx - ink_rect.centerx, rect.centery - ink_rect.centery)
            self.screen.blit(text_surface, topleft)
        else:
            text_rect = text_surface.get_rect(center=rect.center)
            self.screen.blit(text_surface, text_rect)

    def _draw_text(self, text: str, font: pygame.font.Font, color: tuple[int, int, int], pos: tuple[int, int]) -> None:
        """Rendert Text; unterstuetzt mehrzeilige Strings ueber "\\n"-Trennung
        (fuer einzeilige Texte ohne "\\n" identisch zum bisherigen Verhalten)."""
        x, y = pos
        line_height = font.get_linesize()
        for line in text.split("\n"):
            surf = font.render(line, True, color)
            self.screen.blit(surf, (x, y))
            y += line_height

    # GEAENDERT (Nutzer-Feedback): einheitliche Schatten-Konstanten fuer das
    # gesamte Projekt - Buttons (_draw_button), Karten (_draw_qr_card,
    # _draw_text_card) und jetzt auch Ueberschriften-Text
    # (_draw_shadowed_text) verwenden alle denselben Versatz (nach rechts
    # unten, Lichtquelle gedacht oben links) und denselben Anthrazit-Ton mit
    # gleicher Transparenz statt bisher leicht abweichender Werte je Stelle.
    _SHADOW_OFFSET = 6
    _SHADOW_COLOR = (60, 63, 68)
    _SHADOW_ALPHA = 140

    def _draw_shadowed_text(
        self, text: str, font: pygame.font.Font, color: tuple[int, int, int], pos: tuple[int, int],
    ) -> None:
        """Rendert Text mit Schlagschatten - gleiche Optik (Richtung, Farbe,
        Transparenz) wie der Schatten von Buttons/Karten (siehe
        _SHADOW_OFFSET/_SHADOW_COLOR/_SHADOW_ALPHA). Ersetzt den zuvor nur
        beim Haupttitel "Fotobox" per Hand nachgebauten (und davon
        abweichenden) Text-Schatten sowie das Fehlen jeglichen Schattens bei
        den uebrigen Bildschirm-Ueberschriften (Service-Menü,
        Status/Diagnose, ...) - jetzt sehen alle Ueberschriften gleich aus.
        Unterstuetzt mehrzeilige Strings ueber "\\n" wie _draw_text.

        Nur fuer Ueberschriften/freistehenden Text gedacht - Button-
        Beschriftungen bleiben bewusst flach ohne Schatten (siehe
        _draw_button, unveraendert)."""
        x, y = pos
        line_height = font.get_linesize()
        for line in text.split("\n"):
            shadow_surf = font.render(line, True, self._SHADOW_COLOR)
            shadow_surf.set_alpha(self._SHADOW_ALPHA)
            self.screen.blit(shadow_surf, (x + self._SHADOW_OFFSET, y + self._SHADOW_OFFSET))
            surf = font.render(line, True, color)
            self.screen.blit(surf, (x, y))
            y += line_height

    # NEU (Nutzer-Feedback): manche Bildschirm-Ueberschriften kommen aus
    # Laufzeit-Text (model.ui.status_text, z.B. "Dateien mit abweichendem
    # Inhalt gefunden" auf ADMIN_USB_CONFLICTS) statt aus kurzen, fest
    # geprueften Konstanten wie "Service-Menü" - bei voller font_title-
    # Groesse (100pt) ragten laengere Varianten davon rechts ueber den
    # Bildschirmrand hinaus. _title_font_for() liefert stattdessen bei
    # Bedarf eine kleinere, noch passende Variante derselben Schriftart.
    _TITLE_FONT_MIN_SIZE = 40
    _TITLE_FONT_STEP = 4

    def _title_font_for(self, text: str, max_width: int) -> pygame.font.Font:
        """Liefert font_title unveraendert, falls `text` darin bereits in
        `max_width` passt - sonst die groesste noch passende, kleinere
        Variante derselben Schriftart (Cache pro Text+max_width, damit
        nicht jedes Frame neu nach unten gesucht/ein neues Font-Objekt
        erzeugt wird - dieselbe Statuszeile steht ja meist mehrere Frames
        lang unveraendert)."""
        if self.font_title.size(text)[0] <= max_width:
            return self.font_title

        cache_key = (text, max_width)
        cached = self._title_font_cache.get(cache_key)
        if cached is not None:
            return cached

        font = pygame.font.Font(None, self._TITLE_FONT_MIN_SIZE)
        size = 100
        while size > self._TITLE_FONT_MIN_SIZE:
            size -= self._TITLE_FONT_STEP
            candidate = pygame.font.Font(None, size)
            if candidate.size(text)[0] <= max_width:
                font = candidate
                break

        self._title_font_cache[cache_key] = font
        return font

    def _draw_text_card(
        self,
        text: str,
        font: pygame.font.Font,
        text_color: tuple[int, int, int],
        center: tuple[int, int],
        padding_x: int = 40,
        padding_y: int = 20,
        card_opacity: float = 0.5,
    ) -> None:
        """NEU (Lesbarkeit Hauptmenue-Begruessungstext): zeichnet eine
        weisse, abgerundete Karte mit Schatten hinter zentriertem Text -
        gleiche Schatten-Technik wie _draw_button (SRCALPHA-Zwischenflaeche,
        Versatz 6px, Anthrazit-Ton (60,63,68) statt Schwarz). Anders als bei
        Buttons wird bewusst KEIN weisser Rahmen (width=2) um die Karte
        gezogen - der Nutzer wollte explizit "kein erkennbarer Rahmen".
        Unterstuetzt mehrzeilige Texte ueber "\\n" (aktuell ungenutzt, aber
        konsistent zu _draw_text).

        GEAENDERT (Nutzer-Feedback, zwei Nachbesserungsrunden: 75% -> 50%
        deckend): die Karte selbst ist jetzt um `card_opacity` durchscheinend
        (Default 0.5 = 50% deckend / 50% transparent, vom Nutzer als
        "perfekt" bestaetigt) statt vollstaendig deckend weiss - dafuer wird
        die Karte (wie schon der Schatten) ueber eine
        SRCALPHA-Zwischenflaeche geblittet statt direkt mit
        pygame.draw.rect() auf den (selbst nicht transparenten) Bildschirm
        gezeichnet.
        """
        lines = text.split("\n")
        line_height = font.get_linesize()
        line_surfaces = [font.render(line, True, text_color) for line in lines]
        text_w = max(surf.get_width() for surf in line_surfaces)
        text_h = line_height * len(lines)

        card_rect = pygame.Rect(0, 0, text_w + 2 * padding_x, text_h + 2 * padding_y)
        card_rect.center = center

        shadow_surface = pygame.Surface((card_rect.width, card_rect.height), pygame.SRCALPHA)
        pygame.draw.rect(shadow_surface, (*self._SHADOW_COLOR, self._SHADOW_ALPHA), shadow_surface.get_rect(), border_radius=24)
        self.screen.blit(shadow_surface, (card_rect.x + self._SHADOW_OFFSET, card_rect.y + self._SHADOW_OFFSET))

        card_alpha = round(255 * card_opacity)
        card_surface = pygame.Surface((card_rect.width, card_rect.height), pygame.SRCALPHA)
        pygame.draw.rect(card_surface, (255, 255, 255, card_alpha), card_surface.get_rect(), border_radius=24)
        self.screen.blit(card_surface, card_rect.topleft)

        y = card_rect.top + padding_y
        for surf in line_surfaces:
            line_rect = surf.get_rect(centerx=card_rect.centerx, top=y)
            self.screen.blit(surf, line_rect)
            y += line_height

    def _draw_footer(self, model: AppModel, fps: float) -> None:
        if not self.config.features.debug_overlay:
            return
        lines = [
            f"Letztes Event: {model.last_event.type.name if model.last_event else '-'}",
            f"Fotos im Speicher: {len(model.session.photos)}",
            f"FPS: {fps:.1f}",
            "ESC beendet diese Test-App.",
        ]
        y = self.config.screen.height - 140
        for line in lines:
            self._draw_text(line, self.font_small, (220, 220, 220), (60, y))
            y += 30

    @staticmethod
    def _background_color(state: AppState) -> tuple[int, int, int]:
        return {
            AppState.BOOT: (10, 25, 47),
            AppState.MAIN_MENU: (20, 20, 30),
            AppState.PHOTO_INTRO: (30, 30, 40),
            AppState.ATTRACT_GALLERY: (25, 25, 45),
            AppState.GALLERY_GRID: (15, 15, 20),
            # NEU (Etappe 7): dasselbe ruhige Dunkel-Tuerkis wie
            # GALLERY_GRID_BREATHE (LED) andeutungsweise im Hintergrund -
            # sichtbar zur Galerie-Familie gehoerig, aber warm genug fuer
            # eine einladende Botschaft statt eines rein technischen Grids.
            AppState.GALLERY_EMPTY: (10, 22, 20),
            AppState.GALLERY_FULLSCREEN: (5, 5, 5),
            # NEU (Sprint 11, Feature 4): dasselbe Dunkel wie GALLERY_FULLSCREEN -
            # das Foto fuellt ohnehin den ganzen Bildschirm (render() ruft
            # zuerst _draw_gallery_fullscreen()), diese Farbe ist nur der
            # Rahmen fuer den Bruchteil einer Sekunde vor dem ersten Blit.
            AppState.GALLERY_PHOTO_QR: (5, 5, 5),
            AppState.PHOTO_PREVIEW: (30, 30, 40),
            AppState.COUNTDOWN: (60, 30, 20),
            AppState.CAPTURE_PENDING: (20, 40, 20),
            AppState.REVIEW: (40, 50, 40),
            AppState.DELETE_CONFIRM: (50, 20, 20),
            AppState.QR_DISPLAY: (35, 20, 90),
            AppState.INSTRUCTIONS: (20, 20, 35),
            AppState.TERMS: (20, 20, 35),
            AppState.ERROR_SCREEN: (80, 10, 10),
            AppState.PIN_ENTRY: (18, 22, 30),          # NEU (3.3)
            AppState.SHUTDOWN_GOODBYE: (10, 10, 15),   # NEU (3.3)
            AppState.MAINTENANCE: (50, 50, 10),
            AppState.ADMIN_MENU: (18, 22, 30),         # NEU (4.1) - wie PIN_ENTRY
            AppState.ADMIN_STATUS: (18, 22, 30),       # NEU (4.3) - wie ADMIN_MENU
            AppState.ADMIN_CAMERA_SETTINGS: (18, 22, 30),  # NEU (Sprint 11, Feature 2) - wie ADMIN_STATUS
            AppState.ADMIN_RESTART_PENDING: (20, 40, 20),  # NEU (4.3) - wie CAPTURE_PENDING
            # NEU (Sprint-11-Nachbesserung): gleiches Warnrot wie
            # ADMIN_DELETE_CONFIRM - beide sind "gefaehrliche" Sicherheitsabfragen.
            AppState.ADMIN_SHUTDOWN_CONFIRM: (55, 8, 8),
            # NEU (Nutzer-Feedback): eigener, gedaempft-oranger statt roter
            # Ton - ein Neustart ist eine deutlich weniger "gefaehrliche"
            # Bestaetigung als Herunterfahren/Loeschen (folgenlos, laeuft
            # automatisch durch), soll sich aber trotzdem klar von den
            # ruhigen blaugrauen Admin-Screens abheben.
            AppState.ADMIN_RESTART_CONFIRM: (45, 32, 8),
            # NEU (4.4): kraeftiges Dunkelrot als unuebersehbares Warnsignal,
            # deutlich abgesetzt vom ruhigen Blaugrau der uebrigen Admin-Screens.
            AppState.ADMIN_DELETE_CONFIRM: (55, 8, 8),
            AppState.ADMIN_DELETE_RUNNING: (40, 8, 8),
            AppState.ADMIN_DELETE_DONE: (18, 22, 30),
            # NEU (4.6): gedecktes Blaugruen - klar unterscheidbar vom
            # Rot der Loeschwege, gleiche Ruhe wie die uebrigen Admin-Screens.
            AppState.ADMIN_USB_WAIT: (12, 28, 28),
            AppState.ADMIN_USB_CHECK: (12, 28, 28),
            AppState.ADMIN_USB_READY: (10, 32, 26),
            AppState.ADMIN_USB_PROBLEM: (45, 32, 8),
            AppState.ADMIN_USB_EJECT: (12, 28, 28),
            AppState.ADMIN_USB_REMOVE: (10, 32, 26),
            AppState.ADMIN_USB_COPY: (12, 28, 28),      # NEU (4.7)
            AppState.ADMIN_USB_EXPORT_DONE: (10, 32, 26),  # NEU (4.7)
            # NEU (6c): gelblich gedeckt - Aufmerksamkeit noetig (Entscheidung
            # gefragt), aber keine Stoerung; angelehnt an ADMIN_USB_PROBLEM,
            # nur weniger intensiv (kein echtes Problem, nur eine Nachfrage).
            AppState.ADMIN_USB_CONFLICTS: (38, 30, 10),
            # NEU (6c): gleiches Blaugruen wie der eigentliche Kopierlauf -
            # aus Sicht des Gastes/Betreibers "es wird weiter geschrieben".
            AppState.ADMIN_USB_RESOLVE: (12, 28, 28),
            # NEU (Veranstaltungsdaten): wie PIN_ENTRY/ADMIN_STATUS - ruhiges
            # Blaugrau fuer normale Service-Menue-Bildschirme.
            AppState.ADMIN_EVENT_SETTINGS: (18, 22, 30),
            AppState.ADMIN_EVENT_TEXT_ENTRY: (18, 22, 30),
            AppState.ADMIN_EVENT_SAVED: (18, 22, 30),
            # NEU (Nutzer-Feedback): Auswahlliste - gleiches ruhige Blaugrau
            # wie die uebrigen interaktiven Veranstaltungsdaten-Screens.
            AppState.ADMIN_EVENT_WALLPAPER_PICK: (18, 22, 30),
            # NEU (Veranstaltungsdaten): gleiches Blaugruen wie die
            # USB-Vorgaenge (ADMIN_USB_CHECK) - laeuft, nicht abbrechbar.
            # Umbenannt von ADMIN_EVENT_WALLPAPER_IMPORT.
            AppState.ADMIN_EVENT_WALLPAPER_PICK_LOADING: (12, 28, 28),
            # NEU (Veranstaltungsdaten): gleiches Gruen wie ADMIN_USB_READY -
            # Ergebnis-Screen (jetzt nur noch Fehlerfall).
            AppState.ADMIN_EVENT_WALLPAPER_RESULT: (10, 32, 26),
        }[state]
