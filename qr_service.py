from __future__ import annotations

from dataclasses import dataclass

import qrcode


@dataclass(frozen=True)
class QrService:
    photo_url_prefix: str

    def build_photo_url(self, filename: str) -> str:
        return f"{self.photo_url_prefix.rstrip('/')}/{filename}"

    def create_qr_image(self, filename: str):
        url = self.build_photo_url(filename)
        # GEAENDERT (Nutzer-Feedback): border (Ruhezone in QR-Modulen, hier
        # rein bildlich in das erzeugte Bitmap eingebacken) von 4 auf 1
        # reduziert. Bei border=4 dominierte diese eingebackene Ruhezone die
        # sichtbare weisse Umrandung komplett - der eigentlich steuerbare
        # Kartenrand (renderer._draw_qr_card, card_padding) fiel dagegen
        # kaum ins Gewicht, selbst nachdem der schon verkleinert wurde. Die
        # tatsaechliche Ruhezone entsteht jetzt ueberwiegend durch die weisse
        # Karte selbst, auf die das Bild aufgeklebt wird (siehe
        # renderer._draw_qr_card) - border=1 statt 0 bleibt als kleine
        # zusaetzliche Sicherheitsmarge direkt am Code selbst.
        qr = qrcode.QRCode(version=1, box_size=8, border=1)
        qr.add_data(url)
        qr.make(fit=True)
        return qr.make_image(fill_color='black', back_color='white').convert('RGB')
