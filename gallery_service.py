from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path


# Dateinamen, die zwar im Foto-Verzeichnis liegen duerfen, aber nirgends in
# der App angezeigt werden: weder im Galerie-Grid noch in der Vollbild-
# Ansicht noch in der Fliegenden Galerie (Attract-Mode).
#
# Gedacht fuer Diagnose-/Testbilder, die ueber nginx unter
# http://192.168.0.100/fotos/... erreichbar bleiben sollen (z.B. um bei
# einem Event schnell zu pruefen, ob der Foto-Download ueberhaupt
# funktioniert), aber den Gaesten nicht auf dem Display begegnen duerfen.
#
# Der Vergleich in list_photos() erfolgt case-insensitiv - Eintraege hier
# daher konsequent klein schreiben.
DEFAULT_EXCLUDED_FILENAMES: frozenset[str] = frozenset({'testbild.png'})


@dataclass
class GalleryService:
    photo_dir: Path
    max_thumbnail_cache_items: int = 200
    max_fullscreen_cache_items: int = 12
    # Wird von app_with_hw.py aus config.gallery.excluded_filenames gesetzt.
    # Der Default hier ist bewusst identisch, damit auch direkte
    # Instanziierungen (Tests, Hilfsskripte) das Testbild ausblenden.
    excluded_filenames: frozenset[str] = DEFAULT_EXCLUDED_FILENAMES
    thumbnail_cache: OrderedDict[str, object] = field(default_factory=OrderedDict)
    fullscreen_cache: OrderedDict[str, object] = field(default_factory=OrderedDict)

    def list_photos(self) -> list[str]:
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        excluded = {name.lower() for name in self.excluded_filenames}
        files = [
            p for p in self.photo_dir.iterdir()
            if p.is_file()
            and p.suffix.lower() in {'.jpg', '.jpeg', '.png'}
            and p.name.lower() not in excluded
        ]
        return [str(p) for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)]

    def delete_photo(self, photo_path: str) -> bool:
        path = Path(photo_path)
        if not path.exists():
            return False
        path.unlink()
        self.thumbnail_cache.pop(photo_path, None)
        self.fullscreen_cache.pop(photo_path, None)
        return True

    def remember_thumbnail(self, photo_path: str, surface: object) -> None:
        self._remember(self.thumbnail_cache, photo_path, surface, self.max_thumbnail_cache_items)

    def remember_fullscreen(self, photo_path: str, surface: object) -> None:
        self._remember(self.fullscreen_cache, photo_path, surface, self.max_fullscreen_cache_items)

    def get_thumbnail(self, photo_path: str) -> object | None:
        return self.thumbnail_cache.get(photo_path)

    def get_fullscreen(self, photo_path: str) -> object | None:
        return self.fullscreen_cache.get(photo_path)

    def clear_caches(self) -> None:
        self.thumbnail_cache.clear()
        self.fullscreen_cache.clear()

    @staticmethod
    def _remember(cache: OrderedDict[str, object], key: str, value: object, limit: int) -> None:
        if key in cache:
            cache.move_to_end(key)
        cache[key] = value
        while len(cache) > limit:
            cache.popitem(last=False)