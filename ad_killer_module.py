"""AIAdKillerWorker - automatyczne zamykanie reklam (YOLOv11 / ONNX).

Niezależny moduł działający w osobnym wątku :class:`QThread` - nie
blokuje pętli zdarzeń PyQt6, nie dotyka keymappera ani makr
(``macro_runner.py`` / ``action_editor.py`` pozostają nietknięte).

Co ``interval_ms`` (domyślnie 1500 ms) pobiera aktualną klatkę streamu
przez ``AndroidScreenWidget.get_latest_frame()`` (bezpieczna dla wątków -
wewnętrzny lock) i wykonuje inferencję lekkim modelem detekcji obiektów
YOLOv11 wyeksportowanym do ONNX (domyślnie ``models/ad_detector.onnx``).

Wersja produkcyjna używa WYŁĄCZNIE ``onnxruntime`` + ``opencv-python`` +
``numpy`` - bez ciężkiego PyTorcha i Ultralytics. Model ładowany jest
przez ``onnxruntime.InferenceSession``, klatka skalowana do rozmiaru
wejściowego modelu (domyślnie 640x640, letterbox, RGB, normalizacja do
[0, 1], układ CHW), a detekcje filtrowane po progu pewności dla klas
odpowiedzialnych za zamykanie reklam (np. ``close``, ``skip``,
``dismiss``). Środek wykrytego bounding boxa przeliczany jest z powrotem
na natywne współrzędne klatki i wysyłany ``adb.tap(x, y)`` bezpośrednio
z wątku roboczego, po czym skanowanie wstrzymuje się na ``cooldown_s``
(domyślnie 3 s), żeby nie klikać wielokrotnie w tę samą reklamę.

Sygnały (bezpieczne - queued do wątku GUI):
    detected(int, int)      - wysłano tap na środek reklamy (x, y),
    status_message(str)     - komunikat (np. stan modelu / detekcji).

Bezpieczeństwo (kontynuacja poprzedniej iteracji):
- ``stop()`` przez ``threading.Event`` - pętla i czekania są responsywne,
  nie ma ryzyka zniszczenia działającego QThread,
- brak blokowania GUI (cała praca w wątku roboczym),
- logowanie diagnostyczne (``[AIAdKillerWorker]``) - stan modelu, liczba
  wykrytych obiektów, najlepszy score i trafienie,
- zmiana modelu/parametrów możliwa w locie przez bezpieczne settery.
"""

from __future__ import annotations

import os
import sys
import threading

import cv2
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

# Domyślna ścieżka modelu YOLOv11 (ONNX) - obok aplikacji / w katalogu
# roboczym. Plik dostarczany przez użytkownika (np. eksport z Ultralytics:
#   yolo export model=ad_detector.pt format=onnx imgsz=640
# ).
DEFAULT_MODEL_PATH = os.path.join("models", "ad_detector.onnx")

# Klasy odpowiedzialne za zamykanie reklam (domyślne; gdy obok modelu leży
# plik ``<model>.names`` - jedna nazwa klasy na linię - nazwy ładowane są
# z niego, a filtr klas pozostaje ten zbiór).
DEFAULT_CLOSE_CLASSES = frozenset({"close", "skip", "dismiss"})

# Rozmiar wejściowy modelu (imgsz użyty przy eksporcie).
DEFAULT_INPUT_SIZE = 640

# Wartość wypełnienia letterboxa (standard Ultralytics: szary 114).
_LETTERBOX_VALUE = 114.0


def _log(message: str) -> None:
    """Log diagnostyczny Ad Killer (stderr - nie miesza się ze stdout)."""
    print(f"[AIAdKillerWorker] {message}", file=sys.stderr)


def default_model_path() -> str:
    """Zwraca domyślną ścieżkę modelu ONNX.

    W wersji spakowanej PyInstallerem (``getattr(sys, "frozen", False)``)
    najpierw szukamy modelu dołączonego do bundla (``sys._MEIPASS/models/``
    - dodawany przez build.py), a gdy go nie ma - pliku obok .exe
    (model podmieniony przez użytkownika nie znika po restarcie).
    W trybie źródłowym - w bieżącym katalogu roboczym.
    """
    if getattr(sys, "frozen", False):
        bundled = os.path.join(sys._MEIPASS, "models", "ad_detector.onnx")
        if os.path.isfile(bundled):
            return bundled
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.getcwd()
    return os.path.join(base, "models", "ad_detector.onnx")


