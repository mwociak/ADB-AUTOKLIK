"""AndroidScreenWidget - podgląd ekranu telefonu na żywo (scrcpy).

Widżet PyQt6 wyświetla strumień wideo z telefonu przez ``scrcpy-client``
wraz z nakładką zdefiniowanych punktów keymapy. Kliknięcie lewym
przyciskiem myszy na obraz telefonu emituje sygnał
:attr:`point_selected` z rzeczywistymi współrzędnymi ekranu (x_phone,
y_phone) - z uwzględnieniem skalowania i czarnych pasów (letterboxing).

Wymagane zależności: PyQt6, scrcpy-client (z git main -
``leng-yue/py-scrcpy-client``), numpy.
"""

from __future__ import annotations

import threading

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

# scrcpy-client przyjmuje urządzenie jako obiekt adbutils.AdbDevice lub
# serial - używamy wariantu z serialem (spójnie z ADBController).
from scrcpy import Client

from config_manager import KeyPoint

# Domyślne parametry streamu: pełna rozdzielczość telefonu (max_width=0),
# ograniczone fps (oszczędność CPU/bandwidth), klatki tylko gdy nowe.
DEFAULT_MAX_FPS = 30
DEFAULT_BITRATE = 8_000_000

_BG_COLOR = QColor(15, 18, 22)
_PLACEHOLDER = "Brak podglądu - uruchom stream (start_stream)"
_OVERLAY_FILL = QColor(24, 140, 255, 110)
_OVERLAY_BORDER = QColor(255, 255, 255, 220)
_OVERLAY_TEXT = QColor(255, 255, 255)
_OVERLAY_RADIUS = 13.0


