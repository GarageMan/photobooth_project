"""Nutzungsbedingungen der Fotobox als Absatzliste (DE/EN).

GEAENDERT (Sprint 15, Fix 2): bisher direkt in renderer._draw_terms -
jetzt eigenes Modul, damit der Bildschirm (renderer.py) und der Word-Export
fuer USB (terms_docx.py) garantiert denselben Text verwenden.

Absatz-Formate (siehe renderer._build_paragraph_lines):
- ""                -> Leerzeile
- "Text"            -> Fliesstext (wird umgebrochen)
- ("H", text)       -> Ueberschrift
- ("RAW", text)     -> genau eine Zeile (Adressbloecke)
- ("BUL", text)     -> Aufzaehlungspunkt mit haengendem Einzug
"""
from __future__ import annotations

from dataclasses import dataclass

import social_media_settings
from social_media_settings import PLATFORM_BY_KEY, SocialMediaSettings

# Betreiber der Fotobox (unveraendert aus renderer._draw_terms uebernommen).
# GEAENDERT (Nutzer-Feedback 2026-11-15): Angaben in EINER Zeile, durch
# Kommata getrennt (Platz: DE und EN sollen je auf ein DIN-A4-Blatt
# passen); EN mit Land und internationaler Rufnummer.
OPERATOR_LINE_DE = "Lutz Buchholz, Dechant-Fein-Str. 24, 51375 Leverkusen, lutz-peter@imail.de, 0163 8506144"
OPERATOR_LINE_EN = (
    "Lutz Buchholz, Dechant-Fein-Str. 24, 51375 Leverkusen, Germany, "
    "lutz-peter@imail.de, +49 163 8506144"
)


@dataclass(frozen=True)
class TermsContext:
    gallery_enabled: bool
    qr_enabled: bool
    wifi_ssid: str
    wifi_password: str
    social: SocialMediaSettings

    @property
    def qr_active(self) -> bool:
        # QR-Download ist nur aus der Galerie heraus erreichbar (Sprint 11)
        # - beide Schalter muessen aktiv sein.
        return self.qr_enabled and self.gallery_enabled


def context_from_config(config) -> TermsContext:
    return TermsContext(
        gallery_enabled=bool(config.gallery_enabled),
        qr_enabled=bool(config.qr_codes_enabled),
        wifi_ssid=config.network.guest_wifi_ssid,
        wifi_password=config.network.guest_wifi_password,
        social=social_media_settings.settings_from_config(config),
    )


def _channel_bullets(s: SocialMediaSettings, lang: str) -> list:
    publisher = s.publisher or ("the organiser" if lang == "en" else "dem Veranstalter")
    items: list = []
    # GEAENDERT (Nutzer-Feedback 2026-11-15, Platz): Kanaele mit gleichem
    # Betreiber und gleicher Uebermittlungsangabe (z. B. Instagram und
    # Facebook) in EINEM Aufzaehlungspunkt.
    groups: dict[tuple[str, str], list[str]] = {}
    for key in s.platforms:
        if key == "web":
            continue
        p = PLATFORM_BY_KEY[key]
        operator = p.operator_en if lang == "en" else p.operator_de
        transfer = p.transfer_en if lang == "en" else p.transfer_de
        if not transfer:
            transfer = (
                "Processing takes place in the EU." if lang == "en"
                else "Die Verarbeitung erfolgt in der EU."
            )
        groups.setdefault((operator, transfer), []).append(p.label)
    prefix = "operator" if lang == "en" else "Betreiber"
    for (operator, transfer), labels in groups.items():
        items.append(("BUL", f"{', '.join(labels)}: {prefix} {operator}. {transfer}"))
    if "web" in s.platforms:
        items.append(("BUL", (
            f"Website of {publisher} - no disclosure to a platform operator."
            if lang == "en" else
            f"Webseite von {publisher} - keine Weitergabe an einen Plattformbetreiber."
        )))
    for name in s.other_names:
        items.append(("BUL", (
            f"{name}: operator and data processing according to that "
            "platform's privacy policy; a transfer to countries outside "
            "the EU is possible."
            if lang == "en" else
            f"{name}: Betreiber und Datenverarbeitung gemäß den "
            "Datenschutzbestimmungen der Plattform; eine Übermittlung in "
            "Länder außerhalb der EU ist möglich."
        )))
    if s.handle:
        items.append(("BUL", (
            f"Profile/channel: {s.handle}" if lang == "en" else f"Profil/Kanal: {s.handle}"
        )))
    return items


