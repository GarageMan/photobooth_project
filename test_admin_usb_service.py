"""
test_admin_usb_service.py
==========================
Tests fuer die USB-Stick-Logik (Etappe 4a).

Getestet wird alles, was ohne echten Stick pruefbar ist: die Filterung
der lsblk-Ausgabe (inklusive der Sicherheitsregel, dass die SD-Karte des
Pi NIEMALS als Ziel auftaucht) und die Berechnung des Platzbedarfs.
mount/umount selbst brauchen root und Hardware und sind so gebaut, dass
sie im Fehlerfall eine Meldung statt einer Ausnahme liefern.

    python3 -m pytest test_admin_usb_service.py -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from admin_usb_service import (
    _parse_lsblk,
    format_bytes,
    pick_best_partition,
    required_export_bytes,
)


# Wortwoertliche lsblk-Ausgabe eines echten Raspberry Pi 5 (Debian trixie)
# mit eingestecktem bootfaehigem Kali-Linux-Stick. Als Fixture aufgehoben,
# weil sie zwei Dinge belegt, die aus der Theorie nicht ersichtlich waren:
#   1. mmcblk0 (die SD-Karte des Pi!) meldet hotplug=true.
#   2. Ein Boot-Stick bringt eine grosse read-only-ISO-Partition und eine
#      winzige EFI-Partition mit - beide waeren die falsche Wahl.
REAL_PI_LSBLK = {
    "blockdevices": [
        {"name": "loop0", "path": "/dev/loop0", "type": "loop", "size": 2147483648,
         "fstype": "swap", "label": "origin:rpi-swap", "rm": False, "hotplug": False,
         "mountpoint": None},
        {"name": "sda", "path": "/dev/sda", "type": "disk", "size": 8011120640,
         "fstype": "iso9660", "label": "Kali Linux amd64 1", "rm": True, "hotplug": False,
         "mountpoint": None, "children": [
            {"name": "sda1", "path": "/dev/sda1", "type": "part", "size": 4728471552,
             "fstype": "iso9660", "label": "Kali Linux amd64 1", "rm": True, "hotplug": False,
             "mountpoint": "/media/photobox/Kali Linux amd64 1"},
            {"name": "sda2", "path": "/dev/sda2", "type": "part", "size": 3670016,
             "fstype": "vfat", "label": None, "rm": True, "hotplug": False,
             "mountpoint": None},
         ]},
        {"name": "mmcblk0", "path": "/dev/mmcblk0", "type": "disk", "size": 31914983424,
         "fstype": None, "label": None, "rm": False, "hotplug": True,
         "mountpoint": None, "children": [
            {"name": "mmcblk0p1", "path": "/dev/mmcblk0p1", "type": "part", "size": 536870912,
             "fstype": "vfat", "label": "bootfs", "rm": False, "hotplug": True,
             "mountpoint": "/boot/firmware"},
            {"name": "mmcblk0p2", "path": "/dev/mmcblk0p2", "type": "part", "size": 31369723904,
             "fstype": "ext4", "label": "rootfs", "rm": False, "hotplug": True,
             "mountpoint": "/"},
         ]},
        {"name": "zram0", "path": "/dev/zram0", "type": "disk", "size": 2147483648,
         "fstype": "swap", "label": "zram0", "rm": False, "hotplug": False,
         "mountpoint": "[SWAP]"},
    ]
}


def _disk(name: str, removable: bool, children: list[dict], size: int = 10**10) -> dict:
    return {
        "name": name, "path": f"/dev/{name}", "type": "disk", "size": str(size),
        "rm": removable, "hotplug": removable, "children": children,
    }


def _part(name: str, fstype: str | None, size: int = 10**10,
          label: str | None = None, mountpoint: str | None = None) -> dict:
    return {
        "name": name, "path": f"/dev/{name}", "type": "part", "size": str(size),
        "fstype": fstype, "label": label, "mountpoint": mountpoint,
    }


class ParseLsblkTestCase(unittest.TestCase):
    def test_finds_removable_partition(self) -> None:
        data = {"blockdevices": [_disk("sda", True, [_part("sda1", "exfat", label="STICK")])]}
        parts = _parse_lsblk(data)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].device, "/dev/sda1")
        self.assertEqual(parts[0].fstype, "exfat")
        self.assertEqual(parts[0].label, "STICK")

    def test_sd_card_is_never_offered(self) -> None:
        # Sicherheitsregel: die Systemkarte des Pi darf unter keinen
        # Umstaenden als Exportziel erscheinen - auch dann nicht, wenn
        # lsblk sie faelschlich als wechselbar meldet.
        data = {"blockdevices": [
            _disk("mmcblk0", True, [
                _part("mmcblk0p1", "vfat", label="bootfs", mountpoint="/boot/firmware"),
                _part("mmcblk0p2", "ext4", label="rootfs", mountpoint="/"),
            ]),
        ]}
        self.assertEqual(_parse_lsblk(data), [])

    def test_internal_disk_is_ignored(self) -> None:
        data = {"blockdevices": [_disk("sda", False, [_part("sda1", "ext4")])]}
        self.assertEqual(_parse_lsblk(data), [])

    def test_partition_without_filesystem_is_ignored(self) -> None:
        data = {"blockdevices": [_disk("sda", True, [
            _part("sda1", None),
            _part("sda2", "vfat"),
        ])]}
        parts = _parse_lsblk(data)
        self.assertEqual([p.device for p in parts], ["/dev/sda2"])

    def test_string_flags_are_accepted(self) -> None:
        # lsblk liefert je nach Version bool oder "0"/"1".
        data = {"blockdevices": [{
            "name": "sda", "path": "/dev/sda", "type": "disk", "size": "100",
            "rm": "1", "hotplug": "1",
            "children": [_part("sda1", "vfat")],
        }]}
        self.assertEqual(len(_parse_lsblk(data)), 1)

    def test_already_mounted_stick_keeps_its_mountpoint(self) -> None:
        # Der Desktop bindet Sticks u.U. selbst ein - dieser Einhaengepunkt
        # muss uebernommen und darf nicht ueberschrieben werden.
        data = {"blockdevices": [_disk("sdb", True, [
            _part("sdb1", "vfat", label="FOTOS", mountpoint="/media/photobox/FOTOS"),
        ])]}
        parts = _parse_lsblk(data)
        self.assertEqual(parts[0].mountpoint, "/media/photobox/FOTOS")

    def test_multiple_sticks_are_all_listed(self) -> None:
        data = {"blockdevices": [
            _disk("sda", True, [_part("sda1", "vfat")]),
            _disk("sdb", True, [_part("sdb1", "exfat")]),
        ]}
        self.assertEqual(len(_parse_lsblk(data)), 2)

    def test_empty_input_is_safe(self) -> None:
        self.assertEqual(_parse_lsblk({}), [])
        self.assertEqual(_parse_lsblk({"blockdevices": []}), [])

    def test_usb_transport_counts_as_removable(self) -> None:
        # USB-Festplatten und manche Sticks melden rm=0 UND hotplug=0.
        data = {"blockdevices": [{
            "name": "sda", "path": "/dev/sda", "type": "disk", "size": "100",
            "rm": False, "hotplug": False, "tran": "usb",
            "children": [_part("sda1", "exfat")],
        }]}
        self.assertEqual(len(_parse_lsblk(data)), 1)

    def test_internal_sata_disk_is_still_ignored(self) -> None:
        data = {"blockdevices": [{
            "name": "sda", "path": "/dev/sda", "type": "disk", "size": "100",
            "rm": False, "hotplug": False, "tran": "sata",
            "children": [_part("sda1", "ext4")],
        }]}
        self.assertEqual(_parse_lsblk(data), [])

    def test_display_name_falls_back_to_device(self) -> None:
        data = {"blockdevices": [_disk("sda", True, [_part("sda1", "vfat", label="")])]}
        name = _parse_lsblk(data)[0].display_name()
        self.assertIn("/dev/sda1", name)


class RealPiFixtureTestCase(unittest.TestCase):
    """Tests gegen echte Messwerte vom Pi statt gegen erdachte Beispiele."""

    def test_system_sd_card_is_filtered_despite_hotplug_flag(self) -> None:
        # Der wichtigste Test der ganzen Datei: mmcblk0 meldet auf diesem
        # Pi hotplug=true. Ohne den Pfad-Filter waeren /boot/firmware und /
        # als "Wechseldatentraeger" in der Auswahl gelandet.
        parts = _parse_lsblk(REAL_PI_LSBLK)
        for part in parts:
            self.assertNotIn("mmcblk", part.device)
        mountpoints = [p.mountpoint for p in parts]
        self.assertNotIn("/", mountpoints)
        self.assertNotIn("/boot/firmware", mountpoints)

    def test_loop_and_zram_are_not_offered(self) -> None:
        parts = _parse_lsblk(REAL_PI_LSBLK)
        for part in parts:
            self.assertNotIn("loop", part.device)
            self.assertNotIn("zram", part.device)

    def test_boot_stick_partitions_are_recognised(self) -> None:
        parts = _parse_lsblk(REAL_PI_LSBLK)
        self.assertEqual([p.device for p in parts], ["/dev/sda1", "/dev/sda2"])

    def test_readonly_iso_partition_is_never_chosen(self) -> None:
        # sda1 ist mit 4.4 GB die groesste - aber read-only. Die Auswahl
        # muss trotzdem auf die beschreibbare vfat-Partition fallen.
        parts = _parse_lsblk(REAL_PI_LSBLK)
        best = pick_best_partition(parts, required_bytes=50 * 1024 * 1024)
        self.assertIsNotNone(best)
        self.assertEqual(best.device, "/dev/sda2")
        self.assertEqual(best.fstype, "vfat")

    def test_udisks_mountpoint_is_preserved(self) -> None:
        # udisks2 bindet Sticks selbst nach /media/photobox/ ein - dieser
        # Einhaengepunkt muss uebernommen statt ueberschrieben werden.
        parts = _parse_lsblk(REAL_PI_LSBLK)
        sda1 = next(p for p in parts if p.device == "/dev/sda1")
        self.assertEqual(sda1.mountpoint, "/media/photobox/Kali Linux amd64 1")


class PickBestPartitionTestCase(unittest.TestCase):
    def test_returns_none_for_readonly_only(self) -> None:
        data = {"blockdevices": [_disk("sda", True, [_part("sda1", "iso9660")])]}
        self.assertIsNone(pick_best_partition(_parse_lsblk(data)))

    def test_prefers_partition_that_fits(self) -> None:
        data = {"blockdevices": [_disk("sda", True, [
            _part("sda1", "vfat", size=1000),
            _part("sda2", "vfat", size=100_000),
        ])]}
        best = pick_best_partition(_parse_lsblk(data), required_bytes=50_000)
        self.assertEqual(best.device, "/dev/sda2")

    def test_returns_largest_when_nothing_fits(self) -> None:
        # Damit die Pruefung eine aussagekraeftige "zu klein"-Meldung
        # erzeugen kann, statt wortlos nichts zu tun.
        data = {"blockdevices": [_disk("sda", True, [
            _part("sda1", "vfat", size=1000),
            _part("sda2", "vfat", size=5000),
        ])]}
        best = pick_best_partition(_parse_lsblk(data), required_bytes=10**9)
        self.assertEqual(best.device, "/dev/sda2")

    def test_empty_list_is_safe(self) -> None:
        self.assertIsNone(pick_best_partition([], 1000))


class RequiredExportBytesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.photo_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make(self, name: str, size: int = 1000) -> None:
        (self.photo_dir / name).write_bytes(b"X" * size)

    def test_counts_only_images(self) -> None:
        self._make("a.jpg", 1000)
        self._make("b.png", 2000)
        self._make("notizen.txt", 9999)
        count, net, gross = required_export_bytes(self.photo_dir)
        self.assertEqual(count, 2)
        self.assertEqual(net, 3000)

    def test_excluded_file_is_not_counted(self) -> None:
        self._make("a.jpg", 1000)
        self._make("testbild.png", 5000)
        count, net, _ = required_export_bytes(self.photo_dir, frozenset({"testbild.png"}))
        self.assertEqual(count, 1)
        self.assertEqual(net, 1000)

    def test_gross_includes_margin(self) -> None:
        # Cluster-Verschnitt und Verzeichnis-Overhead: der Bruttobedarf
        # muss spuerbar ueber dem Nettobedarf liegen.
        self._make("a.jpg", 1_000_000)
        _, net, gross = required_export_bytes(self.photo_dir)
        self.assertGreater(gross, net)

    def test_empty_directory_needs_nothing(self) -> None:
        count, net, gross = required_export_bytes(self.photo_dir)
        self.assertEqual((count, net, gross), (0, 0, 0))

    def test_missing_directory_is_safe(self) -> None:
        count, net, gross = required_export_bytes(Path("/gibt/es/nicht"))
        self.assertEqual((count, net, gross), (0, 0, 0))

    def test_subdirectories_are_ignored(self) -> None:
        (self.photo_dir / "unterordner").mkdir()
        count, _, _ = required_export_bytes(self.photo_dir)
        self.assertEqual(count, 0)


class FormatBytesTestCase(unittest.TestCase):
    def test_readable_units(self) -> None:
        self.assertEqual(format_bytes(0), "0.00 B")
        self.assertIn("KB", format_bytes(2048))
        self.assertIn("GB", format_bytes(5 * 1024**3))


if __name__ == "__main__":
    unittest.main()
