"""ConfigManager - zarządzanie profilem mapowania punktów (keymap).

Klasa :class:`ConfigManager` zapisuje i odczytuje profil mapowania
klawiszy laptopa na akcje symulowane na ekranie Androida (dotknięcia,
gesty przesunięcia oraz makra - sekwencje kroków) w pliku ``keymap.json``.

Format pliku JSON::

    {
      "points": [
        {"kind": "tap", "name": "Skill 1", "key": "a", "x": 450, "y": 1200},
        {"kind": "swipe", "name": "Dash", "key": "space",
         "x1": 900, "y1": 1500, "x2": 300, "y2": 1500, "duration_ms": 300},
        {"kind": "macro", "name": "Combo", "key": "q", "actions": [
          {"type": "tap", "x": 450, "y": 1200},
          {"type": "delay", "ms": 150},
          {"type": "swipe", "x1": 900, "y1": 1500, "x2": 300, "y2": 1500,
           "duration_ms": 300}
        ]}
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

# Pojedynczy krok makra: słownik {"type": "tap"|"swipe"|"delay", ...}.
Action = dict[str, Any]

# Jeden klawisz = jedna akcja (tap, swipe LUB makro) - unia typów punktów.
AnyPoint = Union["KeyPoint", "SwipePoint", "MacroPoint"]


def tap_action(x: int, y: int) -> Action:
    """Buduje krok makra typu tap."""
    return {"type": "tap", "x": int(x), "y": int(y)}


def swipe_action(
    x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300
) -> Action:
    """Buduje krok makra typu swipe."""
    return {
        "type": "swipe",
        "x1": int(x1),
        "y1": int(y1),
        "x2": int(x2),
        "y2": int(y2),
        "duration_ms": max(0, int(duration_ms)),
    }


def delay_action(ms: int) -> Action:
    """Buduje krok makra typu delay (pauza w milisekundach)."""
    return {"type": "delay", "ms": max(0, int(ms))}


def normalize_action(data: dict[str, Any]) -> Action:
    """Waliduje i uzupełnia pojedynczy krok makra.

    Odporne na brakujące pola (uzupełnia zera / domyślne wartości).
    Nieznany typ kroku zgłasza ``ValueError``.
    """
    kind = str(data.get("type", "")).lower()
    if kind == "tap":
        return tap_action(int(data.get("x", 0)), int(data.get("y", 0)))
    if kind == "swipe":
        return swipe_action(
            int(data.get("x1", 0)),
            int(data.get("y1", 0)),
            int(data.get("x2", 0)),
            int(data.get("y2", 0)),
            int(data.get("duration_ms", 300)),
        )
    if kind == "delay":
        return delay_action(int(data.get("ms", 0)))
    raise ValueError(f"Nieznany typ kroku makra: {kind!r}")


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


@dataclass
class MacroPoint:
    """Punkt mapowania typu macro: klawisz laptopa -> sekwencja kroków.

    ``actions`` to lista słowników kroków w kolejności wykonania
    (patrz :func:`tap_action`, :func:`swipe_action`, :func:`delay_action`):
    ``{"type": "tap", "x": ..., "y": ...}``,
    ``{"type": "swipe", "x1": ..., "y1": ..., "x2": ..., "y2": ...,
    "duration_ms": ...}``,
    ``{"type": "delay", "ms": ...}``.
    """

    name: str
    key: str
    actions: list[Action]

    KIND = "macro"

    def to_dict(self) -> dict[str, Any]:
        """Serializacja punktu do słownika (format pliku JSON)."""
        return {
            "kind": self.KIND,
            "name": self.name,
            "key": self.key,
            "actions": self.actions,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MacroPoint":
        """Deserializacja punktu ze słownika.

        Niepoprawne kroki (nieznany typ, zły format) są pomijane zamiast
        psuć cały profil.
        """
        actions: list[Action] = []
        for entry in data.get("actions", []):
            if isinstance(entry, dict):
                try:
                    actions.append(normalize_action(entry))
                except ValueError:
                    continue
        return cls(
            name=str(data.get("name", "")).strip(),
            key=str(data.get("key", "")).strip().lower(),
            actions=actions,
        )


def _point_from_dict(data: dict[str, Any]) -> AnyPoint:
    """Buduje punkt ze słownika JSON.

    Wpis z ``kind == "swipe"`` staje się :class:`SwipePoint`,
    ``kind == "macro"`` - :class:`MacroPoint`; każdy inny (w tym stary
    format bez pola ``kind``) - :class:`KeyPoint` (tap).
    """
    kind = str(data.get("kind", "")).lower()
    if kind == SwipePoint.KIND:
        return SwipePoint.from_dict(data)
    if kind == MacroPoint.KIND:
        return MacroPoint.from_dict(data)
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

        Zwraca listę punktów (tapy, swipe'y i makra). Jeśli plik nie
        istnieje, zwraca pustą listę (a nie rzuca wyjątku). Przy
        uszkodzonym JSON rzuca ``ValueError``. Stare wpisy bez ``kind``
        traktowane są jako tap.
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
        (tap, swipe lub makro), zostaje ona zastąpiona nową (jeden
        klawisz = jedna akcja).
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

    def add_macro(self, name: str, key: str, actions: list[Action]) -> MacroPoint:
        """Dodaje makro (sekwencję kroków) do profilu i zapisuje konfigurację.

        Kroki są walidowane przez :func:`normalize_action` - nieznany typ
        zgłasza ``ValueError``. Analogicznie do pozostałych metod - dodanie
        makra nadpisuje dotychczasowe mapowanie tego samego klawisza.
        """
        name = name.strip()
        key = key.strip().lower()
        if not name:
            raise ValueError("Nazwa akcji nie może być pusta")
        if not key:
            raise ValueError("Klawisz nie może być pusty")
        if not actions:
            raise ValueError("Makro musi zawierać co najmniej jeden krok")

        point = MacroPoint(
            name=name,
            key=key,
            actions=[normalize_action(action) for action in actions],
        )
        self.points = [p for p in self.points if p.key != key]
        self.points.append(point)
        self.save_config()
        return point

    def remove_point(self, name_or_key: str | int) -> bool:
        """Usuwa punkt (tap, swipe lub makro) po nazwie, klawiszu lub indeksie.

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
        """Zwraca punkt (tap, swipe lub makro) po nazwie lub klawiszu, albo ``None``."""
        target = name_or_key.strip().lower()
        for point in self.points:
            if point.name.lower() == target or point.key == target:
                return point
        return None

    def move_point(self, name: str, new_x: int, new_y: int) -> bool:
        """Przesuwa akcję o nazwie ``name`` do nowego punktu i zapisuje plik.

        Dla :class:`KeyPoint` ustawia (x, y) = (new_x, new_y). Dla
        :class:`SwipePoint` przesuwa cały gest o wektor wyznaczony z nowej
        pozycji punktu startowego (zachowując kierunek i długość). Dla
        :class:`MacroPoint` przesuwa wszystkie kroki tap/swipe o ten sam
        wektor (``new_x``/``new_y`` to nowa pozycja pierwszego kotwicy
        makra). Zwraca ``True``, jeśli akcja została znaleziona i
        przesunięta; ``False`` w przeciwnym razie.
        """
        target = name.strip().lower()
        for point in self.points:
            if point.name.lower() != target:
                continue
            if isinstance(point, KeyPoint):
                point.x, point.y = int(new_x), int(new_y)
            elif isinstance(point, SwipePoint):
                dx = int(new_x) - point.x1
                dy = int(new_y) - point.y1
                point.x1, point.y1 = int(new_x), int(new_y)
                point.x2 = max(0, point.x2 + dx)
                point.y2 = max(0, point.y2 + dy)
            elif isinstance(point, MacroPoint):
                anchor = self._macro_anchor(point)
                if anchor is None:
                    return False
                dx = int(new_x) - anchor[0]
                dy = int(new_y) - anchor[1]
                for action in point.actions:
                    kind = action.get("type")
                    if kind == "tap":
                        action["x"] = max(0, int(action["x"]) + dx)
                        action["y"] = max(0, int(action["y"]) + dy)
                    elif kind == "swipe":
                        action["x1"] = max(0, int(action["x1"]) + dx)
                        action["y1"] = max(0, int(action["y1"]) + dy)
                        action["x2"] = max(0, int(action["x2"]) + dx)
                        action["y2"] = max(0, int(action["y2"]) + dy)
            else:
                return False
            self.save_config()
            return True
        return False

    @staticmethod
    def _macro_anchor(point: MacroPoint) -> tuple[int, int] | None:
        """Kotwica makra: współrzędne pierwszego kroku tap/swipe (albo ``None``)."""
        for action in point.actions:
            kind = action.get("type")
            if kind == "tap":
                return (int(action["x"]), int(action["y"]))
            if kind == "swipe":
                return (int(action["x1"]), int(action["y1"]))
        return None
