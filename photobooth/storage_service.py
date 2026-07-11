from __future__ import annotations

import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class StorageService:
    photo_dir: Path
    web_dir: Path

    def ensure_directories(self) -> None:
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        self.web_dir.mkdir(parents=True, exist_ok=True)

    def build_photo_filename(self, suffix: str = '.jpg') -> str:
        token = secrets.token_hex(12)
        return f'photo_{token}{suffix}'

    def export_to_web(self, photo_path: str | Path, target_name: str | None = None) -> Path:
        self.ensure_directories()
        source = Path(photo_path)
        name = target_name or source.name
        destination = self.web_dir / name
        shutil.copy2(source, destination)
        return destination

    def delete_local_photo(self, photo_path: str | Path) -> bool:
        path = Path(photo_path)
        if not path.exists():
            return False
        path.unlink()
        return True

    def delete_web_photo(self, file_name: str) -> bool:
        path = self.web_dir / file_name
        if not path.exists():
            return False
        path.unlink()
        return True
