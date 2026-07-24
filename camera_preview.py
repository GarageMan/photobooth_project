from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable, Any


@runtime_checkable
class PreviewProvider(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def is_running(self) -> bool: ...
    def get_frame(self) -> Any | None: ...


@dataclass
class CameraPreviewService:
    provider: PreviewProvider

    def start(self) -> None:
        if not self.provider.is_running():
            self.provider.start()

    def stop(self) -> None:
        if self.provider.is_running():
            self.provider.stop()

    def is_running(self) -> bool:
        return self.provider.is_running()

    def get_frame(self) -> Any | None:
        if not self.provider.is_running():
            return None
        return self.provider.get_frame()
