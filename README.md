# ADB-AUTOKLIK 🖱️📱

Zaawansowane narzędzie z graficznym interfejsem użytkownika (GUI) napisane w Pythonie z wykorzystaniem biblioteki Tkinter, które pozwala na łatwe zarządzanie urządzeniami z systemem Android poprzez **Android Debug Bridge (ADB)**.

Projekt powstał z myślą o osobach, które chcą automatyzować codzienne czynności na swoim smartfonie (klikanie, przewijanie, instalacja aplikacji) bez konieczności ręcznego wpisywania komend w terminalu.

## ✨ Główne funkcje

- **Zarządzanie aplikacjami**:
  - Instalacja plików APK.
  - Odinstalowywanie aplikacji.
  - Wyłączanie (disable) i włączanie (enable) pakietów systemowych/użytkownika.
  - Wymuszanie zatrzymania (Force Stop) wybranej aplikacji.

- **Symulacja dotyku i gestów**:
  - Pojedyncze kliknięcie (Tap) we wskazane współrzędne X/Y.
  - Podwójne kliknięcie (Double Tap).
  - Przeciąganie (Swipe) w czterech kierunkach: Góra, Dół, Lewo, Prawo.

- **Informacje o urządzeniu**:
  - Pobieranie i wyświetlanie podstawowych danych o urządzeniu (model, producent, wersja Androida itp.) za pomocą polecenia `adb shell getprop`.

- **Automatyczne klikacze** *(funkcja wbudowana w nazwę projektu)* – możliwość uruchamiania zautomatyzowanych sekwencji dotyku.

## ⚙️ Wymagania wstępne

Zanim uruchomisz program, upewnij się, że masz zainstalowane:

1. **Python 3.6+** – [Pobierz ze strony oficjalnej](https://www.python.org/downloads/)
2. **ADB (Android Platform Tools)** – [Pobierz z Google](https://developer.android.com/studio/releases/platform-tools)
3. **Sterowniki USB** dla Twojego telefonu (dla producentów Samsung, Xiaomi, Huawei itp. – zwykle wymagane osobne sterowniki).
4. **Włączone debugowanie USB** w opcjach programisty na Twoim telefonie.
(Opcjonalnie) Utwórz wirtualne środowisko:

bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# lub
venv\Scripts\activate     # Windows
Projekt korzysta wyłącznie ze standardowej biblioteki Pythona (tkinter, subprocess, threading), więc nie wymaga instalowania zewnętrznych pakietów przez pip.

📍 Ustaw ścieżkę do ADB:

W pliku źródłowym (np. ADB2.py) znajdź zmienną sciezka_adb.

Obecnie domyślnie ustawione są ścieżki:

python
sciezka_adb = r"C:/Users/Admin/Desktop/ADB/ADB.exe"
# lub
sciezka_adb = r"D:/adb/adb.exe"
Zmień ją na bezwzględną ścieżkę do pliku adb.exe na Twoim dysku (np. C:/Platform-tools/adb.exe).

🖥️ Uruchomienie
Po poprawnej konfiguracji ścieżki uruchom program poleceniem:

bash
python ADB2.py
Uwaga: Przed użyciem jakichkolwiek funkcji upewnij się, że Twoje urządzenie jest podłączone kablem USB i widoczne po wpisaniu adb devices w terminalu.

🛠️ Planowane ulepszenia (ToDo)
Projekt jest ciągle rozwijany. W najbliższym czasie planuję dodać:

□ Automatyczne wykrywanie ścieżki ADB w zmiennych środowiskowych PATH.
□ Dynamiczne odświeżanie listy podłączonych urządzeń.
□ Graficzną listę zainstalowanych aplikacji (zamiast ręcznego wpisywania nazw pakietów).
□ Zrzut ekranu jednym kliknięciem.
□ Przycisk do czyszczenia okna logów.
□ Poprawkę zabezpieczającą przed wielokrotnym uruchamianiem skryptu (blokada przycisku START).
🤝 Wkład w rozwój
Jeśli masz pomysł na ulepszenie lub znalazłeś błąd – śmiało:

Zgłoś problem w zakładce Issues.

Wyślij Pull Request z poprawkami.

📝 Licencja
Projekt jest dostępny na licencji MIT. Możesz go dowolnie wykorzystywać, modyfikować i rozpowszechniać.

👤 Autor
mwociak – GitHub

⭐ Jeśli projekt Ci się przydaje, nie zapomnij postawić mu gwiazdki na GitHubie! Dziękuję! 😊

## 🚀 Instalacja i konfiguracja

1. **Sklonuj repozytorium** na swój komputer:
   ```bash
   git clone https://github.com/mwociak/ADB-AUTOKLIK.git
   cd ADB-AUTOKLIK
