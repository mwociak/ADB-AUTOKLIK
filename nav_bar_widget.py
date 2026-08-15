"""NavigationBar - pasek nawigacji Androida + kontrola ekranu.

Pasek działa niezależnie od tego, czy na telefonie włączono nawigację
gestami - wysyła systemowe klawisze przez ADB (``input keyevent``):

    ◀ Wstecz      - keyevent 4   (KEYCODE_BACK)
    ◯ Home        - keyevent 3   (KEYCODE_HOME)
    ▢ Ostatnie    - keyevent 187 (KEYCODE_APP_SWITCH)
    🔒 Wygaszenie - keyevent 223 (KEYCODE_SLEEP)
    🔓 Wybudzenie - keyevent 224 (KEYCODE_WAKEUP) + swipe w górę
    🔄 Poziom     - wymusza tryb poziomy (settings: accelerometer_rotation=0,
                   user_rotation=1) - np. dla gier widocznych błędnie w pionie
    ↕️ Pion (Auto) - powrót do pionu / auto-rotacji (accelerometer_rotation=1,
                   user_rotation=0)

Kliknięcia nie blokują pętli zdarzeń PyQt6: :class:`NavigationBar` emituje
``action_triggered(str)``, a wykonanie komendy ADB odbywa się w osobnym
wątku (:class:`NavigationWorker`), który zwraca wynik przez sygnał
``finished(bool, str)`` - odbiorca (main_window) aktualizuje wskaźnik LED
ADB (zielony = OK, czerwony = błąd).
"""

from __future__ import annotations

import sys
import threading

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from adb_controller import ADBController

# Akcje paska: (etykieta przycisku, opis/sygnał akcji)
_NAV_ACTIONS: dict[str, tuple[str, str]] = {
    "back": ("◀ Wstecz", "Przycisk Wstecz (keyevent 4)"),
    "home": ("◯ Home", "Przycisk Home (keyevent 3)"),
    "recents": ("▢ Ostatnie", "Przegląd ostatnich aplikacji (keyevent 187)"),
    "screen_off": ("🔒 Wygaszenie", "Wyłącza ekran telefonu (keyevent 223)"),
    "screen_on": ("🔓 Wybudzenie", "Budzi ekran i omija blokadę (keyevent 224 + swipe)"),
    "rotate_landscape": (
        "🔄 Poziom",
        "Wymusza tryb poziomy (landscape): auto-rotacja OFF, user_rotation=1",
    ),
    "rotate_portrait": (
        "↕️ Pion (Auto)",
        "Powrót do pionu / auto-rotacji: accelerometer_rotation=1, user_rotation=0",
    ),
}


class NavigationWorker(QObject, threading.Thread):
    """Wykonuje pojedynczą akcję nawigacyjną w osobnym wątku (daemon).

    Sygnały:
        finished(bool, str): wynik komendy (True = OK) + czytelny komunikat
            (np. nazwa akcji i ewentualny błąd ADB).
    """

    finished = pyqtSignal(bool, str)

    def __init__(self, adb: ADBController, action: str) -> None:
        QObject.__init__(self)
        threading.Thread.__init__(self, daemon=True, name=f"nav-{action}")
        self._adb = adb
        self._action = action

    def run(self) -> None:
        action = self._action
        try:
            if action == "back":
                ok = self._adb.press_back()
            elif action == "home":
                ok = self._adb.press_home()
            elif action == "recents":
                ok = self._adb.press_recents()
            elif action == "screen_off":
                ok = self._adb.turn_off_screen()
            elif action == "screen_on":
                ok = self._adb.wake_up_screen()
            elif action == "rotate_landscape":
                ok = self._adb.force_landscape()
            elif action == "rotate_portrait":
                ok = self._adb.enable_auto_rotate()
            else:
                ok = False
        except Exception as exc:  # noqa: BLE001 - komenda nie może wywalać wątku
            ok = False
            print(f"[NavigationWorker] Błąd akcji '{action}': {exc}", file=sys.stderr)
        label = _NAV_ACTIONS.get(action, (action, ""))[0]
        message = (
            f"{label}: OK"
            if ok
            else f"{label}: komenda nieudana (urządzenie niedostępne?)"
        )
        self.finished.emit(ok, message)


class NavigationBar(QWidget):
    """Pasek z przyciskami nawigacji Androida pod podglądem ekranu.

    Sygnały:
        action_triggered(str): nazwa akcji (klucz z :data:`_NAV_ACTIONS`).
    """

    action_triggered = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: dict[str, QPushButton] = {}
        self._build_ui()
        # Bez podłączonego urządzenia pasek jest nieaktywny.
        self.set_connected(False)

    def _build_ui(self) -> None:
        box = QGroupBox("Nawigacja telefonu")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(6)

        for action, (label, tooltip) in _NAV_ACTIONS.items():
            button = QPushButton(label)
            button.setToolTip(tooltip)
            button.clicked.connect(
                lambda _checked=False, a=action: self.action_triggered.emit(a)
            )
            self._buttons[action] = button
            layout.addWidget(button)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)

    def set_connected(self, connected: bool) -> None:
        """Włącza/wyłącza przyciski paska (brak urządzenia = wyłączone)."""
        for button in self._buttons.values():
            button.setEnabled(connected)
