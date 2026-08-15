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
PyInstaller nie wykrywa w pełni automatycznie), ``--collect-all cv2``
(OpenCV - pliki .pyd/.dll ładowane dynamicznie, wymagane przez moduł
Ad Killer) oraz dołącza katalog ``ad_templates/`` (wzorce reklam).
Dopięte są też jawne hidden-importy dla ``pynput`` na Windows (moduły
platformowe ładowane dynamicznie). Plik ``icon.ico`` z repozytorium jest
przekazywany przez ``--icon`` jako ikona .exe i okna aplikacji.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
APP_NAME = "ADB-AUTOKLIK"
SERVER_FILE = "scrcpy-server.jar"
ICON_FILE = PROJECT_ROOT / "icon.ico"  # ikona aplikacji (w repo)


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

    if ICON_FILE.is_file():
        print(f"[build] ikona: {ICON_FILE}")
    else:
        print("[build] UWAGA: brak pliku icon.ico obok build.py - buduję bez ikony")

    templates_dir = PROJECT_ROOT / "ad_templates"
    if templates_dir.is_dir():
        print(f"[build] wzorce ad_templates/: {templates_dir}")
    else:
        print("[build] UWAGA: brak katalogu ad_templates/ - buduję bez wzorców reklam")

    # Separator --add-data zależy od platformy (Windows: ';', POSIX: ':'),
    # dlatego używamy os.pathsep. Cel "scrcpy" = _MEIPASS/scrcpy/ w bundlu,
    # a "ad_templates" - dołączone wzorce startowe reklam.
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
        f"{jar}{os.pathsep}scrcpy",
    ]
    if templates_dir.is_dir():
        cmd += [
            "--add-data",
            f"{templates_dir}{os.pathsep}ad_templates",
        ]
    cmd += [
        # Ikona aplikacji (icon.ico) - ustawia ikonę pliku .exe i okna.
        "--icon",
        str(ICON_FILE),
        # PyAV: podmoduły, pliki danych i binaria (nie wykrywane w pełni automatycznie)
        "--collect-all",
        "av",
        # OpenCV: pliki .pyd/.dll ładowane dynamicznie (Ad Killer - Template Matching)
        "--collect-all",
        "cv2",
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
