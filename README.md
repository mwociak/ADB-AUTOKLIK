# ADB-AUTOKLIK 🎮📱

**Keymapper dla Androida** – mapuj klawisze swojej klawiatury na dotknięcia, gesty przeciągnięcia i złożone makra na ekranie telefonu.  
Aplikacja łączy się z urządzeniem przez **ADB**, wyświetla podgląd ekranu na żywo (via **scrcpy**) i pozwala definiować akcje bezpośrednio na obrazie telefonu.

> 🖥️ **Aplikacja desktopowa** dla systemu Windows (Python + PyQt6).  
> Nie działa w przeglądarce – to natywny program z GUI.

---

## ✨ Główne funkcje

- **Podgląd ekranu telefonu na żywo** – strumień wideo przez scrcpy z niskimi opóźnieniami.
- **Mapowanie klawiszy** – przypisz dowolny klawisz klawiatury do:
  - **Tap** (kliknięcie w wybrane współrzędne X/Y)
  - **Swipe** (przeciągnięcie od punktu do punktu z określonym czasem)
  - **Makro** – sekwencja kroków: tap, swipe, delay (pauza)
- **Intuicyjne dodawanie akcji** – włącz tryb nagrywania, kliknij/przeciągnij na podglądzie telefonu, wciśnij klawisz, podaj nazwę – gotowe.
- **Nakładka na ekranie** – wszystkie zdefiniowane akcje są widoczne na obrazie telefonu (kółka z literami klawiszy, strzałki dla swipe'ów, kroki makr).
- **Keymapper w tle** – globalny nasłuch klawiszy (pynput) działa nawet gdy okno aplikacji nie jest aktywne.
- **Obsługa wielu urządzeń** – przełączanie między podłączonymi telefonami, połączenie przez USB lub Wi-Fi (ADB over TCP/IP).
- **Profile akcji** – zapisywane w pliku `keymap.json` – możesz je edytować ręcznie lub udostępniać.
- **Ciemny motyw** – stylistyka Fusion z dopracowaną kolorystyką.

---

## 🖼️ Zrzut ekranu

*(Wkrótce – możesz dodać obrazek np. `screenshot.png`)*

---

## 📦 Technologie

- **Python 3.11+**
- **PyQt6** – interfejs graficzny
- **pynput** – globalne przechwytywanie klawiszy
- **adbutils** – komunikacja z ADB (wysyłanie komend `input tap/swipe`)
- **scrcpy-client** – strumieniowanie ekranu telefonu (H.264)
- **av** + **numpy** – dekodowanie i przetwarzanie klatek wideo

---

## ⚙️ Wymagania wstępne

1. **Python 3.11 lub nowszy** – [pobierz](https://www.python.org/downloads/)
2. **ADB (Android Platform Tools)** – [pobierz](https://developer.android.com/studio/releases/platform-tools) i dodaj do zmiennej środowiskowej `PATH`
3. **Telefon z Androidem** z włączonym **debugowaniem USB** (oraz autoryzacją komputera)
4. **Sterowniki USB** dla Twojego telefonu (zwykle dla Samsunga, Xiaomi, Huawei itp.)
5. **Połączenie USB** lub **Wi-Fi** (ADB over TCP/IP) – w aplikacji znajdziesz opcję połączenia bezprzewodowego.

---

## 🚀 Instalacja

1. Sklonuj repozytorium:
   ```bash
   git clone https://github.com/mwociak/ADB-AUTOKLIK.git
   cd ADB-AUTOKLIK
   (Zalecane) Utwórz wirtualne środowisko:

bash
python -m venv venv
venv\Scripts\activate      # Windows
# lub source venv/bin/activate (Linux/Mac)
Zainstaluj zależności:

bash
pip install -r requirements.txt
Uwaga: scrcpy-client jest instalowany bezpośrednio z repozytorium GitHub (commit ad57b15), ponieważ wersja z PyPI ma przestarzałe zależności.

▶️ Uruchomienie
Po podłączeniu telefonu i zainstalowaniu zależności uruchom:

bash
python main.py
W oknie aplikacji:

Wybierz swoje urządzenie z listy (lub podaj adres IP dla połączenia Wi-Fi) i kliknij „Połącz”.

Po chwili pojawi się podgląd ekranu telefonu.

Przejdź do panelu „Dodaj nową akcję”:

Wybierz typ: Tap, Swipe lub Makro.

Włącz „Tryb dodawania” (dla Tap/Swipe) lub „Nagraj z ekranu” (dla Makro).

Kliknij/przeciągnij na podglądzie telefonu – aplikacja zapamięta współrzędne.

Wciśnij klawisz na klawiaturze, który ma wywoływać tę akcję.

Podaj nazwę i kliknij „Zapisz akcję”.

Włącz „Keymapper Aktywny” – od teraz wciśnięcie przypisanego klawisza symuluje dotknięcie/gest na telefonie.

📂 Struktura plików
Plik	Opis
main.py	Punkt wejścia – konfiguruje ciemny motyw i uruchamia okno główne.
main_window.py	Główne okno – integracja wszystkich komponentów, logika UI.
adb_controller.py	Komunikacja z ADB – wysyłanie tap, swipe, pobieranie rozdzielczości.
config_manager.py	Zarządzanie profilem akcji (zapis/odczyt keymap.json).
stream_widget.py	Widżet podglądu ekranu (scrcpy) z nakładką i obsługą myszy.
requirements.txt	Lista zależności.
keymap.json	(tworzony automatycznie) – plik z zapisanymi akcjami.

🛠️ Planowane ulepszenia
□ Automatyczne wykrywanie ścieżki ADB (obecnie wymagane w PATH)
□ Możliwość edycji istniejących akcji (zmiana współrzędnych, klawisza)
□ Przeciąganie punktów na nakładce w celu dostosowania
□ Eksport/import profili jako plik JSON
□ Tryb „klikacz” – automatyczne odtwarzanie makra w pętli
🤝 Wkład w rozwój
Jeśli masz pomysł na ulepszenie, znalazłeś błąd lub chcesz pomóc w rozwoju – zapraszam do:

Zakładki Issues

Pull Requestów

📝 Licencja
Projekt udostępniony na licencji MIT – możesz go dowolnie wykorzystywać, modyfikować i rozpowszechniać.

👤 Autor
mwociak – GitHub

⭐ Jeśli projekt Ci pomaga – postaw mu gwiazdkę! Dziękuję! 😊
