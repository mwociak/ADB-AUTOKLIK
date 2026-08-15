# ADB-AUTOKLIK 🎮📱

**Keymapper dla Androida** – mapuj klawisze klawiatury na dotknięcia (Tap), gesty przesunięcia (Swipe) i złożone sekwencje (Makra) na ekranie telefonu.

Aplikacja łączy się z urządzeniem przez **ADB**, pokazuje ekran telefonu na żywo (strumień **scrcpy**) i pozwala definiować akcje bezpośrednio na obrazie telefonu – kółka i strzałki na nakładce pokazują, gdzie trafiają Twoje akcje.

> 🖥️ **Aplikacja desktopowa** (Python + PyQt6) dla systemu Windows.
> Nie działa w przeglądarce – to natywny program z GUI.

---

## ✨ Funkcje

### Mapowanie klawiszy
- **Tap** – pojedyncze dotknięcie w wybrane współrzędne X/Y.
- **Swipe** – przeciągnięcie od punktu do punktu z regulowanym czasem trwania (`duration_ms`).
- **Makro** – sekwencja kroków `Tap` / `Swipe` / `Delay` odtwarzana pod jednym klawiszem.
- Jeden klawisz = jedna akcja (dodanie nowej akcji na zajęty klawisz nadpisuje starą).
- Globalny nasłuch klawiszy (pynput) – działa nawet, gdy okno aplikacji nie jest aktywne.

### Definiowanie akcji na ekranie
- Tryb dodawania: kliknij na podglądzie (Tap), przeciągnij (Swipe) lub nagraj sekwencję kroków (Makro) prosto z ekranu.
- Klawisz możesz wcisnąć **albo wpisać ręcznie** – osobne pole klawisza i nazwy; przycisk zapisu odblokowuje się dynamicznie.
- **Nakładka (overlay)** – wszystkie akcje widoczne na obrazie telefonu: kółka z literami klawiszy, strzałki swipe'ów, kroki makr.
- **Przeciąganie punktów** – złap dowolne kółko/strzałkę i przeciągnij w nowe miejsce; zmiana zapisuje się od razu do `keymap.json`.

### Edycja makr
- Wybierz makro z tabeli, kliknij krok na liście → tryb edycji kroku z podświetleniem na nakładce.
- **Zastąp pozycję** – kliknij/przeciągnij na ekranie, aby ustawić nowe współrzędne kroku.
- **Drag & drop pojedynczego kroku** – ruszasz tylko zaznaczony krok, reszta makra zostaje.
- **Usuń krok** – usuwa z listy, z `keymap.json` i przerysowuje nakładkę na żywo.
- Podczas odtwarzania makra aktualny krok jest podświetlany na liście.

### Panel urządzeń
- Połączenie **USB** lub **Wi-Fi** (ADB over TCP/IP), lista wykrytych urządzeń, przycisk odświeżania.
- **Wskaźnik LED stanu ADB** – 🟢 ostatnia komenda OK, 🔴 błąd (np. odłączone urządzenie), ⚪ rozłączono.
- **Multi-Device Control 🌐** – okno farmy urządzeń:
  - kafelki telefonów z numeracją, nazwą/IP i **podglądem zrzutu ekranu** (pobieranym przez ADB),
  - akcje zbiorcze: **uruchom aplikację po nazwie pakietu na wszystkich**, **sync tapu (X, Y) na wszystkich**, odświeżanie podglądów,
  - odłączanie urządzeń z siatki.

### Zapisywanie i kompatybilność
- Wszystkie akcje trzymane w **`keymap.json`** (prosty JSON – możesz edytować ręcznie).
- Stare pliki z formatu tap/swipe wczytują się bez zmian (kompatybilność wsteczna).

---

## 📦 Technologie

| Składnik | Rola |
|---|---|
| **Python 3.10+** | język |
| **PyQt6** | interfejs graficzny (ciemny motyw Fusion) |
| **pynput** | globalne przechwytywanie klawiszy |
| **adbutils** | komunikacja z ADB (`input tap/swipe`, `screencap`, połączenia) |
| **scrcpy-client** | strumieniowanie ekranu telefonu (H.264) |
| **av + numpy** | dekodowanie i przetwarzanie klatek wideo |
| **PyInstaller** | budowanie samodzielnego `.exe` (skrypt `build.py`) |

---

## ⚙️ Wymagania wstępne