def letterbox(
    frame: np.ndarray,
    size: int = DEFAULT_INPUT_SIZE,
) -> tuple[np.ndarray, float, float, float, float]:
    """Skaluje klatkę do ``size x size`` z zachowaniem proporcji (letterbox).

    Zwraca ``(obraz, scale, pad_x, pad_y, orig_w, orig_h)``, gdzie
    ``scale`` to współczynnik skalowania, a ``pad_x``/``pad_y`` - padding
    dodany po bokach (w pikselach przeskalowanego obrazu). Te wartości
    służą do przeliczenia bounding boxów z przestrzeni 640x640 z powrotem
    na natywne współrzędne klatki.
    """
    h, w = frame.shape[:2]
    scale = min(size / w, size / h)
    new_w, new_h = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), _LETTERBOX_VALUE, dtype=np.uint8)
    pad_x = (size - new_w) // 2
    pad_y = (size - new_h) // 2
    canvas[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized
    return canvas, scale, pad_x, pad_y


def preprocess(
    frame: np.ndarray, size: int = DEFAULT_INPUT_SIZE
) -> tuple[np.ndarray, float, float, float, float]:
    """Przygotowuje klatkę do inferencji ONNX (letterbox, RGB, CHW, [0,1]).

    Zwraca ``(blob, scale, pad_x, pad_y)`` - blob o kształcie
    ``(1, 3, size, size)`` w float32 oraz parametry letterboxa do
    przeliczenia współrzędnych.
    """
    boxed, scale, pad_x, pad_y = letterbox(frame, size)
    rgb = cv2.cvtColor(boxed, cv2.COLOR_BGR2RGB)
    blob = rgb.astype(np.float32) / 255.0
    blob = np.transpose(blob, (2, 0, 1))  # HWC -> CHW
    blob = np.expand_dims(blob, axis=0)  # -> (1, 3, H, W)
    return np.ascontiguousarray(blob), scale, pad_x, pad_y


def load_class_names(model_path: str) -> list[str] | None:
    """Nazwy klas z pliku ``<model>.names`` (jedna na linię) albo ``None``."""
    names_path = os.path.splitext(model_path)[0] + ".names"
    if not os.path.isfile(names_path):
        return None
    try:
        with open(names_path, encoding="utf-8") as handle:
            names = [
                line.strip()
                for line in handle
                if line.strip() and not line.strip().startswith("#")
            ]
        return names or None
    except OSError:
        return None


def decode_detections(
    output: np.ndarray,
    threshold: float,
    class_names: list[str] | None,
    close_classes: frozenset[str],
) -> tuple[float, int, int, str, float]:
    """Dekoduje tensor wyjściowy YOLOv11 (ONNX, bez NMS).

    ``output`` ma kształt ``(1, 4 + nc, N)``: 4 pierwsze wiersze to bboxy
    (``x_center, y_center, width, height`` w pikselach przestrzeni
    wejściowej, np. 640x640), dalej per-klasowe pewności (już po
    sigmoidzie - eksport Ultralytics zawiera go w modelu). Dla każdego
    kotwicy bierzemy klasę o najwyższym score i zostawiamy tylko te, które
    przekraczają ``threshold`` i należą do ``close_classes``.

    Zwraca ``(score, cx, cy, class_name, box_w)`` dla najlepszego trafienia
    (najwyższy score) albo ``None``, gdy brak detekcji powyżej progu.
    """
    if output.ndim == 3:
        output = output[0]  # -> (4 + nc, N)
    rows, anchors = output.shape
    if rows < 5 or anchors == 0:
        return None
    nc = rows - 4
    boxes = output[:4]  # (4, N) - xywh w przestrzeni modelu
    scores = output[4:]  # (nc, N)
    class_ids = np.argmax(scores, axis=0)
    max_scores = scores[class_ids, np.arange(anchors)]

    best: tuple[float, int, int, str, float] | None = None
    for idx in np.where(max_scores >= threshold)[0]:
        cls_id = int(class_ids[idx])
        name = _class_name(cls_id, class_names, close_classes)
        # Filtrujemy klasy odpowiedzialne za zamykanie reklam.
        if name not in close_classes:
            continue
        cx, cy = float(boxes[0, idx]), float(boxes[1, idx])
        bw = float(boxes[2, idx])
        score = float(max_scores[idx])
        if best is None or score > best[0]:
            best = (score, cx, cy, name, bw)
    return best


def _class_name(
    cls_id: int, class_names: list[str] | None, close_classes: frozenset[str]
) -> str:
    """Nazwa klasy dla ``cls_id`` (z pliku .names albo domyślna)."""
    if class_names is not None and cls_id < len(class_names):
        return class_names[cls_id]
    if class_names is None and cls_id < len(DEFAULT_CLOSE_CLASSES):
        # Bez pliku .names zakładamy, że model ma klasy close/skip/dismiss.
        return ("close", "skip", "dismiss")[cls_id]
    return f"class_{cls_id}"


class AIAdKillerWorker(QThread):
    """Skanuje klatki streamu modelem YOLOv11 (ONNX) w poszukiwaniu reklam."""

    detected = pyqtSignal(int, int)  # wysłano tap: (x, y) - środek reklamy
    status_message = pyqtSignal(str)

    def __init__(
        self,
        adb: "ADBController",
        stream: "AndroidScreenWidget",
        model_path: str | None = None,
        threshold: float = 0.7,
        interval_ms: int = 1500,
        cooldown_s: float = 3.0,
        close_classes: frozenset[str] | None = None,
        parent: "QObject | None" = None,
    ) -> None:
        super().__init__(parent)
        self._adb = adb
        self._stream = stream
        self._model_path = model_path or default_model_path()
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._threshold = min(max(float(threshold), 0.0), 1.0)
        self._interval_ms = max(100, int(interval_ms))
        self._cooldown_s = max(0.0, float(cooldown_s))
        self._close_classes = close_classes or DEFAULT_CLOSE_CLASSES
        self._session = None  # onnxruntime.InferenceSession (leniwy)
        self._session_lock = threading.Lock()
        self._class_names: list[str] | None = None
        self._input_size = DEFAULT_INPUT_SIZE
        self._loaded_model_path: str | None = None

    # ------------------------------------------------------------------
    # Parametry (bezpieczne wywołania z wątku GUI)
    # ------------------------------------------------------------------

    def set_threshold(self, threshold: float) -> None:
        """Ustawia próg pewności detekcji (0.0-1.0)."""
        with self._lock:
            self._threshold = min(max(float(threshold), 0.0), 1.0)

    def set_interval_ms(self, interval_ms: int) -> None:
        """Ustawia interwał skanowania w milisekundach (min. 100 ms)."""
        with self._lock:
            self._interval_ms = max(100, int(interval_ms))

    def set_model_path(self, model_path: str) -> None:
        """Podmienia model ONNX w locie (przeładowanie przy następnym cyklu).

        Nie blokuje wątku roboczego - nowa ścieżka jest pobierana pod
        lockiem, a model ładowany na początku kolejnego cyklu skanowania.
        """
        with self._lock:
            self._model_path = model_path or default_model_path()
            self._session = None
            self._loaded_model_path = None
        _log(f"Ustawiono nowy model: {self._model_path}")

    # ------------------------------------------------------------------
    # Ładowanie modelu ONNX
    # ------------------------------------------------------------------

    def _ensure_session(self) -> bool:
        """Ładuje (lub przeładowuje) ``onnxruntime.InferenceSession``.

        Zwraca ``True``, gdy model jest gotowy do inferencji. Wywoływane
        z wątku roboczego; zewnętrzny lock ``_session_lock`` chroni przed
        równoczesnym ładowaniem (np. podmiana modelu w trakcie skanowania).
        """
        with self._lock:
            model_path = self._model_path
        if not os.path.isfile(model_path):
            if self._loaded_model_path != model_path:
                _log(f"Brak modelu ONNX: {model_path}")
                self.status_message.emit(
                    f"🛡️ Brak modelu ONNX: {model_path} - wklej ad_detector.onnx "
                    "do katalogu models/ i wybierz go w konfiguracji."
                )
                self._loaded_model_path = model_path
            return False
        if (
            self._session is not None
            and self._loaded_model_path == model_path
        ):
            return True
        with self._session_lock:
            # Podwójne sprawdzenie - inny wątek mógł załadować w międzyczasie.
            if (
                self._session is not None
                and self._loaded_model_path == model_path
            ):
                return True
            try:
                import onnxruntime as ort

                session = ort.InferenceSession(
                    model_path, providers=ort.get_available_providers()
                )
                self._class_names = load_class_names(model_path)
                input_meta = session.get_inputs()[0]
                shape = input_meta.shape
                if (
                    len(shape) == 4
                    and isinstance(shape[2], int)
                    and shape[2] > 0
                ):
                    self._input_size = int(shape[2])
                else:
                    self._input_size = DEFAULT_INPUT_SIZE
                self._session = session
                self._loaded_model_path = model_path
                names = (
                    ", ".join(self._class_names)
                    if self._class_names
                    else "close, skip, dismiss (domyślne)"
                )
                _log(
                    f"Wczytano model ONNX: {model_path} "
                    f"(wejście {self._input_size}x{self._input_size}, "
                    f"klasy: {names}, provider: {session.get_providers()})"
                )
                self.status_message.emit(
                    f"🛡️ Model AI załadowany: {os.path.basename(model_path)}"
                )
                return True
            except Exception as exc:  # noqa: BLE001 - worker nie może umrzeć
                print(
                    f"[AIAdKillerWorker] Błąd ładowania modelu {model_path}: {exc}",
                    file=sys.stderr,
                )
                self.status_message.emit(
                    f"🛡️ Błąd ładowania modelu: {os.path.basename(model_path)}"
                )
                self._session = None
                self._loaded_model_path = model_path
                return False

    # ------------------------------------------------------------------
    # Cykl skanowania
    # ------------------------------------------------------------------

    def run(self) -> None:
        while not self._stop_event.is_set():
            if not self._ensure_session():
                # Model niedostępny - nie mielimy CPU, spróbuj za chwilę.
                self._stop_event.wait(2.0)
                continue
            frame = self._stream.get_latest_frame()
            if frame is None:
                # Brak streamu/klatki - poczekaj i spróbuj ponownie.
                self._stop_event.wait(self._interval_ms / 1000.0)
                continue
            with self._lock:
                threshold = self._threshold
                close_classes = self._close_classes
            hit = self._scan_frame(frame, threshold, close_classes)
            if hit is not None:
                score, cx, cy, name = hit
                if self._stop_event.is_set():
                    break  # zatrzymano w trakcie skanowania - bez tapu
                _log(
                    f"WYKRYTO reklamę '{name}' (conf={score:.3f}) "
                    f"-> tap ({cx}, {cy})"
                )
                if self._tap(cx, cy):
                    self.detected.emit(cx, cy)
                # Przerwa po trafieniu - reklama potrzebuje czasu na zamknięcie.
                self._stop_event.wait(self._cooldown_s)
            else:
                self._stop_event.wait(self._interval_ms / 1000.0)

    def _scan_frame(
        self,
        frame: np.ndarray,
        threshold: float,
        close_classes: frozenset[str],
    ) -> tuple[float, int, int, str] | None:
        """Pojedyncza inferencja + przeliczenie współrzędnych na natywne.

        Zwraca ``(score, x, y, class_name)`` w natywnej rozdzielczości
        klatki albo ``None``. Loguje diagnostykę: liczbę obiektów powyżej
        progu i najlepszy ``max_val`` (poziom pewności) na aktualnym
        zrzucie ekranu z ADB.
        """
        h, w = frame.shape[:2]
        blob, scale, pad_x, pad_y = preprocess(frame, self._input_size)
        try:
            outputs = self._session.run(None, {self._session.get_inputs()[0].name: blob})
        except Exception as exc:  # noqa: BLE001 - worker nie może umrzeć
            print(
                f"[AIAdKillerWorker] Błąd inferencji: {exc}",
                file=sys.stderr,
            )
            return None
        detection = decode_detections(
            outputs[0], threshold, self._class_names, close_classes
        )
        if detection is None:
            return None
        score, cx, cy, name, _bw = detection
        # Przeliczenie ze współrzędnych modelu (letterbox 640x640)
        # na natywne współrzędne klatki streamu.
        ox = int(round((cx - pad_x) / scale))
        oy = int(round((cy - pad_y) / scale))
        ox = min(max(ox, 0), w - 1)
        oy = min(max(oy, 0), h - 1)
        # Diagnostyka: poziom pewności detekcji na aktualnym zrzucie ekranu.
        _log(
            f"detekcja '{name}': conf={score:.3f} (środek {ox},{oy} "
            f"w klatce {w}x{h})"
        )
        return score, ox, oy, name

    def _tap(self, x: int, y: int) -> bool:
        """Wysyła tap na środek reklamy (z wątku roboczego - nie blokuje GUI)."""
        if self._adb.device_serial is None:
            return False
        try:
            return bool(self._adb.tap(int(x), int(y)))
        except Exception as exc:  # noqa: BLE001 - worker nie może umrzeć
            print(f"[AIAdKillerWorker] Błąd tapu ({x}, {y}): {exc}", file=sys.stderr)
            return False

    def stop(self) -> None:
        """Prosi wątek o zakończenie (nie blokuje; bezpieczne z wątku GUI)."""
        self._stop_event.set()
