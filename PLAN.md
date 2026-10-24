# PLAN: PoE1 Build Assistant

Zasada: LLM tylko rozumuje i woła narzędzia. Liczby i fakty z gry zawsze z narzędzi/bazy.

## Faza 0: szkielet
Struktura katalogów z AGENTS.md, pyproject.toml (uv), pusty test pytest, commit.
Gotowe, gdy: pytest przechodzi.

## Faza 1: parser kodów PoB
Dekodowanie (base64 url-safe -> zlib -> XML) do modeli pydantic: Build (klasa, ascendancy,
level, skill groups z gemami i supportami, główny skill, itemy z modami, keystone/notable, config).
CLI: python -m app.parser plik.txt.
Gotowe, gdy: wszystkie pliki z tests/fixtures parsują się, CLI wypisuje podsumowanie, pytest zielony.

## Faza 2: baza danych gry
update_data.py ściąga RePoE, poewiki (Cargo API), poe.ninja do SQLite z wersją i datą.
Tabele: gems, uniques, mods, prices.
Gotowe, gdy: SQL zwraca supporty pasujące do tagów skilla, aplikacja pokazuje datę danych.

## Faza 3: silnik liczący
PoB Community headless (LuaJIT). calc(build) i calc_with_change(build, change).
Plan B: subproces z własnym skryptem Lua (XML na stdin, JSON na stdout).
Gotowe, gdy: DPS zgadza się z GUI PoB (<1%) na fixtures.

## Faza 4: narzędzia dla modelu
get_build_summary, get_gem, find_supports, search_uniques, get_price, simulate_change, compare_with_meta.
Gotowe, gdy: każde ma testy i działa bez LLM.

## Faza 5: pętla agenta
Ollama /api/chat z tools, limit 8 wywołań, logowanie. Prompt: nie zgaduj, rekomendacje z deltami z simulate_change.
Gotowe, gdy: na "jaki support dodać" agent zwraca ranking z liczbami.

## Faza 6: UI
FastAPI + czat (SSE), panel z podsumowaniem buildu, podgląd wywołanych narzędzi.

## Faza 7: jakość
20 pytań testowych, walidacja nazw gemów względem bazy, opcjonalnie provider chmurowy.