1. **Python 3.10 lub nowszy** – [pobierz](https://www.python.org/downloads/)
2. **ADB (Android Platform Tools)** – [pobierz](https://developer.android.com/studio/releases/platform-tools) i dodaj katalog `platform-tools` do zmiennej środowiskowej `PATH`
3. **Telefon z Androidem** z włączonym **debugowaniem USB** (Developer options → USB debugging) i autoryzacją komputera (potwierdź okno „Allow USB debugging?" na telefonie)
4. **Sterowniki USB** dla swojego telefonu (Samsung, Xiaomi, Huawei itd.)
5. Połączenie **USB** lub **Wi-Fi** (ADB over TCP/IP)

---

## 🚀 Instalacja

```bash
git clone https://github.com/mwociak/ADB-AUTOKLIK.git
cd ADB-AUTOKLIK
```

(Zalecane) Utwórz wirtualne środowisko:

```bash
python -m venv venv
venv\Scripts\activate      # Windows
# lub: source venv/bin/activate   (Linux / Mac)
```

Zainstaluj zależności:

```bash
pip install -r requirements.txt
```

> ℹ️ **Uwaga:** `scrcpy-client` jest instalowany bezpośrednio z repozytorium GitHub (commit `ad57b15`), bo wersja z PyPI ma przestarzałe zależności (`adbutils < 2`, `av < 10`), które kolidują z resztą projektu.

---

## ▶️ Uruchomienie

Po podłączeniu telefonu i instalacji zależności:

```bash
python main.py
```

W oknie aplikacji:

1. **Połącz urządzenie** – wybierz z listy (USB) lub podaj adres `IP:port` dla Wi-Fi i kliknij „Połącz".
2. Po chwili pojawi się **podgląd ekranu telefonu na żywo**.
3. **Dodaj akcję** – w panelu wybierz typ (Tap / Swipe / Makro):
   - *Tap*: włącz tryb dodawania, kliknij punkt na podglądzie.
   - *Swipe*: wciśnij LPM, przeciągnij, puść.
   - *Makro*: nagrywaj kroki z ekranu (klik = Tap, przeciągnięcie = Swipe) i dodawaj pauzy (Delay) spinboxem.
4. Wpisz (lub wciśnij) **klawisz**, podaj **nazwę**, kliknij **„Zapisz akcję"**.
5. Włącz przełącznik **„Keymapper Aktywny"** – od teraz wciśnięcie przypisanego klawisza wykonuje akcję na telefonie.

### Multi-Device Control

Kliknij **🌐 Multi-Device Control** w górnym pasku głównego okna. W oknie farmy urządzeń dodawaj telefony z listy ADB lub ręcznie po `IP:port`, a następnie używaj akcji zbiorczych (uruchomienie aplikacji / sync tapu na wszystkich urządzeniach).

---

## 📦 Budowanie .exe (PyInstaller)

```bash
pip install pyinstaller
python build.py
```

Efekt: **`dist/ADB-AUTOKLIK.exe`** – samodzielny plik wykonywalny.

Skrypt `build.py` automatycznie:

- dołącza **`scrcpy-server.jar`** do bundla w miejscu `sys._MEIPASS/scrcpy/`, którego oczekuje biblioteka (bez tego stream wywala „Failed to connect scrcpy-server after 3 seconds"),
- ustawia **ikonę aplikacji** z pliku `icon.ico` (znajduje się w repozytorium),
- zbiera binaria/kodeki **PyAV** (`--collect-all av`),
- dopina ukryte importy **pynput** dla Windows (`pynput.keyboard._win32`, `pynput.mouse._win32`) – bez nich keymapper milczy w spakowanej wersji.

> 💡 Ikona jest używana tylko jeśli plik `icon.ico` leży obok `build.py` – gdy go brakuje, build przechodzi bez ikony (z ostrzeżeniem).

---

## 📂 Struktura plików

| Plik | Opis |
|---|---|
| `main.py` | Punkt wejścia – ciemny motyw i uruchomienie okna głównego |
| `main_window.py` | Okno główne – kompozycja paneli i łączenie sygnałów Qt |
| `adb_controller.py` | Komunikacja z ADB – `tap`, `swipe`, rozdzielczość, urządzenia |
| `config_manager.py` | Profil akcji – Tap/Swipe/Makro, zapis/odczyt `keymap.json` |
| `stream_widget.py` | Podgląd ekranu (scrcpy) z nakładką, gestami i drag & drop |
| `device_panel.py` | Panel połączeń – lista urządzeń, USB/Wi-Fi, wskaźnik LED ADB |
| `action_editor.py` | Formularz akcji – Tap/Swipe/Makro, edytor i podgląd kroków |
| `keymapper_widget.py` | Przełącznik keymappera + silnik nasłuchu klawiszy (pynput) |
| `macro_runner.py` | Wątek odtwarzania makr z sygnałami postępu kroków |
| `multi_device_window.py` | Farma urządzeń – kafelki telefonów i akcje zbiorcze |
| `build.py` | Skrypt PyInstaller – budowa `dist/ADB-AUTOKLIK.exe` |
| `requirements.txt` | Lista zależności |
| `icon.ico` | Ikona aplikacji (używana przy budowie .exe) |
| `keymap.json` | *(tworzony automatycznie)* – zapisane akcje |

---

## 🛠️ Rozwiązywanie problemów

| Problem | Rozwiązanie |
|---|---|
| `adb: command not found` | Dodaj katalog `platform-tools` z ADB do zmiennej `PATH` i uruchom ponownie terminal |
| „Failed to connect scrcpy-server after 3 seconds" | Użyj wersji ze źródeł (nie buduj exe) albo zbuduj przez `python build.py` – dołącza `scrcpy-server.jar` |
| Telefon nie pojawia się na liście | Sprawdź kabel/port USB, włącz USB debugging, potwierdź autoryzację na telefonie (`adb devices` w terminalu) |
| Keymapper nie reaguje | Włącz przełącznik „Keymapper Aktywny" i sprawdź LED ADB – przy 🔴 urządzenie jest odłączone |
| Błąd instalacji zależności | Upewnij się, że używasz Python 3.10+ i aktualnego `requirements.txt` (scrcpy-client z git commit `ad57b15`) |

---

## 🤝 Wkład w rozwój

Jeśli masz pomysł na ulepszenie, znalazłeś błąd lub chcesz pomóc w rozwoju – zapraszam do:

- zakładki **Issues**
- **Pull Requestów**

---

## 📝 Licencja

Projekt udostępniony na licencji **MIT** – możesz go dowolnie wykorzystywać, modyfikować i rozpowszechniać.

---

## 👤 Autor

**mwociak** – [GitHub](https://github.com/mwociak)

⭐ Jeśli projekt Ci pomaga – postaw mu gwiazdkę! Dziękuję! 😊
