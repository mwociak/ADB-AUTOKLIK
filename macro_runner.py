"""MacroRunner - odtwarzanie makr (sekwencji kroków) w osobnym wątku.

Klasa :class:`MacroRunner` łączy :class:`threading.Thread` (wykonanie
kroków poza pętlą zdarzeń PyQt6, żeby delay'e i komendy ADB nie blokowały
GUI ani nasłuchu klawiszy) z :class:`QObject` (sygnały Qt do bezpiecznej
komunikacji z wątkiem GUI).

Kroki:
    tap:   ``adb.tap(x, y)``
    swipe: ``adb.swipe(x1, y1, x2, y2, duration_ms)``
    delay: pauza przez ``ms`` (przerywalna przez :meth:`stop`)

Sygnały (emitowane z wątku roboczego, odbierane w wątku GUI):
    step_started(int): indeks (0-based) kroku o wykonanie przed każdym krokiem.
    step_result(bool): wynik kroku ADB (True = OK, False = błąd); dla delay
        zawsze True.
    completed(bool): odtwarzanie zakończone; True = wszystkie kroki wykonane,
        False = przerwane przez :meth:`stop`.
"""

from __future__ import annotations

import threading

from PyQt6.QtCore import QObject, pyqtSignal

from adb_controller import ADBController


class MacroRunner(QObject, threading.Thread):
    """Odtwarza sekwencję kroków makra w osobnym wątku (patrz opis modułu)."""

    step_started = pyqtSignal(int)
    step_result = pyqtSignal(bool)
    completed = pyqtSignal(bool)

    def __init__(self, adb: ADBController, actions: list[dict]) -> None:
        QObject.__init__(self)
        threading.Thread.__init__(self, daemon=True, name="macro-runner")
        self._adb = adb
        self._actions = list(actions)
        self._stop_event = threading.Event()

    def run(self) -> None:
        finished_normally = False
        try:
            for index, action in enumerate(self._actions):
                if self._stop_event.is_set():
                    break
                self.step_started.emit(index)
                kind = action.get("type")
                if kind == "delay":
                    # wait(timeout) zamiast sleep - natychmiastowe przerwanie
                    self._stop_event.wait(int(action.get("ms", 0)) / 1000.0)
                    self.step_result.emit(True)
                    if self._stop_event.is_set():
                        break  # przerwano w trakcie pauzy
                elif kind == "tap":
                    self.step_result.emit(
                        self._adb.tap(int(action["x"]), int(action["y"]))
                    )
                elif kind == "swipe":
                    self.step_result.emit(
                        self._adb.swipe(
                            int(action["x1"]),
                            int(action["y1"]),
                            int(action["x2"]),
                            int(action["y2"]),
                            int(action.get("duration_ms", 300)),
                        )
                    )
            else:
                finished_normally = True
        finally:
            self.completed.emit(finished_normally)

    def stop(self) -> None:
        """Przerywa odtwarzanie (bezpieczne z dowolnego wątku)."""
        self._stop_event.set()
