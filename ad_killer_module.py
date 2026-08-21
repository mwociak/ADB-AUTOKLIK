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

# Pusty zbiór oznacza "wykrywaj WSZYSTKIE klasy" - każde trafienie
# powyżej progu pewności generuje tap (przydatne gdy model ma niestandardowe
# nazwy klas lub jedną klasę "ad").
DETECT_ALL_CLASSES: frozenset[str] = frozenset()

# Rozmiar wejściowy modelu (imgsz użyty przy eksporcie).
DEFAULT_INPUT_SIZE = 640

# ---------------------------------------------------------------------------
# Wzorce ręczne ("zaznacz na ekranie") - drugie źródło detekcji obok modelu
# ONNX. Użytkownik zaznacza myszką przycisk zamknięcia/pominięcia reklamy na
# podglądzie streamu, fragment zapisuje się jako PNG w ``ad_templates/``, a
# worker szuka go na klatkach przez ``cv2.matchTemplate`` (skala szarości,
# TM_CCOEFF_NORMED, kilka skal). Działa BEZ wytrenowanego modelu.
# ---------------------------------------------------------------------------
DEFAULT_TEMPLATE_THRESHOLD = 0.8

# Skale przeszukiwania - krzyżyki/przyciski bywają różnej wielkości zależnie
# od rozdzielczości i reklam.
_TEMPLATE_SCALES = (1.0, 0.9, 0.8, 0.7, 0.6, 1.1, 1.25, 1.5)

# Minimalny rozmiar wycinka (px) - mniejsze zaznaczenia są odrzucane.
MIN_TEMPLATE_SIZE = 8

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


def default_templates_dir() -> str:
    """Zwraca katalog wzorców ręcznych (``ad_templates/``).

    W wersji spakowanej PyInstallerem katalog leży obok .exe (wzorce dodane
    przez użytkownika nie znikają po restarcie); w trybie źródłowym - w
    bieżącym katalogu roboczym. Katalog jest tworzony, jeśli go nie ma.
    """
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.getcwd()
    path = os.path.join(base, "ad_templates")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def load_templates(
    templates_dir: str | None = None,
) -> list[tuple[str, np.ndarray]]:
    """Wczytuje wzorce PNG (skala szarości) z katalogu ``ad_templates/``.

    Zwraca listę ``(nazwa_pliku, obraz_gray)``. Uszkodzone/nieczytelne
    pliki są pomijane z logiem - worker nigdy nie umiera przez jeden zły
    plik.
    """
    directory = templates_dir or default_templates_dir()
    templates: list[tuple[str, np.ndarray]] = []
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        return templates
    for entry in entries:
        if not entry.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        path = os.path.join(directory, entry)
        image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if image is None or image.size == 0:
            _log(f"Pomijam uszkodzony wzorzec: {path}")
            continue
        templates.append((entry, image))
    return templates


