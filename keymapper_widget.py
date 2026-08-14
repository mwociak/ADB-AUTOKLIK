"""KeymapperWidget - włącznik keymappera ze statusem nasłuchiwania.

Moduł zawiera:
- :class:`KeymapperEngine` - globalny nasłuch klawiszy przez ``pynput``
  w osobnym wątku (komunikacja z GUI wyłącznie przez sygnały Qt),
- :class:`KeymapperWidget` - widżet GUI: przełącznik "Keymapper Aktywny"
  + etykieta stanu nasłuchiwania.

Nasłuch działa, gdy włączony jest keymapper LUB aktywny jest dowolny tryb
dodawania akcji (przechwytywanie klawisza dla nowej akcji) - o tym decyduje
:meth:`KeymapperWidget.set_add_mode`.
"""

from __future__ import annotations

import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QGroupBox, QLabel, QVBoxLayout, QWidget

# Minimalny odstęp między akcjami tego samego klawisza [s] - zabezpieczenie
# przed "spamowaniem" (edge-trigger + debounce przy szybkim ponownym wciśnięciu).
TAP_DEBOUNCE_S = 0.05


class KeymapperEngine(QObject):
    """Globalny nasłuch klawiszy przez pynput w osobnym wątku.

    Sygnały (emituje je wątek pynput, odbierają sloty w wątku GUI):
        key_pressed(str): naciśnięto klawisz - slot wywołuje przypisaną
            akcję (tap, swipe lub makro) na aktywnym urządzeniu.
        key_captured(str): przechwycono klawisz w trybie dodawania punktu.
        error(str): nie udało się uruchomić nasłuchu.
    """

    key_pressed = pyqtSignal(str)
    key_captured = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._listener = None  # pynput.keyboard.Listener | None
        self._pressed: set[str] = set()  # aktualnie wciśnięte klawisze
        self._last_tap: dict[str, float] = {}  # debounce per klawisz
        self._lock = threading.Lock()
        self._add_mode = False

    # ------------------------------------------------------------------
    # Sterowanie nasłuchem
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._listener is not None and self._listener.is_alive()

    def start(self) -> None:
        """Uruchamia pynput.Listener (nasłuch w osobnym wątku pynput)."""
        if self.is_running:
            return
        try:
            from pynput import keyboard

            self._keyboard = keyboard
            self._listener = keyboard.Listener(
                on_press=self._on_press, on_release=self._on_release
            )
            self._listener.daemon = True
            self._listener.start()
        except Exception as exc:  # noqa: BLE001 - np. brak obsługi platformy
            self._listener = None
            self.error.emit(f"Nie można uruchomić keymappera: {exc}")

    def stop(self) -> None:
        """Zatrzymuje nasłuch i czyści stan (bezpieczne z dowolnego wątku)."""
        listener = self._listener
        self._listener = None
        if listener is not None:
            try:
                listener.stop()
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            self._pressed.clear()
            self._last_tap.clear()

    def set_add_mode(self, active: bool) -> None:
        """W trybie dodawania przechwytujemy klawisz zamiast wykonywać akcję."""
        self._add_mode = active

    # ------------------------------------------------------------------
    # Callbacki pynput (wątek nasłuchu)
    # ------------------------------------------------------------------

    def _on_press(self, key: object) -> None:
        name = self._key_name(key)
        if name is None:
            return
        with self._lock:
            if self._add_mode:
                self.key_captured.emit(name)
                return
            if name in self._pressed:
                return  # edge-trigger: przytrzymanie nie powiela akcji
            self._pressed.add(name)
            now = time.monotonic()
            if now - self._last_tap.get(name, 0.0) < TAP_DEBOUNCE_S:
                return  # debounce: za szybkie ponowne wciśnięcie po zwolnieniu
            self._last_tap[name] = now
        self.key_pressed.emit(name)

    def _on_release(self, key: object) -> None:
        name = self._key_name(key)
        if name is None:
            return
        with self._lock:
            self._pressed.discard(name)

    @staticmethod
    def _key_name(key: object) -> str | None:
        """Normalizuje klawisz pynput do stringa z keymapy ('a', 'space', ...)."""
        try:
            char = getattr(key, "char", None)
            if char is not None:
                name = char.lower()
                return {" ": "space", "\t": "tab", "\r": "enter", "\n": "enter"}.get(
                    name, name
                )
            name = getattr(key, "name", None)
            if name:
                return name.lower()
        except Exception:  # noqa: BLE001
            return None
        return None


class KeymapperWidget(QWidget):
    """Przełącznik keymappera ze statusem nasłuchiwania.

    Sygnały (relay z :class:`KeymapperEngine`):
        toggled(bool): zmieniono stan przełącznika.
        key_pressed(str) / key_captured(str) / error(str): patrz engine.
    """

    toggled = pyqtSignal(bool)
    key_pressed = pyqtSignal(str)
    key_captured = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.engine = KeymapperEngine(self)
        self._add_mode = False

        box = QGroupBox("Keymapper")
        layout = QVBoxLayout(box)
        self.keymapper_check = QCheckBox("Keymapper Aktywny")
        self.keymapper_check.setStyleSheet("QCheckBox { font-weight: bold; }")
        self.status_label = QLabel("Nasłuch: wyłączony")
        self.status_label.setStyleSheet("color: #7f8c8d;")
        layout.addWidget(self.keymapper_check)
        layout.addWidget(self.status_label)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)

        self.keymapper_check.toggled.connect(self._on_check_toggled)
        self.engine.key_pressed.connect(self.key_pressed)
        self.engine.key_captured.connect(self.key_captured)
        self.engine.error.connect(self._on_engine_error)

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """``True``, gdy keymapper jest włączony przełącznikiem."""
        return self.keymapper_check.isChecked()

    def set_add_mode(self, active: bool) -> None:
        """Informuje, że aktywny jest tryb dodawania akcji (wymaga nasłuchu)."""
        self._add_mode = bool(active)
        self._update_engine()

    def set_listening(self, listening: bool) -> None:
        """Aktualizuje etykietę statusu nasłuchiwania."""
        if listening:
            self.status_label.setText("Nasłuch: aktywny")
            self.status_label.setStyleSheet("color: #2ecc71;")
        else:
            self.status_label.setText("Nasłuch: wyłączony")
            self.status_label.setStyleSheet("color: #7f8c8d;")

    def stop(self) -> None:
        """Zatrzymuje nasłuch (przy zamykaniu aplikacji)."""
        self.engine.stop()

    # ------------------------------------------------------------------
    # Wewnętrzne
    # ------------------------------------------------------------------

    def _on_check_toggled(self, checked: bool) -> None:
        self._update_engine()
        # Stan może się zmienić w trakcie (np. błąd startu nasłuchu od razu
        # wyłącza checkbox) - emitujemy aktualny stan, nie wartość wejściową.
        self.toggled.emit(self.keymapper_check.isChecked())

    def _update_engine(self) -> None:
        """Nasłuch działa, gdy włączony jest keymapper LUB tryb dodawania."""
        should_run = self.keymapper_check.isChecked() or self._add_mode
        if should_run and not self.engine.is_running:
            self.engine.start()
        elif not should_run and self.engine.is_running:
            self.engine.stop()
        self.set_listening(should_run)

    def _on_engine_error(self, message: str) -> None:
        self.keymapper_check.setChecked(False)
        self.set_listening(False)
        self.error.emit(message)
