"""AdKillerWorker - automatyczne zamykanie reklam (Template Matching).

Niezależny moduł działający w osobnym wątku :class:`QThread` - nie
blokuje pętli zdarzeń PyQt6, nie dotyka keymappera ani makr
(``macro_runner.py`` / ``action_editor.py`` pozostają nietknięte).

Co ``interval_ms`` (domyślnie 1500 ms) pobiera aktualną klatkę streamu
przez ``AndroidScreenWidget.get_latest_frame()`` (bezpieczna dla wątków -
wewnętrzny lock) i szuka wzorców reklam (obrazy PNG z katalogu
``ad_templates/``) metodą ``cv2.matchTemplate`` w trybie grayscale
(``cv2.TM_CCOEFF_NORMED``). Krzyżyki "X" bywają różnej wielkości, dlatego
każdy wzorzec jest dopasowywany w kilku skalach.

Gdy najlepsze dopasowanie przekroczy próg ``threshold`` (domyślnie 0.8),
obliczany jest środek krzyżyka i wysyłany ``adb.tap(x, y)`` bezpośrednio
z wątku roboczego (komenda i tak nie zablokowałaby GUI, bo worker to
osobny wątek), po czym skanowanie wstrzymuje się na ``cooldown_s``
(domyślnie 3 s), żeby nie klikać wielokrotnie w tę samą reklamę.

Sygnały (bezpieczne - queued do wątku GUI):
    detected(int, int)      - wysłano tap na środek wzorca (x, y),
    status_message(str)     - komunikat (np. liczba wczytanych wzorców).
"""

from __future__ import annotations

import os
import sys
import threading

import cv2
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

# Skale, w których dopasowujemy wzorzec (krzyżyki o różnym rozmiarze).
DEFAULT_SCALES = (0.6, 0.8, 1.0, 1.25, 1.5)


def _log(message: str) -> None:
    """Log diagnostyczny Ad Killer (stderr - nie miesza się ze stdout)."""
    print(f"[AdKillerWorker] {message}", file=sys.stderr)


def default_templates_dir() -> str:
    """Zwraca domyślny katalog wzorców Ad Killer (``ad_templates/``).

    W wersji spakowanej PyInstallerem (``getattr(sys, "frozen", False)``)
    katalog leży obok pliku wykonywalnego - wzorce dodane przez użytkownika
    nie znikają po restarcie (a wbudowane, dołączone do .exe, służą jako
    startowe). W trybie źródłowym - w bieżącym katalogu roboczym.
    """
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.getcwd()
    return os.path.join(base, "ad_templates")


def find_best_match(
    frame: np.ndarray, template: np.ndarray, scales: tuple[float, ...] = DEFAULT_SCALES
) -> tuple[float, int, int] | None:
    """Najlepsze dopasowanie wzorca do klatki (grayscale, multi-skala).

    ``frame`` to klatka BGR (jak z scrcpy), ``template`` - wzorzec
    w grayscale. Zwraca ``(score, center_x, center_y)`` w współrzędnych
    klatki dla najlepszej skali albo ``None``, gdy żadna skala nie
    zmieściła się w klatce.
    """
    frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    fh, fw = frame_gray.shape[:2]
    best: tuple[float, int, int] | None = None
    for scale in scales:
        tw = max(1, int(round(template.shape[1] * scale)))
        th = max(1, int(round(template.shape[0] * scale)))
        if tw >= fw or th >= fh:
            continue  # wzorzec (po przeskalowaniu) większy od klatki
        resized = cv2.resize(template, (tw, th), interpolation=cv2.INTER_AREA)
        result = cv2.matchTemplate(frame_gray, resized, cv2.TM_CCOEFF_NORMED)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
        if best is None or max_val > best[0]:
            best = (
                float(max_val),
                max_loc[0] + tw // 2,
                max_loc[1] + th // 2,
            )
    return best


