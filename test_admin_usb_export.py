"""
test_admin_usb_export.py
=========================
Tests fuer die Export-Logik (Etappe 4b). Alles ohne echten USB-Stick -
Quelle und Ziel sind temporaere Verzeichnisse.

    python3 -m pytest test_admin_usb_export.py -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import dataclasses

from admin_usb_export import (
    DEFAULT_CONFLICT_DECISION,
    ExportConflict,
    ExportProgress,
    ExportResult,
    apply_conflict_resolutions,
    clear_stick,
    export_photos,
    next_free_rename,
    sanitize_folder_name,
)


class ExportPhotosTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.photo_dir = base / "photos"
        self.stick = base / "stick"
        self.photo_dir.mkdir()
        self.stick.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make(self, name: str, size: int = 1024) -> Path:
        path = self.photo_dir / name
        path.write_bytes(b"X" * size)
        return path

    def _export(self, **kwargs):
        params = dict(
            photo_dir=self.photo_dir,
            mountpoint=self.stick,
            excluded_filenames=frozenset({"testbild.png"}),
            progress=ExportProgress(),
            folder_name="Minas Geburtstags-Fotobox",
            verify=True,
        )
        params.update(kwargs)
        return export_photos(**params)

    # -- Grundfunktion -----------------------------------------------------

    def test_copies_all_images(self) -> None:
        self._make("a.jpg")
        self._make("b.png")
        result = self._export()
        self.assertEqual(result.copied, 2)
        self.assertTrue(result.ok)

    def test_folder_is_named_after_the_event(self) -> None:
        self._make("a.jpg")
        result = self._export()
        self.assertEqual(result.target_dir.name, "Minas Geburtstags-Fotobox")
        self.assertTrue((result.target_dir / "a.jpg").exists())

    def test_repeated_export_uses_the_same_folder(self) -> None:
        # Ohne Zeitstempel im Namen landet ein zweiter Lauf im selben
        # Ordner - nur so kann die Uebersprung-Logik ueberhaupt greifen.
        self._make("a.jpg")
        first = self._export()
        second = self._export()
        self.assertEqual(first.target_dir, second.target_dir)
        self.assertEqual(second.skipped, 1)

    def test_excluded_file_is_not_exported(self) -> None:
        self._make("a.jpg")
        self._make("testbild.png")
        result = self._export()
        self.assertEqual(result.copied, 1)
        self.assertFalse((result.target_dir / "testbild.png").exists())

    def test_non_images_are_not_exported(self) -> None:
        self._make("a.jpg")
        self._make("notizen.txt")
        result = self._export()
        self.assertEqual(result.copied, 1)

    def test_empty_directory_exports_nothing(self) -> None:
        result = self._export()
        self.assertEqual(result.copied, 0)
        self.assertTrue(result.ok)

    # -- Verifikation ------------------------------------------------------

    def test_verification_passes_on_good_copy(self) -> None:
        self._make("a.jpg", 5000)
        result = self._export()
        self.assertEqual(result.verified, 1)
        self.assertEqual(result.failed_verify, [])
        self.assertTrue(result.ok)

    def test_verification_catches_corruption(self) -> None:
        self._make("a.jpg", 5000)
        result = self._export()
        # Korruption simulieren: Zieldatei nachtraeglich aendern.
        corrupted = result.target_dir / "a.jpg"
        data = corrupted.read_bytes()
        corrupted.write_bytes(data[:100] + b"\0" * 100 + data[200:])

        # Zweiter Export: Datei hat gleiche Groesse, wird also uebersprungen,
        # aber die Verifikation sieht den Unterschied.
        result2 = self._export()
        self.assertIn("a.jpg", result2.failed_verify)
        self.assertFalse(result2.ok)

    # -- Uebersprung-Logik -------------------------------------------------

    def test_existing_file_with_same_size_is_skipped(self) -> None:
        self._make("a.jpg", 1000)
        r1 = self._export()
        r2 = self._export()
        self.assertEqual(r1.copied, 1)
        self.assertEqual(r2.copied, 0)
        self.assertEqual(r2.skipped, 1)

    def test_partial_reexport_copies_only_missing(self) -> None:
        for i in range(5):
            self._make(f"foto_{i:03d}.jpg")
        r1 = self._export()
        # Zwei Dateien vom Stick loeschen.
        (r1.target_dir / "foto_001.jpg").unlink()
        (r1.target_dir / "foto_003.jpg").unlink()
        r2 = self._export()
        self.assertEqual(r2.copied, 2)
        self.assertEqual(r2.skipped, 3)

    # -- Zusammenfassung ---------------------------------------------------

    def test_summary_mentions_verification(self) -> None:
        self._make("a.jpg")
        result = self._export()
        joined = " ".join(result.summary_lines())
        self.assertIn("SHA256", joined)

    def test_failed_verify_blocks_delete_offer(self) -> None:
        result = ExportResult()
        result.failed_verify.append("bad.jpg")
        joined = " ".join(result.summary_lines())
        self.assertIn("PRÜFSUMMENFEHLER", joined)
        self.assertIn("NICHT", joined)
        self.assertFalse(result.ok)

    def test_summary_mentions_target_folder(self) -> None:
        self._make("a.jpg")
        result = self._export()
        joined = " ".join(result.summary_lines())
        self.assertIn("Minas Geburtstags-Fotobox", joined)

    # -- Fortschritt -------------------------------------------------------

    def test_progress_reaches_done(self) -> None:
        self._make("a.jpg")
        progress = ExportProgress()
        self._export(progress=progress)
        self.assertEqual(progress.phase, "done")
        self.assertEqual(progress.total_files, 1)


class SanitizeFolderNameTestCase(unittest.TestCase):
    """Der Event-Titel ist Freitext aus config.py und kann Zeichen
    enthalten, die FAT/exFAT nicht erlauben."""

    def test_plain_title_is_unchanged(self) -> None:
        self.assertEqual(sanitize_folder_name("Minas Geburtstags-Fotobox"),
                         "Minas Geburtstags-Fotobox")

    def test_umlauts_are_kept(self) -> None:
        self.assertEqual(sanitize_folder_name("Grillfeier Müller"), "Grillfeier Müller")

    def test_forbidden_characters_are_replaced(self) -> None:
        for ch in '<>:"/\\|?*':
            result = sanitize_folder_name(f"Fest{ch}2026")
            self.assertNotIn(ch, result)
            self.assertTrue(result.startswith("Fest"))

    def test_trailing_dot_and_space_are_stripped(self) -> None:
        # Windows kann Ordner mit Punkt/Leerzeichen am Ende nicht oeffnen.
        self.assertEqual(sanitize_folder_name("Sommerfest. "), "Sommerfest")

    def test_empty_title_falls_back(self) -> None:
        self.assertEqual(sanitize_folder_name("   "), "Fotobox")
        self.assertEqual(sanitize_folder_name(""), "Fotobox")

    def test_length_is_capped(self) -> None:
        self.assertLessEqual(len(sanitize_folder_name("x" * 300)), 80)

    def test_multiple_spaces_are_collapsed(self) -> None:
        self.assertEqual(sanitize_folder_name("Fest    2026"), "Fest 2026")


class ClearStickTestCase(unittest.TestCase):
    def test_clears_files_and_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stick = Path(tmp)
            (stick / "datei.txt").write_bytes(b"x")
            (stick / "ordner").mkdir()
            (stick / "ordner" / "inner.txt").write_bytes(b"y")
            deleted, errors = clear_stick(stick)
            self.assertEqual(deleted, 2)
            self.assertEqual(errors, [])
            self.assertEqual(list(stick.iterdir()), [])

    def test_empty_stick_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deleted, errors = clear_stick(Path(tmp))
            self.assertEqual(deleted, 0)
            self.assertEqual(errors, [])


class ConflictDetectionTestCase(unittest.TestCase):
    """Etappe 6a: export_photos(collect_conflicts=True) - Erkennung.

    Nur bei ABWEICHENDEM Inhalt entsteht ein Konflikt; byteweise
    identische Dateien werden weiterhin still uebersprungen.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.photo_dir = base / "photos"
        self.stick = base / "stick"
        self.photo_dir.mkdir()
        self.stick.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make(self, name: str, data: bytes) -> Path:
        path = self.photo_dir / name
        path.write_bytes(data)
        return path

    def _export(self, **kwargs):
        params = dict(
            photo_dir=self.photo_dir,
            mountpoint=self.stick,
            excluded_filenames=frozenset({"testbild.png"}),
            progress=ExportProgress(),
            folder_name="Minas Geburtstags-Fotobox",
            verify=True,
            collect_conflicts=True,
        )
        params.update(kwargs)
        return export_photos(**params)

    def test_new_files_are_copied(self) -> None:
        self._make("a.jpg", b"AAAA")
        self._make("b.png", b"BBBB")
        result = self._export()
        self.assertEqual(result.copied, 2)
        self.assertEqual(result.conflicts, [])
        self.assertTrue(result.ok)

    def test_identical_file_is_skipped_not_conflict(self) -> None:
        self._make("a.jpg", b"AAAA")
        self._export()                      # erster Lauf kopiert
        result = self._export()             # zweiter Lauf: identisch
        self.assertEqual(result.copied, 0)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.conflicts, [])
        self.assertTrue(result.ok)

    def test_differing_content_becomes_conflict(self) -> None:
        self._make("a.jpg", b"AAAA")
        self._export()                      # Stick hat jetzt "AAAA"
        self._make("a.jpg", b"BBBBBB")      # Quelle aendert sich (andere Groesse)
        result = self._export()
        self.assertEqual(result.copied, 0)
        self.assertEqual(result.skipped, 0)
        self.assertEqual(len(result.conflicts), 1)
        conflict = result.conflicts[0]
        self.assertEqual(conflict.name, "a.jpg")
        self.assertEqual(conflict.src_size, 6)
        self.assertEqual(conflict.dst_size, 4)
        # Nicht kopiert: der Stick traegt noch den alten Inhalt.
        self.assertEqual((result.target_dir / "a.jpg").read_bytes(), b"AAAA")

    def test_differing_content_same_size_becomes_conflict(self) -> None:
        # Gleiche Groesse, anderer Inhalt - die alte, rein groessenbasierte
        # Logik haette das faelschlich uebersprungen.
        self._make("a.jpg", b"AAAA")
        self._export()
        self._make("a.jpg", b"ZZZZ")        # gleiche Groesse, anderer Inhalt
        result = self._export()
        self.assertEqual(len(result.conflicts), 1)
        self.assertFalse(result.ok)

    def test_new_files_copied_alongside_conflicts(self) -> None:
        self._make("a.jpg", b"AAAA")
        self._export()
        self._make("a.jpg", b"CHANGED")     # wird Konflikt
        self._make("b.jpg", b"NEW")         # ist neu
        result = self._export()
        self.assertEqual(result.copied, 1)
        self.assertEqual(len(result.conflicts), 1)
        self.assertEqual(result.conflicts[0].name, "a.jpg")
        self.assertTrue((result.target_dir / "b.jpg").exists())

    def test_default_decision_is_rename(self) -> None:
        self._make("a.jpg", b"AAAA")
        self._export()
        self._make("a.jpg", b"CHANGED")
        result = self._export()
        self.assertEqual(result.conflicts[0].decision, "rename")
        self.assertEqual(DEFAULT_CONFLICT_DECISION, "rename")

    def test_open_conflicts_make_result_not_ok(self) -> None:
        self._make("a.jpg", b"AAAA")
        self._export()
        self._make("a.jpg", b"CHANGED")
        result = self._export()
        self.assertFalse(result.ok)

    def test_progress_phase_is_conflicts_when_open(self) -> None:
        self._make("a.jpg", b"AAAA")
        self._export()
        self._make("a.jpg", b"CHANGED")
        progress = ExportProgress()
        self._export(progress=progress)
        self.assertEqual(progress.phase, "conflicts")

    def test_progress_phase_done_without_conflicts(self) -> None:
        self._make("a.jpg", b"AAAA")
        progress = ExportProgress()
        self._export(progress=progress)
        self.assertEqual(progress.phase, "done")