class AndroidScreenWidget(QWidget):
    """Wyświetla strumień wideo z telefonu i przelicza kliknięcia na współrzędne.

    Sygnały:
        point_selected(int, int): kliknięcie na obrazie telefonu,
            emitowane z rzeczywistymi współrzędnymi (x_phone, y_phone).
        stream_started(str): stream dla danego serialu został uruchomiony.
        stream_stopped(str): stream zatrzymany; argument to powód
            ("" przy normalnym zatrzymaniu, inaczej opis błędu/odłączenia).
        stream_error(str): błąd uruchomienia/połączenia (czytelny komunikat).

    Wewnętrzny sygnał ``_frame_received`` przenosi ramkę numpy z wątku
    scrcpy do wątku GUI (przetwarzanie QImage/QPixmap tylko w głównym
    wątku - bezpieczne renderowanie).
    """

    point_selected = pyqtSignal(int, int)
    stream_started = pyqtSignal(str)
    stream_stopped = pyqtSignal(str)
    stream_error = pyqtSignal(str)

    _frame_received = pyqtSignal(object)

    def __init__(
        self,
        max_fps: int = DEFAULT_MAX_FPS,
        bitrate: int = DEFAULT_BITRATE,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setMinimumSize(320, 240)

        self.max_fps = max_fps
        self.bitrate = bitrate

        # Stan streamu (dostępny z wątku GUI)
        self._client = None  # scrcpy.Client | None
        self._client_thread: threading.Thread | None = None
        self._serial: str | None = None

        # Stan obrazu
        self._pixmap: QPixmap | None = None
        self._phone_size: tuple[int, int] | None = None  # (width, height) telefonu
        self._draw_rect = QRectF()  # prostokąt rysowania (letterbox) w koordynatach widżetu
        self._frame_pending = False

        # Nakładka (punkty keymapy)
        self._overlay_points: list[KeyPoint] = []

        self._frame_received.connect(self._on_frame_gui)
        self.setMouseTracking(True)

    # ------------------------------------------------------------------
    # Sterowanie streamem
    # ------------------------------------------------------------------

    def start_stream(self, serial: str) -> None:
        """Uruchamia stream scrcpy dla urządzenia o podanym serialu.

        Połączenie (deploy serwera scrcpy i handshake) wykonuje się
        w osobnym wątku roboczym, żeby nie blokować wątku GUI.
        """
        if self._client is not None:
            self.stop_stream()
        self._serial = serial
        self._pixmap = None
        self._phone_size = self._query_phone_size(serial)
        self._client_thread = threading.Thread(
            target=self._run_client,
            args=(serial,),
            daemon=True,
            name="scrcpy-stream",
        )
        self._client_thread.start()

    def stop_stream(self) -> None:
        """Zatrzymuje stream i zwalnia zasoby (bezpiecznie z wątku GUI)."""
        client = self._client
        self._client = None
        if client is not None:
            try:
                client.stop()
            except Exception as exc:  # noqa: BLE001 - nie psujemy zamykania
                print(f"[AndroidScreenWidget] Błąd przy zatrzymaniu streamu: {exc}")
        if (
            self._client_thread is not None
            and self._client_thread.is_alive()
            and self._client_thread is not threading.current_thread()
        ):
            self._client_thread.join(timeout=2.0)
        self._client_thread = None
        self.stream_stopped.emit("")

    def _run_client(self, serial: str) -> None:
        """Uruchamia klienta scrcpy (wątek roboczy; klatki lecą w osobnym wątku)."""
        try:
            client = Client(
                device=serial,
                max_width=0,  # pełna rozdzielczość telefonu
                max_fps=self.max_fps,
                bitrate=self.bitrate,
                block_frame=True,  # tylko realne (niepuste) klatki
            )
        except Exception as exc:  # noqa: BLE001
            self.stream_error.emit(f"Nie można utworzyć klienta scrcpy: {exc}")
            return

        client.add_listener("frame", self._on_frame_thread)
        client.add_listener("disconnect", self._on_disconnect_thread)
        self._client = client
        try:
            client.start(daemon_threaded=True)
        except Exception as exc:  # noqa: BLE001
            self.stream_error.emit(
                f"Nie można uruchomić streamu dla {serial}: {exc}"
            )
            self._client = None
            return
        self.stream_started.emit(serial)

    def _query_phone_size(self, serial: str) -> tuple[int, int] | None:
        """Pobiera rzeczywistą rozdzielczość telefonu (``wm size``).

        Potrzebna do poprawnego przeliczania współrzędnych kliknięć nawet,
        gdyby strumień był skalowany. Przy błędzie zwraca ``None``
        (wtedy rozmiar zostanie uzupełniony z pierwszej ramki wideo).
        """
        try:
            import adbutils

            size = adbutils.adb.device(serial).window_size()
            return (size.width, size.height)
        except Exception:  # noqa: BLE001 - nie blokujemy startu streamu
            return None

    # ------------------------------------------------------------------
    # Odbieranie klatek (wątek scrcpy -> wątek GUI)
    # ------------------------------------------------------------------

    def _on_frame_thread(self, frame: np.ndarray | None) -> None:
        """Callback klatek z wątku scrcpy - tylko przerzuca ramkę do GUI."""
        if frame is None or self._frame_pending:
            return  # pomiń ramkę, jeśli GUI nie zdążyło jeszcze przetworzyć poprzedniej
        self._frame_pending = True
        self._frame_received.emit(frame)

    def _on_disconnect_thread(self, *args: object) -> None:
        """Urządzenie odłączone lub stream przerwany (wątek scrcpy)."""
        self._client = None
        self.stream_stopped.emit("Urządzenie zostało odłączone lub stream został przerwany")

    def _on_frame_gui(self, frame: np.ndarray) -> None:
        """Przetwarza ramkę w wątku GUI: numpy -> QImage -> QPixmap."""
        self._frame_pending = False
        h, w = frame.shape[:2]
        if self._phone_size is None:
            self._phone_size = (w, h)
        image = QImage(
            frame.data, w, h, frame.strides[0], QImage.Format.Format_RGB888
        ).rgbSwapped()  # klatki są BGR24 -> zamiana na RGB
        self._pixmap = QPixmap.fromImage(image)
        self.update()

    # ------------------------------------------------------------------
    # Rysowanie i skalowanie (letterbox)
    # ------------------------------------------------------------------

    def _fit_rect(self, size: tuple[int, int], widget_rect: QRectF) -> QRectF:
        """Dopasowuje obraz (width, height) do widżetu z zachowaniem proporcji."""
        pw, ph = size
        scale = min(widget_rect.width() / pw, widget_rect.height() / ph)
        w, h = pw * scale, ph * scale
        x = widget_rect.x() + (widget_rect.width() - w) / 2
        y = widget_rect.y() + (widget_rect.height() - h) / 2
        return QRectF(x, y, w, h)

    def paintEvent(self, event) -> None:  # noqa: N802 (nazwa Qt)
        painter = QPainter(self)
        painter.fillRect(self.rect(), _BG_COLOR)
        if self._pixmap is not None and self._phone_size is not None:
            rect = self._fit_rect(self._phone_size, QRectF(self.rect()))
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(rect, self._pixmap, QRectF(self._pixmap.rect()))
            self._draw_rect = rect
            for point in self._overlay_points:
                self._draw_overlay_point(painter, point)
        else:
            painter.setPen(QColor(140, 155, 170))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, _PLACEHOLDER
            )

    def _draw_overlay_point(self, painter: QPainter, point: KeyPoint) -> None:
        """Rysuje półprzezroczyste kółko z klawiszem na pozycji punktu."""
        if self._phone_size is None:
            return
        pw, ph = self._phone_size
        r = self._draw_rect
        cx = r.left() + point.x / pw * r.width()
        cy = r.top() + point.y / ph * r.height()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_OVERLAY_FILL)
        painter.drawEllipse(QPointF(cx, cy), _OVERLAY_RADIUS, _OVERLAY_RADIUS)
        painter.setPen(QPen(_OVERLAY_BORDER, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), _OVERLAY_RADIUS, _OVERLAY_RADIUS)

        font = painter.font()
        font.setBold(True)
        font.setPointSizeF(11.0)
        painter.setFont(font)
        painter.setPen(_OVERLAY_TEXT)
        label = point.key.upper() if point.key else "?"
        painter.drawText(
            QRectF(cx - _OVERLAY_RADIUS, cy - _OVERLAY_RADIUS,
                   2 * _OVERLAY_RADIUS, 2 * _OVERLAY_RADIUS),
            Qt.AlignmentFlag.AlignCenter,
            label,
        )

    # ------------------------------------------------------------------
    # Kliknięcia myszy -> współrzędne telefonu
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 (nazwa Qt)
        if event.button() == Qt.MouseButton.LeftButton:
            phone = self._to_phone(event.position())
            if phone is not None:
                self.point_selected.emit(*phone)
        super().mousePressEvent(event)

    def _to_phone(self, pos: QPointF) -> tuple[int, int] | None:
        """Przelicza pozycję w widżecie na współrzędne telefonu.

        Uwzględnia letterboxing i skalowanie; kliknięcia poza obrazem
        (na czarnych pasach) zwracają ``None``.
        """
        if self._phone_size is None or self._draw_rect.isNull():
            return None
        r = self._draw_rect
        if not r.contains(pos):
            return None
        pw, ph = self._phone_size
        fx = (pos.x() - r.left()) / r.width()
        fy = (pos.y() - r.top()) / r.height()
        x = min(max(int(fx * pw), 0), pw - 1)
        y = min(max(int(fy * ph), 0), ph - 1)
        return (x, y)

    # ------------------------------------------------------------------
    # Nakładka punktów keymapy
    # ------------------------------------------------------------------

    def set_overlay_points(self, points: list[KeyPoint]) -> None:
        """Ustawia punkty keymapy do narysowania na obrazie telefonu."""
        self._overlay_points = list(points)
        self.update()

    # ------------------------------------------------------------------
    # Pomocnicze
    # ------------------------------------------------------------------

    @property
    def is_streaming(self) -> bool:
        return self._client is not None

    @property
    def device_serial(self) -> str | None:
        return self._serial

    def closeEvent(self, event) -> None:  # noqa: N802 (nazwa Qt)
        self.stop_stream()
        super().closeEvent(event)
