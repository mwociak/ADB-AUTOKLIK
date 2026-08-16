"""AdKillerConfigDialog - konfiguracja AI Ad Killera (model YOLOv11/ONNX).

Osobne okno (QDialog) z:
- wyborem pliku modelu ONNX (``models/ad_detector.onnx`` domyślnie),
- suwakiem czułości (confidence threshold, 50-99 %),
- polem interwału skanowania (ms),
- informacją o załadowanych klasach (close/skip/dismiss).

Zmiany ustawień emituje sygnałem ``settings_changed(float, int)``
(threshold, interval_ms), zmianę modelu - ``model_changed(str)``.
Dialog nie wie nic o workerkach - łączenie sygnałów robi main_window.
"""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

from ad_killer_module import default_model_path, load_class_names


class AdKillerConfigDialog(QDialog):
    """Okno konfiguracji AI Ad Killer (model ONNX, czułość, interwał)."""

    settings_changed = pyqtSignal(float, int)  # (threshold, interval_ms)
    model_changed = pyqtSignal(str)  # wybrano nowy plik modelu ONNX

    def __init__(self, model_path: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("🛡️ Ad Killer - konfiguracja (AI)")
        self.setMinimumWidth(420)
        self._model_path = model_path or default_model_path()
        self._build_ui()
        self._update_model_status()

    # ------------------------------------------------------------------
    # Budowa UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Model AI (YOLOv11 / ONNX):"))
        model_row = QHBoxLayout()
        self.model_edit = QLineEdit(self._model_path)
        self.model_edit.setPlaceholderText("ścieżka do ad_detector.onnx")
        self.browse_button = QPushButton("📂 Przeglądaj…")
        self.browse_button.setToolTip("Wybierz plik modelu .onnx (eksport z Ultralytics)")
        model_row.addWidget(self.model_edit, 1)
        model_row.addWidget(self.browse_button)
        layout.addLayout(model_row)

        self.model_status = QLabel("")
        self.model_status.setWordWrap(True)
        layout.addWidget(self.model_status)

        layout.addWidget(
            QLabel(
                "Model wykrywa przyciski zamykania reklam (klasy: close, "
                "skip, dismiss) - środek bounding boxa jest klikany przez ADB. "
                "Eksport np.: yolo export model=ad_detector.pt format=onnx imgsz=640"
            )
        )

        thresh_row = QHBoxLayout()
        thresh_row.addWidget(QLabel("Czułość (confidence):"))
        self.threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.threshold_slider.setRange(50, 99)
        self.threshold_slider.setValue(70)
        self.threshold_label = QLabel("70%")
        self.threshold_label.setMinimumWidth(44)
        thresh_row.addWidget(self.threshold_slider, 1)
        thresh_row.addWidget(self.threshold_label)
        layout.addLayout(thresh_row)

        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel("Interwał skanowania:"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(200, 30_000)
        self.interval_spin.setValue(1500)
        self.interval_spin.setSuffix(" ms")
        interval_row.addWidget(self.interval_spin, 1)
        layout.addLayout(interval_row)

        self.model_edit.editingFinished.connect(self._on_model_edited)
        self.browse_button.clicked.connect(self._on_browse)
        self.threshold_slider.valueChanged.connect(self._on_settings_changed)
        self.interval_spin.valueChanged.connect(self._on_settings_changed)

    # ------------------------------------------------------------------
    # Ustawienia
    # ------------------------------------------------------------------

    @property
    def threshold(self) -> float:
        """Próg pewności detekcji (0.0-1.0) z suwaka."""
        return self.threshold_slider.value() / 100.0

    @property
    def interval_ms(self) -> int:
        """Interwał skanowania w milisekundach."""
        return self.interval_spin.value()

    @property
    def model_path(self) -> str:
        """Aktualna ścieżka pliku modelu ONNX."""
        return self._model_path

    def _on_settings_changed(self) -> None:
        self.threshold_label.setText(f"{self.threshold_slider.value()}%")
        self.settings_changed.emit(self.threshold, self.interval_ms)

    # ------------------------------------------------------------------
    # Wybór modelu
    # ------------------------------------------------------------------

    def _on_browse(self) -> None:
        """Wybierz plik modelu ONNX przez QFileDialog."""
        start_dir = os.path.dirname(self._model_path) or os.getcwd()
        path, _selected = QFileDialog.getOpenFileName(
            self,
            "Wybierz model ONNX (YOLOv11 ad detector)",
            start_dir,
            "Model ONNX (*.onnx);;Wszystkie pliki (*)",
        )
        if not path:
            return
        self.model_edit.setText(path)
        self._apply_model_path(path)

    def _on_model_edited(self) -> None:
        """Ścieżka wpisana ręcznie w polu tekstowym."""
        path = self.model_edit.text().strip()
        if not path:
            return
        self._apply_model_path(path)

    def _apply_model_path(self, path: str) -> None:
        """Akceptuje nową ścieżkę modelu, aktualizuje status i emituje sygnał."""
        if not os.path.isfile(path):
            QMessageBox.warning(
                self,
                "Brak pliku",
                f"Nie znaleziono pliku modelu:\n{path}",
            )
            return
        self._model_path = os.path.abspath(path)
        self._update_model_status()
        self.model_changed.emit(self._model_path)

    def _update_model_status(self) -> None:
        """Pokazuje stan wybranego pliku modelu (istnieje, klasy z .names)."""
        path = self._model_path
        if not os.path.isfile(path):
            self.model_status.setText(
                f"⚠️ Brak pliku modelu: {path}\n"
                "Umieść ad_detector.onnx w katalogu models/ (obok .exe w "
                "wersji spakowanej) i wybierz go powyżej."
            )
            return
        names = load_class_names(path)
        if names:
            info = f"klasy: {', '.join(names)}"
        else:
            info = "klasy: close, skip, dismiss (domyślne - brak pliku .names)"
        size_mb = os.path.getsize(path) / (1024 * 1024)
        self.model_status.setText(
            f"✅ Model: {os.path.basename(path)} "
            f"({size_mb:.1f} MB)\n{info}"
        )
