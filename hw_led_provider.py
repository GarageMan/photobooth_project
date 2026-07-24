"""
hw_led_provider.py
==================
Echter Hardware-Provider für den WS2812-LED-Ring.

Hardware:
  - 35 LEDs, WS2812B
  - Ansteuerung: SPI0 (MOSI), NICHT PWM/DMA - stabile Lösung für den Pi 5
  - Signal: GPIO 10 (SPI0 MOSI / Pin 19)
  - Stromversorgung: 5V-Netzteil über Diode 1N4001 (gedrosselt auf ~4,3 V)
  - Daten-Schutzwiderstand: 220 Ohm zwischen GPIO 10 und DI des Rings

Warum SPI statt der klassischen rpi_ws281x-Bibliothek:
  rpi_ws281x nutzt PWM+DMA-Register, die sich beim Pi 5 durch den neuen
  RP1-Chip grundlegend geändert haben. Der offizielle Pi-5-Support dafür
  erfordert ein selbst kompiliertes Kernelmodul, das nach jedem Kernel-Update
  neu gebaut werden muss (siehe jgarff/rpi_ws281x Wiki). SPI0 ist dagegen ein
  vom Kernel offiziell unterstützter Standard-Treiber, der auf dem Pi 5 ohne
  Sonderbehandlung funktioniert - genau deshalb empfiehlt Adafruit diesen Weg
  ausdrücklich für alle, die keine wiederkehrende Kernelmodul-Pflege wollen.

Installation auf dem Pi:
  sudo raspi-config
  → 3 Interface Options → I4 SPI → aktivieren → Reboot

  pip3 install adafruit-blinka adafruit-circuitpython-neopixel-spi --break-system-packages

Wichtig: Für SPI-Zugriff muss der Nutzer in der Gruppe 'spi' sein, oder das
Skript läuft mit sudo (empfohlen, konsistent mit den anderen HW-Providern).

Einbindung in app.py (mit Feature-Flag):
  if config.features.enable_leds:
      from hw_led_provider import HwLedProvider
      led_provider = HwLedProvider(config)
  else:
      led_provider = None   # LED-Service läuft einfach ohne Ausgabe
"""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass, field

# neopixel_spi nur importieren, wenn wirklich auf dem Pi (mit aktiviertem SPI)
try:
    import board
    import neopixel_spi as neopixel
    _HW_AVAILABLE = True
except (ImportError, NotImplementedError):
    _HW_AVAILABLE = False
    # Dummy-Objekt, damit der Rest des Moduls syntaktisch korrekt bleibt
    class _DummyPixels:  # type: ignore
        def fill(self, color) -> None: pass
        def show(self) -> None: pass
        def __setitem__(self, index, color) -> None: pass

from led_service import LedEffect
import led_shutdown  # NEU (3.5): Sonnenuntergang-Frames fuer SHUTDOWN_SEQUENCE


# ------------------------------------------------------------------------------
# Konfigurations-Konstanten (zentral hier, nicht gestreut)
# ------------------------------------------------------------------------------
_LED_COUNT      = 35
_LED_BRIGHTNESS = 80 / 255  # neopixel_spi erwartet 0.0-1.0 (deutlich gedimmt, vorher 200/255)

# Rotierendes Countdown-Blockmuster: 5 Bloecke aus je 5 LEDs, dazwischen je
# 2 LEDs Luecke -> 5*(5+2) = 35 LEDs, geht exakt auf (35 LEDs am Ring).
_BLOCK_ON  = 5
_BLOCK_GAP = 2
_BLOCK_PERIOD = _BLOCK_ON + _BLOCK_GAP  # 7

# Hauptmenue-Sync-Blinken: identischer 10s-Zyklus wie die Taster-LED in
# app_with_hw.py::_sync_button_led (3x kurz an, dann Pause) - beide lesen
# unabhaengig voneinander dieselbe time.monotonic()-Uhr mit derselben Formel,
# dadurch laufen LED-Ring und Taster-LED garantiert im Gleichtakt, ohne dass
# Parameter zwischen den Threads uebergeben werden muessen.
_SYNC_CYCLE_SEC = 10.0
_SYNC_FLASH_WINDOW = 0.75
_SYNC_FLASH_PERIOD = 0.15

