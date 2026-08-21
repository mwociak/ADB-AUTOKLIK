"""AdKillerConfigDialog - konfiguracja AI Ad Killera (model YOLOv11/ONNX).

Osobne okno (QDialog) z:
- wyborem pliku modelu ONNX (``models/ad_detector.onnx`` domyślnie),
- suwakiem czułości (confidence threshold, 50-99 %),
- polem interwału skanowania (ms),
- informacją o załadowanych klasach (close/skip/dismiss),
- listą wzorców RĘCZNYCH ("zaznacz na ekranie"): użytkownik zaznacza
  myszką przycisk zamknięcia/pominięcia reklamy na podglądzie streamu,
  fragment zapisuje się jako PNG w ``ad_templates/`` i od razu jest
  używany przez workera (Template Matching) - także bez modelu AI.

Zmiany ustawień emituje sygnałem ``settings_changed(float, int)``
(threshold, interval_ms), zmianę modelu - ``model_changed(str)``;
wzorce - ``templates_changed()``, próg wzorców -
``template_settings_changed(float)``, żądanie zaznaczenia na ekranie -
``capture_requested()``.
Dialog nie wie nic o workerkach - łączenie sygnałów robi main_window.
"""

from __future__ import annotations

import os

import cv2
import numpy as np
from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

from ad_killer_module import (
    DEFAULT_TEMPLATE_THRESHOLD,
    DETECT_ALL_CLASSES,
    MIN_TEMPLATE_SIZE,
    default_model_path,
    default_templates_dir,
    load_class_names,
    load_templates,
    save_template_crop,
)


