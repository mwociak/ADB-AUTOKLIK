"""DevicePanel - panel połączenia z urządzeniem Android przez ADB.

Odpowiada za:
- pobieranie listy urządzeń ADB (``get_devices()``) i wybór z ComboBoxa,
- połączenie po USB (wybrane urządzenie) oraz bezprzewodowe (``adb connect``),
- wskaźnik LED stanu ADB (:class:`AdbStatusLed`): zielony = ostatnia komenda
  OK, czerwony = błąd, szary = rozłączono.

Panel nie wie nic o streamie - po udanym połączeniu emituje sygnał
:attr:`DevicePanel.device_connected` z serialem urządzenia, a odbiorca
(main_window) uruchamia stream i resztę aplikacji.
"""

from __future__ import annotations

import adbutils
from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from adb_controller import ADBController, ADBError

# Kolory LED: ok / błąd / rozłączono.
_LED_OK = QColor(46, 204, 113)
_LED_ERROR = QColor(231, 76, 60)
_LED_OFF = QColor(127, 140, 141)

_LED_TOOLTIPS = {
    "ok": "ADB: ostatnia komenda wykonana pomyślnie",
    "error": "ADB: błąd komendy (np. odłączone urządzenie, timeout)",
    "off": "ADB: rozłączono",
}
_LED_LABELS = {"ok": "OK", "error": "Błąd", "off": "Rozłączono"}


