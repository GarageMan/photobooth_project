"""
test_wallpaper_target.py
========================
NEU (Sprint 14, Nutzer-Feedback 2026-11-14): "Wallpaper von USB laden"
kennt zwei Ziele - Hauptmenue (hauptmenu_wallpaper.png) und HDMI-Monitor
(hdmi_monitor_wallpaper.png). Geprueft werden:
- State Machine: Ziel-Umschalter, passendes "wartet"-Flag nach dem
  Zwischenspeichern, Zuruecksetzen bei "Standardwerte"/neuem Einstieg
- event_config_service.import_hdmi_wallpaper: Verkleinern, PNG-Ausgabe,
  EXIF-Drehung, Fehlerfaelle
- layout: Umschalter liegen auf dem Bildschirm und ueberlappen nicht

    python3 -m pytest test/test_wallpaper_target.py -v
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from PIL import Image

import event_config_service as ecs
from config import DEFAULT_CONFIG
from events import AppEvent, EventType
from state_machine import StateMachine
from states import AppState


class WallpaperTargetStateMachineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = StateMachine(DEFAULT_CONFIG)
        model = self.machine.initial_model(1000.0)
        ui = replace(
            model.ui,
            admin_event_wallpaper_candidates=("a.png", "b.jpg"),
            admin_event_wallpaper_selected="b.jpg",
        )
        self.model = model.evolve(state=AppState.ADMIN_EVENT_WALLPAPER_PICK, ui=ui)

    def _send(self, event_type: EventType, payload: dict | None = None):
        event = AppEvent(event_type, payload=payload or {}, source="test")
        result = self.machine.transition(self.model, event, 1001.0)
        self.model = result.model
        return result

    def test_default_target_is_main_menu(self) -> None:
        self.assertEqual(self.model.ui.admin_event_wallpaper_target, "main")

    def test_switch_to_hdmi_and_back(self) -> None:
        self._send(EventType.TAP_ADMIN_EVENT_WALLPAPER_TARGET, {"target": "hdmi"})
        self.assertEqual(self.model.ui.admin_event_wallpaper_target, "hdmi")
        self._send(EventType.TAP_ADMIN_EVENT_WALLPAPER_TARGET, {"target": "main"})
        self.assertEqual(self.model.ui.admin_event_wallpaper_target, "main")

    def test_switching_target_keeps_selection(self) -> None:
        self._send(EventType.TAP_ADMIN_EVENT_WALLPAPER_TARGET, {"target": "hdmi"})
        self.assertEqual(self.model.ui.admin_event_wallpaper_selected, "b.jpg")
        self.assertEqual(self.model.state, AppState.ADMIN_EVENT_WALLPAPER_PICK)

    def test_unknown_target_is_ignored(self) -> None:
        self._send(EventType.TAP_ADMIN_EVENT_WALLPAPER_TARGET, {"target": "beamer2"})
        self.assertEqual(self.model.ui.admin_event_wallpaper_target, "main")

    def test_stage_for_main_sets_only_main_pending(self) -> None:
        self._send(EventType.ADMIN_EVENT_WALLPAPER_STAGE_RESULT, {"ok": True})
        self.assertTrue(self.model.ui.admin_event_wallpaper_pending)
        self.assertFalse(self.model.ui.admin_event_hdmi_wallpaper_pending)
        self.assertEqual(self.model.state, AppState.ADMIN_EVENT_SETTINGS)

    def test_stage_for_hdmi_sets_only_hdmi_pending(self) -> None:
        self._send(EventType.TAP_ADMIN_EVENT_WALLPAPER_TARGET, {"target": "hdmi"})
        self._send(EventType.ADMIN_EVENT_WALLPAPER_STAGE_RESULT, {"ok": True})
        self.assertTrue(self.model.ui.admin_event_hdmi_wallpaper_pending)
        self.assertFalse(self.model.ui.admin_event_wallpaper_pending)
        self.assertEqual(self.model.state, AppState.ADMIN_EVENT_SETTINGS)

    def test_failed_stage_sets_no_pending(self) -> None:
        self._send(EventType.TAP_ADMIN_EVENT_WALLPAPER_TARGET, {"target": "hdmi"})
        self._send(EventType.ADMIN_EVENT_WALLPAPER_STAGE_RESULT, {"ok": False, "message": "kaputt"})
        self.assertFalse(self.model.ui.admin_event_hdmi_wallpaper_pending)
        self.assertEqual(self.model.state, AppState.ADMIN_EVENT_WALLPAPER_RESULT)

    def test_both_wallpapers_can_be_pending(self) -> None:
        self._send(EventType.ADMIN_EVENT_WALLPAPER_STAGE_RESULT, {"ok": True})
        # Zweiter Durchlauf: wieder in die Auswahl, diesmal HDMI.
        self.model = self.model.evolve(state=AppState.ADMIN_EVENT_WALLPAPER_PICK)
        self._send(EventType.TAP_ADMIN_EVENT_WALLPAPER_TARGET, {"target": "hdmi"})
        self._send(EventType.ADMIN_EVENT_WALLPAPER_STAGE_RESULT, {"ok": True})
        self.assertTrue(self.model.ui.admin_event_wallpaper_pending)
        self.assertTrue(self.model.ui.admin_event_hdmi_wallpaper_pending)

    def test_defaults_drop_both_pending_wallpapers(self) -> None:
        ui = replace(self.model.ui, admin_event_wallpaper_pending=True, admin_event_hdmi_wallpaper_pending=True)
        self.model = self.model.evolve(state=AppState.ADMIN_EVENT_SETTINGS, ui=ui)
        result = self._send(EventType.TAP_ADMIN_EVENT_DEFAULTS)
        self.assertFalse(self.model.ui.admin_event_wallpaper_pending)
        self.assertFalse(self.model.ui.admin_event_hdmi_wallpaper_pending)
        self.assertIn("mark_event_wallpaper_reset", result.actions)

    def test_cancel_settings_discards_pending_files(self) -> None:
        ui = replace(self.model.ui, admin_event_hdmi_wallpaper_pending=True)
        self.model = self.model.evolve(state=AppState.ADMIN_EVENT_SETTINGS, ui=ui)
        result = self._send(EventType.TAP_BACK)
        self.assertIn("discard_pending_wallpaper", result.actions)

    def test_fresh_entry_resets_target_and_hdmi_pending(self) -> None:
        ui = replace(self.model.ui, admin_event_wallpaper_target="hdmi", admin_event_hdmi_wallpaper_pending=True)
        model = self.model.evolve(state=AppState.ADMIN_EVENT_SETTINGS, ui=ui)
        result = self.machine._go_admin_event_settings(model, 1002.0)
        self.assertEqual(result.model.ui.admin_event_wallpaper_target, "main")
        self.assertFalse(result.model.ui.admin_event_hdmi_wallpaper_pending)


class ImportHdmiWallpaperTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.target = self.dir / "assets" / ecs.HDMI_WALLPAPER_PENDING_FILENAME

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _image(self, name: str, size: tuple[int, int], fmt: str, **save_kwargs) -> Path:
        path = self.dir / name
        Image.new("RGB", size, (200, 150, 50)).save(path, format=fmt, **save_kwargs)
        return path

    def test_large_jpg_is_scaled_down_and_saved_as_png(self) -> None:
        source = self._image("handy.jpg", (4000, 3000), "JPEG")
        ok, message = ecs.import_hdmi_wallpaper(source, self.target)
        self.assertTrue(ok, message)
        with Image.open(self.target) as result:
            self.assertEqual(result.format, "PNG")
            self.assertEqual(result.size, (1440, 1080))  # 4:3 bleibt 4:3

    def test_wide_image_fits_width(self) -> None:
        source = self._image("breit.png", (3840, 1080), "PNG")
        ok, _ = ecs.import_hdmi_wallpaper(source, self.target)
        self.assertTrue(ok)
        with Image.open(self.target) as result:
            self.assertEqual(result.size, (1920, 540))

    def test_small_image_is_not_enlarged(self) -> None:
        source = self._image("klein.png", (800, 600), "PNG")
        ok, _ = ecs.import_hdmi_wallpaper(source, self.target)
        self.assertTrue(ok)
        with Image.open(self.target) as result:
            self.assertEqual(result.size, (800, 600))

    def test_exif_rotation_is_applied(self) -> None:
        # Hochformat-Handyfoto: Pixel quer gespeichert + EXIF "90 Grad drehen".
        exif = Image.Exif()
        exif[0x0112] = 6  # Orientation: Rotate 90 CW
        source = self._image("hochkant.jpg", (1600, 1200), "JPEG", exif=exif.tobytes())
        ok, _ = ecs.import_hdmi_wallpaper(source, self.target)
        self.assertTrue(ok)
        with Image.open(self.target) as result:
            self.assertEqual(result.size, (810, 1080))  # jetzt hochkant

    def test_broken_image_reports_error_and_leaves_no_files(self) -> None:
        source = self.dir / "kaputt.png"
        source.write_bytes(b"das ist kein Bild")
        ok, message = ecs.import_hdmi_wallpaper(source, self.target)
        self.assertFalse(ok)
        self.assertIn("kein lesbares Bild", message)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.dir.rglob("*.tmp")), [])

    def test_missing_file_reports_error(self) -> None:
        ok, message = ecs.import_hdmi_wallpaper(self.dir / "fehlt.png", self.target)
        self.assertFalse(ok)
        self.assertIn("nicht gefunden", message)

    def test_too_big_file_is_rejected_before_opening(self) -> None:
        source = self.dir / "riesig.png"
        with open(source, "wb") as fh:
            fh.truncate(31 * 1024 * 1024)
        ok, message = ecs.import_hdmi_wallpaper(source, self.target)
        self.assertFalse(ok)
        self.assertIn("zu gross", message)

    def test_existing_target_is_replaced(self) -> None:
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"alt")
        source = self._image("neu.png", (640, 360), "PNG")
        ok, _ = ecs.import_hdmi_wallpaper(source, self.target)
        self.assertTrue(ok)
        with Image.open(self.target) as result:
            self.assertEqual(result.size, (640, 360))


class WallpaperTargetLayoutTestCase(unittest.TestCase):
    def test_target_buttons_on_screen_and_not_overlapping(self) -> None:
        import pygame
        from layout import build_layout, button_rects_for_state
        width, height = DEFAULT_CONFIG.screen.width, DEFAULT_CONFIG.screen.height
        rects = build_layout(width, height)
        screen = pygame.Rect(0, 0, width, height)
        main = rects.admin_event_wallpaper_target_main
        hdmi = rects.admin_event_wallpaper_target_hdmi
        self.assertTrue(screen.contains(main))
        self.assertTrue(screen.contains(hdmi))
        self.assertFalse(main.colliderect(hdmi))
        # Unterhalb der Ueberschrift und oberhalb der Liste (Listenanfang
        # 0.33 * height, siehe renderer._draw_admin_event_wallpaper_pick).
        self.assertLess(main.bottom, round(0.33 * height))
        buttons = button_rects_for_state(AppState.ADMIN_EVENT_WALLPAPER_PICK, rects)
        self.assertIn("admin_event_wallpaper_target_main", buttons)
        self.assertIn("admin_event_wallpaper_target_hdmi", buttons)


if __name__ == "__main__":
    unittest.main()
