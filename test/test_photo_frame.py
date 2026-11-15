"""Tests fuer den Bilderrahmen (Sprint 15, Entwurf B1).

Abgedeckt: Einrechnen (zwei Versionen, Original als rahmenlos_<name> in
eigenem Ordner), EXIF bleibt erhalten, Fehlerfaelle (Foto bleibt
unveraendert), andere Bildgroessen, Mitloeschen, USB-Export,
Speicherschaetzung, Service-Menue-Schalter und Layout.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw

import photo_frame
from admin_delete_service import delete_all_photos
from admin_usb_export import ExportResult, export_unframed
from config import DEFAULT_CONFIG
from events import AppEvent, EventType
from layout import build_layout, button_rects_for_state
from state_machine import StateMachine
from states import AppState
from storage_alarm import assess_storage

REPO = Path(__file__).resolve().parent.parent


def _overlay(path: Path, size=(600, 400)) -> None:
    """Testrahmen: roter Rand (20 px), Mitte transparent, Logo-Quadrat unten rechts."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, size[0] - 1, size[1] - 1), outline=(255, 0, 0, 255), width=20)
    d.rectangle((size[0] - 120, size[1] - 120, size[0] - 40, size[1] - 40), fill=(0, 0, 255, 255))
    img.save(path)


def _photo(path: Path, size=(600, 400), exif: bool = True) -> None:
    img = Image.new("RGB", size, (40, 160, 40))
    kwargs = {}
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "NIKON CORPORATION"   # Make
        kwargs["exif"] = ex.tobytes()
    img.save(path, "JPEG", quality=95, **kwargs)


class FrameCompositorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.photos = self.root / "photos"
        self.unframed = self.root / "rahmenlos"
        self.photos.mkdir()
        self.overlay = self.root / "rahmen.png"
        _overlay(self.overlay)
        self.comp = photo_frame.FrameCompositor(self.overlay)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_two_versions(self) -> None:
        photo = self.photos / "foto_20261205153012.jpg"
        _photo(photo)
        original_bytes = photo.read_bytes()
        ok, msg = self.comp.apply(photo, self.unframed)
        self.assertTrue(ok, msg)
        unframed = self.unframed / "rahmenlos_foto_20261205153012.jpg"
        self.assertEqual(unframed.read_bytes(), original_bytes)
        # Nur die Version mit Rahmen liegt im Fotoordner.
        self.assertEqual([p.name for p in self.photos.iterdir()], ["foto_20261205153012.jpg"])
        with Image.open(photo) as img:
            self.assertEqual(img.size, (600, 400))
            r, g, b = img.getpixel((5, 200))          # Rand -> rot
            self.assertTrue(r > 200 and g < 60, (r, g, b))
            r, g, b = img.getpixel((520, 320))        # Logo-Bereich -> blau
            self.assertTrue(b > 200 and r < 60, (r, g, b))
            r, g, b = img.getpixel((300, 200))        # Mitte unveraendert gruen
            self.assertTrue(g > 130 and r < 70, (r, g, b))
            self.assertEqual(img.getexif().get(0x010F), "NIKON CORPORATION")

    def test_other_photo_size_is_scaled(self) -> None:
        photo = self.photos / "klein.jpg"
        _photo(photo, size=(300, 200))
        ok, _ = self.comp.apply(photo, self.unframed)
        self.assertTrue(ok)
        with Image.open(photo) as img:
            self.assertEqual(img.size, (300, 200))
            self.assertGreater(img.getpixel((3, 100))[0], 200)

    def test_missing_overlay_leaves_photo_untouched(self) -> None:
        photo = self.photos / "foto.jpg"
        _photo(photo)
        before = photo.read_bytes()
        comp = photo_frame.FrameCompositor(self.root / "fehlt.png")
        ok, msg = comp.apply(photo, self.unframed)
        self.assertFalse(ok)
        self.assertIn("fehlt", msg)
        self.assertEqual(photo.read_bytes(), before)
        self.assertFalse(self.unframed.exists())

    def test_broken_photo_keeps_file_and_cleans_tmp(self) -> None:
        photo = self.photos / "kaputt.jpg"
        photo.write_bytes(b"kein jpeg")
        ok, _ = self.comp.apply(photo, self.unframed)
        self.assertFalse(ok)
        self.assertEqual(photo.read_bytes(), b"kein jpeg")
        self.assertEqual(list(self.photos.glob("*.tmp")), [])
        self.assertEqual(list(self.unframed.glob("*.tmp")), [])

    def test_delete_and_list_unframed(self) -> None:
        photo = self.photos / "a.jpg"
        _photo(photo)
        self.comp.apply(photo, self.unframed)
        self.assertEqual([p.name for p in photo_frame.list_unframed(self.unframed)], ["rahmenlos_a.jpg"])
        self.assertTrue(photo_frame.delete_unframed_copy("a.jpg", self.unframed))
        self.assertFalse(photo_frame.delete_unframed_copy("a.jpg", self.unframed))
        self.assertEqual(photo_frame.list_unframed(self.unframed), [])

    def test_real_asset_b1(self) -> None:
        asset = REPO / "assets" / photo_frame.FRAME_FILENAME
        self.assertTrue(asset.is_file())
        with Image.open(asset) as img:
            self.assertEqual(img.size, (6000, 4000))
            self.assertEqual(img.mode, "RGBA")
            alpha = img.getchannel("A")
            self.assertEqual(alpha.getpixel((3000, 2000)), 0)      # Mitte frei
            self.assertIsNotNone(alpha.crop((4900, 2900, 5800, 3800)).getbbox())  # Logo vorhanden
            # Keine weissen Logo-Reste (Punktkranz/BEAUTY & SPA) im Logo-Bereich.
            logo = img.crop((4950, 2950, 5800, 3800))
            raw = logo.tobytes()
            white = sum(
                1 for i in range(0, len(raw), 4)
                if raw[i + 3] > 128 and raw[i] > 245 and raw[i + 1] > 245 and raw[i + 2] > 245
            )
            self.assertEqual(white, 0)


class FrameIntegrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_usb_export_subfolder(self) -> None:
        unframed = self.root / "rahmenlos"
        unframed.mkdir()
        _photo(unframed / "rahmenlos_a.jpg")
        stick = self.root / "stick"
        stick.mkdir()
        result = ExportResult()
        export_unframed(unframed, stick, result, folder_name="Basar")
        self.assertTrue((stick / "Basar" / "rahmenlos" / "rahmenlos_a.jpg").is_file())
        self.assertEqual(result.unframed_copied, 1)
        self.assertIn("Originale ohne Rahmen: 1 (Unterordner rahmenlos)", result.summary_lines())
        again = ExportResult()
        export_unframed(unframed, stick, again, folder_name="Basar")
        self.assertEqual((again.unframed_copied, again.unframed_skipped), (0, 1))

    def test_delete_all_includes_unframed(self) -> None:
        photos, web, logs, unframed = (self.root / n for n in ("photos", "web", "logs", "rahmenlos"))
        for d in (photos, web, logs, unframed):
            d.mkdir()
        _photo(photos / "a.jpg")
        _photo(unframed / "rahmenlos_a.jpg")
        result = delete_all_photos(
            photos, web, logs, delete_from_camera=False, overwrite_before_delete=False, unframed_dir=unframed,
        )
        self.assertEqual(result.deleted_unframed, 1)
        self.assertEqual(list(unframed.iterdir()), [])
        self.assertIn("Originale ohne Rahmen gelöscht: 1", result.summary_lines())

    def test_storage_estimate_counts_two_files(self) -> None:
        class Usage:
            total, used, free = 100_000, 50_000, 50_000
        one = assess_storage(self.root, [], fallback_avg_photo_size_bytes=1000, disk_usage_fn=lambda _: Usage)
        two = assess_storage(
            self.root, [], fallback_avg_photo_size_bytes=1000, disk_usage_fn=lambda _: Usage, files_per_photo=2,
        )
        self.assertEqual((one.estimated_remaining_photos, two.estimated_remaining_photos), (50, 25))

    def test_config_default_off(self) -> None:
        self.assertFalse(DEFAULT_CONFIG.frame_enabled)
        self.assertEqual(DEFAULT_CONFIG.unframed_dir.name, "rahmenlos")
        self.assertNotEqual(DEFAULT_CONFIG.unframed_dir, DEFAULT_CONFIG.photo_dir)


class FrameSettingsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = StateMachine(DEFAULT_CONFIG)
        self.now = 1000.0
        model = self.machine.initial_model(self.now)
        model = self.machine._go_admin_event_settings(model, self.now).model
        self.model = self.send(EventType.ADMIN_EVENT_SETTINGS_READY, model, {"frame_enabled": True}).model

    def send(self, event_type, model=None, payload=None):
        self.now += 0.1
        return self.machine.transition(
            model if model is not None else self.model, AppEvent(event_type, payload=payload or {}, source="t"), self.now,
        )

    def test_ready_and_toggle(self) -> None:
        self.assertTrue(self.model.ui.admin_event_frame_enabled)
        self.model = self.send(EventType.TAP_ADMIN_EVENT_TOGGLE, payload={"field": "frame"}).model
        self.assertFalse(self.model.ui.admin_event_frame_enabled)

    def test_cancel_restores(self) -> None:
        self.model = self.send(EventType.TAP_ADMIN_EVENT_TOGGLE, payload={"field": "frame"}).model
        self.model = self.send(EventType.TAP_BACK).model
        self.assertTrue(self.model.ui.admin_event_frame_enabled)

    def test_defaults_switch_frame_off(self) -> None:
        self.model = self.send(EventType.TAP_ADMIN_EVENT_DEFAULTS).model
        self.assertFalse(self.model.ui.admin_event_frame_enabled)

    def test_layout_page1_six_rows_without_overlap(self) -> None:
        r = build_layout(1280, 720)
        tiles = [
            r.admin_event_title_row, r.admin_event_welcome_text_row, r.admin_event_wifi_ssid_row,
            r.admin_event_wifi_password_row, r.admin_event_prefix_row, r.admin_event_wallpaper_button,
            r.admin_event_qr_toggle, r.admin_event_gallery_toggle, r.admin_event_frame_toggle,
            r.admin_event_color_style_button, r.admin_event_defaults_button,
        ]
        screen = __import__("pygame").Rect(0, 0, 1280, 720)
        for i, a in enumerate(tiles + [r.left, r.right, r.admin_event_page_button]):
            self.assertTrue(screen.contains(a))
            for b in (tiles + [r.left, r.right, r.admin_event_page_button])[i + 1:]:
                self.assertFalse(a.colliderect(b))
        self.assertEqual(len({t.size for t in tiles}), 1)
        self.assertIn("admin_event_toggle_frame", button_rects_for_state(AppState.ADMIN_EVENT_SETTINGS, r))


class RendererOverlayTestCase(unittest.TestCase):
    def test_overlay_only_when_enabled(self) -> None:
        import pygame
        from renderer import Renderer
        pygame.init()
        screen = pygame.display.set_mode((1280, 720))
        off = Renderer(config=DEFAULT_CONFIG, screen=screen)
        self.assertIsNone(off._frame_overlay_surface())
        on = Renderer(config=replace(DEFAULT_CONFIG, frame_enabled=True), screen=screen)
        surface = on._frame_overlay_surface()
        self.assertIsNotNone(surface)
        self.assertEqual(surface.get_size(), (1280, 720))
        self.assertEqual(surface.get_at((640, 360)).a, 0)   # Mitte transparent


if __name__ == "__main__":
    unittest.main()
