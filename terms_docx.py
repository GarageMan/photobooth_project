"""Nutzungsbedingungen als Word-Datei (.docx) fuer den USB-Stick (Sprint 15).

Erzeugt die AUSGEFUELLTEN Nutzungsbedingungen der aktuellen Veranstaltung
(Deutsch + Englisch) aus genau demselben Text wie der Bildschirm
(terms_text.build_terms_paragraphs) - zum Ausdrucken/Aushaengen am Stand
und zur Dokumentation der Einwilligungsbedingungen.

Bewusst ohne Zusatzpaket (python-docx ist auf dem Pi nicht installiert):
ein .docx ist ein ZIP mit wenigen XML-Dateien, die hier direkt geschrieben
werden. Kopieren auf den Stick inkl. SHA256-Pruefung wie beim Foto-Export
(admin_usb_export).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
import shutil
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import terms_text
from admin_usb_export import sanitize_folder_name

TERMS_FILENAME_PREFIX = "Nutzungsbedingungen_"

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
</Relationships>"""

# GEAENDERT (Nutzer-Feedback 2026-11-15): Formatparameter 1:1 aus der
# Vorlage "Nutzungsbedingungen zur Fotobox.docx" - Calibri 11 pt in
# #333333, einfacher Zeilenabstand, 3 pt nach jedem Absatz; Titel 20 pt
# fett zentriert in #365F91 ohne Abstand davor; Abschnittsueberschriften
# 11 pt fett in #4F81BD auf grauem Hintergrund (#DDDDDD), 6 pt davor;
# Aufzaehlungen als echte Word-Aufzaehlung (Symbol-Punkt, Einzug 1,27 cm,
# haengend 0,63 cm, ohne Abstand zwischen den Punkten).
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults>
<w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri" w:eastAsia="Calibri"/><w:lang w:val="de-DE"/></w:rPr></w:rPrDefault>
<w:pPrDefault/>
</w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/>
<w:pPr><w:spacing w:after="60"/></w:pPr><w:rPr><w:color w:val="333333"/><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:keepNext/><w:keepLines/><w:spacing w:before="0" w:after="0"/><w:jc w:val="center"/></w:pPr><w:rPr><w:b/><w:bCs/><w:color w:val="365F91"/><w:sz w:val="40"/><w:szCs w:val="40"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:keepNext/><w:keepLines/><w:shd w:val="clear" w:color="auto" w:fill="DDDDDD"/><w:spacing w:before="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:bCs/><w:color w:val="4F81BD"/><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:ind w:left="720"/><w:contextualSpacing/></w:pPr></w:style>
</w:styles>"""

_NUMBERING = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/>
<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="\uf0b7"/><w:lvlJc w:val="left"/>
<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr><w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol" w:hint="default"/></w:rPr></w:lvl>
</w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
</w:numbering>"""

_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)


def _p(text: str, style: str | None = None, page_break_before: bool = False, bullet: bool = False) -> str:
    ppr = ""
    if style or page_break_before or bullet:
        ppr = "<w:pPr>"
        if style:
            ppr += f'<w:pStyle w:val="{style}"/>'
        if bullet:
            ppr += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
        if page_break_before:
            ppr += "<w:pageBreakBefore/>"
        ppr += "</w:pPr>"
    run = f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>' if text else ""
    return f"<w:p>{ppr}{run}</w:p>"


def _paragraphs_xml(paragraphs: list) -> list[str]:
    out: list[str] = []
    for item in paragraphs:
        if item == "":
            continue  # Abstaende regeln die Absatzformate
        if isinstance(item, tuple):
            kind = item[0]
            if kind == "H":
                out.append(_p(item[1], "Heading1"))
            elif kind == "RAW":
                out.append(_p(item[1]))
            elif kind == "BUL":
                out.append(_p(item[1], "ListParagraph", bullet=True))
            elif kind == "NUM":
                out.append(_p(f"{item[1]}. {item[2]}", "ListParagraph"))
            continue
        out.append(_p(str(item)))
    return out


