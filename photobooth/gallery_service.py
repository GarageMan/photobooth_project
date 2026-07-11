from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GalleryService:
    photo_dir: Path
    max_thumbnail_cache_items: int = 200
    max_fullscreen_cache_items: int = 12
    thumbnail_cache: OrderedDict[str, object] = field(default_factory=OrderedDict)
    fullscreen_cache: OrderedDict[str, object] = field(default_factory=OrderedDict)

    def list_photos(self) -> list[str]:
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        files = [p for p in self.photo_dir.iterdir() if p.is_file() and p.suffix.lower() in {'.jpg', '.jpeg', '.png'}]
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
