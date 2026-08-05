"""
test_config.py
===============
Tests fuer load_event_config() (Etappe 8) - reine Datei-/JSON-Logik,
komplett offline und ohne Import-Seiteneffekte des restlichen config.py
testbar (load_event_config nimmt den Pfad als Parameter entgegen, statt
ihn beim Modul-Import fest zu verdrahten).

    python3 -m pytest test_config.py -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from config import load_event_config


class LoadEventConfigTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, content: str, name: str = "event_config.json") -> Path:
        path = self.base / name
        path.write_text(content, encoding="utf-8")
        return path

    # -- Datei fehlt / nicht lesbar ------------------------------------------

    def test_missing_file_returns_empty_dict(self) -> None:
        path = self.base / "does_not_exist.json"
        self.assertEqual(load_event_config(path), {})

    def test_missing_file_prints_warning(self) -> None:
        path = self.base / "does_not_exist.json"
        with self._capture_stdout() as out:
            load_event_config(path)
        self.assertIn("fehlt", out.getvalue())
        self.assertIn("event_config_example.json", out.getvalue())

    # -- Gueltiger Inhalt ------------------------------------------------------

    def test_valid_json_is_parsed(self) -> None:
        path = self._write('{"event_title": "Testfest", "photo_prefix": "test_"}')
        result = load_event_config(path)
        self.assertEqual(result, {"event_title": "Testfest", "photo_prefix": "test_"})

    def test_partial_json_returns_only_present_keys(self) -> None:
        path = self._write('{"event_title": "Nur der Titel"}')
        result = load_event_config(path)
        self.assertEqual(result, {"event_title": "Nur der Titel"})
        self.assertNotIn("photo_prefix", result)

    def test_umlauts_roundtrip_correctly(self) -> None:
        path = self._write('{"event_title": "Grillfest Müller \\u2013 Köln"}')
        result = load_event_config(path)
        self.assertEqual(result["event_title"], "Grillfest Müller – Köln")

    def test_empty_object_returns_empty_dict(self) -> None:
        path = self._write("{}")
        self.assertEqual(load_event_config(path), {})

    def test_unknown_extra_keys_are_kept(self) -> None:
        # load_event_config urteilt nicht ueber den Inhalt - das
        # entscheiden die Aufrufer in config.py (jeder Wert einzeln).
        path = self._write('{"event_title": "X", "irgendwas_unbekanntes": 42}')
        result = load_event_config(path)
        self.assertEqual(result["irgendwas_unbekanntes"], 42)

    # -- Fehlerhafter Inhalt -----------------------------------------------

    def test_malformed_json_returns_empty_dict(self) -> None:
        path = self._write('{"event_title": "kaputt"')  # fehlende schliessende Klammer
        self.assertEqual(load_event_config(path), {})

    def test_malformed_json_prints_warning(self) -> None:
        path = self._write('{invalid')
        with self._capture_stdout() as out:
            load_event_config(path)
        self.assertIn("konnte nicht gelesen werden", out.getvalue())

    def test_non_dict_root_list_returns_empty_dict(self) -> None:
        path = self._write('["a", "b"]')
        self.assertEqual(load_event_config(path), {})

    def test_non_dict_root_string_returns_empty_dict(self) -> None:
        path = self._write('"nur ein String"')
        self.assertEqual(load_event_config(path), {})

    def test_non_dict_root_prints_warning(self) -> None:
        path = self._write('[1, 2, 3]')
        with self._capture_stdout() as out:
            load_event_config(path)
        self.assertIn("kein JSON-Objekt als Wurzel", out.getvalue())

    def test_empty_file_returns_empty_dict(self) -> None:
        path = self._write("")
        self.assertEqual(load_event_config(path), {})

    # -- Hilfsmittel -------------------------------------------------------

    @staticmethod
    def _capture_stdout():
        import contextlib
        import io
        return contextlib.redirect_stdout(io.StringIO())


if __name__ == "__main__":
    unittest.main()
