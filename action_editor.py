"""ActionEditor - formularz dodawania i edycji akcji keymapy (Tap/Swipe/Macro).

Widżet zawiera:
- tabelę zapisanych akcji (Typ, Nazwa, Klawisz, X, Y) z przyciskiem usuwania,
- formularz dodawania: wybór typu akcji (Tap/Swipe/Makro), tryb rysowania
  gestu na ekranie (klik / przeciągnięcie) + przechwycenie klawisza, nazwa,
- dla makr: edytor kroków (nagrywanie z ekranu, dodawanie Delay, usuwanie
  kroków, zapis makra) oraz podświetlanie aktualnie wykonywanego kroku
  podczas odtwarzania (patrz :meth:`begin_macro_preview`).

Komunikacja z resztą aplikacji odbywa się sygnałami Qt:
    mode_changed(str)           - zmieniono typ akcji ("tap"|"swipe"|"macro"),
    capture_mode_changed(bool)  - włączono/wyłączono przechwytywanie gestów,
    points_changed()            - zmieniono zestaw akcji (dodano/usunięto),
    status_message(str)         - komunikat do paska statusu.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config_manager import (
    ConfigManager,
    MacroPoint,
    SwipePoint,
    delay_action,
    swipe_action,
    tap_action,
)

# Podświetlenie aktualnie wykonywanego kroku makra (podgląd odtwarzania).
_ACTIVE_STEP_BG = QColor(30, 120, 210, 110)
_ACTIVE_STEP_FG = QColor(255, 255, 255)
_INACTIVE_STEP_FG = QColor(215, 222, 230)


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


class ActionEditor(QWidget):
    """Panel akcji mapowania: tabela + formularz dodawania (patrz opis modułu)."""

    mode_changed = pyqtSignal(str)
    capture_mode_changed = pyqtSignal(bool)
    points_changed = pyqtSignal()
    status_message = pyqtSignal(str)

    def __init__(self, config: ConfigManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self._pending: dict[str, int | str | None] = {
            "key": None,
            "x": None,
            "y": None,
            "x1": None,
            "y1": None,
            "x2": None,
            "y2": None,
        }
        self._macro_steps: list[dict] = []
        self._preview_actions: list[dict] | None = None  # podgląd odtwarzanego makra

        self._build_ui()
        self._wire()
        self.reload_table()
        self._on_mode_changed()  # synchronizuje widoczność edytora makra

    # ------------------------------------------------------------------
    # Budowa UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

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
        root.addWidget(points_box, 1)

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
        root.addWidget(add_box)

    def _wire(self) -> None:
        self.table.itemSelectionChanged.connect(self._on_table_selection)
        self.delete_button.clicked.connect(self._on_delete_point)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.add_mode_check.toggled.connect(self._on_add_mode_toggled)
        self.macro_record_check.toggled.connect(self._on_macro_record_toggled)
        self.save_point_button.clicked.connect(self._on_save_point)
        self.name_input.returnPressed.connect(self._on_save_point)
        self.delay_button.clicked.connect(self._on_add_delay)
        self.macro_remove_step_button.clicked.connect(self._on_remove_macro_step)

    # ------------------------------------------------------------------
    # Tabela akcji
    # ------------------------------------------------------------------

    def reload_table(self) -> None:
        """Odświeża tabelę zapisanych akcji z aktualnej konfiguracji."""
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

    def _on_table_selection(self) -> None:
        self.delete_button.setEnabled(self.table.currentRow() >= 0)

    def _on_delete_point(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        name = self.table.item(row, 1).text()
        if self.config.remove_point(name):
            self.reload_table()
            self.points_changed.emit()
            self.status_message.emit(f"Usunięto akcję: {name}")

    # ------------------------------------------------------------------
    # Tryb dodawania (gest na ekranie -> klawisz -> nazwa -> zapis)
    # ------------------------------------------------------------------

    def _gesture_kind(self) -> str:
        """Rodzaj akcji wybranej w panelu dodawania: "tap", "swipe" lub "macro"."""
        return str(self.mode_combo.currentData() or "tap")

    def is_capture_active(self) -> bool:
        """``True``, gdy aktywny jest jakikolwiek tryb przechwytywania gestów."""
        return self.add_mode_check.isChecked() or self.macro_record_check.isChecked()

    def _on_mode_changed(self) -> None:
        kind = self._gesture_kind()
        self._set_macro_editor_visible(kind == "macro")
        self.save_point_button.setText("Zapisz Makro" if kind == "macro" else "Zapisz akcję")
        self._reset_pending()
        self._macro_steps = []
        self._refresh_macro_steps()
        self._update_add_hint()
        self.mode_changed.emit(kind)

    def _set_macro_editor_visible(self, visible: bool) -> None:
        """Przełącza widoczność edytora kroków makra vs. trybu Tap/Swipe."""
        self.macro_record_check.setVisible(visible)
        self.macro_steps_list.setVisible(visible)
        self.macro_delay_row.setVisible(visible)
        self.macro_remove_step_button.setVisible(visible)
        self.add_mode_check.setVisible(not visible)
        self.add_hint.setVisible(not visible)

    def _on_add_mode_toggled(self, checked: bool) -> None:
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
        self.capture_mode_changed.emit(self.is_capture_active())
        self._update_add_hint()

    def _on_macro_record_toggled(self, checked: bool) -> None:
        self.capture_mode_changed.emit(self.is_capture_active())
        self._update_add_hint()

    # ------------------------------------------------------------------
    # Slot y od streamu i keymappera (wywoływane z main_window)
    # ------------------------------------------------------------------

    def on_screen_clicked(self, x: int, y: int) -> None:
        kind = self._gesture_kind()
        if kind == "macro":
            if self.macro_record_check.isChecked():
                self._macro_steps.append(tap_action(x, y))
                self._refresh_macro_steps()
                self._update_add_hint()
            return
        if not self.add_mode_check.isChecked() or kind != "tap":
            return
        self._pending["x"], self._pending["y"] = x, y
        self._update_add_hint()

    def on_swipe_selected(self, x1: int, y1: int, x2: int, y2: int) -> None:
        kind = self._gesture_kind()
        if kind == "macro":
            if self.macro_record_check.isChecked():
                self._macro_steps.append(swipe_action(x1, y1, x2, y2))
                self._refresh_macro_steps()
                self._update_add_hint()
            return
        if not self.add_mode_check.isChecked() or kind != "swipe":
            return
        self._pending["x1"], self._pending["y1"] = x1, y1
        self._pending["x2"], self._pending["y2"] = x2, y2
        self._update_add_hint()

    def on_key_captured(self, key: str) -> None:
        kind = self._gesture_kind()
        if kind == "macro":
            if not self.macro_record_check.isChecked():
                return
        elif not self.add_mode_check.isChecked():
            return
        self._pending["key"] = key
        self._update_add_hint()

    def disable_capture(self) -> None:
        """Wyłącza tryby przechwytywania (np. po błędzie keymappera)."""
        self.add_mode_check.setChecked(False)
        self.macro_record_check.setChecked(False)

    # ------------------------------------------------------------------
    # Kroki makra
    # ------------------------------------------------------------------

    def _on_add_delay(self) -> None:
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
        """Odświeża listę kroków (kompozycja LUB podgląd odtwarzanego makra)."""
        self.macro_steps_list.clear()
        steps = (
            self._preview_actions
            if self._preview_actions is not None
            else self._macro_steps
        )
        for i, action in enumerate(steps, start=1):
            item = QListWidgetItem(f"{i}. {_format_action(action)}")
            item.setForeground(_INACTIVE_STEP_FG)
            self.macro_steps_list.addItem(item)

    # ------------------------------------------------------------------
    # Podgląd odtwarzanego makra (sygnały z MacroRunner)
    # ------------------------------------------------------------------

    def begin_macro_preview(self, actions: list[dict]) -> None:
        """Pokazuje kroki odtwarzanego makra na liście edytora (podgląd)."""
        self._preview_actions = list(actions)
        self._refresh_macro_steps()
        self.set_current_macro_step(0)

    def set_current_macro_step(self, index: int | None) -> None:
        """Podświetla aktualnie wykonywany krok (0-based); ``None`` czyści."""
        for i in range(self.macro_steps_list.count()):
            item = self.macro_steps_list.item(i)
            active = index is not None and i == index
            item.setBackground(_ACTIVE_STEP_BG if active else QColor(0, 0, 0, 0))
            item.setForeground(_ACTIVE_STEP_FG if active else _INACTIVE_STEP_FG)
            if active:
                self.macro_steps_list.scrollToItem(item)

    def end_macro_preview(self) -> None:
        """Przywraca widok kompozycji kroków po zakończeniu odtwarzania."""
        self._preview_actions = None
        self._refresh_macro_steps()
        self.set_current_macro_step(None)

    # ------------------------------------------------------------------
    # Zapisywanie akcji
    # ------------------------------------------------------------------

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
            self.status_message.emit("Podaj nazwę akcji.")
            return
        if p["key"] is None:
            self.status_message.emit("Najpierw wciśnij klawisz.")
            return
        key = p["key"]

        if self._gesture_kind() == "macro":
            if not self._macro_steps:
                self.status_message.emit("Dodaj co najmniej jeden krok makra.")
                return
            try:
                self.config.add_macro(name, key, self._macro_steps)
            except ValueError as exc:
                self.status_message.emit(f"Nie zapisano: {exc}")
                return
            count = len(self._macro_steps)
            self.reload_table()
            self.name_input.clear()
            self.macro_record_check.setChecked(False)  # wyłącza nagrywanie
            self._macro_steps = []
            self._refresh_macro_steps()
            self.points_changed.emit()
            self.status_message.emit(
                f"Zapisano makro '{name}' -> klawisz {key} ({_plural_steps(count)})"
            )
            return

        if self._gesture_kind() == "swipe":
            if any(p[k] is None for k in ("x1", "y1", "x2", "y2")):
                self.status_message.emit("Najpierw przeciągnij na ekranie (start → koniec).")
                return
            try:
                self.config.add_swipe(name, key, p["x1"], p["y1"], p["x2"], p["y2"])
            except ValueError as exc:
                self.status_message.emit(f"Nie zapisano: {exc}")
                return
            self.reload_table()
            self.name_input.clear()
            self.add_mode_check.setChecked(False)  # wyłącza tryb i resetuje stan
            self.points_changed.emit()
            self.status_message.emit(
                f"Zapisano swipe '{name}' -> klawisz {key} "
                f"({p['x1']},{p['y1']}) → ({p['x2']},{p['y2']})"
            )
            return

        # Tap
        if p["x"] is None or p["y"] is None:
            self.status_message.emit("Najpierw kliknij na ekranie.")
            return
        try:
            self.config.add_point(name, key, p["x"], p["y"])
        except ValueError as exc:
            self.status_message.emit(f"Nie zapisano: {exc}")
            return
        self.reload_table()
        self.name_input.clear()
        self.add_mode_check.setChecked(False)  # wyłącza tryb i resetuje stan
        self.points_changed.emit()
        self.status_message.emit(f"Zapisano punkt '{name}' -> klawisz {key} ({p['x']}, {p['y']})")

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
