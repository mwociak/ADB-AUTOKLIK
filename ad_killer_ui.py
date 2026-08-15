"""AdKillerConfigDialog - konfiguracja modułu Ad Killer.

Osobne okno (QDialog) z:
- listą wczytanych wzorców reklam (miniatury PNG z ``ad_templates/``),
- suwakiem czułości (threshold, 50-99 %),
- polem interwału skanowania (ms),
- przyciskiem "Dodaj nowy wzorzec z ekranu": użytkownik zaznacza myszką
  prostokąt na podglądzie telefonu (:class:`AndroidScreenWidget`), a moduł
  wycina go z ostatniej klatki streamu i zapisuje jako PNG do
  ``ad_templates/``.

Zmiany ustawień emituje sygnałem ``settings_changed(float, int)``
(threshold, interval_ms), dodanie wzorca - ``templates_changed()``.
Dialog nie wie nic o workerkach - łączenie sygnałów robi main_window.
"""

from __future__ import annotations

import os

import cv2
from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)


class AdKillerConfigDialog(QDialog):
    """Okno konfiguracji Ad Killer (wzorce, czułość, interwał skanowania)."""

    settings_changed = pyqtSignal(float, int)  # (threshold, interval_ms)
    templates_changed = pyqtSignal()  # dodano nowy wzorzec

    def __init__(
        self,
        stream: "AndroidScreenWidget",
        templates_dir: str = "ad_templates",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("🛡️ Ad Killer - konfiguracja")
        self.setMinimumWidth(360)
        self._stream = stream
        self._templates_dir = templates_dir
        self._build_ui()
        self._load_templates()
        self._stream.rect_selected.connect(self._on_rect_selected)

    # ------------------------------------------------------------------
    # Budowa UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Wzorce reklam (ad_templates/):"))
        self.template_list = QListWidget()
        self.template_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.template_list.setIconSize(QSize(72, 72))
        self.template_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.template_list.setWordWrap(True)
        layout.addWidget(self.template_list, 1)

        thresh_row = QHBoxLayout()
        thresh_row.addWidget(QLabel("Czułość:"))
        self.threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.threshold_slider.setRange(50, 99)
        self.threshold_slider.setValue(80)
        self.threshold_label = QLabel("80%")
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

        self.add_button = QPushButton("➕ Dodaj nowy wzorzec z ekranu")
        self.add_button.setToolTip(
            "Przeciągnij myszką prostokąt wokół krzyżyka (X) na podglądzie telefonu"
        )
        layout.addWidget(self.add_button)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.threshold_slider.valueChanged.connect(self._on_settings_changed)
        self.interval_spin.valueChanged.connect(self._on_settings_changed)
        self.add_button.clicked.connect(self._on_add_template)

    # ------------------------------------------------------------------
    # Ustawienia
    # ------------------------------------------------------------------

    @property
    def threshold(self) -> float:
        """Próg czułości (0.0-1.0) z suwaka."""
        return self.threshold_slider.value() / 100.0

    @property
    def interval_ms(self) -> int:
        """Interwał skanowania w milisekundach."""
        return self.interval_spin.value()

    def _on_settings_changed(self) -> None:
        self.threshold_label.setText(f"{self.threshold_slider.value()}%")
        self.settings_changed.emit(self.threshold, self.interval_ms)

    # ------------------------------------------------------------------
    # Lista wzorców
    # ------------------------------------------------------------------

    def _load_templates(self) -> None:
        """Odświeża listę miniatur wzorców z katalogu ad_templates/."""
        self.template_list.clear()
        if not os.path.isdir(self._templates_dir):
            self.status_label.setText(
                f"Katalog {self._templates_dir}/ nie istnieje."
            )
            return
        count = 0
        for name in sorted(os.listdir(self._templates_dir)):
            path = os.path.join(self._templates_dir, name)
            if not os.path.isfile(path) or not name.lower().endswith(".png"):
                continue
            pixmap = QPixmap(path)
            if pixmap.isNull():
                continue
            item = QListWidgetItem(
                QIcon(
                    pixmap.scaled(
                        72,
                        72,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                ),
                name,
            )
            item.setToolTip(f"{name}\n{pixmap.width()} × {pixmap.height()} px")
            self.template_list.addItem(item)
            count += 1
        if count:
            self.status_label.setText(
                f"Wczytano {count} wzorców z {self._templates_dir}/"
            )

    # ------------------------------------------------------------------
    # Dodawanie wzorca z ekranu
    # ------------------------------------------------------------------

    def _on_add_template(self) -> None:
        """Uzbraja zaznaczanie prostokąta na podglądzie telefonu."""
        self.add_button.setEnabled(False)
        self.status_label.setText(
            "Przeciągnij myszką prostokąt wokół krzyżyka (X) na podglądzie telefonu…"
        )
        self._stream.set_rect_capture(True)

    def _on_rect_selected(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """Zapisuje zaznaczony prostokąt (z ostatniej klatki) jako nowy wzorzec."""
        self._stream.set_rect_capture(False)
        self.add_button.setEnabled(True)
        frame = self._stream.get_latest_frame()
        if frame is None:
            self.status_label.setText(
                "Brak klatki z podglądu - uruchom stream i spróbuj ponownie."
            )
            return
        h, w = frame.shape[:2]
        x1 = min(max(int(x1), 0), w - 1)
        x2 = min(max(int(x2), 0), w - 1)
        y1 = min(max(int(y1), 0), h - 1)
        y2 = min(max(int(y2), 0), h - 1)
        if x2 - x1 < 10 or y2 - y1 < 10:
            self.status_label.setText("Zaznacz większy prostokąt (min. ~10 px).")
            return
        crop = frame[y1 : y2 + 1, x1 : x2 + 1]
        os.makedirs(self._templates_dir, exist_ok=True)
        existing = {
            f for f in os.listdir(self._templates_dir)
            if f.lower().endswith(".png")
        }
        # Pierwszy wolny numer, żeby nie nadpisywać istniejących wzorców.
        n = 1
        while f"template_{n}.png" in existing:
            n += 1
        path = os.path.join(self._templates_dir, f"template_{n}.png")
        cv2.imwrite(path, crop)
        self._load_templates()
        self.templates_changed.emit()
        self.status_label.setText(
            f"Zapisano wzorzec: {os.path.basename(path)}"
        )

    # ------------------------------------------------------------------
    # Zamknięcie
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 (nazwa Qt)
        # Wyłącz tryb zaznaczania prostokąta, gdyby okno zamknięto w trakcie.
        self._stream.set_rect_capture(False)
        super().closeEvent(event)
