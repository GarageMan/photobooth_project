"""
test_audio_standalone.py
====================================================
Minimaler Sound-Test fuer den Pi Zero (Audio Amp SHIM) - OHNE dass die
komplette Fotobox-App (Kamera, GPIO-Taster, LED-Ring) vorhanden sein
muss. Spielt der Reihe nach jedes SoundEvent einmal ab.

Benoetigte Dateien auf dem Pi Zero (per SFTP hochladen), alle im selben
Verzeichnis wie dieses Skript:
  audio_service.py
  hw_audio_provider.py
  assets/sounds/*.wav, *.ogg

Ausfuehren:
  python3 test_audio_standalone.py
"""

from __future__ import annotations

import time
from pathlib import Path

from audio_service import SoundEvent
from hw_audio_provider import HwAudioProvider

SOUNDS_DIR = Path(__file__).resolve().parent / "assets" / "sounds"

# Events, die ueber pygame.mixer.music laufen (siehe hw_audio_provider.py
# _MUSIC_EVENTS) - MAIN_MENU_LOOP wuerde sonst endlos weiterlaufen und den
# Test blockieren, daher hier bewusst nach kurzer Anspielzeit gestoppt.
_LOOPING_EVENTS = {SoundEvent.MAIN_MENU_LOOP}


def main() -> None:
    print(f"Sounds-Verzeichnis: {SOUNDS_DIR}")
    if not SOUNDS_DIR.exists():
        print(f"[WARNUNG] Verzeichnis existiert nicht: {SOUNDS_DIR}")

    provider = HwAudioProvider(sounds_dir=SOUNDS_DIR, master_volume=0.8)

    for event in SoundEvent:
        print(f"\n--- {event.name} ---")
        provider.play(event)
        time.sleep(2.5)
        if event in _LOOPING_EVENTS:
            provider.stop_music()

    provider.stop_music()
    print("\nTest abgeschlossen.")


if __name__ == "__main__":
    main()