def find_template_match(
    frame: np.ndarray,
    templates: list[tuple[str, np.ndarray]],
    threshold: float,
) -> tuple[float, int, int, str] | None:
    """Szuka wzorców na klatce (Template Matching, kilka skal).

    Porównuje każdą parę (wzorzec × skala) z klatką w skali szarości
    (``cv2.TM_CCOEFF_NORMED``) i zwraca najlepsze trafienie powyżej
    ``threshold`` jako ``(score, cx, cy, nazwa)`` - środek dopasowania w
    natywnych współrzędnych klatki - albo ``None``. Dla każdego wzorca
    loguje najwyższy uzyskany ``max_val`` (diagnostyka progu).
    """
    if not templates:
        return None
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    fh, fw = gray.shape[:2]
    best: tuple[float, int, int, str] | None = None
    for name, template in templates:
        th, tw = template.shape[:2]
        template_best = 0.0
        for scale in _TEMPLATE_SCALES:
            new_w, new_h = int(round(tw * scale)), int(round(th * scale))
            if new_w < MIN_TEMPLATE_SIZE or new_h < MIN_TEMPLATE_SIZE:
                continue
            if new_w >= fw or new_h >= fh:
                continue  # wzorzec większy niż klatka w tej skali
            scaled = (
                template
                if scale == 1.0
                else cv2.resize(
                    template, (new_w, new_h), interpolation=cv2.INTER_AREA
                )
            )
            result = cv2.matchTemplate(gray, scaled, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            template_best = max(template_best, float(max_val))
            if float(max_val) >= threshold:
                cx = max_loc[0] + new_w // 2
                cy = max_loc[1] + new_h // 2
                if best is None or float(max_val) > best[0]:
                    best = (float(max_val), int(cx), int(cy), name)
        _log(f"wzorzec '{name}': max_val={template_best:.3f} (próg {threshold:.2f})")
    return best


def save_template_crop(crop: np.ndarray, templates_dir: str | None = None) -> str:
    """Zapisuje wycinek jako ``template_N.png`` (pierwszy wolny numer).

    Zwraca ścieżkę zapisanego pliku. Nie nadpisuje istniejących wzorców.
    """
    directory = templates_dir or default_templates_dir()
    os.makedirs(directory, exist_ok=True)
    index = 1
    while True:
        path = os.path.join(directory, f"template_{index}.png")
        if not os.path.exists(path):
            if not cv2.imwrite(path, crop):
                raise OSError(f"Nie można zapisać wzorca: {path}")
            return path
        index += 1


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
) -> tuple[float, int, int, str, float] | None:
    """Dekoduje tensor wyjściowy YOLO (ONNX, bez NMS).

    Obsługuje **trzy popularne formaty** eksportu YOLO:

    1. **YOLO raw** ``(4+nc, N)`` — klasyczny format Ultralytics:
       wiersze 0-3 to ``x_center, y_center, width, height``,
       kolejne ``nc`` wierszy to pewności per-klasowe (po sigmoidzie).
       N = liczba kotwic (np. 8400 dla 640x640).

    2. **YOLO transponowany** ``(N, 4+nc)`` — każdy wiersz to jeden
       kotwica: ``x1, y1, x2, y2``, potem pewności per-klasowe.

    3. **NMS** ``(N, 6)`` — ``x1, y1, x2, y2, confidence, class_id``
       (ostatnia kolumna to liczba całkowita).

    Automatycznie wykrywa format. Kluczowa heurystyka: ``nc`` (liczba
    klas) powinna być **mała** (1-100), ``N`` (liczba kotwic) **duża**
    (np. 8400). Porównujemy obie interpretacje i wybieramy tę, która
    daje rozsądne ``nc``.

    ``close_classes`` - zbiór nazw klas do wykrycia. Pusty zbiór
    (``DETECT_ALL_CLASSES``) = wykrywaj WSZYSTKIE klasy powyżej progu.

    Zwraca ``(score, cx, cy, class_name, box_w)`` dla najlepszego trafienia
    albo ``None``.
    """
    if output.ndim == 3:
        output = output[0]  # usuń batch dimension

    rows, cols = output.shape
    _log(
        f"decode: output shape=({rows}, {cols}), dtype={output.dtype}, "
        f"close_classes={close_classes or 'ALL'}"
    )

    # ---------------------------------------------------------------
    # Krok 0: Format NMS — (N, 6) z integer class_id
    # Sprawdzamy najpierw, gdy cols == 6.
    # ---------------------------------------------------------------
    if rows >= 2 and cols == 6:
        last_col = output[:, 5]
        is_integer_col = np.all(last_col == last_col.astype(int))
        if is_integer_col:
            xyxy = output[:, :4]
            obj_scores = output[:, 4]
            class_ids = output[:, 5].astype(int)
            cx = (xyxy[:, 0] + xyxy[:, 2]) / 2.0
            cy = (xyxy[:, 1] + xyxy[:, 3]) / 2.0
            bw = xyxy[:, 2] - xyxy[:, 0]
            _log("Format: NMS (N,6) xyxy+conf+class_id")
            return _pick_best(
                obj_scores, class_ids,
                np.stack([cx, cy]), bw,
                threshold, class_names, close_classes, fmt="xywh_center",
            )

    # ---------------------------------------------------------------
    # Krok 1: Porównaj obie interpretacje i wybierz tę z rozsądnym nc.
    # Interpretacja A: (4+nc, N) → nc_a = rows - 4
    # Interpretacja B: (N, 4+nc) → nc_b = cols - 4
    # ---------------------------------------------------------------
    nc_a = rows - 4 if rows >= 5 else -1
    nc_b = cols - 4 if cols >= 5 else -1
    MAX_CLASSES = 200  # rozsądny maksimum klas

    _log(
        f"Interpretacje: A=({nc_a}+4, ?) nc_a={nc_a}, "
        f"B=(?, {nc_b}+4) nc_b={nc_b}"
    )

    # Wybierz interpretację z mniejszym nc (klasy powinny być małe).
    use_a = False
    use_b = False
    if 0 < nc_a <= MAX_CLASSES and (nc_b <= 0 or nc_b > MAX_CLASSES or nc_a <= nc_b):
        use_a = True
    elif 0 < nc_b <= MAX_CLASSES and (nc_a <= 0 or nc_a > MAX_CLASSES or nc_b < nc_a):
        use_b = True
    elif 0 < nc_a <= MAX_CLASSES and 0 < nc_b <= MAX_CLASSES:
        # Oba rozsądne — wybierz z mniejszym nc (bardziej prawdopodobne)
        use_a = nc_a <= nc_b
        use_b = not use_a

    if use_a:
        # Interpretacja A: (4+nc, N) — klasyczny YOLO raw
        _log(f"Wybrano format A: ({rows},{cols}) = ({nc_a}+4, {cols}), nc={nc_a}")
        boxes_xywh = output[:4]   # (4, N) — xywh
        scores = output[4:]       # (nc, N) — per-klasowe pewności
        class_ids = np.argmax(scores, axis=0)
        max_scores = scores[class_ids, np.arange(cols)]
        return _pick_best(
            max_scores, class_ids, boxes_xywh[:2], boxes_xywh[2],
            threshold, class_names, close_classes, fmt="xywh",
        )

    if use_b:
        # Interpretacja B: (N, 4+nc) — transponowany YOLO
        _log(f"Wybrano format B: ({rows},{cols}) = ({rows}, {nc_b}+4), nc={nc_b}")
        boxes_xyxy = output[:, :4]
        scores = output[:, 4:]
        class_ids = np.argmax(scores, axis=1)
        max_scores = scores[np.arange(rows), class_ids]
        cx = (boxes_xyxy[:, 0] + boxes_xyxy[:, 2]) / 2.0
        cy = (boxes_xyxy[:, 1] + boxes_xyxy[:, 3]) / 2.0
        bw = boxes_xyxy[:, 2] - boxes_xyxy[:, 0]
        return _pick_best(
            max_scores, class_ids,
            np.stack([cx, cy]), bw,
            threshold, class_names, close_classes, fmt="xywh_center",
        )

    _log(
        f"Nie rozpoznano formatu output: shape=({rows},{cols}) — "
        f"nc_a={nc_a}, nc_b={nc_b} (oba > {MAX_CLASSES} lub <=0)"
    )
    return None


