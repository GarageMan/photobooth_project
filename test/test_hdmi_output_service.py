"""
test_hdmi_output_service.py
=============================
Tests fuer hdmi_output_service.py (Sprint 14). Getestet wird alles, was
ohne echten Beamer/wlr-randr pruefbar ist: das Parsen einer wlr-randr-
Textausgabe (_parse_wlr_randr) sowie die Subprozess-Verwaltung von
HdmiOutputService ueber eine injizierte Fake-Popen-Fabrik (gleiches
Prinzip wie die Fake-Provider an anderen Stellen des Projekts - siehe
z.B. fake_preview_service.py). set_power()/read_status() selbst rufen
echtes wlr-randr auf und sind wie admin_usb_service.mount_partition() so
gebaut, dass sie im Fehlerfall (Kommando fehlt) eine Meldung statt einer
Ausnahme liefern - das wird hier ueber den garantiert fehlenden Befehl
"wlr-randr" auf dem Testsystem mitgeprueft.

    python3 -m pytest test_hdmi_output_service.py -v
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from hdmi_output_service import (
    HdmiOutputService,
    HdmiOutputStatus,
    _parse_wlr_randr,
    _parse_xrandr,
    _pick_hdmi,
    read_status,
    set_power,
)


# Wortwoertlich angelehnte wlr-randr-Beispielausgabe (Format-Dokumentation
# des Tools) - ein verbundener und ein getrennter Ausgang.
WLR_RANDR_SAMPLE = """\
HDMI-A-1 "Some Beamer Inc. Model X (HDMI-A-1)"
  Make: Some Beamer Inc.
  Model: Model X
  Serial: unknown
  Physical size: 1600x900 mm
  Enabled: yes
  Modes:
    1920x1080 px, 60.000000 Hz (preferred, current)
    1280x720 px, 60.000000 Hz
  Position: 0,0
  Transform: normal
  Scale: 1.000000
  Adaptive Sync: disabled

HDMI-A-2 "Nichts angeschlossen"
  Enabled: no
"""

WLR_RANDR_OFF_SAMPLE = """\
HDMI-A-1 "Some Beamer Inc. Model X (HDMI-A-1)"
  Make: Some Beamer Inc.
  Model: Model X
  Enabled: no
  Modes:
    1920x1080 px, 60.000000 Hz (preferred, current)
  Position: 0,0
"""


# Echte xrandr-Ausgabe des Fotobox-Pi (X11, 2026-11-11), ohne Beamer.
XRANDR_NO_BEAMER = """\
Screen 0: minimum 320 x 200, current 1280 x 720, maximum 4096 x 4096
DSI-2 connected primary 1280x720+0+0 right (normal left inverted right x axis y axis) 0mm x 0mm
   720x1280      60.04*+
HDMI-1-1 disconnected (normal left inverted right x axis y axis)
HDMI-1-2 disconnected (normal left inverted right x axis y axis)
"""

# Beamer an HDMI-1-2, eingeschaltet und rechts neben dem Touch-Display.
XRANDR_BEAMER_ON = """\
Screen 0: minimum 320 x 200, current 3200 x 1080, maximum 4096 x 4096
DSI-2 connected primary 1280x720+0+0 right (normal left inverted right x axis y axis) 0mm x 0mm
   720x1280      60.04*+
HDMI-1-1 disconnected (normal left inverted right x axis y axis)
HDMI-1-2 connected 1920x1080+1280+0 (normal left inverted right x axis y axis) 521mm x 293mm
   1920x1080     60.00*+  50.00    59.94
   1280x720      60.00    50.00    59.94