# Sternenhimmel: Wahrscheinlichkeit pro gerendertem Frame, dass (sofern
# gerade keiner unterwegs ist) ein neuer Komet ueber einen Teil des Rings
# huscht, sowie Laenge seines auslaufenden Schweifs in LEDs.
_COMET_CHANCE = 0.01
_COMET_TAIL = 6


def _sync_flash_active(now: float) -> bool:
    cycle = now % _SYNC_CYCLE_SEC
    return cycle < _SYNC_FLASH_WINDOW and int(cycle / _SYNC_FLASH_PERIOD) % 2 == 0


@dataclass
class HwLedProvider:
    """
    Einziger physischer Schreibpfad zum LED-Ring.

    Läuft in einem eigenen Daemon-Thread, damit der Haupt-Thread
    (Pygame / State Machine) nie blockiert wird.

    Verwendung:
        provider = HwLedProvider()
        provider.start()
        provider.set_effect(LedEffect.MAIN_MENU)
        ...
        provider.stop()
    """

    _pixels: object = field(init=False, repr=False)
    _thread: threading.Thread = field(init=False, repr=False)
    _current_effect: LedEffect = field(default=LedEffect.OFF, init=False)
    _effect_since: float = field(default=0.0, init=False)  # NEU (3.5): Startzeit des aktuellen Effekts
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _running: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not _HW_AVAILABLE:
            print("[HwLedProvider] adafruit-blinka/neopixel_spi nicht verfügbar - LED-Ring deaktiviert.")
            self._pixels = _DummyPixels()
            return
        spi = board.SPI()
        self._pixels = neopixel.NeoPixel_SPI(
            spi,
            _LED_COUNT,
            pixel_order=neopixel.GRB,
            brightness=_LED_BRIGHTNESS,
            auto_write=False,
        )

    # -- Public API ------------------------------------------------------------

    def start(self) -> None:
        """Startet den Hintergrund-Thread für LED-Effekte."""
        if not _HW_AVAILABLE:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._worker,
            name="hw_led_worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stoppt den Thread und schaltet alle LEDs aus."""
        self._running = False
        if _HW_AVAILABLE:
            self._all_off()

    def set_effect(self, effect: LedEffect) -> None:
        """Thread-sicher den gewünschten Effekt setzen."""
        with self._lock:
            if effect != self._current_effect:  # NEU (3.5): Startzeit merken
                self._effect_since = time.monotonic()
            self._current_effect = effect

    def get_effect(self) -> LedEffect:
        with self._lock:
            return self._current_effect

    # -- Worker ----------------------------------------------------------------

    def _worker(self) -> None:
        """
        Haupt-Loop des LED-Threads.
        Liest self._current_effect und rendert den passenden Effekt.
        Keine direkten Hardware-Aufrufe außerhalb dieses Threads!
        """
        breath_step: float = 0.0
        chase_pos: int = 0
        wave_phase: float = 0.0
        sky = self._init_starfield()

        while self._running:
            with self._lock:
                effect = self._current_effect
                effect_since = self._effect_since  # NEU (3.5)

            now = time.monotonic()

            if effect == LedEffect.OFF:
                self._all_off()
                time.sleep(0.1)

            elif effect == LedEffect.BOOT:
                # Startsequenz bis das Hauptmenue erscheint: ruhig
                # rotierender Komet in Blau-Weiss ("System faehrt hoch").
                self._render_boot_comet(chase_pos)
                chase_pos = (chase_pos + 1) % _LED_COUNT
                time.sleep(0.03)

            elif effect == LedEffect.PHOTO_INTRO:
                # Zwei gegenlaeufig rotierende Kometen in Tuerkis - deutlich
                # lebhafter/energiegeladener als das Amber-Atmen, um auf den
                # bevorstehenden Fotomoment einzustimmen.
                self._render_photo_intro(chase_pos)
                chase_pos = (chase_pos + 1) % _LED_COUNT
                time.sleep(0.03)

            elif effect == LedEffect.INSTRUCTIONS_WAVE:
                # Sanfte Sinus-Lichtwelle in Violett-Blau, kein Blinken -
                # angenehm nebenbei zu beobachten, waehrend der Gast liest.
                wave_phase = (wave_phase + 4.0) % 360.0
                self._render_wave(wave_phase)
                time.sleep(0.03)

            elif effect == LedEffect.GALLERY_GRID_BREATHE:
                # Wie MAIN_MENU-Atmen, aber in Blau-Tuerkis statt Amber.
                breath_step = (breath_step + 3.0) % 360.0
                brightness = int((math.sin(math.radians(breath_step)) + 1.0) * 90) + 20
                self._fill((0, int(brightness * 0.55), brightness))
                time.sleep(0.03)

            elif effect == LedEffect.MAIN_MENU:
                if _sync_flash_active(now):
                    # Synchron zur Taster-LED: kurzer, kraeftiger Gelb-Blitz
                    self._fill((255, 200, 0))
                else:
                    # Weiches amber "Atmen"
                    breath_step = (breath_step + 3.0) % 360.0
                    brightness = int((math.sin(math.radians(breath_step)) + 1.0) * 90) + 20
                    self._fill((brightness, int(brightness * 0.6), 0))
                time.sleep(0.03)

            elif effect == LedEffect.PREVIEW:
                # Dauerhaft weiß (Orientierung für Gast)
                self._fill((180, 180, 180))
                time.sleep(0.1)

            elif effect in (LedEffect.COUNTDOWN_5, LedEffect.COUNTDOWN_4, LedEffect.COUNTDOWN_3, LedEffect.COUNTDOWN_2):
                color = {
                    LedEffect.COUNTDOWN_5: (130, 0, 190),    # Violett (kuehl, "noch Zeit")
                    LedEffect.COUNTDOWN_4: (255, 0, 0),      # Rot
                    LedEffect.COUNTDOWN_3: (255, 110, 0),    # Orange
                    LedEffect.COUNTDOWN_2: (255, 220, 0),    # Gelb
                }[effect]
                self._fill_rotating_blocks(color, chase_pos)
                chase_pos = (chase_pos + 1) % _LED_COUNT
                time.sleep(0.05)

            elif effect == LedEffect.COUNTDOWN_1_FLASH:
                # Schnelles, kraeftiges Weiss-Blitzen (~8 Hz)
                on = int(now * 8) % 2 == 0
                self._fill((255, 255, 255) if on else (0, 0, 0))
                time.sleep(0.02)

            elif effect == LedEffect.PRE_TRIGGER_DARK:
                # Kurz vor dem eigentlichen Ausloeseimpuls: dunkel, keine
                # Reflexionen in Brillen waehrend der Aufnahme.
                self._all_off()
                time.sleep(0.05)

            elif effect == LedEffect.CAPTURE_PROCESSING:
                # Voller, satter gruener Kreis waehrend des gphoto2-Downloads
                self._fill((0, 200, 0))
                time.sleep(0.1)

            elif effect == LedEffect.REVIEW_BREATHE:
                # Gelbes Atmen, solange das Foto zur Begutachtung angezeigt wird
                breath_step = (breath_step + 3.0) % 360.0
                brightness = int((math.sin(math.radians(breath_step)) + 1.0) * 90) + 20
                self._fill((brightness, brightness, 0))
                time.sleep(0.03)

            elif effect == LedEffect.DELETE_CONFIRM:
                # Rotes Blinken – Warnung vor Löschung (2 Hz)
                on = int(now * 4) % 2 == 0
                self._fill((255, 0, 0) if on else (0, 0, 0))
                time.sleep(0.05)

            elif effect == LedEffect.QR:
                # Orange Blinken – QR-Code wird angezeigt (2 Hz)
                on = int(now * 4) % 2 == 0
                self._fill((255, 100, 0) if on else (0, 0, 0))
                time.sleep(0.05)

            elif effect == LedEffect.GALLERY_STARFIELD:
                self._render_starfield(sky)
                time.sleep(0.35)

            elif effect == LedEffect.ERROR:
                # Schnelles rotes Blinken (5 Hz)
                on = int(now * 10) % 2 == 0
                self._fill((255, 0, 0) if on else (30, 0, 0))
                time.sleep(0.03)

            elif effect == LedEffect.PIN_ERROR:
                # NEU (3.5): falsche PIN - schnelles Rot/Gelb-Wechselblinken
                # (~6 Hz, spiegelt config.shutdown.error_*_rgb / _flash_hz).
                phase = int(now * 6.0) % 2
                self._fill((200, 0, 0) if phase == 0 else (220, 160, 0))
                time.sleep(0.02)

            elif effect == LedEffect.SHUTDOWN_SEQUENCE:
                # NEU (3.5): Sonnenuntergang aus led_shutdown.frame_colors(t),
                # t = Zeit seit Effektbeginn. Nach TOTAL_SECONDS alles aus; die
                # App loest zeitgleich das eigentliche poweroff aus.
                t = now - effect_since
                for i, color in enumerate(led_shutdown.frame_colors(t)):
                    self._pixels[i] = color
                self._pixels.show()
                time.sleep(0.02)

            else:
                time.sleep(0.1)

    # -- Hilfsmethoden (nur intern, nur dieser Thread schreibt) ---------------

    def _fill(self, color: tuple[int, int, int]) -> None:
        """Alle LEDs auf dieselbe Farbe setzen und show() aufrufen."""
        self._pixels.fill(color)
        self._pixels.show()

    def _all_off(self) -> None:
        self._fill((0, 0, 0))

    def _fill_rotating_blocks(self, color: tuple[int, int, int], chase_pos: int) -> None:
        """
        5 Bloecke aus je 5 LEDs, dazwischen je 2 LEDs Luecke, rotierend.
        35 LEDs / (5 an + 2 aus) = exakt 5 volle Wiederholungen.
        """
        for i in range(_LED_COUNT):
            offset = (i + chase_pos) % _BLOCK_PERIOD
            self._pixels[i] = color if offset < _BLOCK_ON else (0, 0, 0)
        self._pixels.show()

    def _init_starfield(self) -> dict[str, object]:
        """
        Erzeugt einmalig ein festes "Sternbild": eine Auswahl von LEDs wird
        dauerhaft zu Sternen, alle uebrigen bleiben schwarz. Jeder Stern hat
        eine eigene Grundhelligkeit und eine individuelle Funkel-Neigung -
        manche Sterne funkeln fast nie, andere haeufiger. So wie am echten
        Nachthimmel: immer dieselben Sterne, nicht staendig neue Positionen.
        Zusaetzlich wird hier der (anfangs inaktive) Kometen-Zustand mit
        angelegt, siehe `_render_starfield()`.
        """
        rng = random.Random(42)  # fester Seed -> reproduzierbares Sternbild
        star_count = 13
        positions = rng.sample(range(_LED_COUNT), star_count)
        stars: dict[int, dict] = {}
        for idx in positions:
            stars[idx] = {
                "base": rng.uniform(0.30, 0.70),            # Grundhelligkeit 0..1
                "flicker_chance": rng.uniform(0.01, 0.10),  # Funkel-Neigung pro Frame
                "twinkle_frames": 0,                        # verbleibende Funkel-Frames
                "twinkle_level": 0.0,                       # Zusatzhelligkeit waehrend des Funkelns
                "hue": "white",                              # Farbe waehrend des Funkelns
            }
        return {"stars": stars, "comet": None}

    def _render_starfield(self, sky: dict[str, object]) -> None:
        """
        Rendert das statische Sternbild aus `_init_starfield()`. Die meisten
        Sterne stehen ruhig auf ihrer Grundhelligkeit (schwach weiss); ab und
        zu "entscheidet" sich ein Stern (abhaengig von seiner individuellen
        `flicker_chance`) fuer ein kurzes Funkeln - dabei wird er kurz
        heller und wechselt manchmal von Weiss zu Gelb oder Orange, bevor er
        wieder auf seine ruhige weisse Grundhelligkeit zurueckfaellt.

        Zusaetzlich huscht ab und zu (siehe `_COMET_CHANCE`) ein Komet ueber
        einen Teil des Rings - ein hell-blauweisser Kopf mit auslaufendem
        Schweif, der ueber die statischen Sterne hinweg gerendert wird.
        """
        stars: dict[int, dict] = sky["stars"]  # type: ignore[assignment]

        colors: list[tuple[int, int, int]] = [(0, 0, 0)] * _LED_COUNT
        for i in range(_LED_COUNT):
            star = stars.get(i)
            if star is None:
                continue

            if star["twinkle_frames"] > 0:
                star["twinkle_frames"] -= 1
                level = min(1.0, star["base"] + star["twinkle_level"])
                hue = star["hue"]
            else:
                level = star["base"]
                hue = "white"
                if random.random() < star["flicker_chance"]:
                    star["twinkle_frames"] = random.randint(2, 5)
                    star["twinkle_level"] = random.uniform(0.15, 0.45)
                    # nur ein Teil der Funkel-Ereignisse faerbt sich um,
                    # der Rest bleibt beim ruhigen Weiss
                    if random.random() < 0.35:
                        star["hue"] = "yellow" if random.random() < 0.6 else "orange"
                    else:
                        star["hue"] = "white"

            colors[i] = self._star_color(hue, level)

        # -- Kometen-Overlay -----------------------------------------------
        comet = sky.get("comet")
        if comet is None and random.random() < _COMET_CHANCE:
            comet = self._start_comet()
            sky["comet"] = comet
        if comet is not None:
            self._blend_comet(colors, comet)
            finished = self._advance_comet(comet)
            if finished:
                sky["comet"] = None

        for i in range(_LED_COUNT):
            self._pixels[i] = colors[i]
        self._pixels.show()

    @staticmethod
    def _start_comet() -> dict:
        """Startet einen neuen Kometen an zufaelliger Position/Richtung, der
        einen zufaelligen Teilbogen des Rings ueberstreicht (nicht die volle
        Runde) - dadurch wirkt es wie ein Sternschnuppen-Huschen, nicht wie
        ein rotierender Effekt."""
        return {
            "pos": random.uniform(0, _LED_COUNT),
            "direction": random.choice((1, -1)),
            "speed": random.uniform(1.2, 2.2),   # LEDs pro Frame
            "traveled": 0.0,
            "length": random.uniform(10, 20),    # ueberstrichene LEDs bis er verglueht
        }

    @staticmethod
    def _advance_comet(comet: dict) -> bool:
        """Bewegt den Kometen einen Frame weiter. Gibt True zurueck, sobald
        er seine Strecke zurueckgelegt hat und verglueht ist."""
        comet["pos"] += comet["speed"] * comet["direction"]
        comet["traveled"] += comet["speed"]
        return comet["traveled"] >= comet["length"]

    @staticmethod
    def _blend_comet(colors: list[tuple[int, int, int]], comet: dict) -> None:
        """Zeichnet Kopf + auslaufenden Schweif des Kometen in die
        vorbereitete Sternbild-Farbliste (heller Kanal gewinnt, damit der
        Komet sauber ueber die Sterne hinweg leuchtet statt zu addieren)."""
        head = comet["pos"]
        direction = comet["direction"]
        for t in range(_COMET_TAIL):
            idx = int(round(head - direction * t)) % _LED_COUNT
            level = (1.0 - t / _COMET_TAIL) ** 1.3
            r, g, b = int(230 * level), int(230 * level), int(255 * level)
            cr, cg, cb = colors[idx]
            colors[idx] = (max(cr, r), max(cg, g), max(cb, b))

    @staticmethod
    def _star_color(hue: str, level: float) -> tuple[int, int, int]:
        level = max(0.0, min(1.0, level))
        v = int(255 * level)
        if hue == "yellow":
            return (v, int(v * 0.85), int(v * 0.30))
        if hue == "orange":
            return (v, int(v * 0.45), 0)
        # leicht kuehles Weiss, wie echte Sterne
        return (v, v, v)

    def _render_boot_comet(self, chase_pos: int) -> None:
        """
        BOOT: ein weich auslaufender Komet (helle Spitze, abklingender
        Schweif) rotiert in kuehlem Blau-Weiss einmal ums Rund - soll
        "System faehrt hoch" signalisieren, bis das Hauptmenue erscheint.
        """
        tail = 10
        for i in range(_LED_COUNT):
            dist = (chase_pos - i) % _LED_COUNT
            if dist < tail:
                level = (1.0 - dist / tail) ** 1.5
                self._pixels[i] = (int(40 * level), int(90 * level), int(255 * level))
            else:
                self._pixels[i] = (0, 0, 8)  # sehr dunkles Grundblau
        self._pixels.show()

    def _render_photo_intro(self, chase_pos: int) -> None:
        """
        PHOTO_INTRO: zwei gegenlaeufig rotierende, weich auslaufende
        Kometen in Tuerkis - deutlich lebhafter als das ruhige Amber-Atmen
        (MAIN_MENU) und klar unterscheidbar von den Countdown-Farben.
        """
        tail = 8
        tip_a = chase_pos % _LED_COUNT
        tip_b = (-chase_pos) % _LED_COUNT
        for i in range(_LED_COUNT):
            dist_a = (tip_a - i) % _LED_COUNT
            dist_b = (i - tip_b) % _LED_COUNT
            level_a = (1.0 - dist_a / tail) ** 1.5 if dist_a < tail else 0.0
            level_b = (1.0 - dist_b / tail) ** 1.5 if dist_b < tail else 0.0
            level = max(level_a, level_b)
            self._pixels[i] = (0, int(210 * level), int(190 * level))
        self._pixels.show()

    def _render_wave(self, phase: float) -> None:
        """
        INSTRUCTIONS: sanfte Sinus-Lichtwelle in Violett-Blau, laeuft
        gleichmaessig einmal rundherum - bewusst kein Blinken/Blitzen,
        damit es beim Lesen der Anleitung nicht ablenkt.
        """
        for i in range(_LED_COUNT):
            angle = (i / _LED_COUNT) * 360.0 + phase
            level = (math.sin(math.radians(angle)) + 1.0) / 2.0  # 0..1
            level = level ** 2  # etwas kontrastreicher
            self._pixels[i] = (int(90 * level), int(35 * level), int(200 * level))
        self._pixels.show()


