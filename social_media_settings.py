"""Social-Media-Freigabe: Plattformen, Betreiber, Pflichtfelder (Sprint 15).

Reine Logik ohne pygame-/Dateizugriff - wird von config.py (Einlesen aus
event_config.json), state_machine.py (Freigabefrage, Pflichtfeldpruefung),
terms_text.py (Nutzungsbedingungen) und dem Admin-Screen genutzt.

Betreiberangaben (Stand: Recherche 2026-11-15, jeweils EU/EWR-Nutzer):
- Instagram/Facebook: Meta Platforms Ireland Limited (Dublin), Uebermittlung
  in die USA moeglich.
- TikTok: TikTok Technology Limited (Dublin); laut irischer Datenschutz-
  behoerde (Entscheidung 2025) Fernzugriff u. a. aus China.
- Twitch: Twitch Interactive, Inc. (USA), EU-Vertreter Twitch Interactive
  Germany GmbH.
- X: X Internet Unlimited Company (Dublin).
- LinkedIn: LinkedIn Ireland Unlimited Company (Dublin).
- XING: New Work SE (Hamburg) - Verarbeitung in der EU.
- Reddit: Reddit, Inc. (USA).
- Snapchat: Snap Inc. (USA), EU-Vertreter Snap B.V. (Amsterdam).
Diese Angaben koennen sich aendern - vor jeder Veranstaltung mit
Social-Media-Freigabe pruefen (siehe Projekt-Doku).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Platform:
    key: str
    label: str            # Anzeige auf Kachel/Frage (DE und EN gleich)
    operator_de: str
    operator_en: str
    transfer_de: str      # "" = keine Uebermittlung ausserhalb der EU
    transfer_en: str


PLATFORMS: tuple[Platform, ...] = (
    Platform(
        "instagram", "Instagram",
        "Meta Platforms Ireland Limited, Dublin (Irland)",
        "Meta Platforms Ireland Limited, Dublin (Ireland)",
        "Eine Übermittlung in die USA ist möglich.",
        "A transfer to the USA is possible.",
    ),
    Platform(
        "facebook", "Facebook",
        "Meta Platforms Ireland Limited, Dublin (Irland)",
        "Meta Platforms Ireland Limited, Dublin (Ireland)",
        "Eine Übermittlung in die USA ist möglich.",
        "A transfer to the USA is possible.",
    ),
    Platform(
        "tiktok", "TikTok",
        "TikTok Technology Limited, Dublin (Irland)",
        "TikTok Technology Limited, Dublin (Ireland)",
        "Eine Übermittlung bzw. ein Zugriff aus Drittländern (u. a. USA, Singapur, China) ist möglich.",
        "A transfer to or access from third countries (incl. USA, Singapore, China) is possible.",
    ),
    Platform(
        "twitch", "Twitch",
        "Twitch Interactive, Inc. (USA); EU-Vertreter: Twitch Interactive Germany GmbH",
        "Twitch Interactive, Inc. (USA); EU representative: Twitch Interactive Germany GmbH",
        "Die Daten werden in den USA verarbeitet.",
        "The data is processed in the USA.",
    ),
    Platform(
        "x", "X (ehem. Twitter)",
        "X Internet Unlimited Company, Dublin (Irland)",
        "X Internet Unlimited Company, Dublin (Ireland)",
        "Eine Übermittlung in die USA ist möglich.",
        "A transfer to the USA is possible.",
    ),
    Platform(
        "linkedin", "LinkedIn",
        "LinkedIn Ireland Unlimited Company, Dublin (Irland)",
        "LinkedIn Ireland Unlimited Company, Dublin (Ireland)",
        "Eine Übermittlung in die USA ist möglich.",
        "A transfer to the USA is possible.",
    ),
    Platform(
        "xing", "XING",
        "New Work SE, Hamburg (Deutschland)",
        "New Work SE, Hamburg (Germany)",
        "",
        "",
    ),
    Platform(
        "reddit", "Reddit",
        "Reddit, Inc. (USA)",
        "Reddit, Inc. (USA)",
        "Die Daten werden in den USA verarbeitet.",
        "The data is processed in the USA.",
    ),
    Platform(
        "snapchat", "Snapchat",
        "Snap Inc. (USA); EU-Vertreter: Snap B.V., Amsterdam (Niederlande)",
        "Snap Inc. (USA); EU representative: Snap B.V., Amsterdam (Netherlands)",
        "Die Daten werden in den USA verarbeitet.",
        "The data is processed in the USA.",
    ),
    Platform(
        "web", "Webseite",
        "", "", "", "",
    ),
)
PLATFORM_BY_KEY: dict[str, Platform] = {p.key: p for p in PLATFORMS}
PLATFORM_KEYS: tuple[str, ...] = tuple(p.key for p in PLATFORMS)

# Ab dieser Anzahl Kanaele nennt die Freigabefrage keine einzelnen Namen
# mehr (Touchscreen-Lesbarkeit) - die vollstaendige Liste steht dann in
# den Nutzungsbedingungen (Nutzerentscheidung 2026-11-15).
QUESTION_MAX_NAMED = 2


@dataclass(frozen=True)
class SocialMediaSettings:
    enabled: bool = False
    platforms: tuple[str, ...] = ()       # Schluessel aus PLATFORM_KEYS, feste Reihenfolge
    platforms_other: str = ""             # Freitext "weitere"
    company: str = ""
    contact_name: str = ""
    street: str = ""
    city: str = ""
    email: str = ""
    phone: str = ""
    handle: str = ""                      # z. B. @hollerhof
    privacy_url: str = ""
    legacy_contact: str = ""              # altes Feld social_media_contact (Zeilen mit \n)
    other_names: tuple[str, ...] = field(default=())  # aus platforms_other zerlegt

    @property
    def publisher(self) -> str:
        """Name der veroeffentlichenden Stelle fuer Frage und Texte."""
        return self.company or self.contact_name


def normalize_platforms(value) -> tuple[tuple[str, ...], str]:
    """Liest social_media_platforms aus event_config.json.

    Akzeptiert die neue Form (Liste von Schluesseln) und die alte Form
    (Text wie "Instagram" oder "Instagram, Facebook"). Unbekannte Namen
    landen im Freitext "weitere" statt verloren zu gehen. Liefert
    (Schluessel in fester Reihenfolge, Freitext)."""
    if isinstance(value, str):
        items = [part.strip() for part in value.replace(";", ",").split(",")]
    elif isinstance(value, (list, tuple)):
        items = [str(part).strip() for part in value]
    else:
        items = []
    by_label = {p.label.lower(): p.key for p in PLATFORMS}
    by_label["x"] = "x"
    by_label["twitter"] = "x"
    by_label["website"] = "web"
    keys: set[str] = set()
    unknown: list[str] = []
    for item in items:
        if not item:
            continue
        low = item.lower()
        if low in PLATFORM_BY_KEY:
            keys.add(low)
        elif low in by_label:
            keys.add(by_label[low])
        else:
            unknown.append(item)
    return tuple(k for k in PLATFORM_KEYS if k in keys), ", ".join(unknown)


def _text(data: dict, key: str) -> str:
    return str(data.get(key) or "").strip()


def settings_from_event_config(data: dict) -> SocialMediaSettings:
    platforms, unknown = normalize_platforms(data.get("social_media_platforms"))
    other = _text(data, "social_media_platforms_other")
    if unknown:
        other = ", ".join(part for part in (other, unknown) if part)
    return make_settings(
        enabled=bool(data.get("social_media_enabled", False)),
        platforms=platforms,
        platforms_other=other,
        company=_text(data, "social_media_company"),
        contact_name=_text(data, "social_media_contact_name"),
        street=_text(data, "social_media_street"),
        city=_text(data, "social_media_city"),
        email=_text(data, "social_media_email"),
        phone=_text(data, "social_media_phone"),
        handle=_text(data, "social_media_handle"),
        privacy_url=_text(data, "social_media_privacy_url"),
        legacy_contact=_text(data, "social_media_contact"),
    )


def make_settings(**kwargs) -> SocialMediaSettings:
    other = str(kwargs.get("platforms_other") or "").strip()
    names = tuple(part.strip() for part in other.split(",") if part.strip())
    platforms = tuple(k for k in PLATFORM_KEYS if k in set(kwargs.get("platforms") or ()))
    kwargs = {**kwargs, "platforms": platforms, "platforms_other": other, "other_names": names}
    return SocialMediaSettings(**kwargs)


def settings_from_config(config) -> SocialMediaSettings:
    """Baut die Einstellungen aus einer AppConfig (fehlende Attribute -> leer)."""
    def g(name: str) -> str:
        return str(getattr(config, name, "") or "").strip()

    return make_settings(
        enabled=bool(getattr(config, "social_media_enabled", False)),
        platforms=tuple(getattr(config, "social_media_platforms", ()) or ()),
        platforms_other=g("social_media_platforms_other"),
        company=g("social_media_company"),
        contact_name=g("social_media_contact_name"),
        street=g("social_media_street"),
        city=g("social_media_city"),
        email=g("social_media_email"),
        phone=g("social_media_phone"),
        handle=g("social_media_handle"),
        privacy_url=g("social_media_privacy_url"),
        legacy_contact=g("social_media_contact"),
    )


def channel_names(settings: SocialMediaSettings, include_web: bool = True) -> list[str]:
    names = [PLATFORM_BY_KEY[k].label for k in settings.platforms if include_web or k != "web"]
    return names + list(settings.other_names)


def channel_count(settings: SocialMediaSettings) -> int:
    return len(settings.platforms) + len(settings.other_names)


def _join(names: list[str], word: str) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + f" {word} " + names[-1]


def channel_phrase(settings: SocialMediaSettings, lang: str = "de") -> str:
    """Ortsangabe fuer Frage und Texte, z. B. "auf Instagram und Facebook".

    Ab drei Kanaelen: "in sozialen Medien" (plus Webseite, falls gewaehlt)."""
    has_web = "web" in settings.platforms
    social = channel_names(settings, include_web=False)
    if lang == "en":
        web_text, social_text, word = "on the website", "on social media", "and"
    else:
        web_text, social_text, word = "auf der Webseite", "in sozialen Medien", "und"
    if not social and not has_web:
        return social_text
    if channel_count(settings) > QUESTION_MAX_NAMED:
        return f"{social_text} {word} {web_text}" if has_web else social_text
    on = "on" if lang == "en" else "auf"
    parts = [f"{on} {_join(social, word)}"] if social else []
    if has_web:
        parts.append(web_text)
    return f" {word} ".join(parts)


def consent_question(settings: SocialMediaSettings) -> str:
    who = settings.publisher or "der Veranstalter"
    return f"Darf {who} dieses Foto {channel_phrase(settings)} veröffentlichen?"


def validate(settings: SocialMediaSettings) -> list[str]:
    """Pflichtfelder bei eingeschalteter Freigabe. Leere Liste = in Ordnung."""
    if not settings.enabled:
        return []
    problems: list[str] = []
    if channel_count(settings) == 0:
        problems.append("mindestens eine Plattform")
    if not settings.publisher:
        problems.append("Firmen- oder Personenname")
    if not settings.email and not settings.phone:
        problems.append("E-Mail oder Telefon")
    elif settings.email and ("@" not in settings.email or "." not in settings.email.split("@")[-1]):
        problems.append("gültige E-Mail-Adresse")
    return problems


def contact_lines(settings: SocialMediaSettings, lang: str = "de") -> list[str]:
    """Adressblock der veroeffentlichenden Stelle (ohne Ueberschrift)."""
    lines: list[str] = []
    if settings.company:
        lines.append(settings.company)
    if settings.contact_name:
        prefix = "Contact: " if (lang == "en" and settings.company) else (
            "Ansprechpartner/in: " if settings.company else ""
        )
        lines.append(prefix + settings.contact_name)
    structured = any((settings.street, settings.city, settings.email, settings.phone))
    if structured:
        lines += [v for v in (settings.street, settings.city) if v]
        if settings.email:
            lines.append(("E-mail: " if lang == "en" else "E-Mail: ") + settings.email)
        if settings.phone:
            lines.append(("Phone: " if lang == "en" else "Telefon: ") + settings.phone)
    elif settings.legacy_contact:
        lines += [
            line.strip()
            for line in settings.legacy_contact.replace("\\n", "\n").split("\n")
            if line.strip()
        ]
    return lines


def to_event_config(settings: SocialMediaSettings) -> dict:
    """Schluessel fuer event_config.json (neue Form)."""
    return {
        "social_media_enabled": settings.enabled,
        "social_media_platforms": list(settings.platforms),
        "social_media_platforms_other": settings.platforms_other,
        "social_media_company": settings.company,
        "social_media_contact_name": settings.contact_name,
        "social_media_street": settings.street,
        "social_media_city": settings.city,
        "social_media_email": settings.email,
        "social_media_phone": settings.phone,
        "social_media_handle": settings.handle,
        "social_media_privacy_url": settings.privacy_url,
    }