"""

# NEU (Fix 6): echte `xinput --list --name-only`-Ausgabe des Fotobox-Pi
# (2026-11-14) - der Goodix-Controller erscheint ZWEIMAL (Zeiger + Tastatur).
# Genau diese Doppelung liess `xinput map-to-output <Name>` scheitern; die
# fruehere Testattrappe enthielt den Namen nur einmal und konnte den Fehler
# daher nicht aufdecken.
XINPUT_NAMES_PI = """\
Virtual core pointer
Virtual core keyboard
Virtual core XTEST pointer
Virtual core XTEST keyboard
Goodix Capacitive TouchScreen
pwr_button
vc4-hdmi-0
vc4-hdmi-1
Goodix Capacitive TouchScreen
vc4-hdmi-0
vc4-hdmi-1
"""

# Beamer angeschlossen, aber (noch) nicht aktiviert: keine Geometrie.
XRANDR_BEAMER_OFF = """\
Screen 0: minimum 320 x 200, current 1280 x 720, maximum 4096 x 4096
DSI-2 connected primary 1280x720+0+0 right (normal left inverted right x axis y axis) 0mm x 0mm
   720x1280      60.04*+
HDMI-1-1 connected (normal left inverted right x axis y axis) 521mm x 293mm
   1920x1080     60.00 +  50.00    59.94
