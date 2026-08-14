"""MultiDeviceControlWindow - farma urządzeń (Device Grid / Device Wall).

Całkowicie niezależny moduł do równoległego sterowania wieloma urządzeniami
Android (wzorzec "Device Grid" / "Device Wall"):

- lewy panel: dodawanie urządzeń (USB z listy ADB lub przez ``adb connect``
  IP:port) oraz akcje zbiorcze na wszystkich kartach: uruchomienie aplikacji
  po nazwie pakietu, wysłanie tapu (sync akcji), odświeżenie podglądów,
- prawy panel: przewijana siatka kart :class:`DeviceCard` - każda karta
  pokazuje numer urządzenia (#1, #2...), nazwę/IP, diodę statusu (🟢/🔴)
  oraz zmniejszony zrzut ekranu telefonu.

Zrzut ekranu pobierany jest przez ADB (``adb screencap``, metoda
``AdbDevice.screenshot``) w osobnym wątku na kartę; komunikacja z GUI
odbywa się wyłącznie przez sygnały Qt (queued connections), więc pobieranie
nie blokuje okna.
"""

from __future__ import annotations

import threading

import adbutils
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# Liczba kart w wierszu siatki oraz wymiary kafelka urządzenia.
GRID_COLUMNS = 3
CARD_WIDTH = 260
CARD_HEIGHT = 380
_PREVIEW_HEIGHT = 300

_CARD_STYLE = """
DeviceCard {
    background: #20242b;
    border: 1px solid #39414b;
    border-radius: 10px;
}
DeviceCard QLabel#cardTitle {
    color: #e6edf3;
    font-weight: 600;
    font-size: 13px;
}
DeviceCard QLabel#previewLabel {
    background: #161a1f;
    border: 1px solid #2b3138;
    border-radius: 6px;
    color: #8b95a1;
    font-size: 12px;
}
DeviceCard QPushButton {
    background: #2a3038;
    border: 1px solid #39414b;
    border-radius: 6px;
    color: #d7dee5;
    font-size: 12px;
}
DeviceCard QPushButton:hover { background: #3a2f33; border-color: #8a5560; }
"""


def _pil_to_qimage(image: object) -> QImage:
    """Konwertuje obraz PIL (tryb RGB) do QImage z własną kopią danych."""
    w, h = image.size
    data = image.tobytes("raw", "RGB")
    return QImage(data, w, h, w * 3, QImage.Format.Format_RGB888).copy()


