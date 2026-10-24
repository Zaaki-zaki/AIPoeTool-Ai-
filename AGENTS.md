# PoE1 Build Assistant

Lokalna aplikacja: użytkownik wkleja kod Path of Building (PoE 1), pyta o ulepszenia
(supporty, uniqi, kombinacje skilli), a LLM odpowiada na podstawie danych z narzędzi.

## Zasada nadrzędna
LLM NIGDY nie podaje liczb ani faktów o grze z własnej pamięci. Model tylko rozumuje
i woła narzędzia. Każda liczba (DPS, życie, resy, cena) i każdy fakt (gem, unique, mod)
pochodzi z narzędzia lub bazy danych.

## Stack
Python 3.12, uv, FastAPI, pydantic v2, SQLite, pytest, httpx.
Ollama (Qwen3 14B) jako model aplikacji. Silnik liczący: Path of Building Community
przez LuaJIT (headless).

## Struktura
app/parser/   dekodowanie i parsowanie kodów PoB
app/data/     ściąganie i normalizacja danych gry (RePoE, poewiki, poe.ninja) do SQLite
app/engine/   wrapper PoB headless
app/tools/    narzędzia dla modelu (JSON schema + implementacja)
app/agent/    pętla agenta (Ollama /api/chat z tools)
app/web/      FastAPI + frontend
tests/        testy, tests/fixtures/ = prawdziwe kody PoB

## Konwencje
- Type hints wszędzie, pydantic dla modeli danych.
- Każda funkcja publiczna ma test. Najpierw test, potem implementacja tam, gdzie to proste.
- Małe commity, jeden commit = jedna rzecz.
- Bez nowych zależności bez uzasadnienia w komunikacie commita.
- Nie zgaduj struktury zewnętrznych danych: najpierw pobierz próbkę, obejrzyj, potem pisz kod.
- Po każdym zadaniu uruchom pytest i pokaż wynik.

## Zasady pracy
- Rób TYLKO to, o co poproszono w zadaniu. Nie zaczynaj kolejnych faz.
- Kryteria "Gotowe, gdy" z PLAN.md są obowiązujące: nie kończ, dopóki nie są spełnione.
- Jeśli czegoś nie wiesz lub brakuje danych (np. fixtures), zatrzymaj się i napisz co jest potrzebne.
