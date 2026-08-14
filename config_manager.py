"""ConfigManager - zarządzanie profilem mapowania punktów (keymap).

Klasa :class:`ConfigManager` zapisuje i odczytuje profil mapowania
klawiszy laptopa na akcje symulowane na ekranie Androida (dotknięcia
oraz gesty przesunięcia) w pliku ``keymap.json``.

Format pliku JSON::

    {
      "points": [
        {"kind": "tap", "name": "Skill 1", "key": "a", "x": 450, "y": 1200},
        {"kind": "swipe", "name": "Dash", "key": "space",
         "x1": 900, "y1": 1500, "x2": 300, "y2": 1500, "duration_ms": 300}
      ]
    }

Kompatybilność wsteczna: wpisy bez pola ``kind`` (stary format, tylko
tap) są nadal poprawnie wczytywane jako :class:`KeyPoint`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Union

# Jeden klawisz = jedna akcja (tap LUB swipe) - unia obu typów punktów.
AnyPoint = Union["KeyPoint", "SwipePoint"]


@dataclass
class KeyPoint:
    """Pojedynczy punkt mapowania: klawisz laptopa -> dotknięcie ekranu."""

    name: str
    key: str
    x: int
    y: int

    KIND = "tap"

    def to_dict(self) -> dict[str, Any]:
        """Serializacja punktu do słownika (format pliku JSON)."""
        return {"kind": self.KIND, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KeyPoint":
        """Deserializacja punktu ze słownika (odporne na brakujące pola)."""
        return cls(
            name=str(data.get("name", "")).strip(),
            key=str(data.get("key", "")).strip().lower(),
            x=int(data.get("x", 0)),
            y=int(data.get("y", 0)),
        )


@dataclass
class SwipePoint:
    """Punkt mapowania typu swipe: klawisz laptopa -> gest przesunięcia.

    Pola ``x1``/``y1`` to punkt startowy gestu, ``x2``/``y2`` - końcowy,
    ``duration_ms`` - czas trwania przeciągnięcia w milisekundach.
    """

    name: str
    key: str
    x1: int
    y1: int
    x2: int
    y2: int
    duration_ms: int = 300

    KIND = "swipe"

    def to_dict(self) -> dict[str, Any]:
        """Serializacja punktu do słownika (format pliku JSON)."""
        return {"kind": self.KIND, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SwipePoint":
        """Deserializacja punktu ze słownika (odporne na brakujące pola)."""
        return cls(
            name=str(data.get("name", "")).strip(),
            key=str(data.get("key", "")).strip().lower(),
            x1=int(data.get("x1", 0)),
            y1=int(data.get("y1", 0)),
            x2=int(data.get("x2", 0)),
            y2=int(data.get("y2", 0)),
            duration_ms=int(data.get("duration_ms", 300)),
        )


def _point_from_dict(data: dict[str, Any]) -> AnyPoint:
    """Buduje punkt ze słownika JSON.

    Wpis z ``kind == \"swipe\"`` staje się :class:`SwipePoint`; każdy inny
    (w tym stary format bez pola ``kind``) - :class:`KeyPoint` (tap).
    """
    if str(data.get("kind", "")).lower() == SwipePoint.KIND:
        return SwipePoint.from_dict(data)
    return KeyPoint.from_dict(data)


class ConfigManager:
    """Menadżer konfiguracji keymapy zapisywanej w pliku JSON."""

    def __init__(self, config_path: str | Path = "keymap.json") -> None:
        self.config_path = Path(config_path)
        self.points: list[AnyPoint] = []

    # ------------------------------------------------------------------
    # Odczyt / zapis pliku
    # ------------------------------------------------------------------

    def load_config(self) -> list[AnyPoint]:
        """Wczytuje punkty z pliku JSON.

        Zwraca listę punktów (tapów i swipe'ów). Jeśli plik nie istnieje,
        zwraca pustą listę (a nie rzuca wyjątku). Przy uszkodzonym JSON
        rzuca ``ValueError``. Stare wpisy bez ``kind`` traktowane są jako tap.
        """
        self.points = []
        if not self.config_path.exists():
            return self.points

        try:
            raw = self.config_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(
                f"Nie można odczytać pliku konfiguracji {self.config_path}: {exc}"
            ) from exc

        entries = data.get("points", []) if isinstance(data, dict) else []
        self.points = [
            _point_from_dict(entry) for entry in entries if isinstance(entry, dict)
        ]
        return self.points

    def save_config(self) -> None:
        """Zapisuje aktualne punkty do pliku JSON (atomowo, przez plik tymczasowy)."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"points": [p.to_dict() for p in self.points]}
        tmp_path = self.config_path.with_name(self.config_path.name + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self.config_path)

    # ------------------------------------------------------------------
    # Edycja punktów
    # ------------------------------------------------------------------

    def add_point(self, name: str, key: str, x: int, y: int) -> KeyPoint:
        """Dodaje punkt dotknięcia (tap) do profilu i zapisuje konfigurację.

        Jeśli istnieje już akcja przypisana do tego samego klawisza
        (tap lub swipe), zostaje ona zastąpiona nową (jeden klawisz =
        jedna akcja).
        """
        name = name.strip()
        key = key.strip().lower()
        if not name:
            raise ValueError("Nazwa punktu nie może być pusta")
        if not key:
            raise ValueError("Klawisz nie może być pusty")

        point = KeyPoint(name=name, key=key, x=int(x), y=int(y))
        self.points = [p for p in self.points if p.key != key]
        self.points.append(point)
        self.save_config()
        return point

    def add_swipe(
        self,
        name: str,
        key: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
    ) -> SwipePoint:
        """Dodaje gest przesunięcia (swipe) do profilu i zapisuje konfigurację.

        Analogicznie do :meth:`add_point` - dodanie swipe nadpisuje
        dotychczasowe mapowanie tego samego klawisza.
        """
        name = name.strip()
        key = key.strip().lower()
        if not name:
            raise ValueError("Nazwa punktu nie może być pusta")
        if not key:
            raise ValueError("Klawisz nie może być pusty")

        point = SwipePoint(
            name=name,
            key=key,
            x1=int(x1),
            y1=int(y1),
            x2=int(x2),
            y2=int(y2),
            duration_ms=max(0, int(duration_ms)),
        )
        self.points = [p for p in self.points if p.key != key]
        self.points.append(point)
        self.save_config()
        return point

    def remove_point(self, name_or_key: str | int) -> bool:
        """Usuwa punkt (tap lub swipe) po nazwie, klawiszu lub indeksie.

        Zwraca ``True``, jeśli punkt został usunięty, ``False`` w przeciwnym
        razie (np. nie znaleziono). Konfiguracja jest zapisywana tylko
        w przypadku faktycznego usunięcia punktu.
        """
        if isinstance(name_or_key, int):
            try:
                del self.points[name_or_key]
            except IndexError:
                return False
        else:
            target = name_or_key.strip().lower()
            for i, point in enumerate(self.points):
                if point.name.lower() == target or point.key == target:
                    del self.points[i]
                    break
            else:
                return False

        self.save_config()
        return True

    # ------------------------------------------------------------------
    # Pomocnicze
    # ------------------------------------------------------------------

    def get_point(self, name_or_key: str) -> AnyPoint | None:
        """Zwraca punkt (tap lub swipe) po nazwie lub klawiszu, albo ``None``."""
        target = name_or_key.strip().lower()
        for point in self.points:
            if point.name.lower() == target or point.key == target:
                return point
        return None