class AdbStatusLed(QWidget):
    """Mały wskaźnik stanu ADB w kształcie kółka (LED).

    Stany (patrz :meth:`set_status`):
        ``"ok"``    - zielony: ostatnia komenda ADB wykonana pomyślnie,
        ``"error"`` - czerwony: błąd komendy ADB,
        ``"off"``   - szary: brak połączenia (rozłączono).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._status = "off"
        self.setFixedSize(16, 16)
        self.setToolTip(_LED_TOOLTIPS[self._status])

    def set_status(self, status: str) -> None:
        """Ustawia stan wskaźnika: ``"ok"``, ``"error"`` lub ``"off"``."""
        if status not in _LED_TOOLTIPS:
            raise ValueError(
                f"Nieznany stan LED: {status!r} (oczekiwano 'ok'|'error'|'off')"
            )
        self._status = status
        self.setToolTip(_LED_TOOLTIPS[status])
        self.update()

    @property
    def status(self) -> str:
        return self._status

    def paintEvent(self, event) -> None:  # noqa: N802 (nazwa Qt)
        color = {
            "ok": _LED_OK,
            "error": _LED_ERROR,
            "off": _LED_OFF,
        }[self._status]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2.0, 2.0, -2.0, -2.0)
        painter.setPen(QPen(color.darker(140), 1.2))
        painter.setBrush(color)
        painter.drawEllipse(rect)


class DevicePanel(QWidget):
    """Panel połączenia: lista urządzeń ADB + połączenie USB/Wi-Fi + LED.

    Sygnały:
        device_connected(str): połączono z urządzeniem (serial).
        status_message(str): komunikat do paska statusu głównego okna.
    """

    device_connected = pyqtSignal(str)
    status_message = pyqtSignal(str)

    def __init__(self, adb: ADBController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.adb = adb
        self._build_ui()
        self._wire()

    # ------------------------------------------------------------------
    # Budowa UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        box = QGroupBox("Połączenie")
        layout = QVBoxLayout(box)

        # Wiersz statusu: LED + etykieta stanu ADB
        led_row = QHBoxLayout()
        self.adb_led = AdbStatusLed()
        self.led_label = QLabel(_LED_LABELS["off"])
        self.led_label.setStyleSheet("color: #7f8c8d;")
        led_row.addWidget(self.adb_led)
        led_row.addWidget(self.led_label)
        led_row.addStretch(1)
        layout.addLayout(led_row)

        device_row = QHBoxLayout()
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(150)
        self.refresh_button = QPushButton("Odśwież")
        device_row.addWidget(self.device_combo, 1)
        device_row.addWidget(self.refresh_button)
        layout.addLayout(device_row)

        self.connect_button = QPushButton("Połącz")
        self.connect_button.setEnabled(False)
        layout.addWidget(self.connect_button)

        wireless_row = QHBoxLayout()
        self.wireless_input = QLineEdit()
        self.wireless_input.setPlaceholderText("IP:port (np. 192.168.1.10:5555)")
        self.wireless_button = QPushButton("Połącz bezprzewodowo")
        wireless_row.addWidget(self.wireless_input, 1)
        wireless_row.addWidget(self.wireless_button)
        layout.addLayout(wireless_row)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)

    def _wire(self) -> None:
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.connect_button.clicked.connect(self._on_connect)
        self.wireless_button.clicked.connect(self._on_wireless_connect)
        self.wireless_input.returnPressed.connect(self._on_wireless_connect)

    # ------------------------------------------------------------------
    # Stan LED
    # ------------------------------------------------------------------

    def set_adb_status(self, status: str) -> None:
        """Ustawia wskaźnik LED (''ok'', ''error'' lub ''off'')."""
        self.adb_led.set_status(status)
        self.led_label.setText(_LED_LABELS[status])
        color = {
            "ok": "#2ecc71",
            "error": "#e74c3c",
            "off": "#7f8c8d",
        }[status]
        self.led_label.setStyleSheet(f"color: {color};")

    # ------------------------------------------------------------------
    # Logika połączenia
    # ------------------------------------------------------------------

    def refresh_devices(self) -> None:
        """Odświeża listę urządzeń ADB w ComboBoxie (i wskaźnik LED)."""
        current = self.device_combo.currentData()
        self.device_combo.clear()
        try:
            devices = self.adb.get_devices()
        except ADBError as exc:
            self.set_adb_status("error")
            self.status_message.emit(f"Błąd: {exc}")
            self.connect_button.setEnabled(False)
            return
        for serial in devices:
            self.device_combo.addItem(serial, serial)
        if current in devices:
            self.device_combo.setCurrentIndex(devices.index(current))
        self.connect_button.setEnabled(self.device_combo.count() > 0)
        # Komenda ADB się powiodła; brak urządzeń to stan "rozłączono".
        self.set_adb_status("ok" if self.device_combo.count() else "off")
        self.status_message.emit(
            f"Znaleziono urządzeń: {self.device_combo.count()}"
            if self.device_combo.count()
            else "Brak urządzeń ADB"
        )

    def _on_connect(self) -> None:
        serial = self.device_combo.currentData()
        if not serial:
            self.status_message.emit("Wybierz urządzenie z listy.")
            return
        try:
            self.adb.connect_device(serial)
        except ADBError as exc:
            self.set_adb_status("error")
            self.status_message.emit(f"Błąd połączenia: {exc}")
            return
        self.set_adb_status("ok")
        self.device_connected.emit(serial)

    def _on_wireless_connect(self) -> None:
        text = self.wireless_input.text().strip()
        if not text:
            self.status_message.emit("Podaj adres IP:port urządzenia.")
            return
        if ":" not in text:
            text = f"{text}:5555"  # adb connect domyślnie używa portu 5555
        try:
            reply = adbutils.adb.connect(text)
        except Exception as exc:  # noqa: BLE001 - czytelny komunikat
            self.set_adb_status("error")
            self.status_message.emit(f"Nie można połączyć bezprzewodowo: {exc}")
            return
        lowered = reply.lower()
        if any(
            word in lowered
            for word in ("unable to connect", "failed to connect", "cannot connect")
        ):
            self.set_adb_status("error")
            self.status_message.emit(f"Połączenie bezprzewodowe nieudane: {reply}")
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
        self.status_message.emit(f"Połączono bezprzewodowo: {text}")
