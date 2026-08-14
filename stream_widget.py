"""AndroidScreenWidget - podgląd ekranu telefonu na żywo (scrcpy).

Widżet PyQt6 wyświetla strumień wideo z telefonu przez ``scrcpy-client``
wraz z nakładką zdefiniowanych akcji keymapy (tapy, swipe'y i makra).
Kliknięcie lewym przyciskiem myszy na obraz telefonu emituje sygnał
:attr:`point_selected` z rzeczywistymi współrzędnymi ekranu (x_phone,
y_phone) - z uwzględnieniem skalowania i czarnych pasów (letterboxing).

W trybie ``swipe`` (patrz :meth:`set_gesture_mode`) użytkownik definiuje
gest myszą: wciska LPM w punkcie startowym, przeciąga i puszcza w punkcie
końcowym - widżet emituje wtedy :attr:`swipe_selected` (x1, y1, x2, y2).
Podczas przeciągania rysowany jest podgląd strzałki; zapisane swipe'y
rysowane są na nakładce jako strzałki z klawiszem przy punkcie startowym.
Makra rysowane są jako seria kroków (kółka tapów i strzałki swipe'ów)
z klawiszem przy pierwszym kroku.

Wymagane zależności: PyQt6, scrcpy-client (z git main -
``leng-yue/py-scrcpy-client``), numpy.
"""

from __future__ import annotations

import math
import threading

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

# scrcpy-client przyjmuje urządzenie jako obiekt adbutils.AdbDevice lub
# serial - używamy wariantu z serialem (spójnie z ADBController).
from scrcpy import Client

from config_manager import AnyPoint, KeyPoint, MacroPoint, SwipePoint

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
# Strzałka gestu swipe (nakładka) i podgląd przeciągania myszą.
_SWIPE_ARROW = QColor(255, 170, 40, 230)
_DRAG_PREVIEW = QColor(255, 255, 255, 170)
_SWIPE_END_RADIUS = 5.0
# Minimalne przesunięcie myszy [px], aby uznać gest za swipe (a nie klik).
_SWIPE_MIN_DRAG_PX = 10
# Promień trafienia [px] przy chwytaniu punktu nakładki (drag & drop).
_DRAG_HIT_RADIUS = 26.0


