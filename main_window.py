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
    QButtonGroup,
    QCheckBox,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from action_editor import ActionEditor, _plural_steps
from ad_killer_module import AIAdKillerWorker
from adb_controller import ADBController
from config_manager import ConfigManager, MacroPoint, SwipePoint
from device_panel import DevicePanel
from keymapper_widget import KeymapperWidget
from macro_runner import MacroRunner
from nav_bar_widget import NavigationBar, NavigationWorker
from stream_widget import CONTROL_SWIPE_DURATION_MS, AndroidScreenWidget, ControlWorker

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
        self.nav_bar = NavigationBar()

        self._macro_runner: MacroRunner | None = None
        self._nav_worker: NavigationWorker | None = None
        self._control_worker: ControlWorker | None = None
        self._multi_device_window = None  # MultiDeviceControlWindow (tworzony leniwie)
        self._ad_killer: AIAdKillerWorker | None = None
        self._ad_killer_dialog = None  # AdKillerConfigDialog (tworzony leniwie)
        # Zatrzymane workery, które jeszcze kończą wątek w tle - trzymamy
        # referencje, żeby QThread nie został zniszczony w trakcie działania
        # (zapobiega nakładaniu się wielu instancji po wielokrotnym WŁ/WYŁ).
        self._retired_ad_killers: list[AIAdKillerWorker] = []

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

        # Górny pasek: tryb streamu (Sterowanie/Mapowanie) + Multi-Device Control
        topbar = QHBoxLayout()
        topbar.setContentsMargins(8, 6, 8, 2)
        self.control_btn = QPushButton("🎮 Sterowanie")
        self.control_btn.setCheckable(True)
        self.control_btn.setChecked(True)
        self.control_btn.setToolTip(
            "Mysz steruje telefonem: klik = tap, przeciągnięcie = swipe (przez ADB)"
        )
        self.map_btn = QPushButton("➕ Mapowanie")
        self.map_btn.setCheckable(True)
        self.map_btn.setToolTip(
            "Kliknięcia/gesty definiują akcje keymapy (dodawanie Tap/Swipe/Makro)"
        )
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.control_btn)
        self.mode_group.addButton(self.map_btn)
        topbar.addWidget(self.control_btn)
        topbar.addWidget(self.map_btn)
        self.ad_killer_check = QCheckBox("🛡️ Auto-Zamykanie [WYŁ]")
        self.ad_killer_check.setToolTip(
            "Automatycznie zamyka reklamy (AI - model YOLOv11/ONNX przez onnxruntime)"
        )
        topbar.addWidget(self.ad_killer_check)
        topbar.addStretch(1)
        self.ad_killer_config_button = QPushButton("🛡️ Ad Killer Config")
        self.ad_killer_config_button.setToolTip(
            "Konfiguracja Ad Killer: model ONNX, czułość (confidence), interwał"
        )
        topbar.addWidget(self.ad_killer_config_button)
        self.multi_device_button = QPushButton("🌐 Multi-Device Control")
        self.multi_device_button.setToolTip(
            "Otwórz farmę urządzeń (Device Grid / Device Wall)"
        )
        topbar.addWidget(self.multi_device_button)
        outer.addLayout(topbar)

        # Kolumna streamu: podgląd telefonu + pasek nawigacji pod spodem
        stream_col = QVBoxLayout()
        stream_col.setSpacing(6)
        stream_col.addWidget(self.stream, 1)
        stream_col.addWidget(self.nav_bar)

        root = QHBoxLayout()
        root.setContentsMargins(8, 8, 8, 8)
        root.addLayout(stream_col, 1)
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
        ae.macro_edit_changed.connect(self._on_macro_edit_changed)
        ae.screen_gesture_capture.connect(self.stream.set_capture_enabled)

        # Keymapper
        km.toggled.connect(self._on_keymapper_toggled)
        km.key_pressed.connect(self._on_key_pressed)
        km.key_captured.connect(ae.on_key_captured)
        km.error.connect(self._on_engine_error)

        # Stream -> edytor (gesty) oraz status
        st.point_selected.connect(ae.on_screen_clicked)
        st.swipe_selected.connect(ae.on_swipe_selected)
        st.control_tap.connect(self._on_control_tap)
        st.control_swipe.connect(self._on_control_swipe)
        st.point_moved.connect(self._on_point_moved)
        st.macro_step_moved.connect(self._on_macro_step_moved)

        # Przełącznik trybu streamu (Sterowanie / Mapowanie)
        self.control_btn.clicked.connect(lambda: self._set_stream_mode(False))
        self.map_btn.clicked.connect(lambda: self._set_stream_mode(True))
        st.stream_started.connect(
            lambda serial: self._status(f"Stream uruchomiony: {serial}")
        )
        st.stream_stopped.connect(self._on_stream_stopped)
        st.stream_error.connect(self._status)

        # Pasek nawigacji telefonu (ADB keyevent - w tle, bez blokowania GUI)
        self.nav_bar.action_triggered.connect(self._on_nav_action)

        # Multi-Device Control
        self.multi_device_button.clicked.connect(self._open_multi_device)

        # Ad Killer (automatyczne zamykanie reklam)
        self.ad_killer_check.toggled.connect(self._on_ad_killer_toggled)
        self.ad_killer_config_button.clicked.connect(self._open_ad_killer_config)

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
    # Ad Killer (automatyczne zamykanie reklam - niezależny moduł)
    # ------------------------------------------------------------------

    def _open_ad_killer_config(self) -> None:
        """Otwiera (raz, leniwie) okno konfiguracji Ad Killer."""
        if self._ad_killer_dialog is None:
            from ad_killer_ui import AdKillerConfigDialog

            self._ad_killer_dialog = AdKillerConfigDialog()
            self._ad_killer_dialog.settings_changed.connect(
                self._on_ad_killer_settings
            )
            self._ad_killer_dialog.model_changed.connect(
                self._on_ad_model_changed
            )
        self._ad_killer_dialog.show()
        self._ad_killer_dialog.raise_()
        self._ad_killer_dialog.activateWindow()

    def _on_ad_killer_toggled(self, checked: bool) -> None:
        """Start/zatrzymanie workera Ad Killer (checkbox na pasku)."""
        self.ad_killer_check.setText(
            "🛡️ Auto-Zamykanie [WŁ]" if checked else "🛡️ Auto-Zamykanie [WYŁ]"
        )
        if checked:
            if self._ad_killer is not None and self._ad_killer.isRunning():
                # Zabezpieczenie przed nakładaniem się wątków skanowania.
                self._status("🛡️ Ad Killer już działa - pomijam start.")
                return
            threshold = 0.7
            interval_ms = 1500
            model_path = None
            if self._ad_killer_dialog is not None:
                threshold = self._ad_killer_dialog.threshold
                interval_ms = self._ad_killer_dialog.interval_ms
                model_path = self._ad_killer_dialog.model_path
            worker = AIAdKillerWorker(
                self.adb,
                self.stream,
                model_path=model_path,
                threshold=threshold,
                interval_ms=interval_ms,
            )
            worker.detected.connect(self._on_ad_detected)
            worker.status_message.connect(self._status)
            self._ad_killer = worker
            worker.start()
            self._status(
                f"🛡️ Ad Killer aktywny (AI, próg {threshold:.0%}, co {interval_ms} ms)"
            )
        else:
            worker = self._ad_killer
            self._ad_killer = None
            if worker is not None:
                worker.stop()
                # Nie blokujemy GUI (wait w tle): zatrzymany worker kończy
                # wątek w tle (pętla czeka na event, nie na sleep), a my
                # trzymamy referencję aż do sygnału finished.
                self._retired_ad_killers.append(worker)
                worker.finished.connect(
                    lambda w=worker: self._on_ad_killer_retired(w)
                )
                if not worker.isRunning():
                    # Wątek zdążył się już zakończyć - posprzątaj od razu.
                    self._on_ad_killer_retired(worker)
            self._status("🛡️ Ad Killer wyłączony.")

    def _on_ad_killer_retired(self, worker: AIAdKillerWorker) -> None:
        """Sprząta po zatrzymanym workerze (wątek zakończył pracę)."""
        if worker in self._retired_ad_killers:
            self._retired_ad_killers.remove(worker)
        worker.deleteLater()

    def _on_ad_killer_settings(self, threshold: float, interval_ms: int) -> None:
        """Aplikuje zmiany ustawień z okna konfiguracji do działającego workera."""
        if self._ad_killer is not None:
            self._ad_killer.set_threshold(threshold)
            self._ad_killer.set_interval_ms(interval_ms)

    def _on_ad_model_changed(self, model_path: str) -> None:
        """Wybrano nowy model ONNX - podmień w działającym workerce."""
        if self._ad_killer is not None:
            self._ad_killer.set_model_path(model_path)
            self._status(f"🛡️ Nowy model Ad Killer: {model_path}")

    def _on_ad_detected(self, x: int, y: int) -> None:
        """Wykryto i zamknięto reklamę - LED ADB + komunikat."""
        self.device_panel.set_adb_status("ok")
        self._status(f"🛡️ Wykryto i zamknięto reklamę ({x}, {y})")

    # ------------------------------------------------------------------
    # Połączenie / stream
    # ------------------------------------------------------------------

    def _on_device_connected(self, serial: str) -> None:
        self.stream.start_stream(serial)
        self.nav_bar.set_connected(True)
        self._status(f"Połączono: {serial}")

    def _on_stream_stopped(self, reason: str) -> None:
        if reason:
            # Urządzenie odłączone - LED wraca do stanu "rozłączono".
            self.device_panel.set_adb_status("off")
            self.nav_bar.set_connected(False)
            self._status(reason)

    # ------------------------------------------------------------------
    # Pasek nawigacji (keyevent ADB w osobnym wątku)
    # ------------------------------------------------------------------

    def _on_nav_action(self, action: str) -> None:
        """Wysyła komendę nawigacyjną w tle (nie blokuje interfejsu)."""
        if self.adb.device_serial is None:
            self._status("Połącz urządzenie, zanim użyjesz nawigacji.")
            return
        worker = NavigationWorker(self.adb, action)
        worker.finished.connect(self._on_nav_finished)
        self._nav_worker = worker
        worker.start()

    def _on_nav_finished(self, ok: bool, message: str) -> None:
        """Wynik komendy z paska nawigacji -> LED ADB + komunikat."""
        self.device_panel.set_adb_status("ok" if ok else "error")
        self._status(message)

    # ------------------------------------------------------------------
    # Interaktywne sterowanie streamem (tap/swipe przez ADB, w tle)
    # ------------------------------------------------------------------

    def _on_control_tap(self, x: int, y: int) -> None:
        self._run_control("tap", (x, y))

    def _on_control_swipe(self, x1: int, y1: int, x2: int, y2: int) -> None:
        self._run_control("swipe", (x1, y1, x2, y2, CONTROL_SWIPE_DURATION_MS))

    def _run_control(self, kind: str, args: tuple[int, ...]) -> None:
        """Wykonuje gest sterowania w osobnym wątku (nie blokuje GUI)."""
        if self.adb.device_serial is None:
            return
        worker = ControlWorker(self.adb, kind, args)
        worker.finished.connect(self._on_control_finished)
        self._control_worker = worker
        worker.start()

    def _on_control_finished(self, ok: bool) -> None:
        """Wynik gestu sterowania -> LED ADB."""
        self.device_panel.set_adb_status("ok" if ok else "error")

    # ------------------------------------------------------------------
    # Tryby edytora -> stream i keymapper
    # ------------------------------------------------------------------

    def _on_mode_changed(self, kind: str) -> None:
        # Macro używa streamu w trybie "swipe": klik -> Tap, przeciągnięcie -> Swipe
        self.stream.set_gesture_mode("swipe" if kind in ("swipe", "macro") else "tap")

    def _on_capture_mode_changed(self, active: bool) -> None:
        """Tryb przechwytywania zmieniony (checkboxy edytora / zapis akcji).

        Synchronizuje stream, keymapper i przyciski Sterowanie/Mapowanie.
        """
        self.stream.set_capture_enabled(active)
        self.keymapper_widget.set_add_mode(active)
        self.control_btn.setChecked(not active)
        self.map_btn.setChecked(active)

    def _set_stream_mode(self, mapping: bool) -> None:
        """Przełącza tryb streamu: False = Sterowanie, True = Mapowanie."""
        if mapping:
            self.action_editor.enable_capture()
        else:
            self.action_editor.disable_capture()
        # Bezpośrednio, gdy checkboxy edytora już były w docelowym stanie
        # (żaden toggled nie wystrzeli) - stan streamu musi być spójny.
        self.stream.set_capture_enabled(mapping)
        self.keymapper_widget.set_add_mode(mapping)
        self.control_btn.setChecked(not mapping)
        self.map_btn.setChecked(mapping)

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

    def _on_macro_edit_changed(self, macro_name: str | None, step_index: int | None) -> None:
        """Ustawia wyróżnienie edytowanego kroku makra na nakładce."""
        self.stream.set_macro_step_edit(macro_name, step_index)

    def _on_macro_step_moved(
        self, macro_name: str, step_index: int, new_x: int, new_y: int
    ) -> None:
        """Drag & drop pojedynczego kroku: aktualizuje TYLKO ten krok i zapisuje."""
        macro = self.config.get_point(macro_name)
        if not isinstance(macro, MacroPoint):
            self._status(f"Nie znaleziono makra: {macro_name}")
            return
        if step_index < 0 or step_index >= len(macro.actions):
            return
        action = macro.actions[step_index]
        kind = action.get("type")
        if kind == "tap":
            action["x"], action["y"] = new_x, new_y
        elif kind == "swipe":
            dx = new_x - int(action["x1"])
            dy = new_y - int(action["y1"])
            action["x1"], action["y1"] = new_x, new_y
            action["x2"] = max(0, int(action["x2"]) + dx)
            action["y2"] = max(0, int(action["y2"]) + dy)
        else:
            return  # Delay nie ma pozycji
        self.config.save_config()
        self.stream.set_overlay_points(self.config.load_config())
        self.action_editor.reload_macro_steps()
        self._status(f"Zaktualizowano krok {step_index + 1} makra '{macro_name}'")

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
        if self._ad_killer is not None:
            self._ad_killer.stop()
        for worker in list(self._retired_ad_killers):
            worker.stop()
        self.keymapper_widget.stop()
        self.stream.stop_stream()
        super().closeEvent(event)
