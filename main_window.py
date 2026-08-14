"""MainWindow - główne okno aplikacji ADB-AUTOKLIK.

Okno składa wyłącznie panele (patrz Zadanie: refaktoryzacja na mniejsze
moduły) i zarządza komunikacją między nimi przez sygnały Qt:

- :class:`DevicePanel` (``device_panel.py``) - połączenie ADB (USB/Wi-Fi),
  lista urządzeń, wskaźnik LED stanu ADB,
- :class:`ActionEditor` (``action_editor.py``) - tabela akcji + formularz
  dodawania Tap/Swipe/Makro, edytor kroków makra, podświetlanie kroku,
- :class:`KeymapperWidget` (``keymapper_widget.py``) - przełącznik keymappera
  ze statusem nasłuchiwania (pynput w osobnym wątku),
- :class:`AndroidScreenWidget` (``stream_widget.py``) - podgląd ekranu
  telefonu (scrcpy) z nakładką i drag & drop punktów,
- :class:`MacroRunner` (``macro_runner.py``) - odtwarzanie makr w osobnym
  wątku z sygnałami postępu.

Komunikacja między wątkami (pynput/scrcpy/makro) a pętlą zdarzeń PyQt6
odbywa się wyłącznie przez sygnały Qt (queued connections).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from action_editor import ActionEditor, _plural_steps
from adb_controller import ADBController
from config_manager import ConfigManager, MacroPoint, SwipePoint
from device_panel import DevicePanel
from keymapper_widget import KeymapperWidget
from macro_runner import MacroRunner
from stream_widget import AndroidScreenWidget

_STATUS_TIMEOUT_MS = 6000


class MainWindow(QMainWindow):
    """Główne okno: stream + panele (połączenie, akcje, keymapper)."""

    def __init__(self, config_path: str = "keymap.json") -> None:
        super().__init__()
        self.setWindowTitle("ADB-AUTOKLIK — Keymapper")
        self.resize(1240, 760)

        self.config = ConfigManager(config_path)
        self.adb = ADBController()

        # Panele (każdy odpowiedzialny za swoją domenę)
        self.device_panel = DevicePanel(self.adb)
        self.action_editor = ActionEditor(self.config)
        self.keymapper_widget = KeymapperWidget()
        self.stream = AndroidScreenWidget()
        self.stream.setMinimumWidth(480)

        self._macro_runner: MacroRunner | None = None
        self._multi_device_window = None  # MultiDeviceControlWindow (tworzony leniwie)

        self._build_ui()
        self._wire_signals()
        self.device_panel.refresh_devices()

    # ------------------------------------------------------------------
    # Budowa UI (tylko kompozycja layoutów)
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 12, 12, 12)
        panel_layout.setSpacing(12)
        panel_layout.addWidget(self.device_panel)
        panel_layout.addWidget(self.action_editor, 1)
        panel_layout.addWidget(self.keymapper_widget)
        panel_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(330)
        scroll.setMaximumWidth(420)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Górny pasek: wejście do niezależnego modułu Multi-Device Control
        topbar = QHBoxLayout()
        topbar.setContentsMargins(8, 6, 8, 2)
        self.multi_device_button = QPushButton("🌐 Multi-Device Control")
        self.multi_device_button.setToolTip(
            "Otwórz farmę urządzeń (Device Grid / Device Wall)"
        )
        topbar.addWidget(self.multi_device_button)
        topbar.addStretch(1)
        outer.addLayout(topbar)

        root = QHBoxLayout()
        root.setContentsMargins(8, 8, 8, 8)
        root.addWidget(self.stream, 1)
        root.addWidget(scroll)
        outer.addLayout(root, 1)
        self.setCentralWidget(central)

        self.statusBar().showMessage("Gotowy. Wybierz urządzenie i naciśnij 'Połącz'.")

    # ------------------------------------------------------------------
    # Sygnały -> sloty (komunikacja między panelami)
    # ------------------------------------------------------------------

    def _wire_signals(self) -> None:
        dp = self.device_panel
        ae = self.action_editor
        km = self.keymapper_widget
        st = self.stream

        # Połączenie
        dp.device_connected.connect(self._on_device_connected)
        dp.status_message.connect(self._status)

        # Edytor akcji
        ae.mode_changed.connect(self._on_mode_changed)
        ae.capture_mode_changed.connect(self._on_capture_mode_changed)
        ae.points_changed.connect(self._on_points_changed)
        ae.status_message.connect(self._status)

        # Keymapper
        km.toggled.connect(self._on_keymapper_toggled)
        km.key_pressed.connect(self._on_key_pressed)
        km.key_captured.connect(ae.on_key_captured)
        km.error.connect(self._on_engine_error)

        # Stream -> edytor (gesty) oraz status
        st.point_selected.connect(ae.on_screen_clicked)
        st.swipe_selected.connect(ae.on_swipe_selected)
        st.point_moved.connect(self._on_point_moved)
        st.stream_started.connect(
            lambda serial: self._status(f"Stream uruchomiony: {serial}")
        )
        st.stream_stopped.connect(self._on_stream_stopped)
        st.stream_error.connect(self._status)

        # Multi-Device Control
        self.multi_device_button.clicked.connect(self._open_multi_device)

    # ------------------------------------------------------------------
    # Multi-Device Control
    # ------------------------------------------------------------------

    def _open_multi_device(self) -> None:
        """Otwiera (raz, leniwie) niezależne okno Multi-Device Control."""
        if self._multi_device_window is None:
            from multi_device_window import MultiDeviceControlWindow

            self._multi_device_window = MultiDeviceControlWindow()
        self._multi_device_window.show()
        self._multi_device_window.raise_()
        self._multi_device_window.activateWindow()

    # ------------------------------------------------------------------
    # Połączenie / stream
    # ------------------------------------------------------------------

    def _on_device_connected(self, serial: str) -> None:
        self.stream.start_stream(serial)
        self._status(f"Połączono: {serial}")

    def _on_stream_stopped(self, reason: str) -> None:
        if reason:
            # Urządzenie odłączone - LED wraca do stanu "rozłączono".
            self.device_panel.set_adb_status("off")
            self._status(reason)

    # ------------------------------------------------------------------
    # Tryby edytora -> stream i keymapper
    # ------------------------------------------------------------------

    def _on_mode_changed(self, kind: str) -> None:
        # Macro używa streamu w trybie "swipe": klik -> Tap, przeciągnięcie -> Swipe
        self.stream.set_gesture_mode("swipe" if kind in ("swipe", "macro") else "tap")

    def _on_capture_mode_changed(self, active: bool) -> None:
        self.stream.set_capture_enabled(active)
        self.keymapper_widget.set_add_mode(active)

    def _on_points_changed(self) -> None:
        """Zestaw akcji się zmienił - odśwież nakładkę na streamie."""
        self.stream.set_overlay_points(self.config.load_config())

    # ------------------------------------------------------------------
    # Drag & drop punktów na nakładce
    # ------------------------------------------------------------------

    def _on_point_moved(self, name: str, new_x: int, new_y: int) -> None:
        if not self.config.move_point(name, new_x, new_y):
            self._status(f"Nie znaleziono akcji: {name}")
            return
        self.action_editor.reload_table()
        self.stream.set_overlay_points(self.config.load_config())
        self._status(f"Przesunięto '{name}' → ({new_x}, {new_y})")

    # ------------------------------------------------------------------
    # Keymapper / wykonanie akcji
    # ------------------------------------------------------------------

    def _on_keymapper_toggled(self, checked: bool) -> None:
        self._status("Keymapper aktywny." if checked else "Keymapper wyłączony.")

    def _on_engine_error(self, message: str) -> None:
        self.action_editor.disable_capture()
        self._status(message)

    def _on_key_pressed(self, key: str) -> None:
        point = self.config.get_point(key)
        if point is None or self.adb.device_serial is None:
            return
        if isinstance(point, MacroPoint):
            self._start_macro(point)
            return
        if isinstance(point, SwipePoint):
            ok = self.adb.swipe(
                point.x1, point.y1, point.x2, point.y2, point.duration_ms
            )
        else:
            ok = self.adb.tap(point.x, point.y)
        self.device_panel.set_adb_status("ok" if ok else "error")
        if not ok:
            self._status(
                f"Swipe nieudany dla klawisza '{key}'"
                if isinstance(point, SwipePoint)
                else f"Tap nieudany dla klawisza '{key}'"
            )

    # ------------------------------------------------------------------
    # Makra (MacroRunner + podgląd postępu w edytorze)
    # ------------------------------------------------------------------

    def _start_macro(self, point: MacroPoint) -> None:
        """Uruchamia odtwarzanie makra w osobnym wątku (nie blokuje GUI)."""
        if self.adb.device_serial is None:
            return
        if self._macro_runner is not None and self._macro_runner.is_alive():
            self._status(f"Makro już trwa - pomijam klawisz '{point.key}'")
            return
        runner = MacroRunner(self.adb, point.actions)
        runner.step_started.connect(self._on_macro_step_started)
        runner.step_result.connect(self._on_macro_step_result)
        runner.completed.connect(self._on_macro_completed)
        self._macro_runner = runner
        self.action_editor.begin_macro_preview(point.actions)
        runner.start()
        self._status(
            f"Odtwarzam makro '{point.name}' ({_plural_steps(len(point.actions))})"
        )

    def _on_macro_step_started(self, index: int) -> None:
        """Podświetla wykonywany krok na liście edytora (wątek GUI)."""
        self.action_editor.set_current_macro_step(index)

    def _on_macro_step_result(self, ok: bool) -> None:
        self.device_panel.set_adb_status("ok" if ok else "error")

    def _on_macro_completed(self, finished: bool) -> None:
        self._macro_runner = None
        self.action_editor.end_macro_preview()
        self._status("Makro zakończone." if finished else "Makro przerwane.")

    # ------------------------------------------------------------------
    # Zamknięcie
    # ------------------------------------------------------------------

    def _status(self, message: str) -> None:
        self.statusBar().showMessage(message, _STATUS_TIMEOUT_MS)

    def closeEvent(self, event) -> None:  # noqa: N802 (nazwa Qt)
        if self._macro_runner is not None:
            self._macro_runner.stop()
        self.keymapper_widget.stop()
        self.stream.stop_stream()
        super().closeEvent(event)
