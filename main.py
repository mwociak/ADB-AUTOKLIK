"""ADB-AUTOKLIK - punkt wejścia aplikacji.

Uruchamia QApplication w ciemnym motywie (Fusion + paleta) i pokazuje
główne okno :class:`MainWindow`.
"""

from __future__ import annotations

import sys

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from main_window import MainWindow

# Ciemna paleta dla stylu Fusion (spójna z motywem streamu)
_DARK_PALETTE: dict[QPalette.ColorRole, QColor] = {
    QPalette.ColorRole.Window: QColor(24, 27, 32),
    QPalette.ColorRole.WindowText: QColor(220, 226, 233),
    QPalette.ColorRole.Base: QColor(30, 34, 40),
    QPalette.ColorRole.AlternateBase: QColor(34, 38, 45),
    QPalette.ColorRole.ToolTipBase: QColor(30, 34, 40),
    QPalette.ColorRole.ToolTipText: QColor(220, 226, 233),
    QPalette.ColorRole.Text: QColor(220, 226, 233),
    QPalette.ColorRole.Button: QColor(38, 43, 50),
    QPalette.ColorRole.ButtonText: QColor(220, 226, 233),
    QPalette.ColorRole.BrightText: QColor(255, 96, 96),
    QPalette.ColorRole.Link: QColor(80, 170, 240),
    QPalette.ColorRole.Highlight: QColor(30, 120, 210),
    QPalette.ColorRole.HighlightedText: QColor(255, 255, 255),
    QPalette.ColorRole.PlaceholderText: QColor(140, 150, 160),
}

_STYLE_SHEET = """
QGroupBox {
    border: 1px solid #2b3138;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 10px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: #7fd1ff;
}
QTableWidget {
    background: #1c2026;
    border: 1px solid #2b3138;
    border-radius: 6px;
    gridline-color: #262c33;
}
QHeaderView::section {
    background: #23272e;
    color: #b9c2cc;
    border: none;
    padding: 5px 6px;
    font-weight: 600;
}
QPushButton {
    background: #2a3038;
    border: 1px solid #39414b;
    border-radius: 6px;
    padding: 6px 12px;
}
QPushButton:hover { background: #333b45; }
QPushButton:pressed { background: #23282f; }
QPushButton:checked {
    background: #1f5c8f;
    border-color: #2f7fc4;
}
QPushButton:disabled { color: #6b7480; background: #23282e; }
QLineEdit, QComboBox {
    background: #1c2026;
    border: 1px solid #39414b;
    border-radius: 6px;
    padding: 5px 8px;
}
QLineEdit:focus, QComboBox:focus { border-color: #2f7fc4; }
QComboBox QAbstractItemView {
    background: #1c2026;
    selection-background-color: #1f5c8f;
}
QCheckBox { spacing: 8px; }
QScrollArea { border: none; }
QScrollBar:vertical { background: #14171b; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #39414b; border-radius: 5px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QStatusBar { color: #aab6c2; }
"""


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("ADB-AUTOKLIK")
    app.setOrganizationName("ADB-AUTOKLIK")

    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in _DARK_PALETTE.items():
        palette.setColor(role, color)
    app.setPalette(palette)
    app.setStyleSheet(_STYLE_SHEET)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