def _social_section(s: SocialMediaSettings, lang: str) -> list:
    pub = s.publisher or ("the organiser" if lang == "en" else "der Veranstalter")
    phrase = social_media_settings.channel_phrase(s, lang)
    only_web = s.platforms == ("web",) and not s.other_names
    if lang == "en":
        platforms_note = (
            "" if only_web else
            "The platform operators also process published photos under "
            "their own responsibility and privacy policies; if data is "
            "transferred to countries outside the EU, the level of data "
            "protection there may be lower. "
        )
        section = [
            ("H", "Publication"),
            "",
            (
                f"After saving, the photo booth asks whether {pub} may "
                f"publish your photo {phrase} (also for advertising "
                "purposes). Only if you tap \"Yes\" will the photo be released "
                f"for this purpose and handed over to {pub} marked as such. "
                "Your consent is voluntary - without it you can use the "
                "photo booth without restriction and your photo stays saved."
            ),
            "",
            "Planned channels:",
            *_channel_bullets(s, lang),
            "",
            (
                "Please only agree if everyone in the photo agrees. For "
                "children and young people under 16, a parent or legal "
                "guardian must decide."
            ),
            (
                "Published photos are publicly visible. " + platforms_note
                + f"You can withdraw your consent at any time by contacting "
                f"{pub} - the photo will then be removed. Copies already made by "
                "third parties cannot be retrieved."
            ),
        ]
        if s.privacy_url:
            section.append(f"More information on data protection at {pub}: {s.privacy_url}")
        return section + [""]
    platforms_note = (
        "" if only_web else
        "Die Plattformbetreiber verarbeiten veröffentlichte Fotos zusätzlich "
        "in eigener Verantwortung nach ihren Datenschutzbestimmungen; bei "
        "einer Übermittlung in Länder außerhalb der EU kann dort ein "
        "geringeres Datenschutzniveau bestehen. "
    )
    section = [
        ("H", "Veröffentlichung"),
        "",
        (
            f"Nach dem Speichern fragt dich die Fotobox, ob {pub} dein Foto "
            f"{phrase} veröffentlichen darf (auch zu Werbezwecken). Nur wenn "
            "du \"Ja\" antippst, wird das Foto dafür freigegeben und "
            f"gekennzeichnet an {pub} übergeben. Deine Einwilligung ist "
            "freiwillig - ohne sie kannst du die Fotobox uneingeschränkt "
            "nutzen, dein Foto bleibt gespeichert."
        ),
        "",
        "Vorgesehene Kanäle:",
        *_channel_bullets(s, lang),
        "",
        (
            "Bitte stimme nur zu, wenn alle Personen auf dem Foto "
            "einverstanden sind. Für Kinder und Jugendliche unter 16 Jahren "
            "muss eine erziehungsberechtigte Person entscheiden."
        ),
        (
            "Veröffentlichte Fotos sind öffentlich sichtbar. " + platforms_note
            + f"Du kannst deine Einwilligung jederzeit bei {pub} widerrufen - "
            "das Foto wird dann entfernt. Bereits von Dritten erstellte "
            "Kopien lassen sich nicht zurückholen."
        ),
    ]
    if s.privacy_url:
        section.append(f"Weitere Informationen zum Datenschutz bei {pub}: {s.privacy_url}")
    return section + [""]