def _pick_best(
    max_scores: np.ndarray,
    class_ids: np.ndarray,
    centers: np.ndarray,
    widths: np.ndarray,
    threshold: float,
    class_names: list[str] | None,
    close_classes: frozenset[str],
    fmt: str = "xywh",
) -> tuple[float, int, int, str, float] | None:
    """Wybiera najlepsze trafienie spośród detekcji powyżej progu."""
    above = np.where(max_scores >= threshold)[0]
    _log(
        f"detekcje powyżej progu {threshold:.2f}: {len(above)} "
        f"(z {len(max_scores)} łącznie)"
    )
    best: tuple[float, int, int, str, float] | None = None
    for idx in above:
        cls_id = int(class_ids[idx])
        name = _class_name(cls_id, class_names)
        _log(
            f"  kandydat: klasa={cls_id}('{name}') conf={max_scores[idx]:.3f}"
        )
        # Filtr: pusty close_classes = wykrywaj WSZYSTKIE klasy.
        if close_classes and name not in close_classes:
            _log(f"    -> odrzucono (klasa '{name}' nie w close_classes)")
            continue
        if fmt == "xywh":
            cx, cy = float(centers[0, idx]), float(centers[1, idx])
            bw = float(widths[idx])
        else:  # xywh_center
            cx, cy = float(centers[0, idx]), float(centers[1, idx])
            bw = float(widths[idx])
        score = float(max_scores[idx])
        if best is None or score > best[0]:
            best = (score, cx, cy, name, bw)
    return best