# ------------------------------------------------------------------------------
# Manuelle Schnell-Tests (direkt auf dem Pi ausführen)
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    EFFECTS = {
        "boot":             LedEffect.BOOT,
        "main_menu":        LedEffect.MAIN_MENU,
        "photo_intro":      LedEffect.PHOTO_INTRO,
        "instructions":     LedEffect.INSTRUCTIONS_WAVE,
        "preview":          LedEffect.PREVIEW,
        "countdown_5":      LedEffect.COUNTDOWN_5,
        "countdown_4":      LedEffect.COUNTDOWN_4,
        "countdown_3":      LedEffect.COUNTDOWN_3,
        "countdown_2":      LedEffect.COUNTDOWN_2,
        "countdown_1":      LedEffect.COUNTDOWN_1_FLASH,
        "pre_trigger_dark": LedEffect.PRE_TRIGGER_DARK,
        "processing":       LedEffect.CAPTURE_PROCESSING,
        "review":           LedEffect.REVIEW_BREATHE,
        "delete_confirm":   LedEffect.DELETE_CONFIRM,
        "qr":               LedEffect.QR,
        "gallery_grid":     LedEffect.GALLERY_GRID_BREATHE,
        "starfield":        LedEffect.GALLERY_STARFIELD,
        "error":            LedEffect.ERROR,
        "pin_error":        LedEffect.PIN_ERROR,         # NEU (3.5)
        "shutdown_seq":     LedEffect.SHUTDOWN_SEQUENCE,  # NEU (3.5)
        "off":              LedEffect.OFF,
    }

    if len(sys.argv) < 2 or sys.argv[1] not in EFFECTS:
        print("Verwendung: sudo python3 hw_led_provider.py <effekt>")
        print("Verfügbare Effekte:", ", ".join(EFFECTS))
        sys.exit(1)

    provider = HwLedProvider()
    provider.start()
    provider.set_effect(EFFECTS[sys.argv[1]])

    if sys.argv[1] == "off":
        # Einmaliger Befehl - kurz wirken lassen, dann selbständig beenden
        time.sleep(0.3)
        provider.stop()
        print("LEDs aus.")
    else:
        print(f"Effekt '{sys.argv[1]}' läuft - STRG+C zum Beenden")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            provider.stop()
            print("LEDs aus. Tschüss!")
