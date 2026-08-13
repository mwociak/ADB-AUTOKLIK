"""MainWindow - główne okno aplikacji ADB-AUTOKLIK.

Integruje wszystkie warstwy projektu:
- :class:`AndroidScreenWidget` - podgląd ekranu telefonu (scrcpy) z nakładką,
- :class:`ADBController` - komunikacja ADB (dotknięcia),
- :class:`ConfigManager` - profile punktów mapowania (keymap.json),
- :class:`KeymapperEngine` - globalny nasłuch klawiszy (pynput) w osobnym wątku.

Komunikacja między wątkiem pynput/scrcpy a pętlą zdarzeń PyQt6 odbywa się
wyłącznie przez sygnały Qt (queued connections) - nasłuch nie blokuje GUI.
"""

from __future__ import annotations

import threading
import time

import adbutils
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from adb_controller import ADBController, ADBError
from config_manager import ConfigManager
from stream_widget import AndroidScreenWidget

# Minimalny odstęp między tapami tego samego klawisza [s] - zabezpieczenie
# przed "spamowaniem" (edge-trigger + debounce przy szybkim ponownym wciśnięciu).
TAP_DEBOUNCE_S = 0.05

_STATUS_TIMEOUT_MS = 6000


class KeymapperEngine(QObject):
    """Globalny nasłuch klawiszy przez pynput w osobnym wątku.

    Sygnały (emituje je wątek pynput, odbierają sloty w wątku GUI):
        key_pressed(str): naciśnięto klawisz - slot wywołuje ``tap`` na
            aktywnym urządzeniu (jeśli klawisz ma przypisany punkt).
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
        """W trybie dodawania przechwytujemy klawisz zamiast wykonywać tap."""
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
                return  # edge-trigger: przytrzymanie nie powiela tapów
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


class MainWindow(QMainWindow):
    """Główne okno aplikacji: stream + panel sterowania + keymapper."""

    def __init__(self, config_path: str = "keymap.json") -> None:
        super().__init__()
        self.setWindowTitle("ADB-AUTOKLIK — Keymapper")
        self.resize(1240, 760)

        self.config = ConfigManager(config_path)
        self.adb = ADBController()
        self.engine = KeymapperEngine(self)
        self._pending = {"x": None, "y": None, "key": None}

        self._build_ui()
        self._wire_signals()
        self.refresh_devices()
        self._reload_points()

    # ------------------------------------------------------------------
    # Budowa UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Lewa część: stream telefonu
        self.stream = AndroidScreenWidget()
        self.stream.setMinimumWidth(480)

        # Prawa część: panel sterowania (przewijany)
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 12, 12, 12)
        panel_layout.setSpacing(12)

        # --- Połączenie ---
        conn_box = QGroupBox("Połączenie")
        conn_layout = QVBoxLayout(conn_box)
        device_row = QHBoxLayout()
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(150)
        self.refresh_button = QPushButton("Odśwież")
        device_row.addWidget(self.device_combo, 1)
        device_row.addWidget(self.refresh_button)
        conn_layout.addLayout(device_row)

        self.connect_button = QPushButton("Połącz")
        self.connect_button.setEnabled(False)
        conn_layout.addWidget(self.connect_button)

        wireless_row = QHBoxLayout()
        self.wireless_input = QLineEdit()
        self.wireless_input.setPlaceholderText("IP:port (np. 192.168.1.10:5555)")
        self.wireless_button = QPushButton("Połącz bezprzewodowo")
        wireless_row.addWidget(self.wireless_input, 1)
        wireless_row.addWidget(self.wireless_button)
        conn_layout.addLayout(wireless_row)
        panel_layout.addWidget(conn_box)

        # --- Punkty mapowania ---
        points_box = QGroupBox("Punkty mapowania")
        points_layout = QVBoxLayout(points_box)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Nazwa", "Klawisz", "X", "Y"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, header.ResizeMode.Stretch)
        for col in (1, 2, 3):
            header.setSectionResizeMode(col, header.ResizeMode.ResizeToContents)
        self.delete_button = QPushButton("Usuń zaznaczony punkt")
        self.delete_button.setEnabled(False)
        points_layout.addWidget(self.table, 1)
        points_layout.addWidget(self.delete_button)
        panel_layout.addWidget(points_box, 1)

        # --- Dodawanie punktu ---
        add_box = QGroupBox("Dodaj nowy punkt")
        add_layout = QVBoxLayout(add_box)
        self.add_mode_check = QCheckBox("Tryb dodawania (kliknij na ekranie, potem klawisz)")
        self.add_hint = QLabel("Nieaktywny.")
        self.add_hint.setWordWrap(True)
        name_row = QHBoxLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Nazwa punktu")
        self.save_point_button = QPushButton("Zapisz punkt")
        self.save_point_button.setEnabled(False)
        name_row.addWidget(self.name_input, 1)
        name_row.addWidget(self.save_point_button)
        add_layout.addWidget(self.add_mode_check)
        add_layout.addWidget(self.add_hint)
        add_layout.addLayout(name_row)
        panel_layout.addWidget(add_box)

        # --- Keymapper ---
        km_box = QGroupBox("Keymapper")
        km_layout = QVBoxLayout(km_box)
        self.keymapper_check = QCheckBox("Keymapper Aktywny")
        self.keymapper_check.setStyleSheet("QCheckBox { font-weight: bold; }")
        km_layout.addWidget(self.keymapper_check)
        panel_layout.addWidget(km_box)
        panel_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(330)
        scroll.setMaximumWidth(420)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.addWidget(self.stream, 1)
        root.addWidget(scroll)
        self.setCentralWidget(central)

        self.statusBar().showMessage("Gotowy. Wybierz urządzenie i naciśnij 'Połącz'.")

    # ------------------------------------------------------------------
    # Sygnały -> sloty
    # ------------------------------------------------------------------

    def _wire_signals(self) -> None:
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.connect_button.clicked.connect(self._on_connect)
        self.wireless_button.clicked.connect(self._on_wireless_connect)

        self.table.itemSelectionChanged.connect(self._on_table_selection)
        self.delete_button.clicked.connect(self._on_delete_point)
        self.add_mode_check.toggled.connect(self._on_add_mode_toggled)
        self.save_point_button.clicked.connect(self._on_save_point)
        self.name_input.returnPressed.connect(self._on_save_point)

        self.keymapper_check.toggled.connect(self._on_keymapper_toggled)
        self.engine.key_pressed.connect(self._on_key_pressed)
        self.engine.key_captured.connect(self._on_key_captured)
        self.engine.error.connect(self._on_engine_error)

        self.stream.point_selected.connect(self._on_screen_clicked)
        self.stream.stream_started.connect(
            lambda serial: self._status(f"Stream uruchomiony: {serial}")
        )
        self.stream.stream_stopped.connect(self._on_stream_stopped)
        self.stream.stream_error.connect(self._status)

    # ------------------------------------------------------------------
    # Połączenie z urządzeniem
    # ------------------------------------------------------------------

    def refresh_devices(self) -> None:
        """Odświeża listę urządzeń ADB w ComboBoxie."""
        current = self.device_combo.currentData()
        self.device_combo.clear()
        try:
            devices = self.adb.get_devices()
        except ADBError as exc:
            self._status(f"Błąd: {exc}")
            self.connect_button.setEnabled(False)
            return
        for serial in devices:
            self.device_combo.addItem(serial, serial)
        if current in devices:
            self.device_combo.setCurrentIndex(devices.index(current))
        self.connect_button.setEnabled(self.device_combo.count() > 0)
        self._status(
            f"Znaleziono urządzeń: {self.device_combo.count()}"
            if self.device_combo.count()
            else "Brak urządzeń ADB"
        )

    def _on_connect(self) -> None:
        serial = self.device_combo.currentData()
        if not serial:
            self._status("Wybierz urządzenie z listy.")
            return
        try:
            self.adb.connect_device(serial)
        except ADBError as exc:
            self._status(f"Błąd połączenia: {exc}")
            return
        self.stream.start_stream(serial)
        self._status(f"Połączono: {serial}")

    def _on_wireless_connect(self) -> None:
        text = self.wireless_input.text().strip()
        if not text:
            self._status("Podaj adres IP:port urządzenia.")
            return
        if ":" not in text:
            text = f"{text}:5555"  # adb connect domyślnie używa portu 5555
        try:
            reply = adbutils.adb.connect(text)
        except Exception as exc:  # noqa: BLE001 - czytelny komunikat
            self._status(f"Nie można połączyć bezprzewodowo: {exc}")
            return
        lowered = reply.lower()
        if any(
            word in lowered
            for word in ("unable to connect", "failed to connect", "cannot connect")
        ):
            self._status(f"Połączenie bezprzewodowe nieudane: {reply}")
            return
        # adb connect zwraca komunikat serwera (np. "connected to ..."), a nie
        # serial - wybieramy urządzenie po adresie wpisanym przez użytkownika.
        self.refresh_devices()
        serials = [
            self.device_combo.itemData(i) for i in range(self.device_combo.count())
        ]
        if text in serials:
            self.device_combo.setCurrentIndex(serials.index(text))
        self._on_connect()
        self._status(f"Połączono bezprzewodowo: {text}")

    # ------------------------------------------------------------------
    # Punkty mapowania
    # ------------------------------------------------------------------

    def _reload_points(self) -> None:
        """Odświeża tabelę punktów i nakładkę na streamie."""
        points = self.config.load_config()
        self.table.setRowCount(0)
        for point in points:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = (point.name, point.key, str(point.x), str(point.y))
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)
        self.stream.set_overlay_points(points)

    def _on_table_selection(self) -> None:
        self.delete_button.setEnabled(self.table.currentRow() >= 0)

    def _on_delete_point(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        name = self.table.item(row, 0).text()
        self.config.remove_point(name)
        self._reload_points()
        self._status(f"Usunięto punkt: {name}")

    # ------------------------------------------------------------------
    # Dodawanie punktu (klik -> klawisz -> nazwa -> zapis)
    # ------------------------------------------------------------------

    def _on_add_mode_toggled(self, checked: bool) -> None:
        self.engine.set_add_mode(checked)
        self._reset_pending()
        self.add_hint.setText(
            "Kliknij lewym przyciskiem na ekranie telefonu, a potem wciśnij klawisz."
            if checked
            else "Nieaktywny."
        )
        self._update_engine()

    def _on_screen_clicked(self, x: int, y: int) -> None:
        if not self.add_mode_check.isChecked():
            return
        self._pending["x"], self._pending["y"] = x, y
        self._update_add_hint()

    def _on_key_captured(self, key: str) -> None:
        if not self.add_mode_check.isChecked():
            return
        self._pending["key"] = key
        self._update_add_hint()

    def _update_add_hint(self) -> None:
        p = self._pending
        steps = []
        if p["x"] is not None:
            steps.append(f"X={p['x']}, Y={p['y']}")
        else:
            steps.append("kliknij na ekranie")
        if p["key"] is not None:
            steps.append(f"klawisz '{p['key']}'")
        else:
            steps.append("wciśnij klawisz")
        self.add_hint.setText(" -> ".join(steps) + ". Podaj nazwę i zapisz.")
        self.save_point_button.setEnabled(
            p["x"] is not None and p["y"] is not None and p["key"] is not None
        )

    def _on_save_point(self) -> None:
        p = self._pending
        name = self.name_input.text().strip()
        if p["x"] is None or p["y"] is None or p["key"] is None:
            self._status("Najpierw kliknij na ekranie i wciśnij klawisz.")
            return
        if not name:
            self._status("Podaj nazwę punktu.")
            return
        key, x, y = p["key"], p["x"], p["y"]
        try:
            self.config.add_point(name, key, x, y)
        except ValueError as exc:
            self._status(f"Nie zapisano: {exc}")
            return
        self._reload_points()
        self.name_input.clear()
        self.add_mode_check.setChecked(False)  # wyłącza tryb i resetuje stan
        self._status(f"Zapisano punkt '{name}' -> klawisz {key} ({x}, {y})")

    def _reset_pending(self) -> None:
        self._pending = {"x": None, "y": None, "key": None}
        self.save_point_button.setEnabled(False)

    # ------------------------------------------------------------------
    # Keymapper
    # ------------------------------------------------------------------

    def _on_keymapper_toggled(self, checked: bool) -> None:
        self._update_engine()
        self._status("Keymapper aktywny." if checked else "Keymapper wyłączony.")

    def _update_engine(self) -> None:
        """Nasłuch działa, gdy włączony jest keymapper LUB tryb dodawania."""
        should_run = self.keymapper_check.isChecked() or self.add_mode_check.isChecked()
        if should_run and not self.engine.is_running:
            self.engine.start()
        elif not should_run and self.engine.is_running:
            self.engine.stop()

    def _on_key_pressed(self, key: str) -> None:
        point = self.config.get_point(key)
        if point is None or self.adb.device_serial is None:
            return
        if not self.adb.tap(point.x, point.y):
            self._status(f"Tap nieudany dla klawisza '{key}'")

    def _on_engine_error(self, message: str) -> None:
        self.keymapper_check.setChecked(False)
        self.add_mode_check.setChecked(False)
        self._status(message)

    # ------------------------------------------------------------------
    # Stream / zamknięcie
    # ------------------------------------------------------------------

    def _on_stream_stopped(self, reason: str) -> None:
        if reason:
            self._status(reason)

    def _status(self, message: str) -> None:
        self.statusBar().showMessage(message, _STATUS_TIMEOUT_MS)

    def closeEvent(self, event) -> None:  # noqa: N802 (nazwa Qt)
        self.engine.stop()
        self.stream.stop_stream()
        super().closeEvent(event)
