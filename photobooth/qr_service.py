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
        qr = qrcode.QRCode(version=1, box_size=8, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        return qr.make_image(fill_color='black', back_color='white').convert('RGB')
