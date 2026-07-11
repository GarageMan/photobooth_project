from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FakeCaptureService:
    fixture_dir: Path
    fixture_name: str = 'demo_capture.jpg'

    def capture(self, target_dir: Path) -> Path:
        target_dir.mkdir(parents=True, exist_ok=True)
        source = self.fixture_dir / self.fixture_name
        if not source.exists():
            raise FileNotFoundError(f'Fixture nicht gefunden: {source}')
        destination = target_dir / source.name
        shutil.copy2(source, destination)
        return destination
