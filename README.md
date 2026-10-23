# AIPoeTool

Lokalny asystent AI do Path of Exile 1.

## Plan
- Lokalny model AI przez Ollama
- Agent programistyczny OpenCode
- Doradca buildów
- Analiza zrzutów ekranu
- Obsługa głosu

## Faza 1 — lokalny czat tekstowy (zrealizowana)

Pierwsza wersja to działający czat w konsoli z lokalnym modelem AI przez Ollama.

### Wymagania
- Windows 10/11
- Python 3.11+
- Ollama zainstalowany lokalnie (serwer pod `http://localhost:11434`)
- Model AI pobrany w Ollama, domyślnie `qwen3-coder:30b`

### Instalacja zależności
Aplikacja korzysta wyłącznie ze standardowej biblioteki Pythona, więc nie jest wymagane `pip install` dla działania samego programu.

Do uruchomienia testów wystarczy wbudowany moduł `unittest`.

### Uruchomienie
Z folderu projektu `C:\Users\zacha\Documents\GitHub\AIPoeTool` w PowerShell:

```powershell
python main.py
```

### Konfiguracja
Plik `config.json` pozwala zmienić:
- `ollama_host` — adres serwera Ollama (domyślnie `http://localhost:11434`)
- `model` — nazwa modelu (domyślnie `qwen3-coder:30b`)
- `language` — język odpowiedzi (domyślnie `pl`)

### Komendy w czacie
- `/exit` — zakończ rozmowę
- `/reset` — wyczyść kontekst i rozpocznij nową konwersację
- `/help` — pokaż pomoc

### Testy
```powershell
python -m unittest discover -s .
```

### Kolejne etapy
- Obsługa głosu (STT/TTS)
- Analiza zrzutów ekranu i OCR
- Doradca buildów i integracja z Path of Building
- System wtyczek/modułów
- Interfejs graficzny

## Zasady bezpieczeństwa (Path of Exile)

Asystent jest narzędziem doradczym i nie wchodzi w interakcję z procesem gry:
- brak automatyzacji kliknięć i naciśnięć klawiszy,
- brak wstrzykiwania do `PathOfExile.exe`,
- wyłącznie odczyt lokalnych danych udostępnionych przez użytkownika.