class DeviceCard(QWidget):
    """Kafelek urządzenia w siatce farmy.

    Sygnały:
        remove_requested(DeviceCard): użytkownik chce odłączyć urządzenie.
    Wewnętrzne sygnały ``_screenshot_ready`` / ``_screenshot_error`` przenoszą
    wynik pobierania zrzutu z wątku roboczego do wątku GUI.
    """

    remove_requested = pyqtSignal(object)
    _screenshot_ready = pyqtSignal(object)  # PIL.Image (RGB) z wątku
    _screenshot_error = pyqtSignal(str)

    def __init__(
        self, index: int, label: str, serial: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.index = index
        self.label = label
        self.serial = serial
        self._fetching = False

        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self.setStyleSheet(_CARD_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Nagłówek: dioda LED + numer/nazwa + przycisk odłączenia
        header = QHBoxLayout()
        self.led = QLabel("🟢")
        self.led.setToolTip("Połączono")
        self.led.setStyleSheet("font-size: 14px; background: transparent; border: none;")
        self.title = QLabel(f"#{index}  {label}")
        self.title.setObjectName("cardTitle")
        self.title.setToolTip(label)
        remove_btn = QPushButton("✕")
        remove_btn.setFixedSize(26, 26)
        remove_btn.setToolTip("Odłącz urządzenie")
        remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        header.addWidget(self.led)
        header.addWidget(self.title, 1)
        header.addWidget(remove_btn)
        layout.addLayout(header)

        # Podgląd zrzutu ekranu (skalowany z zachowaniem proporcji)
        self.preview = QLabel("Ładowanie zrzutu...")
        self.preview.setObjectName("previewLabel")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedHeight(_PREVIEW_HEIGHT)
        layout.addWidget(self.preview, 1)

        self._screenshot_ready.connect(self._on_screenshot)
        self._screenshot_error.connect(self._on_screenshot_error)

    # ------------------------------------------------------------------
    # Zrzut ekranu (osobny wątek na kartę)
    # ------------------------------------------------------------------

    def fetch_screenshot(self) -> None:
        """Pobiera zrzut ekranu urządzenia (``adb screencap``) w tle."""
        if self._fetching:
            return
        self._fetching = True
        self.preview.setPixmap(QPixmap())
        self.preview.setText("Pobieranie zrzutu...")
        threading.Thread(
            target=self._fetch_worker, daemon=True, name=f"screencap-{self.serial}"
        ).start()

    def _fetch_worker(self) -> None:
        try:
            device = adbutils.adb.device(self.serial)
            image = device.screenshot(error_ok=False)  # PIL.Image (RGB)
            self._screenshot_ready.emit(image)
        except Exception as exc:  # noqa: BLE001 - czytelny komunikat na kafelku
            self._screenshot_error.emit(f"Błąd zrzutu: {exc}")

    def _on_screenshot(self, image: object) -> None:
        self._fetching = False
        try:
            qimage = _pil_to_qimage(image)
        except Exception as exc:  # noqa: BLE001
            self._set_error(f"Nie można przetworzyć zrzutu: {exc}")
            return
        self._set_led(True)
        pixmap = QPixmap.fromImage(qimage)
        scaled = pixmap.scaled(
            self.preview.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview.setPixmap(scaled)
        self.preview.setToolTip("")

    def _on_screenshot_error(self, message: str) -> None:
        self._fetching = False
        self._set_error(message)

    def _set_error(self, message: str) -> None:
        self._set_led(False)
        self.preview.setPixmap(QPixmap())
        self.preview.setText(message)
        self.preview.setToolTip(message)

    def _set_led(self, ok: bool) -> None:
        self.led.setText("🟢" if ok else "🔴")
        self.led.setToolTip(
            "Połączono — ostatni zrzut OK" if ok else "Błąd urządzenia / odłączono"
        )


class MultiDeviceControlWindow(QMainWindow):
    """Okno farmy urządzeń: dodawanie + akcje zbiorcze + siatka kart."""

    _batch_done = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Multi-Device Control — farma urządzeń")
        self.resize(1150, 720)
        self._cards: list[DeviceCard] = []
        self._build_ui()
        self._wire()
        self._relayout()  # pusty stan: etykieta "Brak urządzeń"
        self._refresh_usb()

    # ------------------------------------------------------------------
    # Budowa UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(12)

        # --- Lewy panel: dodawanie + sterowanie zbiorcze ---
        left = QWidget()
        left.setFixedWidth(320)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        add_group = QGroupBox("Dodaj urządzenie")
        add_layout = QVBoxLayout(add_group)
        add_layout.addWidget(QLabel("Urządzenia wykryte przez ADB:"))
        self.device_combo = QComboBox()
        add_layout.addWidget(self.device_combo)
        self.refresh_button = QPushButton("Odśwież listę USB")
        add_layout.addWidget(self.refresh_button)
        add_layout.addWidget(QLabel("lub połącz przez IP:port:"))
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("np. 192.168.1.5:5555")
        add_layout.addWidget(self.ip_input)
        self.add_button = QPushButton("Dodaj i Połącz")
        add_layout.addWidget(self.add_button)
        left_layout.addWidget(add_group)

        control_group = QGroupBox("Sterowanie wszystkimi urządzeniami")
        control_layout = QVBoxLayout(control_group)
        control_layout.addWidget(QLabel("Uruchom appkę (nazwa pakietu):"))
        self.package_input = QLineEdit()
        self.package_input.setPlaceholderText("np. com.example.game")
        control_layout.addWidget(self.package_input)
        self.launch_button = QPushButton("Uruchom appkę na wszystkich")
        control_layout.addWidget(self.launch_button)

        tap_row = QHBoxLayout()
        tap_row.addWidget(QLabel("Tap (X,Y):"))
        self.tap_x = QSpinBox()
        self.tap_x.setRange(0, 10_000)
        self.tap_y = QSpinBox()
        self.tap_y.setRange(0, 10_000)
        tap_row.addWidget(self.tap_x)
        tap_row.addWidget(self.tap_y)
        control_layout.addLayout(tap_row)
        self.tap_all_button = QPushButton("Wyślij tap na wszystkich (sync)")
        control_layout.addWidget(self.tap_all_button)

        self.refresh_shots_button = QPushButton("Odśwież podglądy (zrzuty ekranu)")
        control_layout.addWidget(self.refresh_shots_button)
        left_layout.addWidget(control_group)
        left_layout.addStretch(1)

        # --- Prawy panel: siatka kart urządzeń ---
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.grid.setSpacing(12)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.empty_label = QLabel("Brak urządzeń. Dodaj je z lewego panelu.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color: #8b95a1; padding: 24px;")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.grid_host)

        root.addWidget(left)
        root.addWidget(scroll, 1)
        self.setCentralWidget(central)

        self.statusBar().showMessage("Gotowy.")

    def _wire(self) -> None:
        self.refresh_button.clicked.connect(self._refresh_usb)
        self.add_button.clicked.connect(self._on_add)
        self.ip_input.returnPressed.connect(self._on_add)
        self.launch_button.clicked.connect(self._on_launch_all)
        self.package_input.returnPressed.connect(self._on_launch_all)
        self.tap_all_button.clicked.connect(self._on_tap_all)
        self.refresh_shots_button.clicked.connect(self._on_refresh_shots)
        self._batch_done.connect(self._status)

    # ------------------------------------------------------------------
    # Karty urządzeń (siatka)
    # ------------------------------------------------------------------

    def _add_card(self, label: str, serial: str) -> DeviceCard:
        card = DeviceCard(len(self._cards) + 1, label, serial)
        card.remove_requested.connect(self._remove_card)
        self._cards.append(card)
        self._relayout()
        card.fetch_screenshot()
        return card

    def _remove_card(self, card: DeviceCard) -> None:
        if ":" in card.serial:  # urządzenie TCP/IP -> rozłącz w ADB
            try:
                adbutils.adb.disconnect(card.serial)
            except Exception:  # noqa: BLE001 - nie psujemy usuwania kafelka
                pass
        if card in self._cards:
            self._cards.remove(card)
        card.deleteLater()
        self._relayout()
        self._status(f"Odłączono: {card.label}")

    def _relayout(self) -> None:
        """Układa karty w siatce (z ponumerowaniem #1, #2...) i czyści siatkę."""
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)
        if not self._cards:
            self.grid.addWidget(self.empty_label, 0, 0)
            return
        for i, card in enumerate(self._cards, start=1):
            card.index = i
            card.title.setText(f"#{i}  {card.label}")
            row, col = divmod(i - 1, GRID_COLUMNS)
            self.grid.addWidget(card, row, col)

    # ------------------------------------------------------------------
    # Dodawanie urządzeń
    # ------------------------------------------------------------------

    def _refresh_usb(self) -> None:
        current = self.device_combo.currentData()
        self.device_combo.clear()
        try:
            devices = adbutils.adb.device_list()
        except Exception as exc:  # noqa: BLE001 - czytelny komunikat
            self._status(f"Błąd ADB: {exc}")
            return
        existing = {card.serial for card in self._cards}
        for device in devices:
            if device.serial not in existing:
                self.device_combo.addItem(device.serial, device.serial)
        if current is not None:
            index = self.device_combo.findData(current)
            if index >= 0:
                self.device_combo.setCurrentIndex(index)
        self._status(f"Wykryto urządzeń: {self.device_combo.count()}")

    def _on_add(self) -> None:
        ip = self.ip_input.text().strip()
        if ip:
            if ":" not in ip:
                ip = f"{ip}:5555"  # adb connect domyślnie używa portu 5555
            try:
                reply = adbutils.adb.connect(ip)
            except Exception as exc:  # noqa: BLE001 - czytelny komunikat
                self._status(f"Nie można połączyć z {ip}: {exc}")
                return
            lowered = reply.lower()
            if any(
                word in lowered
                for word in ("unable to connect", "failed to connect", "cannot connect")
            ):
                self._status(f"Połączenie nieudane: {reply}")
                return
            serial, label = ip, ip
        else:
            serial = self.device_combo.currentData()
            if not serial:
                self._status("Wybierz urządzenie z listy lub podaj IP:port.")
                return
            label = serial
        if any(card.serial == serial for card in self._cards):
            self._status(f"Urządzenie już dodane: {serial}")
            return
        self._add_card(label, serial)
        self.ip_input.clear()
        self._status(f"Dodano i połączono: {serial}")

    # ------------------------------------------------------------------
    # Akcje zbiorcze (na wszystkich kartach)
    # ------------------------------------------------------------------

    def _run_on_all(self, args_fn) -> None:
        """Wykonuje komendę shell na wszystkich urządzeniach (wątek na urządzenie)."""
        if not self._cards:
            self._status("Brak urządzeń - dodaj co najmniej jedno.")
            return
        self._status(f"Wysyłam komendę na {len(self._cards)} urządzeń...")

        def worker(serial: str) -> None:
            try:
                adbutils.adb.device(serial).shell(args_fn(serial), timeout=8)
                self._batch_done.emit(f"OK: {serial}")
            except Exception as exc:  # noqa: BLE001
                self._batch_done.emit(f"Błąd ({serial}): {exc}")

        for card in self._cards:
            threading.Thread(target=worker, args=(card.serial,), daemon=True).start()

    def _on_launch_all(self) -> None:
        pkg = self.package_input.text().strip()
        if not pkg:
            self._status("Podaj nazwę pakietu.")
            return
        self._run_on_all(
            lambda serial: [
                "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1",
            ]
        )

    def _on_tap_all(self) -> None:
        x, y = self.tap_x.value(), self.tap_y.value()
        self._run_on_all(lambda serial: ["input", "tap", str(x), str(y)])

    def _on_refresh_shots(self) -> None:
        if not self._cards:
            self._status("Brak urządzeń.")
            return
        for card in self._cards:
            card.fetch_screenshot()
        self._status(f"Odświeżam zrzuty ({len(self._cards)} urządzeń)...")

    # ------------------------------------------------------------------
    # Pomocnicze
    # ------------------------------------------------------------------

    def _status(self, message: str) -> None:
        self.statusBar().showMessage(message, 6000)
