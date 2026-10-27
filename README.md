# AIPoeTool

Lokalny doradca do Path of Exile 1: wklejasz kod Path of Building, pytasz po polsku, a lokalny model woła narzędzia,
które liczą wszystko w prawdziwym PoB. Model nie podaje liczb z pamięci.

## Uruchomienie

1. Uruchom **Ollamę** (model: `qwen3-coder-32k`, zmiana: zmienna `AIPOE_MODEL`).
2. Silnik PoB działa w procesie aplikacji (LuaJIT z pakietu `lupa`), bez Dockera. Przy pierwszym starcie sam pobiera Path of Building z GitHuba do `engine/` (ręcznie: `uv run python -m app.calc.setup`). Dane gry: `scripts\update_gamedata.ps1`. Docker jest tylko rezerwą, gdy nie da się pobrać PoB.
3. Aplikacja okienkowa: jednorazowo `uv add pywebview` i `scripts\make_shortcut.ps1` (skrót na pulpicie), potem klik w skrót.
   Ręcznie: `uv run python -m app.desktop`. Tryb przeglądarkowy: `uv run python -m app.web` -> http://127.0.0.1:8000
4. Wklej kod PoB, kliknij "Załaduj build", zadaj pytanie.

Testy: `uv run pytest -q`

## Planer postaci

Bez wczytanego buildu aplikacja działa jako planer: pytasz „w co grać?”, model robi research (YouTube, dla bieżącej
wersji gry i ligi, nie dla okna czasowego) i generuje plan w sekcjach (Akt 1–10, mapy) jak w poradnikach. Cały plan
liczy PoB:

- drzewko: notable'e, **mastery** (z wyborem efektu) i **keystone'y** (tylko przy wyraźnym zysku), punkty z questów
  (24 w kampanii, źródła: mobalytics.gg, poedb.tw) i ascendancy (labirynty 33/55/68/75, źródło: maxroll.gg),
- ekwipunek poglądowy: rare'y z modami z tierami dozwolonymi na dany poziom (dane `data/gear.json` z PoB, odświeżane
  `scripts/build_gear_data.py`), mody dobiera PoB; typ obrażeń wynika z tagów skilla,
- supporty wchodzą do linku tylko, gdy PoB (z ekwipunkiem) pokazuje >= 3% poprawy; przy każdym widać wkład w %,
- opcjonalny budżet gemów (`budget_chaos`) wg cen poe.ninja; rare'y poglądowe nie mają ceny.

Założenia (tempo poziomów w aktach, linki, brak vendorów/kolorów socketów) są wypisywane w wyniku. Podgląd: przycisk
**Plan rozwoju**. Research wymaga darmowego klucza YouTube Data API v3: wpisz `YOUTUBE_API_KEY=...` w pliku `.env`
(wzór: `.env.example`).

## Klejnoty

Przycisk **Klejnoty**: lista socketów z zawartością, zamiana jewel między socketami i próba dowolnego jewela (wynik liczy PoB,
bez zapisywania, dopóki nie klikniesz „Zostaw w buildzie”). Zakładka **Timeless** skanuje wszystkie seedy wybranego jewela
na danych PoB po wpisanych statach (po angielsku, np. `critical strike chance; 2`), najlepsze liczy pełnym kalkulatorem na
buildzie i opcjonalnie sprawdza najtańsze oferty na trade. Czat ma do tego narzędzia `jewel_sockets` i `find_timeless_jewels`.

## Ceny (trade)

Przycisk **Ceny** i skrót **Ctrl+D** w grze: aplikacja kopiuje item pod kursorem (Ctrl+C), szuka podobnych na oficjalnym
trade i pokazuje ceny w małym oknie przy kursorze (cena ogólna, najtańsze oferty, kiedy wystawione; znika samo po odsunięciu
kursora albo po 25 s). **Ctrl+T** pokazuje półprzezroczystą listę węzłów drzewka do wzięcia w następnym etapie planu rozwoju (klawisz ponownie ją chowa; kliknięcia przechodzą do gry; konkretny węzeł znajdziesz w grze przez Ctrl+F w drzewku). Czat z AI działa tylko w oknie na pulpicie (pasek czatu w grze jest domyślnie wyłączony: `AIPOE_STRIP=left` go włącza, wtedy pytania liczy mały model na CPU, `AIPOE_GAME_MODEL`). Nakładki widać tylko przy aktywnej grze i tylko w trybie okna bez ramki (windowed fullscreen); są lekkie (Tkinter) i nie kradną fokusu. Domyślnie szuka tylko ofert z instant buyout (opcja trade „securable"; w oknie głównym można zmienić status). Ceny są przeliczane na chaosy
kursami z poe.ninja. Skrót działa tylko przy aktywnym oknie „Path of Exile”, w innych oknach Ctrl+D działa jak zwykle. Filtry modów (min. wartość, włączenie/wyłączenie) poprawisz w oknie głównym (przycisk Ceny).
Zapytania są odstępowane i respektują limity GGG; wynik to oferty, a nie zrealizowane transakcje.

## Zmienne środowiskowe

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `AIPOE_MODEL` | `qwen3-coder-32k` | model Ollamy |
| `OLLAMA_URL` | `http://localhost:11434` | adres Ollamy |
| `AIPOE_DB` | `data/game.db` | baza gry |
| `YOUTUBE_API_KEY` | (brak) | research popularności buildów |
| `POE_CLIENT_TXT` | (autodetekcja) | ścieżka do logu gry |
| `AIPOE_LEAGUE` | pierwsza liga z poe.ninja | liga do cen |
| `AIPOE_PRICE_KEY` | `D` | litera skrótu cen (Ctrl+litera) |
| `AIPOE_TREE_KEY` | `T` | klawisz listy węzłów drzewka (Ctrl+klawisz) |
| `AIPOE_OVERLAY` | (włączona) | `off` wyłącza nakładki (ceny i czat zostają w oknie głównym) |
| `AIPOE_STRIP` | `off` | pasek czatu w grze: `left`, `center`, `right` albo `off` |
| `AIPOE_NO_HOTKEY` | (brak) | `1` wyłącza globalny skrót |
| `AIPOE_GAME_TITLE` | `Path of Exile` | tytuł okna gry, przy którym skrót działa |
| `POB_ENGINE_CMD` | silnik natywny (lupa), rezerwa: Docker | własne polecenie startu silnika PoB |

## Architektura

- `app/parser` kod PoB -> model buildu
- `app/gamedata` baza gry (SQLite) z danych PoB
- `app/calc` klient silnika PoB (headless, LuaJIT przez lupa; rezerwa Docker)
- `app/trade` klient trade, parser itemów, ceny; `app/hotkey.py` skrót Ctrl+D (Windows)
- `app/planner/jewels.py` jewele i Timeless
- `app/tools` narzędzia wysokiego poziomu (`recommend_supports`)
- `app/agent` sesja, narzędzia dla modelu, pętla agenta, klient Ollamy
- `app/web` serwer FastAPI i interfejs
