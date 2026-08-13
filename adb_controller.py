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
