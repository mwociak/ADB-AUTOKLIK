"""ADBController - komunikacja z urządzeniami Android przez ADB.

Klasa :class:`ADBController` odpowiada za wykrywanie podłączonych urządzeń,
wybór aktywnego urządzenia oraz wysyłanie komend dotknięć ekranu.
To warstwa komunikacyjna keymappera: klawisz laptopa -> ``input tap`` na
telefonie, z podglądem ekranu dostarczanym osobno (scrcpy).

Komendy wysyłane są przez lokalny serwer ADB (``adb start-server``),
więc każda operacja to szybki round-trip TCP na localhost (rzędu <1 ms).
"""

from __future__ import annotations

import sys

import adbutils
from adbutils import AdbDevice
from adbutils.errors import AdbConnectionError

# Limit czasu pojedynczej komendy shell. Domyślny timeout adbutils to 600 s -
# martwe urządzenie zablokowałoby tap na kilka minut. Przy keymapperze
# (gorąca ścieżka) chcemy szybkiego błędu zamiast czekania.
SHELL_TIMEOUT = 2.0

# Kody systemowych klawiszy Androida (``input keyevent``) - patrz
# https://developer.android.com/reference/android/view/KeyEvent
KEYCODE_BACK = 4
KEYCODE_HOME = 3
KEYCODE_APP_SWITCH = 187
KEYCODE_SLEEP = 223
KEYCODE_WAKEUP = 224
KEYCODE_ENTER = 66


class ADBError(Exception):
    """Błąd komunikacji z ADB z czytelnym (polskim) komunikatem dla użytkownika."""