class AndroidScreenWidget(QWidget):
    """Wyświetla strumień wideo z telefonu i przelicza gesty myszy na współrzędne.

    Sygnały:
        point_selected(int, int): kliknięcie na obrazie telefonu,
            emitowane z rzeczywistymi współrzędnymi (x_phone, y_phone).
        swipe_selected(int, int, int, int): przeciągnięcie myszą na
            obrazie telefonu; argumenty to (x1, y1, x2, y2) - rzeczywiste
            współrzędne startu i końca gestu.
        stream_started(str): stream dla danego serialu został uruchomiony.
        stream_stopped(str): stream zatrzymany; argument to powód
            ("" przy normalnym zatrzymaniu, inaczej opis błędu/odłączenia).
        stream_error(str): błąd uruchomienia/połączenia (czytelny komunikat).

    Wewnętrzny sygnał ``_frame_received`` przenosi ramkę numpy z wątku
    scrcpy do wątku GUI (przetwarzanie QImage/QPixmap tylko w głównym
    wątku - bezpieczne renderowanie).
    """

    point_selected = pyqtSignal(int, int)
    swipe_selected = pyqtSignal(int, int, int, int)
    point_moved = pyqtSignal(str, int, int)  # (name, new_x, new_y) - drag & drop nakładki
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

        # Nakładka (akcje keymapy: tapy, swipe'y i makra)
        self._overlay_points: list[KeyPoint | SwipePoint | MacroPoint] = []

        # Gesty myszy: "tap" (klik) lub "swipe" (przeciągnij i puść)
        self._gesture_mode = "tap"
        self._drag_start: QPointF | None = None  # pozycja wciśnięcia LPM (widżet)
        self._drag_current: QPointF | None = None  # aktualna pozycja myszy (podgląd)

        # Drag & drop punktów nakładki (aktywne, gdy przechwytywanie gestów wyłączone)
        self._capture_enabled = False
        self._move_point: KeyPoint | SwipePoint | MacroPoint | None = None
        self._move_start: QPointF | None = None  # pozycja wciśnięcia (widżet)
        self._move_current: QPointF | None = None  # aktualna pozycja myszy (podgląd)
        self._move_origin: tuple[int, int] | None = None  # oryginalna kotwica (telefon)

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
            # Podgląd przeciągania myszą (tryb swipe)
            if self._drag_start is not None and self._drag_current is not None:
                self._draw_overlay_arrow(
                    painter,
                    (self._drag_start.x(), self._drag_start.y()),
                    (self._drag_current.x(), self._drag_current.y()),
                    _DRAG_PREVIEW,
                    width=2.0,
                )
            # Podgląd przeciągania punktu nakładki (drag & drop)
            if self._move_point is not None and self._move_current is not None:
                self._draw_move_ghost(painter)
        else:
            painter.setPen(QColor(140, 155, 170))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, _PLACEHOLDER
            )

    def _draw_overlay_point(
        self, painter: QPainter, point: KeyPoint | SwipePoint | MacroPoint
    ) -> None:
        """Rysuje akcję keymapy: tap = kółko, swipe = strzałka, makro = kroki."""
        if self._phone_size is None:
            return
        if isinstance(point, MacroPoint):
            self._draw_macro_overlay(painter, point)
            return
        if isinstance(point, SwipePoint):
            sx, sy = self._to_widget(point.x1, point.y1)
            ex, ey = self._to_widget(point.x2, point.y2)
            self._draw_overlay_arrow(painter, (sx, sy), (ex, ey), _SWIPE_ARROW)
            self._draw_overlay_circle(painter, sx, sy, point.key)
            # Małe kółko na końcu gestu (bez klawisza)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_OVERLAY_BORDER)
            painter.drawEllipse(QPointF(ex, ey), _SWIPE_END_RADIUS, _SWIPE_END_RADIUS)
            return
        cx, cy = self._to_widget(point.x, point.y)
        self._draw_overlay_circle(painter, cx, cy, point.key)

    def _draw_macro_overlay(self, painter: QPainter, point: MacroPoint) -> None:
        """Rysuje kroki makra: tapy (kółka), swipe'y (strzałki), klawisz przy pierwszym kroku."""
        self._draw_macro_overlay_at(painter, point, 0.0, 0.0)

    def _draw_macro_overlay_at(
        self, painter: QPainter, point: MacroPoint, dx: float, dy: float
    ) -> None:
        """Rysuje kroki makra przesunięte o (dx, dy) px (używane przy drag & drop)."""
        if self._phone_size is None:
            return
        first: tuple[float, float] | None = None
        for action in point.actions:
            kind = action.get("type")
            if kind == "tap":
                x, y = self._to_widget(int(action["x"]), int(action["y"]))
                x, y = x + dx, y + dy
                if first is None:
                    first = (x, y)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(24, 140, 255, 90))
                painter.drawEllipse(QPointF(x, y), 8.0, 8.0)
            elif kind == "swipe":
                sx, sy = self._to_widget(int(action["x1"]), int(action["y1"]))
                ex, ey = self._to_widget(int(action["x2"]), int(action["y2"]))
                sx, sy = sx + dx, sy + dy
                ex, ey = ex + dx, ey + dy
                if first is None:
                    first = (sx, sy)
                self._draw_overlay_arrow(
                    painter, (sx, sy), (ex, ey), _SWIPE_ARROW, width=2.5
                )
        if first is not None:
            self._draw_overlay_circle(painter, first[0], first[1], point.key)

    def _draw_overlay_circle(self, painter: QPainter, cx: float, cy: float, key: str) -> None:
        """Rysuje półprzezroczyste kółko z literą klawisza w środku."""
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
        label = key.upper() if key else "?"
        painter.drawText(
            QRectF(cx - _OVERLAY_RADIUS, cy - _OVERLAY_RADIUS,
                   2 * _OVERLAY_RADIUS, 2 * _OVERLAY_RADIUS),
            Qt.AlignmentFlag.AlignCenter,
            label,
        )

    def _draw_overlay_arrow(
        self,
        painter: QPainter,
        start: tuple[float, float],
        end: tuple[float, float],
        color: QColor,
        width: float = 3.0,
        head_len: float = 12.0,
    ) -> None:
        """Rysuje strzałkę od ``start`` do ``end`` (współrzędne widżetu)."""
        x1, y1 = start
        x2, y2 = end
        pen = QPen(color, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        if math.hypot(x2 - x1, y2 - y1) < 1.0:
            return
        angle = math.atan2(y2 - y1, x2 - x1)
        for offset in (math.pi * 5 / 6, -math.pi * 5 / 6):
            tip = QPointF(
                x2 + head_len * math.cos(angle + offset),
                y2 + head_len * math.sin(angle + offset),
            )
            painter.drawLine(QPointF(x2, y2), tip)

    # ------------------------------------------------------------------
    # Gesty myszy -> współrzędne telefonu
    # ------------------------------------------------------------------

    def set_gesture_mode(self, mode: str) -> None:
        """Ustawia interpretację myszy: ``"tap"`` lub ``"swipe"``.

        W trybie ``"tap"`` kliknięcie emituje :attr:`point_selected`.
        W trybie ``"swipe"`` przeciągnięcie (wciśnij -> przeciągnij ->
        puść) emituje :attr:`swipe_selected` (x1, y1, x2, y2); kliknięcie
        bez przeciągnięcia nadal emituje :attr:`point_selected`.
        """
        if mode not in ("tap", "swipe"):
            raise ValueError(f"Nieznany tryb gestu: {mode!r}")
        self._gesture_mode = mode
        self._drag_start = None
        self._drag_current = None
        self.update()

    def set_capture_enabled(self, enabled: bool) -> None:
        """Włącza/wyłącza przechwytywanie gestów myszy do definiowania akcji.

        Gdy przechwytywanie jest włączone (tryb dodawania tap/swipe lub
        nagrywanie makra), kliknięcia/przeciągnięcia na ekranie są
        zamieniane na sygnały :attr:`point_selected` / :attr:`swipe_selected`.
        Gdy wyłączone - mysz służy do chwytania i przeciągania punktów
        nakładki (drag & drop; patrz :attr:`point_moved`).
        """
        self._capture_enabled = bool(enabled)
        self._drag_start = None
        self._drag_current = None
        self._move_point = None
        self._move_start = None
        self._move_current = None
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (nazwa Qt)
        if event.button() == Qt.MouseButton.LeftButton:
            if self._capture_enabled:
                if self._gesture_mode == "swipe":
                    self._drag_start = event.position()
                    self._drag_current = event.position()
                    self.update()
                else:
                    phone = self._to_phone(event.position())
                    if phone is not None:
                        self.point_selected.emit(*phone)
            else:
                point = self._hit_test_point(event.position())
                if point is not None:
                    self._move_point = point
                    self._move_start = event.position()
                    self._move_current = event.position()
                    self._move_origin = self._point_anchor_phone(point)
                    self.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (nazwa Qt)
        if self._move_point is not None:
            self._move_current = event.position()
            self.update()
        elif self._capture_enabled and self._gesture_mode == "swipe" and self._drag_start is not None:
            self._drag_current = event.position()
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (nazwa Qt)
        if event.button() == Qt.MouseButton.LeftButton and self._move_point is not None:
            point = self._move_point
            start = self._move_start
            end = event.position()
            self._move_point = None
            self._move_current = None
            self.update()
            if start is None:
                return
            dx_w = end.x() - start.x()
            dy_w = end.y() - start.y()
            dx, dy = self._widget_delta_to_phone(dx_w, dy_w)
            origin = self._move_origin
            if origin is None:
                return
            nx, ny = self._clamp_phone(origin[0] + round(dx), origin[1] + round(dy))
            if (nx, ny) != origin:
                self.point_moved.emit(point.name, nx, ny)
            return
        if (
            self._capture_enabled
            and self._gesture_mode == "swipe"
            and self._drag_start is not None
            and event.button() == Qt.MouseButton.LeftButton
        ):
            start = self._drag_start
            end = event.position()
            self._drag_start = None
            self._drag_current = None
            self.update()
            p1 = self._to_phone(start)
            p2 = self._to_phone(end)
            if p1 is None or p2 is None:
                return
            if math.hypot(end.x() - start.x(), end.y() - start.y()) >= _SWIPE_MIN_DRAG_PX:
                self.swipe_selected.emit(*p1, *p2)
            else:
                self.point_selected.emit(*p1)  # klik bez przeciągnięcia = tap
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # Drag & drop punktów nakładki
    # ------------------------------------------------------------------

    def _point_anchor_phone(self, point: AnyPoint) -> tuple[int, int] | None:
        """Kotwica punktu w współrzędnych telefonu (kółko chwytane myszą).

        Dla makra jest to pierwszy krok tap/swipe - spójnie z rysowaniem
        klawisza na nakładce.
        """
        if isinstance(point, KeyPoint):
            return (point.x, point.y)
        if isinstance(point, SwipePoint):
            return (point.x1, point.y1)
        if isinstance(point, MacroPoint):
            for action in point.actions:
                kind = action.get("type")
                if kind == "tap":
                    return (int(action["x"]), int(action["y"]))
                if kind == "swipe":
                    return (int(action["x1"]), int(action["y1"]))
        return None

    def _point_anchor_widget(self, point: AnyPoint) -> tuple[float, float] | None:
        """Kotwica punktu w współrzędnych widżetu (do trafienia myszą)."""
        anchor = self._point_anchor_phone(point)
        if anchor is None:
            return None
        return self._to_widget(*anchor)

    def _hit_test_point(self, pos: QPointF) -> AnyPoint | None:
        """Zwraca punkt nakładki, którego kółko zawiera pozycję myszy (albo ``None``)."""
        if self._phone_size is None or self._draw_rect.isNull():
            return None
        best: AnyPoint | None = None
        best_dist = _DRAG_HIT_RADIUS
        for point in self._overlay_points:
            anchor = self._point_anchor_widget(point)
            if anchor is None:
                continue
            dist = math.hypot(pos.x() - anchor[0], pos.y() - anchor[1])
            if dist < best_dist:
                best, best_dist = point, dist
        return best

    def _widget_delta_to_phone(self, dx: float, dy: float) -> tuple[float, float]:
        """Przelicza przesunięcie myszy [px widżetu] na różnicę w współrzędnych telefonu."""
        if self._phone_size is None or self._draw_rect.isNull():
            return (0.0, 0.0)
        pw, ph = self._phone_size
        r = self._draw_rect
        return (dx / r.width() * pw, dy / r.height() * ph)

    def _clamp_phone(self, x: int, y: int) -> tuple[int, int]:
        """Ogranicza współrzędne telefonu do rozmiaru ekranu."""
        if self._phone_size is None:
            return (x, y)
        pw, ph = self._phone_size
        return (min(max(x, 0), pw - 1), min(max(y, 0), ph - 1))

    def _draw_move_ghost(self, painter: QPainter) -> None:
        """Rysuje "ducha" przeciąganego punktu (podąża za myszą)."""
        point = self._move_point
        current = self._move_current
        start = self._move_start
        if point is None or current is None or start is None:
            return
        dx = current.x() - start.x()
        dy = current.y() - start.y()
        if isinstance(point, KeyPoint):
            self._draw_overlay_circle(
                painter, current.x(), current.y(), point.key
            )
        elif isinstance(point, SwipePoint):
            sx, sy = self._to_widget(point.x1, point.y1)
            ex, ey = self._to_widget(point.x2, point.y2)
            self._draw_overlay_arrow(
                painter, (sx + dx, sy + dy), (ex + dx, ey + dy), _SWIPE_ARROW
            )
            self._draw_overlay_circle(painter, sx + dx, sy + dy, point.key)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_OVERLAY_BORDER)
            painter.drawEllipse(
                QPointF(ex + dx, ey + dy), _SWIPE_END_RADIUS, _SWIPE_END_RADIUS
            )
        elif isinstance(point, MacroPoint):
            self._draw_macro_overlay_at(painter, point, dx, dy)

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

    def _to_widget(self, x: int, y: int) -> tuple[float, float]:
        """Przelicza współrzędne telefonu na pozycję w widżecie (rysowanie)."""
        if self._phone_size is None or self._draw_rect.isNull():
            return (0.0, 0.0)
        pw, ph = self._phone_size
        r = self._draw_rect
        return (
            r.left() + x / pw * r.width(),
            r.top() + y / ph * r.height(),
        )

    # ------------------------------------------------------------------
    # Nakładka akcji keymapy
    # ------------------------------------------------------------------

    def set_overlay_points(
        self, points: list[KeyPoint | SwipePoint | MacroPoint]
    ) -> None:
        """Ustawia akcje keymapy (tapy, swipe'y, makra) do narysowania na ekranie."""
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
