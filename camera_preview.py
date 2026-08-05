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

    def run_with_camera(self, fn):
        """NEU (Kamera-Menue 2.0): Passthrough zu provider.run_with_camera()
        (nur HwGphoto2PreviewProvider hat das - bewusst nicht Teil des
        PreviewProvider-Protocols, damit FakePreviewService & Co. nicht
        angepasst werden muessen). Ohne laufende Vorschau oder bei einem
        Provider ohne diese Faehigkeit (z.B. FakePreviewService) gibt es
        (False, None) zurueck - der Aufrufer faellt dann auf eine eigene
        Kamera-Sitzung zurueck (siehe hw_camera_settings_provider.py)."""
        if not self.provider.is_running():
            return False, None
        method = getattr(self.provider, "run_with_camera", None)
        if method is None:
            return False, None
        return method(fn)