class AdKillerConfigDialog(QDialog):
    """Okno konfiguracji AI Ad Killer (model ONNX, czułość, interwał)."""

    settings_changed = pyqtSignal(float, int)  # (threshold, interval_ms)
    model_changed = pyqtSignal(str)  # wybrano nowy plik modelu ONNX
    detect_all_changed = pyqtSignal(bool)  # zmiana trybu "wykrywaj WSZYSTKIE klasy"
    capture_requested = pyqtSignal()  # użytkownik chce zaznaczyć wzorzec na ekranie
    templates_changed = pyqtSignal()  # lista wzorców ręcznych się zmieniła
    template_settings_changed = pyqtSignal(float)  # próg dopasowania wzorców

    def __init__(self, model_path: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("🛡️ Ad Killer - konfiguracja")
        self.setMinimumWidth(460)
        self._model_path = model_path or default_model_path()
        self._templates_dir = default_templates_dir()
        self._build_ui()
        self._update_model_status()
        self._refresh_templates()

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

        self.detect_all_check = QCheckBox(
            "🎯 Wykrywaj WSZYSTKIE klasy (ignoruj nazwy klas)"
        )
        self.detect_all_check.setToolTip(
            "Gdy włączone - każde wykrycie powyżej progu generuje tap, "
            "niezależnie od nazwy klasy. Przydatne gdy model ma niestandardowe "
            "nazwy klas lub jedną klasę."
        )
        layout.addWidget(self.detect_all_check)

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

        # --------------------------------------------------------------
        # Wzorce ręczne ("zaznacz na ekranie") - działa też bez modelu AI
        # --------------------------------------------------------------
        layout.addWidget(QLabel("📋 Wzorce ręczne (zaznaczone na ekranie):"))
        self.template_list = QListWidget()
        self.template_list.setIconSize(QSize(48, 48))
        self.template_list.setFixedHeight(110)
        self.template_list.setToolTip(
            "Wzorce z katalogu ad_templates/ - worker klika środek "
            "dopasowania na żywej klatce streamu"
        )
        layout.addWidget(self.template_list)

        template_buttons = QHBoxLayout()
        self.add_screen_button = QPushButton("✂️ Zaznacz na ekranie")
        self.add_screen_button.setToolTip(
            "Zaznacz myszką prostokąt wokół przycisku zamknięcia/pominięcia "
            "reklamy na podglądzie telefonu - wzorzec zapisze się automatycznie"
        )
        self.delete_template_button = QPushButton("🗑 Usuń zaznaczony")
        self.delete_template_button.setToolTip("Usuń wybrany wzorzec z dysku")
        template_buttons.addWidget(self.add_screen_button)
        template_buttons.addWidget(self.delete_template_button)
        layout.addLayout(template_buttons)

        tpl_thresh_row = QHBoxLayout()
        tpl_thresh_row.addWidget(QLabel("Czułość wzorców:"))
        self.template_slider = QSlider(Qt.Orientation.Horizontal)
        self.template_slider.setRange(50, 99)
        self.template_slider.setValue(int(DEFAULT_TEMPLATE_THRESHOLD * 100))
        self.template_slider_label = QLabel(f"{self.template_slider.value()}%")
        self.template_slider_label.setMinimumWidth(44)
        tpl_thresh_row.addWidget(self.template_slider, 1)
        tpl_thresh_row.addWidget(self.template_slider_label)
        layout.addLayout(tpl_thresh_row)

        hint = QLabel(
            f"Katalog wzorców: {self._templates_dir}\n"
            f"Minimalny rozmiar zaznaczenia: {MIN_TEMPLATE_SIZE}x{MIN_TEMPLATE_SIZE} px. "
            "Wzorce i model AI działają równolegle - Ad Killer klika najlepszego trafienia."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        layout.addWidget(hint)

        self.model_edit.editingFinished.connect(self._on_model_edited)
        self.browse_button.clicked.connect(self._on_browse)
        self.threshold_slider.valueChanged.connect(self._on_settings_changed)
        self.interval_spin.valueChanged.connect(self._on_settings_changed)
        self.detect_all_check.toggled.connect(self._on_detect_all_toggled)
        self.add_screen_button.clicked.connect(self._on_capture_clicked)
        self.delete_template_button.clicked.connect(self._on_delete_template)
        self.template_slider.valueChanged.connect(self._on_template_threshold_changed)

    # ------------------------------------------------------------------
    # Ustawienia
    # ------------------------------------------------------------------

    @property
    def threshold(self) -> float:
        """Próg pewności detekcji (0.0-1.0) z suwaka."""
        return self.threshold_slider.value() / 100.0

    @property
    def detect_all(self) -> bool:
        """Czy wykrywać WSZYSTKIE klasy (ignoruj nazwy klas)."""
        return self.detect_all_check.isChecked()

    @property
    def close_classes(self) -> frozenset[str]:
        """Zwraca zbiór klas do wykrywania lub pusty (detect_all)."""
        if self.detect_all:
            return DETECT_ALL_CLASSES
        return frozenset({"close", "skip", "dismiss"})

    @property
    def interval_ms(self) -> int:
        """Interwał skanowania w milisekundach."""
        return self.interval_spin.value()

    @property
    def template_threshold(self) -> float:
        """Próg dopasowania wzorców ręcznych (0.0-1.0)."""
        return self.template_slider.value() / 100.0

    @property
    def model_path(self) -> str:
        """Aktualna ścieżka pliku modelu ONNX."""
        return self._model_path

    def _on_settings_changed(self) -> None:
        self.threshold_label.setText(f"{self.threshold_slider.value()}%")
        self.settings_changed.emit(self.threshold, self.interval_ms)

    def _on_detect_all_toggled(self, checked: bool) -> None:
        self.detect_all_changed.emit(checked)

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

    # ------------------------------------------------------------------
    # Wzorce ręczne ("zaznacz na ekranie")
    # ------------------------------------------------------------------

    def _refresh_templates(self) -> None:
        """Przeładowuje listę miniaturek z katalogu ``ad_templates/``."""
        self.template_list.clear()
        templates = load_templates(self._templates_dir)
        for name, _gray in templates:
            item = QListWidgetItem(name)
            pixmap = QPixmap(os.path.join(self._templates_dir, name))
            if not pixmap.isNull():
                item.setIcon(QIcon(
                    pixmap.scaled(
                        48,
                        48,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                ))
            self.template_list.addItem(item)
        if not templates:
            self.template_list.addItem(QListWidgetItem("(brak wzorców)"))

    def _on_capture_clicked(self) -> None:
        """Uzbraja zaznaczanie prostokąta na podglądzie streamu (main_window
        podłącza ten sygnał do ``AndroidScreenWidget.set_rect_capture``)."""
        self.capture_requested.emit()

    def add_template_from_crop(self, crop: np.ndarray) -> str | None:
        """Zapisuje wycinek klatki jako nowy wzorzec i odświeża listę.

        Wywoływane z main_window po zaznaczeniu prostokąta na streamie.
        Zwraca ścieżkę zapisanego pliku albo ``None`` przy błędnym wycinku.
        Emituje ``templates_changed`` - działający worker przeładowuje
        wzorce na żywo, bez restartu.
        """
        if crop is None or getattr(crop, "size", 0) == 0:
            QMessageBox.warning(self, "Ad Killer", "Nie udało się pobrać wycinka ekranu.")
            return None
        h, w = crop.shape[:2]
        if w < MIN_TEMPLATE_SIZE or h < MIN_TEMPLATE_SIZE:
            QMessageBox.warning(
                self,
                "Ad Killer",
                f"Zaznaczenie jest za małe ({w}x{h} px). "
                f"Minimalny rozmiar: {MIN_TEMPLATE_SIZE}x{MIN_TEMPLATE_SIZE} px.",
            )
            return None
        try:
            path = save_template_crop(crop, self._templates_dir)
        except OSError as exc:
            QMessageBox.warning(self, "Ad Killer", f"Nie można zapisać wzorca:\n{exc}")
            return None
        self.template_status_flash(f"Zapisano wzorzec: {os.path.basename(path)}")
        self._refresh_templates()
        self.templates_changed.emit()
        return path

    def template_status_flash(self, message: str) -> None:
        """Krótka informacja na etykiecie modelu (bez okienek modalnych)."""
        self.model_status.setText(f"✅ {message}")

    def _on_delete_template(self) -> None:
        """Usuwa wybrany wzorzec PNG z dysku i odświeża listę."""
        row = self.template_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "Ad Killer", "Zaznacz wzorzec na liście.")
            return
        name = self.template_list.item(row).text()
        path = os.path.join(self._templates_dir, name)
        if not os.path.isfile(path):
            return  # pozycja "(brak wzorców)"
        try:
            os.remove(path)
        except OSError as exc:
            QMessageBox.warning(self, "Ad Killer", f"Nie można usunąć pliku:\n{exc}")
            return
        self._refresh_templates()
        self.templates_changed.emit()

    def _on_template_threshold_changed(self) -> None:
        self.template_slider_label.setText(f"{self.template_slider.value()}%")
        self.template_settings_changed.emit(self.template_threshold)