def _class_name(
    cls_id: int, class_names: list[str] | None,
) -> str:
    """Nazwa klasy dla ``cls_id`` (z pliku .names albo domyślna)."""
    if class_names is not None and cls_id < len(class_names):
        return class_names[cls_id]
    # Domyślne nazwy dla klas 0-2 (popularne w modelach ad-killera).
    defaults = {0: "close", 1: "skip", 2: "dismiss"}
    return defaults.get(cls_id, f"class_{cls_id}")


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
        templates_dir: str | None = None,
        template_threshold: float = DEFAULT_TEMPLATE_THRESHOLD,
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
        # Wzorce ręczne ("zaznacz na ekranie") - drugie źródło detekcji.
        self._templates_dir = templates_dir or default_templates_dir()
        self._templates: list[tuple[str, np.ndarray]] = []
        self._templates_lock = threading.Lock()
        self._template_threshold = min(max(float(template_threshold), 0.0), 1.0)
        self._known_templates: set[str] = set()

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

    def set_close_classes(self, close_classes: frozenset[str]) -> None:
        """Ustawia zbiór klas do wykrywania (pusty = wykrywaj WSZYSTKIE)."""
        with self._lock:
            self._close_classes = close_classes
        _log(f"Ustawiono close_classes: {close_classes or 'ALL (detect all)'}")

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

    def set_template_threshold(self, threshold: float) -> None:
        """Ustawia próg dopasowania wzorców ręcznych (0.0-1.0)."""
        with self._lock:
            self._template_threshold = min(max(float(threshold), 0.0), 1.0)

    def reload_templates(self) -> None:
        """Wczytuje wzorce z katalogu ``ad_templates/`` od nowa (bez cache).

        Wywoływane na starcie workera i po każdej zmianie plików na dysku
        (dodanie wzorca "zaznaczeniem na ekranie" lub ręcznie) - wzorce
        nigdy nie są brane z cache utworzonego przy starcie aplikacji.
        """
        loaded = load_templates(self._templates_dir)
        with self._templates_lock:
            self._templates = loaded
            self._known_templates = {name for name, _ in loaded}
        names = ", ".join(name for name, _ in loaded) or "brak"
        _log(f"Wczytano {len(loaded)} wzorców ręcznych z {self._templates_dir}/: {names}")
        self.status_message.emit(
            f"🛡️ Wzorce ręczne: {len(loaded)} (AI + matchTemplate)"
        )

    def _templates_changed_on_disk(self) -> bool:
        """Tani check (os.listdir): czy pliki wzorców zmieniły się na dysku."""
        try:
            current = {
                entry
                for entry in os.listdir(self._templates_dir)
                if entry.lower().endswith((".png", ".jpg", ".jpeg"))
            }
        except OSError:
            return False
        return current != self._known_templates

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
                # Zaloguj kształt wyjścia modelu - kluczowe dla diagnostyki.
                out_meta = session.get_outputs()[0] if session.get_outputs() else None
                out_shape = out_meta.shape if out_meta else "unknown"
                _log(
                    f"Wczytano model ONNX: {model_path}\n"
                    f"  wejście: {input_meta.name} {shape}\n"
                    f"  wyjście: {out_meta.name if out_meta else '?'} {out_shape}\n"
                    f"  klasy: {names}, provider: {session.get_providers()}"
                )
                self.status_message.emit(
                    f"🛡️ Model AI: {os.path.basename(model_path)} "
                    f"(wyjście {out_shape})"
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
        _log("Worker uruchomiony - rozpoczynam skanowanie (AI + wzorce ręczne)")
        self.reload_templates()
        while not self._stop_event.is_set():
            # Model jest opcjonalny - worker działa też wyłącznie na
            # wzorcach ręcznych (gdy brak pliku .onnx).
            session_ready = self._ensure_session()
            frame = self._stream.get_latest_frame()
            if frame is None:
                _log("Brak klatki ze streamu - czekam...")
                self._stop_event.wait(self._interval_ms / 1000.0)
                continue
            # Tani check zmian na dysku - nowy wzorzec (np. właśnie
            # zaznaczony na ekranie) działa od razu, bez restartu, nawet
            # gdy poprzednie cykle trafiały w reklamę.
            if self._templates_changed_on_disk():
                self.reload_templates()
            with self._lock:
                threshold = self._threshold
                close_classes = self._close_classes
                template_threshold = self._template_threshold
            hit = None
            if session_ready:
                hit = self._scan_frame(frame, threshold, close_classes)
            template_hit = self._scan_templates(frame, template_threshold)
            if template_hit is not None and (hit is None or template_hit[0] > hit[0]):
                hit = template_hit
            if hit is None:
                self._stop_event.wait(self._interval_ms / 1000.0)
                continue
            score, cx, cy, name = hit
            if self._stop_event.is_set():
                break  # zatrzymano w trakcie skanowania - bez tapu
            _log(
                f"WYKRYTO reklamę '{name}' (score={score:.3f}) "
                f"-> tap ({cx}, {cy})"
            )
            if self._tap(cx, cy):
                self.detected.emit(cx, cy)
            # Przerwa po trafieniu - reklama potrzebuje czasu na zamknięcie.
            self._stop_event.wait(self._cooldown_s)

    def _scan_templates(
        self, frame: np.ndarray, threshold: float
    ) -> tuple[float, int, int, str] | None:
        """Szuka wzorców ręcznych na klatce (Template Matching)."""
        with self._templates_lock:
            templates = list(self._templates)
        if not templates:
            return None
        return find_template_match(frame, templates, threshold)

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
        _log(f"Skanowanie klatki {w}x{h} -> blob {blob.shape}, scale={scale:.3f}")
        try:
            outputs = self._session.run(None, {self._session.get_inputs()[0].name: blob})
            _log(f"Inferencja OK: {len(outputs)} output(s), shape={[o.shape for o in outputs]}")
        except Exception as exc:  # noqa: BLE001 - worker nie może umrzeć
            _log(f"Błąd inferencji: {exc}")
            return None
        detection = decode_detections(
            outputs[0], threshold, self._class_names, close_classes
        )
        if detection is None:
            _log("Brak detekcji powyżej progu")
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
