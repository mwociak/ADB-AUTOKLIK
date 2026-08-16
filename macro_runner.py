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

Klasa :class:`TapRepeatWorker` działa analogicznie i powtarza pojedynczy
tap w pętli (co ``delay_ms``) aż do :meth:`TapRepeatWorker.stop` - służy
w trybie "Powtarzanie" aplikacji.
"""

from __future__ import annotations

import threading

from PyQt6.QtCore import QObject, pyqtSignal

from adb_controller import ADBController

# Domyślna zwłoka między powtórzeniami tapu (gdy punkt nie ma ustawionej
# własnej wartości ``repeat_delay_ms``).
DEFAULT_REPEAT_DELAY_MS = 500


class TapRepeatWorker(QObject, threading.Thread):
    """Powtarza tap w punkcie (x, y) co ``delay_ms`` aż do :meth:`stop`.

    Używany w trybie Powtarzanie: jedno tapnięcie uruchamia pętlę
    tap -> zwłoka -> tap -> ..., wykonywaną poza wątkiem GUI (komendy
    ADB nie blokują interfejsu ani keymappera).

    Sygnały:
        tap_result(bool): wynik pojedynczego tapu (True = OK, False = błąd).
        finished(): wątek zakończył pracę (po ``stop()``).
    """

    tap_result = pyqtSignal(bool)
    finished = pyqtSignal()

    def __init__(
        self,
        adb: ADBController,
        x: int,
        y: int,
        delay_ms: int = DEFAULT_REPEAT_DELAY_MS,
    ) -> None:
        QObject.__init__(self)
        threading.Thread.__init__(self, daemon=True, name="tap-repeat")
        self._adb = adb
        self._x = int(x)
        self._y = int(y)
        self._delay_ms = max(0, int(delay_ms))
        self._stop_event = threading.Event()

    def run(self) -> None:
        try:
            while not self._stop_event.is_set():
                self.tap_result.emit(self._adb.tap(self._x, self._y))
                if self._delay_ms > 0:
                    # wait(timeout) zamiast sleep - natychmiastowe przerwanie
                    self._stop_event.wait(self._delay_ms / 1000.0)
        finally:
            self.finished.emit()

    def stop(self) -> None:
        """Przerywa pętlę powtarzania (bezpieczne z dowolnego wątku)."""
        self._stop_event.set()


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