class ADBController:
    """Kontroler komunikacji ADB: wykrywanie, wybór urządzenia i dotknięcia."""

    def __init__(self) -> None:
        self._device: AdbDevice | None = None

    # ------------------------------------------------------------------
    # Wykrywanie i wybór urządzenia
    # ------------------------------------------------------------------

    def get_devices(self) -> list[str]:
        """Zwraca listę seriali podłączonych urządzeń Android.

        Zwraca seriale urządzeń w stanie gotowości ("device") widzianych
        przez serwer ADB - ``adbutils.adb.device_list()`` filtruje do tego
        stanu, więc urządzenia offline/nieautoryzowane nie są zwracane.
        Jeśli serwer ADB nie działa, zgłasza :class:`ADBError` ze
        wskazówką jak go uruchomić.
        """
        try:
            return [device.serial for device in adbutils.adb.device_list()]
        except AdbConnectionError as exc:
            raise ADBError(
                "Nie można połączyć się z serwerem ADB (127.0.0.1:5037). "
                "Uruchom serwer komendą `adb start-server` i spróbuj ponownie."
            ) from exc
        except (adbutils.AdbError, OSError) as exc:
            raise ADBError(f"Nie można pobrać listy urządzeń ADB: {exc}") from exc

    def connect_device(self, serial: str | None = None) -> str:
        """Wybiera aktywne urządzenie i zwraca jego serial.

        Jeśli ``serial`` jest podany, wybiera urządzenie o tym serialu.
        Jeśli ``serial`` jest ``None``, a podłączone jest dokładnie jedno
        urządzenie w stanie gotowości - wybiera je automatycznie.
        W pozostałych przypadkach zgłasza :class:`ADBError` z czytelnym
        opisem (brak urządzeń / wiele urządzeń / nieautoryzowany dostęp).
        """
        try:
            devices = adbutils.adb.device_list()
        except (adbutils.AdbError, OSError) as exc:
            raise ADBError(f"Nie można pobrać listy urządzeń ADB: {exc}") from exc

        if serial:
            serial = serial.strip()
            device = next((d for d in devices if d.serial == serial), None)
            if device is None:
                raise ADBError(
                    f"Nie znaleziono urządzenia o serialu '{serial}'. "
                    f"Podłączone urządzenia: {', '.join(d.serial for d in devices) or 'brak'}"
                )
            return self._select(device)

        ready = [d for d in devices if self._state(d) == "device"]
        if len(ready) == 1:
            return self._select(ready[0])
        if len(devices) == 0:
            raise ADBError(
                "Brak podłączonych urządzeń Android. Podłącz telefon z włączonym "
                "USB debugging i sprawdź `adb devices`."
            )
        if len(ready) == 0:
            states = ", ".join(f"{d.serial} ({self._state(d)})" for d in devices)
            raise ADBError(
                "Żadne urządzenie nie jest gotowe. Potwierdź okno autoryzacji na "
                f"telefonie i sprawdź stan: {states}"
            )
        raise ADBError(
            "Podłączono więcej niż jedno gotowe urządzenie. Podaj serial, np. "
            f"connect_device({ready[0].serial!r}). Dostępne: "
            + ", ".join(d.serial for d in ready)
        )

    def _select(self, device: AdbDevice) -> str:
        """Weryfikuje stan urządzenia i ustawia je jako aktywne."""
        state = self._state(device)
        if state != "device":
            raise ADBError(
                f"Urządzenie {device.serial} nie jest gotowe (stan: {state}). "
                + ("Potwierdź okno autoryzacji USB na telefonie." if state == "unauthorized" else "Sprawdź połączenie USB i `adb devices`.")
            )
        self._device = device
        return device.serial

    @staticmethod
    def _state(device: AdbDevice) -> str:
        try:
            return device.get_state()
        except (adbutils.AdbError, OSError) as exc:
            raise ADBError(
                f"Nie można sprawdzić stanu urządzenia {device.serial}: {exc}"
            ) from exc

    def _require_device(self) -> AdbDevice:
        if self._device is None:
            raise ADBError("Nie wybrano urządzenia - najpierw wywołaj connect_device().")
        return self._device

    # ------------------------------------------------------------------
    # Komendy na aktywnym urządzeniu
    # ------------------------------------------------------------------

    def tap(self, x: int, y: int) -> bool:
        """Wysyła dotknięcie ekranu w punkcie (x, y) na aktywnym urządzeniu.

        Zwraca ``True`` przy powodzeniu, ``False`` przy niepowodzeniu
        (np. urządzenie odłączone lub timeout) - keymapper nie powinien
        wywalać całej aplikacji przy pojedynczym nieudanym dotknięciu.
        O szczegółach błędu informuje log na stderr.

        Uwaga o latencji: używamy bezpośrednio ``device.shell`` z listą
        argumentów (bez parsowania stringa na urządzeniu) i krótkim
        timeoutem, żeby komenda wykonała się jak najszybciej.
        """
        device = self._require_device()
        x, y = int(x), int(y)
        try:
            device.shell(["input", "tap", str(x), str(y)], timeout=SHELL_TIMEOUT)
            return True
        except (adbutils.AdbError, OSError) as exc:
            print(
                f"[ADBController] Nie udało się wysłać dotknięcia ({x}, {y}) "
                f"na {device.serial}: {exc}",
                file=sys.stderr,
            )
            return False

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
    ) -> bool:
        """Wysyła gest przesunięcia (swipe/drag) od (x1, y1) do (x2, y2).

        ``duration_ms`` to czas przeciągnięcia palcem: krótkie wartości
        (np. 100-300) dają szybki "flick", dłuższe (600-800) wolniejszy,
        bardziej naturalny drag. Zwraca ``True`` przy powodzeniu,
        ``False`` przy niepowodzeniu (odłączone urządzenie, timeout) -
        analogicznie do :meth:`tap`.
        """
        device = self._require_device()
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
        duration_ms = max(0, int(duration_ms))
        try:
            device.shell(
                ["input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)],
                timeout=SHELL_TIMEOUT,
            )
            return True
        except (adbutils.AdbError, OSError) as exc:
            print(
                f"[ADBController] Nie udało się wysłać swipe "
                f"({x1}, {y1}) -> ({x2}, {y2}) na {device.serial}: {exc}",
                file=sys.stderr,
            )
            return False

    # ------------------------------------------------------------------
    # Systemowe zdarzenia klawiszy (nawigacja) i kontrola ekranu
    # ------------------------------------------------------------------

    def send_keyevent(self, key_code: int) -> bool:
        """Wysyła systemowy klawisz Android (``input keyevent``).

        Przydatne np. dla paska nawigacji (Wstecz/Home/Ostatnie) - działa
        niezależnie od tego, czy na telefonie włączono sterowanie gestami.
        Zwraca ``True`` przy powodzeniu, ``False`` przy niepowodzeniu
        (np. odłączone urządzenie) - analogicznie do :meth:`tap`.
        """
        device = self._require_device()
        key_code = int(key_code)
        try:
            device.shell(
                ["input", "keyevent", str(key_code)], timeout=SHELL_TIMEOUT
            )
            return True
        except (adbutils.AdbError, OSError) as exc:
            print(
                f"[ADBController] Nie udało się wysłać keyevent {key_code} "
                f"na {device.serial}: {exc}",
                file=sys.stderr,
            )
            return False

    def press_back(self) -> bool:
        """Wstecz (KEYCODE_BACK = 4)."""
        return self.send_keyevent(KEYCODE_BACK)

    def press_home(self) -> bool:
        """Home (KEYCODE_HOME = 3)."""
        return self.send_keyevent(KEYCODE_HOME)

    def press_recents(self) -> bool:
        """Przegląd ostatnich aplikacji (KEYCODE_APP_SWITCH = 187)."""
        return self.send_keyevent(KEYCODE_APP_SWITCH)

    def turn_off_screen(self) -> bool:
        """Wygasza ekran telefonu (KEYCODE_SLEEP = 223)."""
        return self.send_keyevent(KEYCODE_SLEEP)

    def wake_up_screen(self) -> bool:
        """Budzi ekran (KEYCODE_WAKEUP = 224) i przeciąga palcem w górę.

        Swipe w górę (500, 1500 -> 500, 500) omija ekran blokady, więc
        po wybudzeniu telefon jest od razu gotowy do interakcji.
        Zwraca ``False``, gdy wybudzenie lub swipe się nie powiedzie.
        """
        if not self.send_keyevent(KEYCODE_WAKEUP):
            return False
        return self.swipe(500, 1500, 500, 500, duration_ms=150)

    def unlock_with_pin(self, pin: str) -> bool:
        """Budzi ekran i odblokowuje PIN-em: swipe w górę + ``input text`` + Enter.

        Sekwencja: wakeup -> swipe w górę (ekran blokady) -> wpisanie PIN-u
        (``input text``) -> potwierdzenie klawiszem Enter (keyevent 66).
        Pusty PIN nie jest wysyłany (zwraca ``False``).
        """
        pin = (pin or "").strip()
        if not pin:
            print(
                "[ADBController] Pusty PIN - pomijam odblokowanie.",
                file=sys.stderr,
            )
            return False
        if not self.wake_up_screen():
            return False
        device = self._require_device()
        try:
            device.shell(["input", "text", pin], timeout=SHELL_TIMEOUT)
            device.shell(
                ["input", "keyevent", str(KEYCODE_ENTER)], timeout=SHELL_TIMEOUT
            )
            return True
        except (adbutils.AdbError, OSError) as exc:
            print(
                f"[ADBController] Nie udało się odblokować PIN-em "
                f"na {device.serial}: {exc}",
                file=sys.stderr,
            )
            return False

    # ------------------------------------------------------------------
    # Orientacja ekranu (obrót / wymuszenie trybu poziomego)
    # ------------------------------------------------------------------

    def set_orientation(self, rotation: int) -> bool:
        """Wymusza orientację ekranu urządzenia (0-3) i wyłącza auto-rotację.

        Wyłącza automatyczną rotację (``accelerometer_rotation = 0``)
        i ustawia sztywny obrót ``user_rotation``:
            0 - pion (portrait),
            1 - poziom (landscape, 90°),
            2 - pion odwrócony (180°),
            3 - poziom odwrócony (270°).

        Zwraca ``True`` przy powodzeniu, ``False`` przy błędzie
        (np. odłączone urządzenie) - analogicznie do :meth:`tap`.
        """
        device = self._require_device()
        rotation = int(rotation) % 4
        try:
            device.shell(
                ["settings", "put", "system", "accelerometer_rotation", "0"],
                timeout=SHELL_TIMEOUT,
            )
            device.shell(
                ["settings", "put", "system", "user_rotation", str(rotation)],
                timeout=SHELL_TIMEOUT,
            )
            return True
        except (adbutils.AdbError, OSError) as exc:
            print(
                f"[ADBController] Nie udało się ustawić orientacji {rotation} "
                f"na {device.serial}: {exc}",
                file=sys.stderr,
            )
            return False

    def force_landscape(self) -> bool:
        """Wymusza tryb poziomy (landscape): auto-rotacja OFF + ``user_rotation 1``.

        Przydatne np. dla gier, które wyświetlają się poprawnie tylko
        w poziomie (gdy preview streamu pokazuje je błędnie w pionie).
        """
        return self.set_orientation(1)

    def force_portrait(self) -> bool:
        """Wymusza tryb pionowy (portrait): auto-rotacja OFF + ``user_rotation 0``."""
        return self.set_orientation(0)

    def enable_auto_rotate(self) -> bool:
        """Przywraca automatyczną rotację ekranu (powrót do pionu).

        Włącza ``accelerometer_rotation = 1`` i resetuje ``user_rotation 0``
        - ekran znów obraca się razem z telefonem, a domyślna orientacja
        to pion. Zwraca ``True`` przy powodzeniu, ``False`` przy błędzie.
        """
        device = self._require_device()
        try:
            device.shell(
                ["settings", "put", "system", "accelerometer_rotation", "1"],
                timeout=SHELL_TIMEOUT,
            )
            device.shell(
                ["settings", "put", "system", "user_rotation", "0"],
                timeout=SHELL_TIMEOUT,
            )
            return True
        except (adbutils.AdbError, OSError) as exc:
            print(
                f"[ADBController] Nie udało się włączyć auto-rotacji "
                f"na {device.serial}: {exc}",
                file=sys.stderr,
            )
            return False

    def get_screen_size(self) -> tuple[int, int] | None:
        """Zwraca rozdzielczość ekranu telefonu jako krotkę (width, height).

        Rozmiar pobierany jest przez ``wm size`` (z uwzględnieniem rotacji).
        Przy błędzie (odłączone urządzenie itd.) zwraca ``None`` zamiast
        przerywać działanie aplikacji.
        """
        device = self._require_device()
        try:
            size = device.window_size()
            return (size.width, size.height)
        except (adbutils.AdbError, OSError) as exc:
            print(
                f"[ADBController] Nie można pobrać rozdzielczości z {device.serial}: {exc}",
                file=sys.stderr,
            )
            return None

    # ------------------------------------------------------------------
    # Pomocnicze
    # ------------------------------------------------------------------

    @property
    def device_serial(self) -> str | None:
        """Serial aktualnie wybranego urządzenia (``None``, gdy brak wyboru)."""
        return self._device.serial if self._device else None

    @property
    def is_connected(self) -> bool:
        """``True``, gdy wybrano aktywne urządzenie."""
        return self._device is not None
