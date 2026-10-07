"""Move sounds.

If QtMultimedia is unavailable or the sound server is silent, the program
simply runs without sound - it must not crash because of this.
"""

from __future__ import annotations

from PySide6.QtCore import QUrl

from . import theme

NAMES = ("move", "capture", "check")


class Sounds:
    def __init__(self, enabled: bool = True, volume: float = 0.35) -> None:
        self.enabled = enabled
        self._effects: dict[str, object] = {}
        self.available = False

        try:
            from PySide6.QtMultimedia import QSoundEffect
        except ImportError:
            return

        for name in NAMES:
            path = theme.ASSETS / f"{name}.wav"
            if not path.is_file():
                continue
            effect = QSoundEffect()
            effect.setSource(QUrl.fromLocalFile(str(path)))
            effect.setVolume(volume)
            self._effects[name] = effect

        self.available = bool(self._effects)

    def play(self, name: str) -> None:
        if not self.enabled:
            return
        effect = self._effects.get(name)
        if effect is not None:
            effect.play()

    def set_volume(self, volume: float) -> None:
        for effect in self._effects.values():
            effect.setVolume(volume)
