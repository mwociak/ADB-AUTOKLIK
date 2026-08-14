"""MainWindow - główne okno aplikacji ADB-AUTOKLIK.

Integruje wszystkie warstwy projektu:
- :class:`AndroidScreenWidget` - podgląd ekranu telefonu (scrcpy) z nakładką,
- :class:`ADBController` - komunikacja ADB (dotknięcia, swipe'y),
- :class:`ConfigManager` - profile akcji mapowania (keymap.json),
- :class:`KeymapperEngine` - globalny nasłuch klawiszy (pynput) w osobnym wątku,
- :class:`MacroRunner` - odtwarzanie makr (sekwencji kroków) w osobnym wątku.

Komunikacja między wątkami (pynput/scrcpy/makro) a pętlą zdarzeń PyQt6
odbywa się wyłącznie przez sygnały Qt (queued connections) - nasłuch
i odtwarzanie makr nie blokują GUI.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import adbutils
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from adb_controller import ADBController, ADBError
from config_manager import ConfigManager, MacroPoint, SwipePoint
from stream_widget import AndroidScreenWidget

# Minimalny odstęp między akcjami tego samego klawisza [s] - zabezpieczenie
# przed "spamowaniem" (edge-trigger + debounce przy szybkim ponownym wciśnięciu).
TAP_DEBOUNCE_S = 0.05

_STATUS_TIMEOUT_MS = 6000


def _plural_steps(n: int) -> str:
    """Polska odmiana liczby kroków: 1 krok, 2-4 kroki, 5+ kroków."""
    if n == 1:
        return "1 krok"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} kroki"
    return f"{n} kroków"


def _format_action(action: dict) -> str:
    """Czytelny opis pojedynczego kroku makra (do listy w GUI)."""
    kind = action.get("type")
    if kind == "tap":
        return f"Tap ({action['x']}, {action['y']})"
    if kind == "swipe":
        return (
            f"Swipe ({action['x1']},{action['y1']}) "
            f"→ ({action['x2']},{action['y2']})"
        )
    if kind == "delay":
        return f"Delay {action.get('ms', 0)} ms"
    return str(action)


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


class MacroRunner(threading.Thread):
    """Odtwarza sekwencję kroków makra w osobnym wątku.

    Dzięki osobnemu wątkowi odtwarzanie (w tym ``delay``) nie blokuje
    pętli zdarzeń PyQt6 ani nasłuchu pynput. Kroki:
        tap:   ``adb.tap(x, y)``
        swipe: ``adb.swipe(x1, y1, x2, y2, duration_ms)``
        delay: pauza przez ``ms`` (przerywalna przez :meth:`stop`)

    Po zakończeniu (lub przerwaniu) wywoływany jest ``finished``.
    """

    def __init__(
        self,
        adb: ADBController,
        actions: list[dict],
        finished: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(daemon=True, name="macro-runner")
        self._adb = adb
        self._actions = list(actions)
        self._finished = finished
        self._stop_event = threading.Event()

    def run(self) -> None:
        try:
            for action in self._actions:
                if self._stop_event.is_set():
                    break
                kind = action.get("type")
                if kind == "delay":
                    # wait(timeout) zamiast sleep - natychmiastowe przerwanie
                    self._stop_event.wait(int(action.get("ms", 0)) / 1000.0)
                elif kind == "tap":
                    self._adb.tap(int(action["x"]), int(action["y"]))
                elif kind == "swipe":
                    self._adb.swipe(
                        int(action["x1"]),
                        int(action["y1"]),
                        int(action["x2"]),
                        int(action["y2"]),
                        int(action.get("duration_ms", 300)),
                    )
        finally:
            if self._finished is not None:
                self._finished()

    def stop(self) -> None:
        """Przerywa odtwarzanie (bezpieczne z dowolnego wątku)."""
        self._stop_event.set()


class MainWindow(QMainWindow):
    """Główne okno aplikacji: stream + panel sterowania + keymapper."""

    _macro_done = pyqtSignal()

    def __init__(self, config_path: str = "keymap.json") -> None:
        super().__init__()
        self.setWindowTitle("ADB-AUTOKLIK — Keymapper")
        self.resize(1240, 760)

        self.config = ConfigManager(config_path)
        self.adb = ADBController()
        self.engine = KeymapperEngine(self)
        self._pending = {
            "key": None,
            "x": None,
            "y": None,
            "x1": None,
            "y1": None,
            "x2": None,
            "y2": None,
        }
        self._macro_steps: list[dict] = []
        self._macro_runner: MacroRunner | None = None

        self._build_ui()
        self._wire_signals()
        self.refresh_devices()
        self._reload_points()
        self._on_mode_changed()  # synchronizuje tryb streamu i podpowiedź

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

        # --- Akcje mapowania ---
        points_box = QGroupBox("Akcje mapowania")
        points_layout = QVBoxLayout(points_box)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Typ", "Nazwa", "Klawisz", "X", "Y"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, header.ResizeMode.Stretch)
        for col in (0, 2, 3, 4):
            header.setSectionResizeMode(col, header.ResizeMode.ResizeToContents)
        self.delete_button = QPushButton("Usuń zaznaczoną akcję")
        self.delete_button.setEnabled(False)
        points_layout.addWidget(self.table, 1)
        points_layout.addWidget(self.delete_button)
        panel_layout.addWidget(points_box, 1)

        # --- Dodawanie akcji ---
        add_box = QGroupBox("Dodaj nową akcję")
        add_layout = QVBoxLayout(add_box)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Typ akcji:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Dodaj kliknięcie (Tap)", "tap")
        self.mode_combo.addItem("Dodaj przesunięcie (Swipe)", "swipe")
        self.mode_combo.addItem("Dodaj makro (sekwencja)", "macro")
        mode_row.addWidget(self.mode_combo, 1)
        add_layout.addLayout(mode_row)

        # Tryb Tap/Swipe: gest na ekranie + klawisz
        self.add_mode_check = QCheckBox("Tryb dodawania (narysuj na ekranie, potem klawisz)")
        self.add_hint = QLabel("Nieaktywny.")
        self.add_hint.setWordWrap(True)
        add_layout.addWidget(self.add_mode_check)
        add_layout.addWidget(self.add_hint)

        # Tryb Macro: edytor kroków (widoczny tylko przy "macro")
        self.macro_record_check = QCheckBox(
            "Nagraj z ekranu (klik = Tap, przeciągnij = Swipe)"
        )
        self.macro_steps_list = QListWidget()
        self.macro_steps_list.setMaximumHeight(160)
        self.macro_delay_row = QWidget()
        delay_layout = QHBoxLayout(self.macro_delay_row)
        delay_layout.setContentsMargins(0, 0, 0, 0)
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(10, 10_000)
        self.delay_spin.setValue(300)
        self.delay_spin.setSuffix(" ms")
        self.delay_button = QPushButton("Dodaj Delay")
        delay_layout.addWidget(QLabel("Delay:"))
        delay_layout.addWidget(self.delay_spin, 1)
        delay_layout.addWidget(self.delay_button)
        self.macro_remove_step_button = QPushButton("Usuń zaznaczony krok")
        add_layout.addWidget(self.macro_record_check)
        add_layout.addWidget(self.macro_steps_list)
        add_layout.addWidget(self.macro_delay_row)
        add_layout.addWidget(self.macro_remove_step_button)

        name_row = QHBoxLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Nazwa akcji")
        self.save_point_button = QPushButton("Zapisz akcję")
        self.save_point_button.setEnabled(False)
        name_row.addWidget(self.name_input, 1)
        name_row.addWidget(self.save_point_button)
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
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.add_mode_check.toggled.connect(self._on_add_mode_toggled)
        self.save_point_button.clicked.connect(self._on_save_point)
        self.name_input.returnPressed.connect(self._on_save_point)
        self.macro_record_check.toggled.connect(self._on_macro_record_toggled)
        self.delay_button.clicked.connect(self._on_add_delay)
        self.macro_remove_step_button.clicked.connect(self._on_remove_macro_step)

        self.keymapper_check.toggled.connect(self._on_keymapper_toggled)
        self.engine.key_pressed.connect(self._on_key_pressed)
        self.engine.key_captured.connect(self._on_key_captured)
        self.engine.error.connect(self._on_engine_error)

        self.stream.point_selected.connect(self._on_screen_clicked)
        self.stream.swipe_selected.connect(self._on_swipe_selected)
        self.stream.stream_started.connect(
            lambda serial: self._status(f"Stream uruchomiony: {serial}")
        )
        self.stream.stream_stopped.connect(self._on_stream_stopped)
        self.stream.stream_error.connect(self._status)

        self._macro_done.connect(lambda: self._status("Makro zakończone."))

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
    # Akcje mapowania
    # ------------------------------------------------------------------

    def _reload_points(self) -> None:
        """Odświeża tabelę akcji i nakładkę na streamie."""
        points = self.config.load_config()
        self.table.setRowCount(0)
        for point in points:
            row = self.table.rowCount()
            self.table.insertRow(row)
            if isinstance(point, MacroPoint):
                typ = "Macro"
                x_label = _plural_steps(len(point.actions))
                y_label = "—"
            elif isinstance(point, SwipePoint):
                typ = "Swipe"
                x_label = f"{point.x1} → {point.x2}"
                y_label = f"{point.y1} → {point.y2}"
            else:
                typ = "Tap"
                x_label, y_label = str(point.x), str(point.y)
            values = (typ, point.name, point.key, x_label, y_label)
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col != 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)
        self.stream.set_overlay_points(points)

    def _on_table_selection(self) -> None:
        self.delete_button.setEnabled(self.table.currentRow() >= 0)

    def _on_delete_point(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        name = self.table.item(row, 1).text()
        self.config.remove_point(name)
        self._reload_points()
        self._status(f"Usunięto akcję: {name}")

    # ------------------------------------------------------------------
    # Dodawanie akcji (gest na ekranie -> klawisz -> nazwa -> zapis)
    # ------------------------------------------------------------------

    def _gesture_kind(self) -> str:
        """Rodzaj akcji wybranej w panelu dodawania: "tap", "swipe" lub "macro"."""
        return str(self.mode_combo.currentData() or "tap")

    def _on_mode_changed(self) -> None:
        kind = self._gesture_kind()
        # Macro używa streamu w trybie "swipe": klik -> Tap, przeciągnięcie -> Swipe
        self.stream.set_gesture_mode("swipe" if kind in ("swipe", "macro") else "tap")
        self._set_macro_editor_visible(kind == "macro")
        self.save_point_button.setText("Zapisz Makro" if kind == "macro" else "Zapisz akcję")
        self._reset_pending()
        self._macro_steps = []
        self._refresh_macro_steps()
        self._update_add_hint()

    def _set_macro_editor_visible(self, visible: bool) -> None:
        """Przełącza widoczność edytora kroków makra vs. trybu Tap/Swipe."""
        self.macro_record_check.setVisible(visible)
        self.macro_steps_list.setVisible(visible)
        self.macro_delay_row.setVisible(visible)
        self.macro_remove_step_button.setVisible(visible)
        self.add_mode_check.setVisible(not visible)
        self.add_hint.setVisible(not visible)

    def _on_add_mode_toggled(self, checked: bool) -> None:
        self.engine.set_add_mode(checked)
        self._reset_pending()
        if checked:
            if self._gesture_kind() == "swipe":
                hint = (
                    "Przeciągnij myszą na ekranie telefonu (start → koniec), "
                    "a potem wciśnij klawisz."
                )
            else:
                hint = "Kliknij lewym przyciskiem na ekranie telefonu, a potem wciśnij klawisz."
            self.add_hint.setText(hint)
        else:
            self.add_hint.setText("Nieaktywny.")
        self._update_engine()

    def _on_macro_record_toggled(self, checked: bool) -> None:
        self.engine.set_add_mode(checked)
        self._update_engine()
        self._update_add_hint()

    def _on_screen_clicked(self, x: int, y: int) -> None:
        kind = self._gesture_kind()
        if kind == "macro":
            if self.macro_record_check.isChecked():
                from config_manager import tap_action

                self._macro_steps.append(tap_action(x, y))
                self._refresh_macro_steps()
                self._update_add_hint()
            return
        if not self.add_mode_check.isChecked() or kind != "tap":
            return
        self._pending["x"], self._pending["y"] = x, y
        self._update_add_hint()

    def _on_swipe_selected(self, x1: int, y1: int, x2: int, y2: int) -> None:
        kind = self._gesture_kind()
        if kind == "macro":
            if self.macro_record_check.isChecked():
                from config_manager import swipe_action

                self._macro_steps.append(swipe_action(x1, y1, x2, y2))
                self._refresh_macro_steps()
                self._update_add_hint()
            return
        if not self.add_mode_check.isChecked() or kind != "swipe":
            return
        self._pending["x1"], self._pending["y1"] = x1, y1
        self._pending["x2"], self._pending["y2"] = x2, y2
        self._update_add_hint()

    def _on_add_delay(self) -> None:
        from config_manager import delay_action

        self._macro_steps.append(delay_action(self.delay_spin.value()))
        self._refresh_macro_steps()
        self._update_add_hint()

    def _on_remove_macro_step(self) -> None:
        row = self.macro_steps_list.currentRow()
        if row < 0 or row >= len(self._macro_steps):
            return
        del self._macro_steps[row]
        self._refresh_macro_steps()
        self._update_add_hint()

    def _refresh_macro_steps(self) -> None:
        """Odświeża listę kroków makra w GUI."""
        self.macro_steps_list.clear()
        for i, action in enumerate(self._macro_steps, start=1):
            self.macro_steps_list.addItem(f"{i}. {_format_action(action)}")

    def _on_key_captured(self, key: str) -> None:
        kind = self._gesture_kind()
        if kind == "macro":
            if not self.macro_record_check.isChecked():
                return
        elif not self.add_mode_check.isChecked():
            return
        self._pending["key"] = key
        self._update_add_hint()

    def _update_add_hint(self) -> None:
        p = self._pending
        if self._gesture_kind() == "macro":
            if self._macro_steps:
                steps = [f"nagrano {_plural_steps(len(self._macro_steps))}"]
            else:
                steps = ["dodaj kroki (klik/przeciągnij na ekranie lub Delay)"]
            gesture_ready = len(self._macro_steps) > 0
        elif self._gesture_kind() == "swipe":
            if p["x1"] is not None:
                steps = [f"start ({p['x1']}, {p['y1']})", f"koniec ({p['x2']}, {p['y2']})"]
            else:
                steps = ["przeciągnij na ekranie (start → koniec)"]
            gesture_ready = all(p[k] is not None for k in ("x1", "y1", "x2", "y2"))
        else:
            if p["x"] is not None:
                steps = [f"X={p['x']}, Y={p['y']}"]
            else:
                steps = ["kliknij na ekranie"]
            gesture_ready = p["x"] is not None and p["y"] is not None
        if p["key"] is not None:
            steps.append(f"klawisz '{p['key']}'")
        else:
            steps.append("wciśnij klawisz")
        self.add_hint.setText(" -> ".join(steps) + ". Podaj nazwę i zapisz.")
        self.save_point_button.setEnabled(gesture_ready and p["key"] is not None)

    def _on_save_point(self) -> None:
        p = self._pending
        name = self.name_input.text().strip()
        if not name:
            self._status("Podaj nazwę akcji.")
            return
        if p["key"] is None:
            self._status("Najpierw wciśnij klawisz.")
            return
        key = p["key"]

        if self._gesture_kind() == "macro":
            if not self._macro_steps:
                self._status("Dodaj co najmniej jeden krok makra.")
                return
            try:
                self.config.add_macro(name, key, self._macro_steps)
            except ValueError as exc:
                self._status(f"Nie zapisano: {exc}")
                return
            count = len(self._macro_steps)
            self._reload_points()
            self.name_input.clear()
            self.macro_record_check.setChecked(False)  # wyłącza nagrywanie
            self._macro_steps = []
            self._refresh_macro_steps()
            self._status(
                f"Zapisano makro '{name}' -> klawisz {key} ({_plural_steps(count)})"
            )
            return

        if self._gesture_kind() == "swipe":
            if any(p[k] is None for k in ("x1", "y1", "x2", "y2")):
                self._status("Najpierw przeciągnij na ekranie (start → koniec).")
                return
            try:
                self.config.add_swipe(name, key, p["x1"], p["y1"], p["x2"], p["y2"])
            except ValueError as exc:
                self._status(f"Nie zapisano: {exc}")
                return
            self._reload_points()
            self.name_input.clear()
            self.add_mode_check.setChecked(False)  # wyłącza tryb i resetuje stan
            self._status(
                f"Zapisano swipe '{name}' -> klawisz {key} "
                f"({p['x1']},{p['y1']}) → ({p['x2']},{p['y2']})"
            )
            return

        # Tap
        if p["x"] is None or p["y"] is None:
            self._status("Najpierw kliknij na ekranie.")
            return
        try:
            self.config.add_point(name, key, p["x"], p["y"])
        except ValueError as exc:
            self._status(f"Nie zapisano: {exc}")
            return
        self._reload_points()
        self.name_input.clear()
        self.add_mode_check.setChecked(False)  # wyłącza tryb i resetuje stan
        self._status(f"Zapisano punkt '{name}' -> klawisz {key} ({p['x']}, {p['y']})")

    def _reset_pending(self) -> None:
        self._pending = {
            "key": None,
            "x": None,
            "y": None,
            "x1": None,
            "y1": None,
            "x2": None,
            "y2": None,
        }
        self.save_point_button.setEnabled(False)

    # ------------------------------------------------------------------
    # Keymapper / Makra
    # ------------------------------------------------------------------

    def _on_keymapper_toggled(self, checked: bool) -> None:
        self._update_engine()
        self._status("Keymapper aktywny." if checked else "Keymapper wyłączony.")

    def _update_engine(self) -> None:
        """Nasłuch działa, gdy włączony jest keymapper LUB dowolny tryb dodawania."""
        should_run = (
            self.keymapper_check.isChecked()
            or self.add_mode_check.isChecked()
            or self.macro_record_check.isChecked()
        )
        if should_run and not self.engine.is_running:
            self.engine.start()
        elif not should_run and self.engine.is_running:
            self.engine.stop()

    def _on_key_pressed(self, key: str) -> None:
        point = self.config.get_point(key)
        if point is None or self.adb.device_serial is None:
            return
        if isinstance(point, MacroPoint):
            self._start_macro(point)
        elif isinstance(point, SwipePoint):
            if not self.adb.swipe(
                point.x1, point.y1, point.x2, point.y2, point.duration_ms
            ):
                self._status(f"Swipe nieudany dla klawisza '{key}'")
        elif not self.adb.tap(point.x, point.y):
            self._status(f"Tap nieudany dla klawisza '{key}'")

    def _start_macro(self, point: MacroPoint) -> None:
        """Uruchamia odtwarzanie makra w osobnym wątku (nie blokuje GUI)."""
        if self.adb.device_serial is None:
            return
        if self._macro_runner is not None and self._macro_runner.is_alive():
            self._status(f"Makro już trwa - pomijam klawisz '{point.key}'")
            return
        self._macro_runner = MacroRunner(
            self.adb, point.actions, finished=self._macro_finished
        )
        self._macro_runner.start()
        self._status(
            f"Odtwarzam makro '{point.name}' ({_plural_steps(len(point.actions))})"
        )

    def _macro_finished(self) -> None:
        """Wywoływany z wątku roboczego - bezpieczne przejście do wątku GUI."""
        self._macro_done.emit()

    def _on_engine_error(self, message: str) -> None:
        self.keymapper_check.setChecked(False)
        self.add_mode_check.setChecked(False)
        self.macro_record_check.setChecked(False)
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
        if self._macro_runner is not None:
            self._macro_runner.stop()
        self.engine.stop()
        self.stream.stop_stream()
        super().closeEvent(event)
