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

### 🔁 Tryb Powtarzanie (pętla akcji)
- Przełącznik **🔁 Powtarzanie [WYŁ/WŁ]** na górnym pasku uruchamia pętlę dla **ostatniej wybranej akcji** – aż do wyłączenia:
  - **Makro** – po zakończeniu odtwarzania automatycznie zaczyna się od początku (w kółko),
  - **Tap** – wciśnięcie przypisanego klawisza zaczyna powtarzać tapnięcia co ustawioną zwłokę (tap → zwłoka → tap → …).
- Pętla działa w **osobnym wątku** (nie blokuje GUI ani keymappera); wybranie nowej akcji przejmuje pętlę (tap zatrzymuje makro i odwrotnie), a wyłączenie przełącznika zatrzymuje wszystko natychmiast.
- **Zwłoka powtórzenia per punkt**: zaznacz tap w tabeli akcji → pojawi się pole **🔁 Zwłoka powtórzenia** (0–60 000 ms, „Zastosuj"). Wartość zapisywana jest w `keymap.json` jako `repeat_delay_ms`; 0 oznacza domyślną zwłokę programu (500 ms).

### Sterowanie telefonem na żywo (🎮 Sterowanie / ➕ Mapowanie)
- Górny pasek przełącza tryb podglądu: **🎮 Sterowanie** – klik = **tap**, przeciągnięcie = **swipe** wysyłane do telefonu przez ADB (możesz nawigować po aplikacjach bez dotykania ekranu) oraz **➕ Mapowanie** – kliknięcia/gesty definiują akcje keymapy.
- Rozpoczęcie dodawania akcji/makra **automatycznie przełącza** na Mapowanie, a zapis wraca do Sterowania.
- Gest sterowania wykonuje się w osobnym wątku (nie blokuje interfejsu), a wynik aktualizuje wskaźnik LED ADB.

### Edycja makr
- Wybierz makro z tabeli, kliknij krok na liście → tryb edycji kroku z podświetleniem na nakładce.
- **Zastąp pozycję** – kliknij/przeciągnij na ekranie, aby ustawić nowe współrzędne kroku.
- **Drag & drop pojedynczego kroku** – ruszasz tylko zaznaczony krok, reszta makra zostaje.
- **Usuń krok** – usuwa z listy, z `keymap.json` i przerysowuje nakładkę na żywo.
- Podczas odtwarzania makra aktualny krok jest podświetlany na liście.

### Panel urządzeń
- Połączenie **USB** lub **Wi-Fi** (ADB over TCP/IP), lista wykrytych urządzeń, przycisk odświeżania.
- **Wskaźnik LED stanu ADB** – 🟢 ostatnia komenda OK, 🔴 błąd (np. odłączone urządzenie), ⚪ rozłączono.

### Pasek nawigacji telefonu
- Fizyczny pasek pod podglądem ekranu – działa niezależnie od tego, czy na telefonie włączono nawigację gestami:
  - **◀ Wstecz** (keyevent 4), **◯ Home** (keyevent 3), **▢ Ostatnie** (keyevent 187),
  - **🔒 Wygaszenie ekranu** (keyevent 223, KEYCODE_SLEEP),
  - **🔓 Wybudzenie** (keyevent 224 + swipe w górę, aby minąć ekran blokady),
  - `unlock_with_pin(pin)` – wybudzenie + swipe + wpisanie PIN-u + Enter (do odblokowania kodem),
- Komendy wykonują się w osobnym wątku (nie blokują interfejsu), a wynik aktualizuje wskaźnik LED ADB (🟢 OK / 🔴 błąd).
- **Multi-Device Control 🌐** – okno farmy urządzeń:
  - kafelki telefonów z numeracją, nazwą/IP i **podglądem zrzutu ekranu** (pobieranym przez ADB),
  - akcje zbiorcze: **uruchom aplikację po nazwie pakietu na wszystkich**, **sync tapu (X, Y) na wszystkich**, odświeżanie podglądów,
  - odłączanie urządzeń z siatki.

### Ad Killer 🛡️ (automatyczne zamykanie reklam – AI + wzorce ręczne)
- Skanuje klatki streamu w **osobnym wątku** i wykrywa przyciski zamykania reklam lekkim modelem detekcji obiektów **YOLOv11 wyeksportowanym do ONNX** (np. `models/ad_detector.onnx`).
- Inferencja działa przez **`onnxruntime`** (bez ciężkiego PyTorcha/Ultralytics): klatka skalowana jest letterboxem do rozmiaru modelu (640×640), normalizowana i analizowana; detekcje powyżej progu pewności (domyślnie **70%**) dla klas `close` / `skip` / `dismiss` są klikane w środku bounding boxa przez ADB.
- **Wzorce ręczne „✂️ Zaznacz na ekranie”** – działają **bez modelu AI**, obok niego: gdy na streamie pojawi się reklama, kliknij w oknie konfiguracji **Zaznacz na ekranie** i zaznacz myszką prostokąt wokół krzyżyka/przycisku pominięcia. Wycinek zapisuje się jako `template_N.png` w `ad_templates/` i od razu jest używany – worker szuka go na klatkach przez **Template Matching** (`cv2.matchTemplate`, skala szarości, kilka skal) i klika środek dopasowania.
- Model AI i wzorce ręczne działają **równolegle** – Ad Killer klika trafienie z najwyższym wynikiem; wystarczy samo jedno ze źródeł (można używać bez wytrenowanego modelu).
- **🛡️ Auto-Zamykanie [WŁ/WYŁ]** na pasku głównym startuje/zatrzymuje skanowanie.
- **🛡️ Ad Killer Config** – okno konfiguracji: wybór pliku modelu `.onnx` (📂 Przeglądaj…), suwak czułości (confidence threshold), interwał skanowania (ms), podgląd załadowanych klas (z opcjonalnego pliku `<model>.names`), lista wzorców ręcznych z miniaturkami (➕ zaznaczanie na ekranie / 🗑 usuwanie) oraz suwak czułości wzorców.
- Bezpieczne działanie: wątek można zatrzymać w każdej chwili (bez nakładania się instancji), GUI nie jest blokowane, a po kliknięciu reklamy skanowanie robi przerwę (cooldown), żeby nie klikać wielokrotnie. Nowe/usunięte wzorce są wykrywane na żywo (bez restartu).
- Eksport modelu (np. z Ultralytics): `yolo export model=ad_detector.pt format=onnx imgsz=640` – wytrenowany na klasach zamykania reklam (`close`, `skip`, `dismiss`).

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
| **onnxruntime** | inferencja modelu YOLOv11/ONNX – Ad Killer (AI) |
| **opencv-python** | przetwarzanie klatek (letterbox, BGR→RGB) – Ad Killer (AI) |
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
6. **Sterowanie telefonem** – w trybie **🎮 Sterowanie** kliknięcia i gesty myszy na podglądzie są wysyłane do telefonu (nawiguj po aplikacjach bez dotykania ekranu); przełącz na **➕ Mapowanie**, aby zamiast tego definiować akcje.
7. **Powtarzanie akcji** – zaznacz **🔁 Powtarzanie [WŁ]** i wciśnij klawisz tapu albo makra: akcja wykonuje się w pętli aż do wyłączenia przełącznika. Zwłokę między kolejnymi tapnięciami ustawisz po zaznaczeniu tapu w tabeli akcji (pole „🔁 Zwłoka powtórzenia").

### Multi-Device Control

Kliknij **🌐 Multi-Device Control** w górnym pasku głównego okna. W oknie farmy urządzeń dodawaj telefony z listy ADB lub ręcznie po `IP:port`, a następnie używaj akcji zbiorczych (uruchomienie aplikacji / sync tapu na wszystkich urządzeniach).

### Ad Killer (AI + wzorce ręczne)

1. *(Opcjonalnie)* Umieść model **YOLOv11/ONNX** w katalogu `models/` (np. `models/ad_detector.onnx`) – wyeksportowany np. komendą `yolo export model=ad_detector.pt format=onnx imgsz=640`.
2. Otwórz **🛡️ Ad Killer Config** – jeśli używasz modelu, wskaż plik (📂 Przeglądaj…); okno pokaże stan modelu i klasy (close/skip/dismiss, ewentualnie z pliku `<model>.names`).
3. **Bez modelu**: poczekaj aż na telefonie pojawi się reklama, kliknij **✂️ Zaznacz na ekranie** i zaznacz prostokąt wokół przycisku zamknięcia/pominięcia na podglądzie. Wzorzec zapisze się w `ad_templates/` i będzie automatycznie klikany przy kolejnych reklamach (Template Matching).
4. Zaznacz **🛡️ Auto-Zamykanie [WŁ]** – aplikacja sama zamyka reklamy (model AI i/lub wzorce).
5. Czułość detekcji (confidence), czułość wzorców i częstotliwość skanowania regulujesz w oknie konfiguracji.

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
- zbiera binaria/kodeki **PyAV** (`--collect-all av`), **OpenCV** (`--collect-all cv2` – .pyd/.dll ładowane dynamicznie) i **onnxruntime** (`--collect-all onnxruntime` – binaria inferencji ONNX, wymagane przez AI Ad Killer),
- dołącza katalog **`models/`** z modelem ONNX (`ad_detector.onnx`); w wersji spakowanej model leży w `sys._MEIPASS/models/`, a użytkownik może podmienić plik obok .exe (nie znika po restarcie),
- dopina ukryte importy **pynput** dla Windows (`pynput.keyboard._win32`, `pynput.mouse._win32`) – bez nich keymapper milczy w spakowanej wersji.

> 💡 Ikona jest używana tylko jeśli plik `icon.ico` leży obok `build.py` – gdy go brakuje, build przechodzi bez ikony (z ostrzeżeniem).

---

## 📂 Struktura plików

| Plik | Opis |
|---|---|
| `main.py` | Punkt wejścia – ciemny motyw i uruchomienie okna głównego |
| `main_window.py` | Okno główne – kompozycja paneli i łączenie sygnałów Qt |
| `adb_controller.py` | Komunikacja z ADB – `tap`, `swipe`, rozdzielczość, urządzenia |
| `config_manager.py` | Profil akcji – Tap/Swipe/Makro (w tym `repeat_delay_ms` dla tapów), zapis/odczyt `keymap.json` |
| `stream_widget.py` | Podgląd ekranu (scrcpy) z nakładką, gestami, drag & drop i interaktywnym sterowaniem (tap/swipe) |
| `device_panel.py` | Panel połączeń – lista urządzeń, USB/Wi-Fi, wskaźnik LED ADB |
| `action_editor.py` | Formularz akcji – Tap/Swipe/Makro, edytor i podgląd kroków, zwłoka powtórzenia tapu |
| `keymapper_widget.py` | Przełącznik keymappera + silnik nasłuchu klawiszy (pynput) |
| `macro_runner.py` | Wątek odtwarzania makr z sygnałami postępu kroków + `TapRepeatWorker` (pętla tapów w trybie Powtarzanie) |
| `ad_killer_module.py` | Moduł AI Ad Killer – worker QThread z inferencją YOLOv11/ONNX (onnxruntime) |
| `ad_killer_ui.py` | Okno konfiguracji Ad Killer – wybór modelu `.onnx`, czułość (confidence), interwał |
| `models/` | Katalog na model ONNX (`ad_detector.onnx`) dla Ad Killer |
| `multi_device_window.py` | Farma urządzeń – kafelki telefonów i akcje zbiorcze |
| `nav_bar_widget.py` | Pasek nawigacji telefonu – Wstecz/Home/Ostatnie + wygaszanie/wybudzanie ekranu |
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