class NextFreeRenameTestCase(unittest.TestCase):
    def test_scheme_starts_at_001(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "foto.jpg").write_bytes(b"x")
            self.assertEqual(next_free_rename(target, "foto.jpg").name, "foto_001.jpg")

    def test_scheme_counts_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "foto.jpg").write_bytes(b"x")
            (target / "foto_001.jpg").write_bytes(b"x")
            (target / "foto_002.jpg").write_bytes(b"x")
            self.assertEqual(next_free_rename(target, "foto.jpg").name, "foto_003.jpg")

    def test_preserves_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "bild.png").write_bytes(b"x")
            self.assertEqual(next_free_rename(target, "bild.png").name, "bild_001.png")


class ConflictResolutionTestCase(unittest.TestCase):
    """Etappe 6a: apply_conflict_resolutions() - Phase 2."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.photo_dir = base / "photos"
        self.stick = base / "stick"
        self.photo_dir.mkdir()
        self.stick.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make(self, name: str, data: bytes) -> None:
        (self.photo_dir / name).write_bytes(data)

    def _export(self, progress=None):
        return export_photos(
            photo_dir=self.photo_dir,
            mountpoint=self.stick,
            excluded_filenames=frozenset(),
            progress=progress or ExportProgress(),
            folder_name="Fest",
            verify=True,
            collect_conflicts=True,
        )

    def _conflicted_result(self):
        """Erzeugt ein Ergebnis mit genau einem offenen Konflikt auf a.jpg
        (Stick: b'ALT', Quelle: b'NEU')."""
        self._make("a.jpg", b"ALT")
        self._export()
        self._make("a.jpg", b"NEU")
        result = self._export()
        self.assertEqual(len(result.conflicts), 1)
        return result

    def _set_decision(self, result, name, decision):
        result.conflicts = [
            dataclasses.replace(c, decision=decision) if c.name == name else c
            for c in result.conflicts
        ]

    def test_overwrite_replaces_file(self) -> None:
        result = self._conflicted_result()
        self._set_decision(result, "a.jpg", "overwrite")
        apply_conflict_resolutions(self.photo_dir, result, ExportProgress())
        self.assertEqual(result.overwritten, 1)
        self.assertEqual(result.renamed, 0)
        self.assertEqual((result.target_dir / "a.jpg").read_bytes(), b"NEU")
        self.assertEqual(result.conflicts, [])
        self.assertTrue(result.ok)

    def test_rename_keeps_original_and_adds_copy(self) -> None:
        result = self._conflicted_result()
        self._set_decision(result, "a.jpg", "rename")
        apply_conflict_resolutions(self.photo_dir, result, ExportProgress())
        self.assertEqual(result.renamed, 1)
        self.assertEqual(result.overwritten, 0)
        # Original bleibt unveraendert, neue Kopie unter a_001.jpg.
        self.assertEqual((result.target_dir / "a.jpg").read_bytes(), b"ALT")
        self.assertEqual((result.target_dir / "a_001.jpg").read_bytes(), b"NEU")
        self.assertTrue(result.ok)

    def test_rename_avoids_existing_numbered_file(self) -> None:
        result = self._conflicted_result()
        # a_001.jpg existiert bereits auf dem Stick -> Kopie muss a_002.jpg werden.
        (result.target_dir / "a_001.jpg").write_bytes(b"belegt")
        self._set_decision(result, "a.jpg", "rename")
        apply_conflict_resolutions(self.photo_dir, result, ExportProgress())
        self.assertEqual((result.target_dir / "a_001.jpg").read_bytes(), b"belegt")
        self.assertEqual((result.target_dir / "a_002.jpg").read_bytes(), b"NEU")

    def test_resolution_verifies_written_files(self) -> None:
        result = self._conflicted_result()
        self._set_decision(result, "a.jpg", "overwrite")
        before = result.verified
        apply_conflict_resolutions(self.photo_dir, result, ExportProgress())
        self.assertEqual(result.verified, before + 1)
        self.assertEqual(result.failed_verify, [])

    def test_log_records_overwrite_and_rename(self) -> None:
        # Zwei Konflikte, unterschiedliche Entscheidungen.
        self._make("a.jpg", b"ALT")
        self._make("b.jpg", b"ALT")
        self._export()
        self._make("a.jpg", b"NEU1")
        self._make("b.jpg", b"NEU2")
        result = self._export()
        self.assertEqual(len(result.conflicts), 2)
        self._set_decision(result, "a.jpg", "overwrite")
        self._set_decision(result, "b.jpg", "rename")
        apply_conflict_resolutions(self.photo_dir, result, ExportProgress())
        joined = " | ".join(result.log_actions)
        self.assertIn("überschrieben: a.jpg", joined)
        self.assertIn("umbenannt: b.jpg -> b_001.jpg", joined)

    def test_progress_reaches_done_after_resolution(self) -> None:
        result = self._conflicted_result()
        self._set_decision(result, "a.jpg", "rename")
        progress = ExportProgress()
        apply_conflict_resolutions(self.photo_dir, result, progress)
        self.assertEqual(progress.phase, "done")


class ConflictSummaryTestCase(unittest.TestCase):
    def test_summary_mentions_overwritten(self) -> None:
        result = ExportResult(copied=3, overwritten=2)
        joined = " ".join(result.summary_lines())
        self.assertIn("Überschrieben: 2", joined)

    def test_summary_mentions_renamed(self) -> None:
        result = ExportResult(copied=1, renamed=4)
        joined = " ".join(result.summary_lines())
        self.assertIn("Umbenannt", joined)
        self.assertIn("4", joined)

    def test_summary_omits_zero_lines(self) -> None:
        # Weder ueberschrieben noch umbenannt -> keine solchen Zeilen.
        result = ExportResult(copied=5, verified=5)
        joined = " ".join(result.summary_lines())
        self.assertNotIn("Überschrieben", joined)
        self.assertNotIn("Umbenannt", joined)

    def test_open_conflict_marks_result_not_ok(self) -> None:
        result = ExportResult(copied=1)
        result.conflicts.append(
            ExportConflict(name="a.jpg", src_size=1, dst_size=2, src_mtime=0.0, dst_mtime=0.0)
        )
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
