"""ActionEditor - formularz dodawania i edycji akcji keymapy (Tap/Swipe/Macro).

Widżet zawiera:
- tabelę zapisanych akcji (Typ, Nazwa, Klawisz, X, Y) z przyciskiem usuwania,
- formularz dodawania: wybór typu akcji (Tap/Swipe/Makro), tryb rysowania
  gestu na ekranie (klik / przeciągnięcie), osobne pola ``key_input``
  (klawisz - ustawiany automatycznie przez pynput LUB ręcznie) i
  ``name_input`` (nazwa akcji),
- dla makr: edytor kroków (nagrywanie z ekranu, dodawanie Delay, usuwanie
  kroków, zapis makra) oraz podświetlanie aktualnie wykonywanego kroku
  podczas odtwarzania (patrz :meth:`begin_macro_preview`),
- edycję kroków istniejącego makra: wybór wiersza Macro w tabeli wczytuje
  kroki do listy, kliknięcie kroku aktywuje tryb "Edycja kroku" (zapis
  zmiany / zastąpienie pozycji gestem z ekranu / drag & drop na nakładce),
  a przycisk "Usuń zaznaczony krok" usuwa krok z makra i zapisuje config.

Komunikacja z resztą aplikacji odbywa się sygnałami Qt:
    mode_changed(str)           - zmieniono typ akcji ("tap"|"swipe"|"macro"),
    capture_mode_changed(bool)  - włączono/wyłączono przechwytywanie gestów,
    points_changed()            - zmieniono zestaw akcji (dodano/usunięto),
    status_message(str)         - komunikat do paska statusu,
    macro_edit_changed(str|None, int|None) - edytowany krok makra (dla nakładki),
    screen_gesture_capture(bool) - przechwytywanie TYLKO streamu (bez pynput).
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
    KeyPoint,
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
# Podświetlenie kroku edytowanego (tryb "Edycja kroku").
_EDIT_STEP_BG = QColor(230, 120, 20, 90)


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
    macro_edit_changed = pyqtSignal(object, object)  # (nazwa makra, indeks kroku)
    screen_gesture_capture = pyqtSignal(bool)  # przechwytywanie TYLKO streamu

    def __init__(self, config: ConfigManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        # Współrzędne gestu wybranego na ekranie (klawisz żyje w ``key_input``).
        self._pending: dict[str, int | None] = {
            "x": None,
            "y": None,
            "x1": None,
            "y1": None,
            "x2": None,
            "y2": None,
        }
        self._macro_steps: list[dict] = []
        self._preview_actions: list[dict] | None = None  # podgląd odtwarzanego makra
        # Tryb edycji kroków istniejącego makra (wybranego w tabeli)
        self._editing_macro_name: str | None = None
        self._editing_step: int | None = None
        self._replace_step_mode = False  # następny gest na ekranie zastępuje krok

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
        # Zwłoka przed kolejnym powtórzeniem tapu (widoczna tylko dla
        # zaznaczonego tapu w trybie Powtarzanie).
        self.repeat_delay_row = QWidget()
        repeat_layout = QHBoxLayout(self.repeat_delay_row)
        repeat_layout.setContentsMargins(0, 0, 0, 0)
        repeat_layout.addWidget(QLabel("🔁 Zwłoka powtórzenia:"))
        self.repeat_delay_spin = QSpinBox()
        self.repeat_delay_spin.setRange(0, 60_000)
        self.repeat_delay_spin.setValue(500)
        self.repeat_delay_spin.setSuffix(" ms")
        self.repeat_delay_spin.setToolTip(
            "Zwłoka przed kolejnym tapnięciem w trybie Powtarzanie "
            "(0 = domyślna 500 ms)"
        )
        self.repeat_delay_apply_button = QPushButton("Zastosuj")
        repeat_layout.addWidget(self.repeat_delay_spin, 1)
        repeat_layout.addWidget(self.repeat_delay_apply_button)
        self.repeat_delay_row.setVisible(False)
        points_layout.addWidget(self.repeat_delay_row)
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

        # Tryb edycji kroków istniejącego makra (widoczny po zaznaczeniu kroku)
        self.macro_edit_label = QLabel("")
        self.macro_edit_label.setWordWrap(True)
        self.macro_edit_label.setStyleSheet("color: #ffb84d; font-size: 12px;")
        self.step_edit_row = QWidget()
        step_row_layout = QHBoxLayout(self.step_edit_row)
        step_row_layout.setContentsMargins(0, 0, 0, 0)
        self.step_save_button = QPushButton("Zapisz zmianę w kroku")
        self.step_replace_button = QPushButton("Zastąp pozycję (kliknij na ekranie)")
        step_row_layout.addWidget(self.step_save_button)
        step_row_layout.addWidget(self.step_replace_button)
        add_layout.addWidget(self.macro_edit_label)
        add_layout.addWidget(self.step_edit_row)

        # Klawisz: ustawiany automatycznie po przechwyceniu przez pynput,
        # ale użytkownik może go też wpisać/zmienić ręcznie.
        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("Klawisz:"))
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("np. a, space, shift")
        key_row.addWidget(self.key_input, 1)
        add_layout.addLayout(key_row)

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
        self.key_input.textChanged.connect(self._update_save_button_state)
        self.name_input.textChanged.connect(self._update_save_button_state)
        self.delay_button.clicked.connect(self._on_add_delay)
        self.macro_remove_step_button.clicked.connect(self._on_remove_macro_step)
        self.macro_steps_list.itemClicked.connect(self._on_macro_step_clicked)
        self.step_save_button.clicked.connect(self._on_step_save)
        self.step_replace_button.clicked.connect(self._on_step_replace)
        self.repeat_delay_apply_button.clicked.connect(self._on_repeat_delay_apply)

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
        row = self.table.currentRow()
        if row < 0:
            self._stop_editing_macro()
            self._show_repeat_delay_row(None)
            return
        name = self.table.item(row, 1).text()
        point = self.config.get_point(name)
        if isinstance(point, MacroPoint):
            self._start_editing_macro(point.name)
        else:
            self._stop_editing_macro()
        # Tylko tap ma zwłokę powtórzenia - pokaż edytor tylko dla niego.
        self._show_repeat_delay_row(point if isinstance(point, KeyPoint) else None)

    def _show_repeat_delay_row(self, point: KeyPoint | None) -> None:
        """Pokazuje edytor zwłoki powtórzenia tylko dla zaznaczonego tapu."""
        if point is None:
            self.repeat_delay_row.setVisible(False)
            return
        self.repeat_delay_spin.setValue(max(0, point.repeat_delay_ms))
        self.repeat_delay_row.setVisible(True)

    def _on_repeat_delay_apply(self) -> None:
        """Zapisuje zwłokę przed kolejnym powtórzeniem dla zaznaczonego tapu."""
        row = self.table.currentRow()
        if row < 0:
            return
        name = self.table.item(row, 1).text()
        point = self.config.get_point(name)
        if not isinstance(point, KeyPoint):
            self.status_message.emit("Zwłoka powtórzenia dotyczy tylko akcji Tap.")
            return
        point.repeat_delay_ms = self.repeat_delay_spin.value()
        self.config.save_config()
        self.status_message.emit(
            f"Ustawiono zwłokę powtórzenia '{point.name}': "
            f"{point.repeat_delay_ms} ms"
        )

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
        if kind != "macro":
            self._stop_editing_macro()
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
            self._handle_step_replace("tap", x, y)
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
            self._handle_step_replace("swipe", x1, y1, x2, y2)
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
        # Pisanie w polach tekstowych nie nadpisuje pola klawisza znakami,
        # które użytkownik wpisuje do nazwy (lub ręcznie do pola klawisza).
        if self.key_input.hasFocus() or self.name_input.hasFocus():
            return
        self.key_input.setText(key)
        self.name_input.setFocus()  # od razu przejdź do wpisania nazwy
        self._update_add_hint()

    def disable_capture(self) -> None:
        """Wyłącza tryby przechwytywania (np. po błędzie keymappera)."""
        self.add_mode_check.setChecked(False)
        self.macro_record_check.setChecked(False)

    def enable_capture(self) -> None:
        """Włącza przechwytywanie gestów (tryb Mapowania akcji).

        Zaznacza właściwy przełącznik w zależności od wybranego typu akcji:
        ``add_mode_check`` dla Tap/Swipe, ``macro_record_check`` dla Makra.
        Zmiana stanu checkboxa emituje ``capture_mode_changed``, więc
        stream i keymapper zsynchronizują się automatycznie.
        """
        if self._gesture_kind() == "macro":
            self.add_mode_check.setChecked(False)  # tylko jeden tryb naraz
            if not self.macro_record_check.isChecked():
                self.macro_record_check.setChecked(True)
        else:
            self.macro_record_check.setChecked(False)
            if not self.add_mode_check.isChecked():
                self.add_mode_check.setChecked(True)

    # ------------------------------------------------------------------
    # Kroki makra (kompozycja / edycja)
    # ------------------------------------------------------------------

    def _on_add_delay(self) -> None:
        self._macro_steps.append(delay_action(self.delay_spin.value()))
        self._refresh_macro_steps()
        self._update_add_hint()

    def _on_remove_macro_step(self) -> None:
        """Usuwa zaznaczony krok: z kompozycji nowego makra LUB z edytowanego makra."""
        row = self.macro_steps_list.currentRow()
        if row < 0:
            self.status_message.emit("Zaznacz krok na liście.")
            return
        if self._editing_macro_name:
            self._remove_editing_macro_step(row)
            return
        if row >= len(self._macro_steps):
            return
        del self._macro_steps[row]
        self._refresh_macro_steps()
        self._update_add_hint()

    def _remove_editing_macro_step(self, index: int) -> None:
        """Usuwa krok z edytowanego makra, zapisuje config i odświeża nakładkę."""
        macro = self._editing_macro()
        if macro is None:
            return
        if index < 0 or index >= len(macro.actions):
            return
        del macro.actions[index]
        self.config.save_config()
        # Poprawne zaznaczenie po usunięciu: ten sam indeks albo ostatni krok.
        if macro.actions:
            self._editing_step = min(index, len(macro.actions) - 1)
        else:
            self._editing_step = None  # makro bez kroków - wyczyść zaznaczenie
        self._replace_step_mode = False
        self.screen_gesture_capture.emit(False)
        self._refresh_macro_steps()
        if self._editing_step is not None:
            self.macro_steps_list.setCurrentRow(self._editing_step)
        self._emit_macro_edit()      # wyróżnienie na nakładce (aktualny krok)
        self.points_changed.emit()   # przeładowanie nakładki - krok znika na żywo
        self.status_message.emit(f"Usunięto krok {index + 1} makra '{macro.name}'.")

    def _steps_source(self) -> list[dict]:
        """Kroki do wyświetlenia: edytowane makro > podgląd odtwarzania > kompozycja."""
        if self._editing_macro_name:
            macro = self._editing_macro()
            return macro.actions if macro else []
        if self._preview_actions is not None:
            return self._preview_actions
        return self._macro_steps

    def _refresh_macro_steps(self) -> None:
        """Odświeża listę kroków (kompozycja / podgląd odtwarzania / edycja makra)."""
        self.macro_steps_list.clear()
        for i, action in enumerate(self._steps_source(), start=1):
            item = QListWidgetItem(f"{i}. {_format_action(action)}")
            item.setForeground(_INACTIVE_STEP_FG)
            self.macro_steps_list.addItem(item)
        self._update_edit_ui()

    def reload_macro_steps(self) -> None:
        """Publiczne odświeżenie listy kroków (np. po drag & drop kroku na nakładce)."""
        self._refresh_macro_steps()

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
    # Edycja kroków istniejącego makra
    # ------------------------------------------------------------------

    def _editing_macro(self) -> MacroPoint | None:
        """Aktualny (żywy) obiekt edytowanego makra z konfiguracji."""
        if not self._editing_macro_name:
            return None
        point = self.config.get_point(self._editing_macro_name)
        return point if isinstance(point, MacroPoint) else None

    def _start_editing_macro(self, name: str) -> None:
        """Wczytuje kroki makra do listy i wchodzi w tryb edycji kroków."""
        point = self.config.get_point(name)
        if not isinstance(point, MacroPoint):
            return
        self._editing_macro_name = name
        self._editing_step = None
        self._replace_step_mode = False
        # Pokaż edytor kroków (przełącz tryb na "macro")
        if self._gesture_kind() != "macro":
            index = self.mode_combo.findData("macro")
            if index >= 0:
                self.mode_combo.setCurrentIndex(index)
        # Wyłącz nagrywanie/przechwytywanie nowych gestów w trybie edycji
        self.add_mode_check.setChecked(False)
        self.macro_record_check.setChecked(False)
        self._refresh_macro_steps()
        self._emit_macro_edit()

    def _stop_editing_macro(self) -> None:
        if self._editing_macro_name is None and self._editing_step is None:
            return
        was_armed = self._replace_step_mode
        self._editing_macro_name = None
        self._editing_step = None
        self._replace_step_mode = False
        if was_armed:
            self.screen_gesture_capture.emit(False)
        self._refresh_macro_steps()
        self._emit_macro_edit()

    def _on_macro_step_clicked(self, item: QListWidgetItem) -> None:
        """Kliknięcie kroku w liście aktywuje tryb "Edycja kroku"."""
        if self._editing_macro_name is None:
            return
        self._editing_step = self.macro_steps_list.row(item)
        self._replace_step_mode = False
        self.screen_gesture_capture.emit(False)
        self._update_edit_ui()
        self._emit_macro_edit()

    def _update_edit_ui(self) -> None:
        """Aktualizuje widok trybu edycji: przyciski, etykieta, podświetlenie kroku."""
        editing = self._editing_macro_name is not None
        self.macro_edit_label.setVisible(editing)
        self.step_edit_row.setVisible(editing)
        self.macro_record_check.setEnabled(not editing)
        macro = self._editing_macro() if editing else None
        if editing and macro is not None:
            total = len(macro.actions)
            if self._editing_step is None:
                self.macro_edit_label.setText(
                    f"Edycja makra: {self._editing_macro_name} — kliknij krok na liście."
                )
            else:
                self.macro_edit_label.setText(
                    f"Edycja: {self._editing_macro_name}, "
                    f"krok {self._editing_step + 1}/{total}."
                )
            self.step_replace_button.setText(
                "Zastąp pozycję (kliknij na ekranie)… aktywne"
                if self._replace_step_mode
                else "Zastąp pozycję (kliknij na ekranie)"
            )
        for i in range(self.macro_steps_list.count()):
            item = self.macro_steps_list.item(i)
            active = editing and i == self._editing_step
            item.setBackground(_EDIT_STEP_BG if active else QColor(0, 0, 0, 0))

    def _emit_macro_edit(self) -> None:
        self.macro_edit_changed.emit(self._editing_macro_name, self._editing_step)

    def update_step(self, index: int, new_action: dict) -> None:
        """Podmienia krok ``index`` w edytowanym makrze i zapisuje konfigurację."""
        macro = self._editing_macro()
        if macro is None:
            raise ValueError("Brak edytowanego makra")
        if index < 0 or index >= len(macro.actions):
            raise IndexError(f"Niepoprawny indeks kroku: {index}")
        macro.actions[index] = dict(new_action)
        self.config.save_config()
        self._refresh_macro_steps()
        self.status_message.emit(
            f"Zaktualizowano krok {index + 1} makra '{macro.name}'."
        )

    def _on_step_save(self) -> None:
        """Zatwierdza zmiany edytowanego kroku (zapis + koniec edycji kroku)."""
        macro = self._editing_macro()
        if macro is None:
            return
        self.config.save_config()
        self._editing_step = None
        self._replace_step_mode = False
        self.screen_gesture_capture.emit(False)
        self._refresh_macro_steps()
        self._emit_macro_edit()
        self.status_message.emit(f"Zapisano zmiany w makrze '{macro.name}'.")

    def _on_step_replace(self) -> None:
        """Uzbraja tryb: następny gest na ekranie zastąpi pozycję wybranego kroku."""
        macro = self._editing_macro()
        if macro is None:
            return
        if self._editing_step is None or self._editing_step >= len(macro.actions):
            self.status_message.emit("Najpierw zaznacz krok na liście.")
            return
        action = macro.actions[self._editing_step]
        if action.get("type") == "delay":
            self.status_message.emit("Krok Delay nie ma pozycji do zastąpienia.")
            return
        self._replace_step_mode = True
        self.screen_gesture_capture.emit(True)  # tylko stream, bez pynput
        self._update_edit_ui()
        self.status_message.emit(
            "Kliknij (Tap) lub przeciągnij (Swipe) na ekranie, aby zastąpić pozycję kroku."
        )

    def _handle_step_replace(self, kind: str, *coords: int) -> bool:
        """Podmienia pozycję edytowanego kroku gestem z ekranu (True = obsłużono)."""
        macro = self._editing_macro()
        if macro is None or self._editing_step is None or not self._replace_step_mode:
            return False
        if self._editing_step >= len(macro.actions):
            return False
        action = macro.actions[self._editing_step]
        if action.get("type") != kind:
            self.status_message.emit(
                "Typ kroku nie pasuje do gestu — kliknij dla Tap, przeciągnij dla Swipe."
            )
            return True
        if kind == "tap":
            action.update(tap_action(coords[0], coords[1]))
        else:  # swipe
            action.update(swipe_action(coords[0], coords[1], coords[2], coords[3]))
        self.config.save_config()
        self._replace_step_mode = False
        self.screen_gesture_capture.emit(False)
        self._refresh_macro_steps()
        self.status_message.emit(
            f"Zastąpiono pozycję kroku {self._editing_step + 1} makra '{macro.name}'."
        )
        return True

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
        elif self._gesture_kind() == "swipe":
            if p["x1"] is not None:
                steps = [f"start ({p['x1']}, {p['y1']})", f"koniec ({p['x2']}, {p['y2']})"]
            else:
                steps = ["przeciągnij na ekranie (start → koniec)"]
        else:
            if p["x"] is not None:
                steps = [f"X={p['x']}, Y={p['y']}"]
            else:
                steps = ["kliknij na ekranie"]
        key = self.key_input.text().strip()
        if key:
            steps.append(f"klawisz '{key}'")
        else:
            steps.append("wciśnij klawisz lub wpisz go")
        self.add_hint.setText(" -> ".join(steps) + ". Podaj nazwę i zapisz.")
        self._update_save_button_state()

    def _gesture_ready(self) -> bool:
        """Czy wybrano gest/koordynaty na ekranie (zależnie od typu akcji)."""
        p = self._pending
        if self._gesture_kind() == "macro":
            return len(self._macro_steps) > 0
        if self._gesture_kind() == "swipe":
            return all(p[k] is not None for k in ("x1", "y1", "x2", "y2"))
        return p["x"] is not None and p["y"] is not None

    def _update_save_button_state(self) -> None:
        """Przycisk zapisu aktywny, gdy: gest wybrany + klawisz + nazwa.

        Podpięty do ``textChanged`` obu pól, więc odblokowuje się
        dynamicznie podczas pisania (również ręcznie wpisanego klawisza).
        """
        ready = (
            self._gesture_ready()
            and bool(self.key_input.text().strip())
            and bool(self.name_input.text().strip())
        )
        self.save_point_button.setEnabled(ready)

    def _on_save_point(self) -> None:
        p = self._pending
        name = self.name_input.text().strip()
        if not name:
            self.status_message.emit("Podaj nazwę akcji.")
            return
        key = self.key_input.text().strip().lower()
        if not key:
            self.status_message.emit("Podaj klawisz (np. 'a', 'space').")
            return

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
            self.key_input.clear()
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
            self.key_input.clear()
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
        self.key_input.clear()
        self.add_mode_check.setChecked(False)  # wyłącza tryb i resetuje stan
        self.points_changed.emit()
        self.status_message.emit(f"Zapisano punkt '{name}' -> klawisz {key} ({p['x']}, {p['y']})")

    def _reset_pending(self) -> None:
        self._pending = {
            "x": None,
            "y": None,
            "x1": None,
            "y1": None,
            "x2": None,
            "y2": None,
        }
        self.key_input.clear()
        self._update_save_button_state()
