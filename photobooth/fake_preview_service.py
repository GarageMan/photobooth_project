from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pygame


@dataclass
class FakePreviewService:
    width: int = 1080
    height: int = 1920
    running: bool = False
    label: str = 'Fake Preview'

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def is_running(self) -> bool:
        return self.running

    def get_frame(self) -> pygame.Surface:
        surface = pygame.Surface((self.width, self.height))
        surface.fill((35, 35, 55))
        font = pygame.font.Font(None, 72)
        text = font.render(self.label, True, (255, 255, 255))
        rect = text.get_rect(center=(self.width // 2, self.height // 2))
        surface.blit(text, rect)
        return surface
