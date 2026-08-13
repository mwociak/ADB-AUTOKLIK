"""ConfigManager - zarządzanie profilem mapowania punktów (keymap).

Klasa :class:`ConfigManager` zapisuje i odczytuje profil mapowania
klawiszy laptopa na współrzędne symulowanych dotknięć ekranu Androida
w pliku ``keymap.json``.

Format pliku JSON::

    {
      "points": [
        {"name": "Skill 1", "key": "a", "x": 450, "y": 1200},
        {"name": "Jump", "key": "space", "x": 900, "y": 1500}
      ]
    }
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class KeyPoint:
    """Pojedynczy punkt mapowania: klawisz laptopa -> dotknięcie ekranu."""

    name: str
    key: str
    x: int
    y: int

    def to_dict(self) -> dict[str, Any]:
        """Serializacja punktu do słownika (format pliku JSON)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KeyPoint":
        """Deserializacja punktu ze słownika (odporne na brakujące pola)."""
        return cls(
            name=str(data.get("name", "")).strip(),
            key=str(data.get("key", "")).strip().lower(),
            x=int(data.get("x", 0)),
            y=int(data.get("y", 0)),
        )


class ConfigManager:
    """Menadżer konfiguracji keymapy zapisywanej w pliku JSON."""

    def __init__(self, config_path: str | Path = "keymap.json") -> None:
        self.config_path = Path(config_path)
        self.points: list[KeyPoint] = []

    # ------------------------------------------------------------------
    # Odczyt / zapis pliku
    # ------------------------------------------------------------------

    def load_config(self) -> list[KeyPoint]:
        """Wczytuje punkty z pliku JSON.

        Zwraca listę punktów. Jeśli plik nie istnieje, zwraca pustą listę
        (a nie rzuca wyjątku). Przy uszkodzonym JSON rzuca ``ValueError``.
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
            KeyPoint.from_dict(entry) for entry in entries if isinstance(entry, dict)
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
        """Dodaje punkt do profilu i zapisuje konfigurację.

        Jeśli istnieje już punkt przypisany do tego samego klawisza,
        zostaje on zastąpiony nowym (jeden klawisz = jedno dotknięcie).
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

    def remove_point(self, name_or_key: str | int) -> bool:
        """Usuwa punkt po nazwie, klawiszu lub indeksie.

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

    def get_point(self, name_or_key: str) -> KeyPoint | None:
        """Zwraca punkt po nazwie lub klawiszu, albo ``None``, gdy brak."""
        target = name_or_key.strip().lower()
        for point in self.points:
            if point.name.lower() == target or point.key == target:
                return point
        return None