def build_docx_bytes_parts(ctx: terms_text.TermsContext, event_title: str, today: _dt.date) -> dict[str, str]:
    body: list[str] = []
    for index, lang in enumerate(("de", "en")):
        paragraphs = terms_text.build_terms_paragraphs(ctx, lang)
        # Erste Ueberschrift ("Nutzungsbedingungen zur Fotobox") als Titel.
        first, rest = paragraphs[0], paragraphs[1:]
        title = first[1] if isinstance(first, tuple) else str(first)
        # GEAENDERT (Nutzer-Feedback 2026-11-15): kein Untertitel mehr
        # (Veranstaltung/Stand) - Platz, je Sprache genau ein DIN-A4-Blatt.
        body.append(_p(title, "Title", page_break_before=index > 0))
        body.extend(_paragraphs_xml(rest))
    # Seitenformat/-raender wie die Vorlage: A4, 1 cm rundum.
    sect = (
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="567" w:right="567" w:bottom="567" w:left="567" '
        'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {_NS}><w:body>{''.join(body)}{sect}</w:body></w:document>"
    )
    created = _dt.datetime.combine(today, _dt.time(12, 0)).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{escape('Nutzungsbedingungen ' + event_title)}</dc:title>"
        "<dc:creator>Fotobox</dc:creator>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>'
        "</cp:coreProperties>"
    )
    return {
        "[Content_Types].xml": _CONTENT_TYPES,
        "_rels/.rels": _RELS,
        "word/_rels/document.xml.rels": _DOC_RELS,
        "word/styles.xml": _STYLES,
        "word/numbering.xml": _NUMBERING,
        "word/document.xml": document,
        "docProps/core.xml": core,
    }


def write_terms_docx(
    target: Path, ctx: terms_text.TermsContext, event_title: str, today: _dt.date | None = None,
) -> None:
    """Schreibt die Datei atomar (.tmp + os.replace)."""
    parts = build_docx_bytes_parts(ctx, event_title, today or _dt.date.today())
    tmp = target.with_name(target.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in parts.items():
            zf.writestr(name, content.encode("utf-8"))
    os.replace(tmp, target)


def terms_filename(event_title: str) -> str:
    return f"{TERMS_FILENAME_PREFIX}{sanitize_folder_name(event_title)}.docx"


def _fsync_file(path: Path) -> None:
    """Schreibt den Inhalt einer Datei sicher auf ihr Laufwerk (nur diese
    Datei, kein globales sync)."""
    try:
        with open(path, "rb+") as handle:
            os.fsync(handle.fileno())
    except OSError:
        pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_terms_to_stick(
    ctx: terms_text.TermsContext, event_title: str, mountpoint: Path, work_dir: Path,
) -> tuple[bool, tuple[str, ...]]:
    """Erzeugt die Word-Datei und legt sie unter <Stick>/<Veranstaltung>/ ab
    - derselbe Ordner wie beim Foto-Export (sanitize_folder_name). Eine
    vorhandene Datei gleichen Namens wird ersetzt (sie ist immer aus den
    aktuellen Daten erzeugt). Liefert (ok, Anzeigezeilen)."""
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        name = terms_filename(event_title)
        local = work_dir / name
        write_terms_docx(local, ctx, event_title)
        folder = sanitize_folder_name(event_title)
        target_dir = mountpoint / folder
        target_dir.mkdir(parents=True, exist_ok=True)
        tmp = target_dir / (name + ".tmp")
        shutil.copyfile(local, tmp)
        # GEAENDERT (Bugfix 2026-11-15): nur DIESE Datei auf den Stick
        # schreiben (fsync) statt os.sync() - os.sync() wartet auf ALLE
        # eingebundenen Dateisysteme und blieb auf dem Pi haengen, wenn z. B.
        # das CIFS-Backup-Laufwerk nicht erreichbar war.
        _fsync_file(tmp)
        os.replace(tmp, target_dir / name)
        if _sha256(local) != _sha256(target_dir / name):
            return False, ("Prüfsumme stimmt nicht - bitte erneut versuchen.",)
        return True, (f"{folder}/{name}", "Deutsch und Englisch, zum Ausdrucken geeignet.")
    except OSError as exc:
        return False, ("Schreiben auf den Stick fehlgeschlagen.", str(exc)[:70])