HDMI-1-2 disconnected (normal left inverted right x axis y axis)
"""


class ParseXrandrTestCase(unittest.TestCase):
    def test_no_beamer_lists_all_outputs_as_known(self) -> None:
        outputs = _parse_xrandr(XRANDR_NO_BEAMER)
        self.assertEqual(set(outputs), {"DSI-2", "HDMI-1-1", "HDMI-1-2"})
        self.assertTrue(outputs["DSI-2"]["connected"])
        self.assertEqual(outputs["DSI-2"]["geometry"], (1280, 720, 0, 0))
        self.assertFalse(outputs["HDMI-1-1"]["connected"])

    def test_active_beamer_has_geometry(self) -> None:
        outputs = _parse_xrandr(XRANDR_BEAMER_ON)
        self.assertTrue(outputs["HDMI-1-2"]["connected"])
        self.assertEqual(outputs["HDMI-1-2"]["geometry"], (1920, 1080, 1280, 0))

    def test_connected_but_inactive_beamer_has_no_geometry(self) -> None:
        outputs = _parse_xrandr(XRANDR_BEAMER_OFF)
        self.assertTrue(outputs["HDMI-1-1"]["connected"])
        self.assertIsNone(outputs["HDMI-1-1"]["geometry"])

    def test_auto_picks_first_connected_hdmi_not_touch_display(self) -> None:
        self.assertEqual(_pick_hdmi(_parse_xrandr(XRANDR_BEAMER_ON), "auto"), "HDMI-1-2")
        self.assertEqual(_pick_hdmi(_parse_xrandr(XRANDR_BEAMER_OFF), "auto"), "HDMI-1-1")

    def test_auto_without_beamer_returns_none(self) -> None:
        self.assertIsNone(_pick_hdmi(_parse_xrandr(XRANDR_NO_BEAMER), "auto"))

    def test_fixed_name_must_be_known_to_xrandr(self) -> None:
        outputs = _parse_xrandr(XRANDR_NO_BEAMER)
        self.assertEqual(_pick_hdmi(outputs, "HDMI-1-2"), "HDMI-1-2")
        self.assertIsNone(_pick_hdmi(outputs, "HDMI-9-9"))


def _fake_run(responses: dict[str, str], calls: list[list[str]]):
    """subprocess.run-Ersatz: antwortet je nach Kommando mit stdout-Text."""
    def run(args, **kwargs):
        calls.append(list(args))
        text = responses.get(args[0], "")
        return mock.Mock(returncode=0, stdout=text, stderr="")
    return run


class ReadStatusXrandrTestCase(unittest.TestCase):
    def _status(self, text: str, connector: str = "auto") -> HdmiOutputStatus:
        with mock.patch.dict("os.environ", {}, clear=False), \
             mock.patch("hdmi_output_service._use_wayland", return_value=False), \
             mock.patch("hdmi_output_service.subprocess.run", _fake_run({"xrandr": text}, [])):
            return read_status(connector)

    def test_no_beamer_is_available_but_not_connected(self) -> None:
        status = self._status(XRANDR_NO_BEAMER)
        self.assertTrue(status.available)
        self.assertFalse(status.connected)
        self.assertEqual(status.error, "")

    def test_active_beamer_reports_resolution_and_geometry(self) -> None:
        status = self._status(XRANDR_BEAMER_ON)
        self.assertTrue(status.connected)
        self.assertTrue(status.powered_on)
        self.assertEqual(status.resolution, "1920x1080")
        self.assertEqual(status.connector, "HDMI-1-2")
        self.assertEqual(status.geometry, (1920, 1080, 1280, 0))

    def test_inactive_beamer_reports_powered_off(self) -> None:
        status = self._status(XRANDR_BEAMER_OFF)
        self.assertTrue(status.connected)
        self.assertFalse(status.powered_on)
        self.assertIsNone(status.geometry)


class SetPowerXrandrTestCase(unittest.TestCase):
    def _call(self, text: str, on: bool):
        calls: list[list[str]] = []
        responses = {"xrandr": text, "xinput": XINPUT_NAMES_PI}
        with mock.patch("hdmi_output_service._use_wayland", return_value=False), \
             mock.patch("hdmi_output_service.subprocess.run", _fake_run(responses, calls)):
            result = set_power("auto", on)
        return result, calls

    def test_power_on_places_output_right_of_touch_display_and_maps_touch(self) -> None:
        (ok, message), calls = self._call(XRANDR_BEAMER_OFF, True)
        self.assertTrue(ok)
        self.assertEqual(message, "")
        self.assertIn(
            ["xrandr", "--output", "HDMI-1-1", "--auto", "--right-of", "DSI-2"], calls,
        )
        # Fix 6: eindeutiges Zeigergeraet per "pointer:"-Praefix.
        self.assertIn(
            ["xinput", "map-to-output", "pointer:Goodix Capacitive TouchScreen", "DSI-2"], calls,
        )
        # Touch-Zuordnung erst NACH dem Einschalten (neue X-Flaeche).
        on_index = calls.index(["xrandr", "--output", "HDMI-1-1", "--auto", "--right-of", "DSI-2"])
        map_index = calls.index(
            ["xinput", "map-to-output", "pointer:Goodix Capacitive TouchScreen", "DSI-2"])
        self.assertLess(on_index, map_index)

    def test_power_off_uses_off_flag(self) -> None:
        (ok, _), calls = self._call(XRANDR_BEAMER_ON, False)
        self.assertTrue(ok)
        self.assertIn(["xrandr", "--output", "HDMI-1-2", "--off"], calls)

    def test_power_off_remaps_touch_afterwards(self) -> None:
        """NEU (Fix 6): nach "HDMI aus" schrumpft die X-Flaeche - Touch muss
        neu zugeordnet werden, und zwar NACH dem Abschalten."""
        (ok, message), calls = self._call(XRANDR_BEAMER_ON, False)
        self.assertTrue(ok)
        self.assertEqual(message, "")
        off_index = calls.index(["xrandr", "--output", "HDMI-1-2", "--off"])
        map_index = calls.index(
            ["xinput", "map-to-output", "pointer:Goodix Capacitive TouchScreen", "DSI-2"])
        self.assertLess(off_index, map_index)

    def test_missing_touchscreen_is_only_a_warning(self) -> None:
        calls: list[list[str]] = []
        responses = {"xrandr": XRANDR_BEAMER_OFF, "xinput": "Virtual core pointer\n"}
        with mock.patch("hdmi_output_service._use_wayland", return_value=False), \
             mock.patch("hdmi_output_service.subprocess.run", _fake_run(responses, calls)):
            ok, message = set_power("auto", True)
        self.assertTrue(ok)
        self.assertIn("kein Goodix-Touchscreen", message)

    def test_power_on_without_beamer_reports_no_device(self) -> None:
        (ok, message), calls = self._call(XRANDR_NO_BEAMER, True)
        self.assertFalse(ok)
        self.assertEqual(message, "Kein Ausgabegerät erkannt.")
        self.assertEqual([c[0] for c in calls], ["xrandr"])


class ParseWlrRandrTestCase(unittest.TestCase):
    def test_connected_and_enabled_output_is_parsed(self) -> None:
        result = _parse_wlr_randr(WLR_RANDR_SAMPLE, "HDMI-A-1")
        self.assertEqual(
            result,
            {"connected": True, "powered_on": True, "resolution": "1920x1080"},
        )

    def test_connected_but_disabled_output_is_parsed(self) -> None:
        result = _parse_wlr_randr(WLR_RANDR_OFF_SAMPLE, "HDMI-A-1")
        self.assertEqual(result["connected"], True)
        self.assertEqual(result["powered_on"], False)
        self.assertEqual(result["resolution"], "1920x1080")

    def test_unknown_connector_name_is_not_connected(self) -> None:
        result = _parse_wlr_randr(WLR_RANDR_SAMPLE, "HDMI-A-9")
        self.assertEqual(result, {"connected": False, "powered_on": None, "resolution": ""})

    def test_disabled_second_output_without_modes(self) -> None:
        result = _parse_wlr_randr(WLR_RANDR_SAMPLE, "HDMI-A-2")
        self.assertEqual(result["connected"], True)
        self.assertEqual(result["powered_on"], False)
        self.assertEqual(result["resolution"], "")

    def test_empty_text_is_not_connected(self) -> None:
        result = _parse_wlr_randr("", "HDMI-A-1")
        self.assertFalse(result["connected"])


class ReadStatusAndSetPowerMissingCommandTestCase(unittest.TestCase):
    """Simuliert ein fehlendes xrandr, unabhaengig vom echten System."""

    def test_read_status_reports_missing_command(self) -> None:
        with mock.patch("hdmi_output_service._use_wayland", return_value=False), \
             mock.patch("hdmi_output_service.subprocess.run", side_effect=FileNotFoundError):
            status = read_status("auto")
        self.assertFalse(status.available)
        self.assertFalse(status.connected)
        self.assertIn("xrandr", status.error)

    def test_set_power_reports_missing_command(self) -> None:
        with mock.patch("hdmi_output_service._use_wayland", return_value=False), \
             mock.patch("hdmi_output_service.subprocess.run", side_effect=FileNotFoundError):
            ok, message = set_power("auto", True)
        self.assertFalse(ok)
        self.assertIn("xrandr", message)


class FakePopen:
    """Minimaler Popen-Ersatz fuer HdmiOutputService-Tests - kein echter
    Subprozess, nur Zustandsverwaltung fuer poll()/terminate()/wait()."""

    def __init__(self, args) -> None:
        self.args = args
        self._terminated = False
        self._returncode: int | None = None

    def poll(self):
        return self._returncode

    def terminate(self) -> None:
        self._terminated = True
        self._returncode = 0

    def kill(self) -> None:
        self._returncode = -9

    def wait(self, timeout=None):
        return self._returncode


class HdmiOutputServiceSlideshowTestCase(unittest.TestCase):
    ACTIVE = HdmiOutputStatus(
        available=True, connected=True, powered_on=True, resolution="1920x1080",
        error="", connector="HDMI-1-2", geometry=(1920, 1080, 1280, 0),
    )

    def _make_service(self) -> tuple[HdmiOutputService, list[FakePopen]]:
        created: list[FakePopen] = []

        def factory(args, **kwargs):
            proc = FakePopen(args)
            created.append(proc)
            return proc

        service = HdmiOutputService(
            connector_name="auto",
            photo_dir=Path("/tmp/fake-photos"),
            popen_factory=factory,
        )
        return service, created

    def _start(self, service: HdmiOutputService, status: HdmiOutputStatus | None = None):
        with mock.patch("hdmi_output_service._use_wayland", return_value=False), \
             mock.patch("hdmi_output_service.read_status", return_value=status or self.ACTIVE):
            return service.start_slideshow()

    def test_not_running_before_start(self) -> None:
        service, _ = self._make_service()
        self.assertFalse(service.is_slideshow_running())

    def test_start_slideshow_passes_connector_geometry_and_photo_dir(self) -> None:
        service, created = self._make_service()
        ok, message = self._start(service)
        self.assertTrue(ok)
        self.assertEqual(message, "")
        args = created[0].args
        self.assertEqual(args[args.index("--connector") + 1], "HDMI-1-2")
        self.assertEqual(args[args.index("--geometry") + 1], "1920x1080+1280+0")
        self.assertIn("--photo-dir", args)
        self.assertTrue(service.is_slideshow_running())

    def test_start_without_beamer_does_not_launch_process(self) -> None:
        service, created = self._make_service()
        status = HdmiOutputStatus(
            available=True, connected=False, powered_on=None, resolution="", error="",
        )
        ok, message = self._start(service, status)
        self.assertFalse(ok)
        self.assertEqual(message, "Kein Ausgabegerät erkannt.")
        self.assertEqual(created, [])

    def test_start_with_output_switched_off_asks_to_enable_first(self) -> None:
        service, created = self._make_service()
        status = HdmiOutputStatus(
            available=True, connected=True, powered_on=False, resolution="",
            error="", connector="HDMI-1-1", geometry=None,
        )
        ok, message = self._start(service, status)
        self.assertFalse(ok)
        self.assertIn("einschalten", message)
        self.assertEqual(created, [])

    def test_start_reports_tool_error(self) -> None:
        service, created = self._make_service()
        status = HdmiOutputStatus(
            available=False, connected=False, powered_on=None, resolution="",
            error="xrandr ist auf diesem System nicht installiert.",
        )
        ok, message = self._start(service, status)
        self.assertFalse(ok)
        self.assertIn("xrandr", message)
        self.assertEqual(created, [])

    def test_start_slideshow_is_a_noop_if_already_running(self) -> None:
        service, created = self._make_service()
        self._start(service)
        self._start(service)
        self.assertEqual(len(created), 1)

    def test_stop_slideshow_terminates_process(self) -> None:
        service, created = self._make_service()
        self._start(service)
        service.stop_slideshow()
        self.assertTrue(created[0]._terminated)
        self.assertFalse(service.is_slideshow_running())

    def test_stop_slideshow_without_running_process_does_nothing(self) -> None:
        service, _ = self._make_service()
        service.stop_slideshow()  # darf keine Ausnahme werfen
        self.assertFalse(service.is_slideshow_running())

    def test_is_slideshow_running_detects_process_that_died_on_its_own(self) -> None:
        service, created = self._make_service()
        self._start(service)
        created[0]._returncode = 1  # simuliert einen Absturz
        self.assertFalse(service.is_slideshow_running())
        # Nach dem Erkennen des Absturzes muss ein Neustart wieder moeglich sein.
        self._start(service)
        self.assertEqual(len(created), 2)

    def test_run_test_reports_missing_tool(self) -> None:
        service, _ = self._make_service()
        with mock.patch("hdmi_output_service._use_wayland", return_value=False), \
             mock.patch("hdmi_output_service.subprocess.run", side_effect=FileNotFoundError):
            message = service.run_test(5.0)
        self.assertIn("fehlgeschlagen", message)

    def test_run_test_names_connector_and_resolution(self) -> None:
        service, _ = self._make_service()
        with mock.patch("hdmi_output_service.read_status", return_value=self.ACTIVE):
            message = service.run_test(5.0)
        self.assertEqual(message, "Ausgabegerät erkannt (HDMI-1-2: 1920x1080, eingeschaltet).")


if __name__ == "__main__":
    unittest.main()


class TouchPrimaryTestCase(unittest.TestCase):
    """NEU: Fotobox soll auf dem Touch-Display bleiben, nicht auf dem Beamer."""

    def test_pick_display_index_prefers_matching_size(self):
        from hdmi_output_service import pick_display_index
        self.assertEqual(pick_display_index([(1920, 1080), (1280, 720)], (1280, 720)), 1)
        self.assertEqual(pick_display_index([(1280, 720), (1920, 1080)], (1280, 720)), 0)
        self.assertEqual(pick_display_index([(1920, 1080)], (1280, 720)), 0)
        self.assertEqual(pick_display_index([], (1280, 720)), 0)

    def test_ensure_touch_primary_runs_xrandr_primary(self):
        from unittest import mock
        import hdmi_output_service as h
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        def fake_run_with_touch(cmd, **kwargs):
            calls.append(cmd)
            stdout = XINPUT_NAMES_PI if cmd[:2] == ["xinput", "--list"] else ""
            return mock.Mock(returncode=0, stdout=stdout, stderr="")

        with mock.patch.object(h, "_use_wayland", return_value=False), \
                mock.patch("hdmi_output_service.subprocess.run", fake_run_with_touch):
            self.assertEqual(h.ensure_touch_primary("DSI-2"), "")
        # GEAENDERT (Fix 6): nach --primary folgt die Touch-Zuordnung.
        self.assertEqual(calls, [
            ["xrandr", "--output", "DSI-2", "--primary"],
            ["xinput", "--list", "--name-only"],
            ["xinput", "map-to-output", "pointer:Goodix Capacitive TouchScreen", "DSI-2"],
        ])
        del fake_run


class StraySlideshowTestCase(unittest.TestCase):
    """NEU: Waisen-Prozesse der Beamer-Wiedergabe muessen beendet werden."""

    def test_stop_slideshow_kills_strays_even_without_own_process(self):
        from unittest import mock
        import hdmi_output_service as h
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("hdmi_output_service.subprocess.run", fake_run):
            service = h.HdmiOutputService("auto", Path("/tmp"))
            calls.clear()
            service.stop_slideshow()
        self.assertEqual(calls, [["pkill", "-f", r"python.*hdmi_slideshow\.py"]])


class WallpaperTestCase(unittest.TestCase):
    """NEU: Standbild auf dem Beamer, solange die Galerie gestoppt ist."""

    def _status(self, geometry=(1920, 1080, 1280, 0)):
        import hdmi_output_service as h
        return h.HdmiOutputStatus(
            available=True, connected=True, powered_on=geometry is not None,
            resolution="1920x1080", error="", slideshow_running=False,
            connector="HDMI-1-1", geometry=geometry,
        )

    def test_show_wallpaper_starts_process_with_wallpaper_arg(self):
        import tempfile
        from unittest import mock
        import hdmi_output_service as h
        with tempfile.TemporaryDirectory() as tmp:
            wp = Path(tmp) / "w.png"
            wp.write_bytes(b"x")
            started = []
            service = h.HdmiOutputService(
                "auto", Path(tmp), wallpaper_path=wp,
                popen_factory=lambda args, **kw: started.append(args) or mock.Mock(poll=lambda: None),
            )
            with mock.patch.object(h, "read_status", return_value=self._status()), \
                    mock.patch.object(h, "kill_stray_slideshows"):
                ok, _ = service.show_wallpaper()
        self.assertTrue(ok)
        self.assertIn("--wallpaper", started[0])
        self.assertIn(str(wp), started[0])
        self.assertIn("1920x1080+1280+0", started[0])

    def test_show_wallpaper_requires_active_output_and_file(self):
        import tempfile
        from unittest import mock
        import hdmi_output_service as h
        with tempfile.TemporaryDirectory() as tmp:
            service = h.HdmiOutputService("auto", Path(tmp), wallpaper_path=Path(tmp) / "fehlt.png")
            with mock.patch.object(h, "kill_stray_slideshows"):
                self.assertFalse(service.show_wallpaper()[0])
            wp = Path(tmp) / "w.png"
            wp.write_bytes(b"x")
            service = h.HdmiOutputService("auto", Path(tmp), wallpaper_path=wp)
            with mock.patch.object(h, "read_status", return_value=self._status(None)), \
                    mock.patch.object(h, "kill_stray_slideshows"):
                self.assertFalse(service.show_wallpaper()[0])


class TouchMappingGuardTestCase(unittest.TestCase):
    """NEU (Fix 6): automatische Neu-Zuordnung bei geaenderter Aufteilung
    (z.B. Monitor im laufenden Betrieb eingesteckt)."""

    def _guard(self, signatures):
        import hdmi_output_service as h
        remapped: list[str] = []
        sequence = iter(signatures)
        guard = h.TouchMappingGuard(
            "DSI-2",
            read_signature=lambda: next(sequence),
            remap=lambda output: remapped.append(output) or "",
        )
        return guard, remapped

    def test_first_check_only_remembers_layout(self) -> None:
        guard, remapped = self._guard(["A"])
        self.assertFalse(guard.check_once())
        self.assertEqual(remapped, [])

    def test_unchanged_layout_does_not_remap(self) -> None:
        guard, remapped = self._guard(["A", "A", "A"])
        guard.check_once()
        self.assertFalse(guard.check_once())
        self.assertFalse(guard.check_once())
        self.assertEqual(remapped, [])

    def test_changed_layout_remaps_once(self) -> None:
        guard, remapped = self._guard(["A", "B", "B"])
        guard.check_once()
        self.assertTrue(guard.check_once())
        self.assertFalse(guard.check_once())
        self.assertEqual(remapped, ["DSI-2"])

    def test_unreadable_layout_is_ignored(self) -> None:
        guard, remapped = self._guard(["A", None, "A"])
        guard.check_once()
        self.assertFalse(guard.check_once())
        self.assertFalse(guard.check_once())
        self.assertEqual(remapped, [])

    def test_note_layout_changed_suppresses_double_remap(self) -> None:
        # App hat selbst umgeschaltet (set_power ordnet bereits zu) - der
        # Waechter soll das nicht noch einmal tun.
        guard, remapped = self._guard(["A", "B", "B"])
        guard.check_once()
        guard.note_layout_changed()
        self.assertFalse(guard.check_once())
        self.assertEqual(remapped, [])

    def test_start_and_stop_thread(self) -> None:
        import hdmi_output_service as h
        guard = h.TouchMappingGuard(
            "DSI-2", interval_seconds=0.01,
            read_signature=lambda: "A", remap=lambda output: "",
        )
        guard.start()
        guard.stop()
        self.assertIsNone(guard._thread)

    def test_layout_signature_uses_listmonitors(self) -> None:
        import hdmi_output_service as h
        calls: list[list[str]] = []
        with mock.patch.object(h, "_use_wayland", return_value=False), \
             mock.patch("hdmi_output_service.subprocess.run",
                        _fake_run({"xrandr": "Monitors: 1\n 0: +*DSI-2 ...\n"}, calls)):
            self.assertEqual(h.read_layout_signature(), "Monitors: 1\n 0: +*DSI-2 ...")
        self.assertEqual(calls, [["xrandr", "--listmonitors"]])


class EnsureHdmiOffTestCase(unittest.TestCase):
    """NEU (Nutzer-Feedback 2026-11-14): HDMI bei App-Start, Neustart und
    Herunterfahren abschalten - nur wenn der Ausgang wirklich aktiv ist."""

    def _call(self, xrandr_text: str):
        import hdmi_output_service as h
        calls: list[list[str]] = []
        responses = {"xrandr": xrandr_text, "xinput": XINPUT_NAMES_PI}
        with mock.patch.object(h, "_use_wayland", return_value=False), \
             mock.patch("hdmi_output_service.subprocess.run", _fake_run(responses, calls)):
            result = h.ensure_hdmi_off("auto")
        return result, calls

    def test_active_output_is_switched_off_and_touch_remapped(self) -> None:
        (switched_off, message), calls = self._call(XRANDR_BEAMER_ON)
        self.assertTrue(switched_off)
        self.assertEqual(message, "")
        self.assertIn(["xrandr", "--output", "HDMI-1-2", "--off"], calls)
        self.assertIn(
            ["xinput", "map-to-output", "pointer:Goodix Capacitive TouchScreen", "DSI-2"], calls,
        )

    def test_inactive_output_is_left_alone(self) -> None:
        (switched_off, message), calls = self._call(XRANDR_BEAMER_OFF)
        self.assertFalse(switched_off)
        self.assertEqual(message, "")
        self.assertFalse(any("--off" in c for c in calls))

    def test_no_device_does_nothing(self) -> None:
        (switched_off, message), calls = self._call(XRANDR_NO_BEAMER)
        self.assertFalse(switched_off)
        self.assertEqual(message, "")
        self.assertEqual(calls, [["xrandr"]])

    def test_missing_xrandr_reports_message_without_exception(self) -> None:
        import hdmi_output_service as h
        with mock.patch.object(h, "_use_wayland", return_value=False), \
             mock.patch("hdmi_output_service.subprocess.run", side_effect=FileNotFoundError):
            switched_off, message = h.ensure_hdmi_off("auto")
        self.assertFalse(switched_off)
        self.assertIn("xrandr", message)