def build_terms_paragraphs(ctx: TermsContext, lang: str = "de") -> list:
    s = ctx.social
    social = s.enabled
    if lang == "en":
        pub = s.publisher or "the organiser"
        if ctx.gallery_enabled:
            gallery_visibility_text = (
                "During the event, your photos are visible to other "
                "guests of the photo booth on the display. Anyone who "
                "opens the gallery can view every photo in it"
                + (" and also download it" if ctx.qr_enabled else "")
                + " - not just their own."
            )
        else:
            gallery_visibility_text = (
                "A gallery feature is not active for this event - other "
                "guests cannot view your photos on the photo booth's "
                "display."
            )
        qr_download_paragraphs: list = [
            ("H", "Local download (WiFi)"),
            "",
            (
                f"Via the WiFi network \"{ctx.wifi_ssid}\" "
                f"(password: {ctx.wifi_password}) you can "
                "download your photo after the shot using a QR code. Since "
                "this is an event network, the image files are theoretically "
                "viewable by other logged-in users. Don't download any "
                "pictures if you're not comfortable with that."
            ),
            "",
        ] if ctx.qr_active else []
        return [
            ("H", "Terms of use for the photo booth"),
            "",
            (
                "By using this photo booth (e.g. by pressing the shutter "
                "button) you agree that photographs will be taken of you. "
                "Use of the photo booth is voluntary."
            ),
            "",
            ("H", "Purpose & storage"),
            "",
            (
                "The photos serve as a keepsake of the event. They are "
                f"stored locally on the photo booth and handed over to {pub} "
                "as the organiser after the event."
            ) if social else (
                "The photos serve as a keepsake for family, friends and "
                "relatives as well as the host. They are first stored "
                "locally on the photo booth and are subsequently processed "
                "further by the host within a private circle."
            ),
            gallery_visibility_text,
            (
                "Photos are only published if you expressly allow it "
                "(see \"Publication\" below). Otherwise they are not passed "
                "on to uninvolved third parties, published online, or used "
                "commercially."
            ) if social else (
                "The photos are not passed on to uninvolved third parties, "
                "published online, or used commercially."
            ),
            "",
            *(_social_section(s, "en") if social else []),
            *qr_download_paragraphs,
            ("H", "Your rights"),
            "",
            (
                "You can object to the storage of your picture directly "
                "after the shot via the \"Delete\" button. You also have "
                "the right, at any time, to information, correction, "
                "deletion, restriction of processing, data portability and "
                "objection. Simply contact the data controller named below "
                "for this. Any consent given can be withdrawn at any time "
                "with effect for the future."
            ),
            "",
            (
                "All stored photos are irrevocably deleted from the photo "
                "booth within two (2) days after the event."
                + (
                    f" Released photos are kept by {pub} for as long as they "
                    "are published or until you withdraw your consent."
                    if social else ""
                )
            ),
            "",
            (
                "Children & young people should only use the photo booth "
                "when accompanied by, or with the consent of, a parent or "
                "legal guardian."
            ),
            "",
            *([
                ("H", "Responsible for publication"),
                "",
                social_media_settings.contact_line(s, "en"),
                "",
            ] if social else []),
            ("H", "Responsible for this photo booth"),
            "",
            OPERATOR_LINE_EN,
        ]

    pub = s.publisher or "der Veranstalter"
    if ctx.gallery_enabled:
        gallery_visibility_text = (
            "Während der Veranstaltung sind deine Fotos auf dem "
            "Display von anderen Nutzern der Fotobox einsehbar. Jede "
            "Person, die die Galerie öffnet, kann dabei jedes Foto "
            "ansehen"
            + (" und auch herunterladen" if ctx.qr_enabled else "")
            + " - nicht nur die eigenen Aufnahmen."
        )
    else:
        gallery_visibility_text = (
            "Eine Galerie-Funktion ist bei dieser Veranstaltung nicht "
            "aktiv - andere Gäste können deine Fotos auf dem Display "
            "der Fotobox nicht einsehen."
        )
    qr_download_paragraphs = [
        ("H", "Lokaler Download (WLAN)"),
        "",
        (
            f"Über das WLAN \"{ctx.wifi_ssid}\" "
            f"(Passwort: {ctx.wifi_password}) kannst "
            "du dein Foto nach der Aufnahme per QR-Code herunterladen. Da "
            "es sich um ein Veranstaltungsnetzwerk handelt, sind die "
            "Bilddateien dabei theoretisch für andere angemeldete Nutzer "
            "einsehbar. Lade keine Bilder herunter, wenn du damit nicht "
            "einverstanden bist."
        ),
        "",
    ] if ctx.qr_active else []
    return [
        ("H", "Nutzungsbedingungen zur Fotobox"),
        "",
        (
            "Mit der Nutzung dieser Fotobox (z. B. durch Betätigen des "
            "Auslösers) erklärst du dich damit einverstanden, dass "
            "Fotografien von dir angefertigt werden. Die Nutzung ist "
            "freiwillig."
        ),
        "",
        ("H", "Verwendungszweck & Speicherung"),
        "",
        (
            "Die Fotos dienen als Erinnerung an die Veranstaltung. Sie "
            "werden lokal auf der Fotobox gespeichert und nach der "
            f"Veranstaltung an {pub} als Veranstalter übergeben."
        ) if social else (
            "Die Fotos dienen als Erinnerung für Familie, Freunde und "
            "Verwandte sowie den Gastgeber. Sie werden zunächst lokal "
            "auf der Fotobox gespeichert und anschließend vom "
            "Gastgeber in einem privaten Kreis weiterverarbeitet."
        ),
        gallery_visibility_text,
        (
            "Fotos werden nur veröffentlicht, wenn du es ausdrücklich "
            "erlaubst (siehe \"Veröffentlichung\" unten). Ansonsten "
            "findet keine Weitergabe an unbeteiligte Dritte, keine "
            "Veröffentlichung im Internet und keine kommerzielle "
            "Nutzung statt."
        ) if social else (
            "Eine Weitergabe an unbeteiligte Dritte, eine "
            "Veröffentlichung bspw. im Internet oder eine "
            "kommerzielle Nutzung findet nicht statt."
        ),
        "",
        *(_social_section(s, "de") if social else []),
        *qr_download_paragraphs,
        ("H", "Deine Rechte"),
        "",
        (
            "Du kannst der Speicherung deines Bildes direkt nach der "
            "Aufnahme über die \"Löschen\"-Taste widersprechen. "
            "Außerdem hast du jederzeit das Recht auf Auskunft, "
            "Berichtigung, Löschung, Einschränkung der Verarbeitung, "
            "Datenübertragbarkeit und Widerspruch. Wende dich dazu "
            "einfach an den unten genannten Verantwortlichen bzw. an "
            "die unten genannte Verantwortliche. Eine erteilte "
            "Einwilligung kannst du jederzeit mit Wirkung für die "
            "Zukunft widerrufen."
        ),
        "",
        (
            "Alle gespeicherten Fotos werden unwiderruflich innerhalb "
            "von zwei (2) Tagen nach der Veranstaltung von der Fotobox "
            "gelöscht."
            + (
                f" Freigegebene Fotos bewahrt {pub} auf, solange sie "
                "veröffentlicht sind oder bis du deine Einwilligung "
                "widerrufst."
                if social else ""
            )
        ),
        "",
        (
            "Kinder & Jugendliche nutzen die Fotobox bitte nur in "
            "Begleitung bzw. mit Zustimmung einer erziehungs-"
            "berechtigten Person."
        ),
        "",
        *([
            ("H", "Verantwortlich für die Veröffentlichung"),
            "",
            social_media_settings.contact_line(s, "de"),
            "",
        ] if social else []),
        ("H", "Verantwortlich für den Betrieb"),
        "",
        OPERATOR_LINE_DE,
    ]