class AdKillerWorker(QThread):
    """Skanuje klatki streamu w poszukiwaniu wzorców reklam (osobny wątek)."""

    detected = pyqtSignal(int, int)  # wysłano tap: (x, y) - środek wzorca
    status_message = pyqtSignal(str)

    def __init__(
        self,
        adb: "ADBController",
        stream: "AndroidScreenWidget",
        templates_dir: str | None = None,
        threshold: float = 0.8,
        interval_ms: int = 1500,
        cooldown_s: float = 3.0,
        parent: "QObject | None" = None,
    ) -> None:
        super().__init__(parent)
        self._adb = adb
        self._stream = stream
        self._templates_dir = templates_dir or default_templates_dir()
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._threshold = float(threshold)
        self._interval_ms = max(100, int(interval_ms))
        self._cooldown_s = max(0.0, float(cooldown_s))
        self._templates: list[tuple[str, np.ndarray]] = []
        # Sygnatura (lista plików) katalogu wzorców - do wykrywania zmian
        # na dysku bez cache z czasu startu aplikacji.
        self._dir_signature: list[str] | None = None

    # ------------------------------------------------------------------
    # Parametry (bezpieczne wywołania z wątku GUI)
    # ------------------------------------------------------------------

    def set_threshold(self, threshold: float) -> None:
        """Ustawia próg czułości dopasowania (0.0-1.0)."""
        with self._lock:
            self._threshold = min(max(float(threshold), 0.0), 1.0)

    def set_interval_ms(self, interval_ms: int) -> None:
        """Ustawia interwał skanowania w milisekundach (min. 100 ms)."""
        with self._lock:
            self._interval_ms = max(100, int(interval_ms))

    def reload_templates(self) -> None:
        """Przeładowuje listę wzorców PNG z katalogu (bezpieczne z GUI).

        Wywoływane przy każdym starcie workera, po zmianie plików w katalogu
        oraz z okna konfiguracji - wzorce NIGDY nie pochodzą z cache
        utworzonego przy starcie aplikacji.
        """
        templates: list[tuple[str, np.ndarray]] = []
        names: list[str] = []
        if os.path.isdir(self._templates_dir):
            for name in sorted(os.listdir(self._templates_dir)):
                names.append(name)
                if not name.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
                    continue
                path = os.path.join(self._templates_dir, name)
                image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
                if image is None:
                    continue
                templates.append((name, image))
        with self._lock:
            self._templates = templates
            self._dir_signature = names
        if templates:
            loaded = ", ".join(name for name, _ in templates)
            _log(f"Wczytano {len(templates)} wzorców z {self._templates_dir}/: {loaded}")
            self.status_message.emit(
                f"🛡️ Wczytano {len(templates)} wzorców reklam z {self._templates_dir}/"
            )
        else:
            _log(f"Brak wzorców w katalogu {self._templates_dir}/")
            self.status_message.emit(
                f"🛡️ Brak wzorców w katalogu {self._templates_dir}/"
            )

    def _templates_changed_on_disk(self) -> bool:
        """``True``, gdy lista plików w katalogu wzorców się zmieniła.

        Tani check (``os.listdir``) wykonywany co cykl skanowania; pełne
        przeładowanie (``imread``) tylko przy faktycznej zmianie, więc
        wzorce dodane/usunięte ręcznie są podchwytywane na żywo.
        """
        try:
            names = sorted(os.listdir(self._templates_dir))
        except OSError:
            names = []
        with self._lock:
            if names == self._dir_signature:
                return False
            self._dir_signature = names
            return True

    # ------------------------------------------------------------------
    # Cykl skanowania
    # ------------------------------------------------------------------

    def run(self) -> None:
        # Lista wzorców wczytywana NA NOWO przy każdym starcie workera
        # (a także na żywo przy zmianach plików - patrz pętla poniżej).
        self.reload_templates()
        while not self._stop_event.is_set():
            if self._templates_changed_on_disk():
                self.reload_templates()
            frame = self._stream.get_latest_frame()
            if frame is None:
                # Brak streamu/klatki - poczekaj i spróbuj ponownie.
                self._stop_event.wait(self._interval_ms / 1000.0)
                continue
            with self._lock:
                templates = list(self._templates)
                threshold = self._threshold
            best_name: str | None = None
            best_center: tuple[int, int] | None = None
            best_score = threshold  # szukamy dopasowania powyżej progu
            for name, template in templates:
                match = find_best_match(frame, template)
                if match is None:
                    continue
                score, cx, cy = match
                # Diagnostyka: poziom dopasowania danego wzorca na aktualnym
                # zrzucie ekranu (pomaga dobrać próg czułości).
                _log(f"wzorzec '{name}': max_val={score:.3f} (środek {cx},{cy})")
                if score > best_score:
                    best_score = score
                    best_center = (cx, cy)
                    best_name = name
            if best_center is not None:
                if self._stop_event.is_set():
                    break  # zatrzymano w trakcie skanowania - bez tapu
                cx, cy = best_center
                _log(
                    f"WYKRYTO reklamę '{best_name}' (max_val={best_score:.3f}) "
                    f"-> tap ({cx}, {cy})"
                )
                if self._tap(cx, cy):
                    self.detected.emit(cx, cy)
                # Przerwa po trafieniu - reklama potrzebuje czasu na zamknięcie.
                self._stop_event.wait(self._cooldown_s)
            else:
                self._stop_event.wait(self._interval_ms / 1000.0)

    def _tap(self, x: int, y: int) -> bool:
        """Wysyła tap na środek wzorca (z wątku roboczego - nie blokuje GUI)."""
        if self._adb.device_serial is None:
            return False
        try:
            return bool(self._adb.tap(int(x), int(y)))
        except Exception as exc:  # noqa: BLE001 - worker nie może umrzeć
            print(f"[AdKillerWorker] Błąd tapu ({x}, {y}): {exc}", file=sys.stderr)
            return False

    def stop(self) -> None:
        """Prosi wątek o zakończenie (nie blokuje; bezpieczne z wątku GUI)."""
        self._stop_event.set()
