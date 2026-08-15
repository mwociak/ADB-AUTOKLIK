"""Build script for PyInstaller - pakuje ADB-AUTOKLIK w pojedynczy plik .exe.

Użycie::

    pip install pyinstaller
    python build.py

Efekt: ``dist/ADB-AUTOKLIK.exe`` (Windows) - aplikacja z dołączonym
serwerem scrcpy.

Dlaczego to jest potrzebne
--------------------------
``scrcpy.Client`` twardo zakodował ścieżkę serwera jako
``os.path.dirname(scrcpy.__file__) + \"/scrcpy-server.jar\"`` - bez
parametru ze ścieżką. W spakowanej aplikacji ``scrcpy.__file__`` wskazuje
na ``sys._MEIPASS/scrcpy/``, więc plik musi trafić do bundla dokładnie
tam: cel ``scrcpy`` w ``--add-data`` daje ``sys._MEIPASS/scrcpy/\\
scrcpy-server.jar``. Skrypt znajduje jar w zainstalowanym pakiecie
``scrcpy`` (site-packages) i przekazuje go PyInstallerowi.

Dodatkowo skrypt dopina ``--collect-all av`` (binaria/kodeki PyAV, które
PyInstaller nie wykrywa w pełni automatycznie) oraz jawne hidden-importy
dla ``pynput`` na Windows (moduły platformowe ładowane dynamicznie).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
APP_NAME = "ADB-AUTOKLIK"
SERVER_FILE = "scrcpy-server.jar"


def find_scrcpy_server() -> Path | None:
    """Lokalizuje ``scrcpy-server.jar`` w zainstalowanym pakiecie scrcpy."""
    try:
        import scrcpy  # noqa: PLC0415 - import celowo lokalny (opcjonalna zależność)
    except ImportError:
        return None
    jar = Path(scrcpy.__file__).resolve().parent / SERVER_FILE
    return jar if jar.is_file() else None


def main() -> int:
    jar = find_scrcpy_server()
    if jar is None:
        print(
            "BŁĄD: nie znaleziono scrcpy-server.jar w zainstalowanym pakiecie "
            "scrcpy. Sprawdź, czy `pip install -r requirements.txt` się powiodło.",
            file=sys.stderr,
        )
        return 1
    print(f"[build] scrcpy-server.jar: {jar}")

    # Separator --add-data zależy od platformy (Windows: ';', POSIX: ':'),
    # dlatego używamy os.pathsep. Cel "scrcpy" = _MEIPASS/scrcpy/ w bundlu.
    add_data = f"{jar}{os.pathsep}scrcpy"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name",
        APP_NAME,
        "--add-data",
        add_data,
        # PyAV: podmoduły, pliki danych i binaria (nie wykrywane w pełni automatycznie)
        "--collect-all",
        "av",
        "--hidden-import",
        "adbutils",
    ]
    if sys.platform.startswith("win"):
        # pynput ładuje moduły platformowe dynamicznie - bez tego nasłuch
        # klawiszy milczy w spakowanej aplikacji.
        cmd += [
            "--hidden-import",
            "pynput.keyboard._win32",
            "--hidden-import",
            "pynput.mouse._win32",
        ]

    cmd.append(str(PROJECT_ROOT / "main.py"))

    print("[build] komenda PyInstaller:")
    print("   ", " ".join(cmd))
    return subprocess.call(cmd, cwd=PROJECT_ROOT)


if __name__ == "__main__":
    sys.exit(main())
