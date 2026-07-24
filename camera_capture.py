from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class CaptureProvider(Protocol):
    def capture(self, target_dir: Path) -> Path: ...


@dataclass
class CaptureResult:
    ok: bool
    photo_path: Path | None = None
    error_message: str | None = None


@dataclass
class CameraCaptureService:
    provider: CaptureProvider
    target_dir: Path

    def capture_photo(self) -> CaptureResult:
        self.target_dir.mkdir(parents=True, exist_ok=True)
        try:
            photo_path = self.provider.capture(self.target_dir)
        except Exception as exc:
            return CaptureResult(ok=False, error_message=str(exc))
        return CaptureResult(ok=True, photo_path=photo_path)
