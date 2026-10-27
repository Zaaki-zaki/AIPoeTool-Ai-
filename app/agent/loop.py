"""Pętla agenta: pytanie -> (wywołania narzędzi)* -> odpowiedź. Generuje zdarzenia dla interfejsu."""

from __future__ import annotations

import json
import queue
import threading
import time
import re
from collections.abc import Iterator
from typing import Any

from .llm import LLM, LLMError
from .session import Session
from app.tools.common import GOALS
from app.tools.items import SLOT_FILES

from app.knowledge import Knowledge, parse_teach
from .tools import TOOLS, ToolBox, available_tools

MAX_TOOL_CALLS = 10

SYSTEM_PROMPT = """Jesteś asystentem do Path of Exile 1. Odpowiadasz po polsku, zwięźle i konkretnie.
Gra to Path of Exile 1, tylko PvE. PvP, tryby wieloosobowe i inne gry nie istnieją w tym kontekście: nigdy o nich nie pisz.

ZASADY (bez wyjątków):
0. Narzędzia wołasz SAM, w tej samej odpowiedzi. Nigdy nie każ użytkownikowi użyć narzędzia ani nie pisz,
   że "można sprawdzić" czegoś, co możesz sprawdzić sam. Najpierw wywołaj narzędzie, dopiero potem odpowiadaj.
   Nie wypisuj ogólnych rad z głowy ("sprawdź drzewko", "zmień jewele").
   Pytanie ogólne ("co mogę ulepszyć?", "co poprawić?") = wywołaj recommend_supports dla głównego skilla
   (skill_groups w streszczeniu, grupa ze skillem z main_skill_in_pob), bez free_sockets i keep, i oprzyj
   odpowiedź na wyniku. Pytanie o konkretny slot albo item = recommend_items. Jeśli pytanie jest ogólne i
   zależy ci na pełniejszym obrazie, możesz wywołać też recommend_items dla slotów, w których stoi unik
   z listy items w streszczeniu oraz recommend_tree. W odpowiedzi powiedz wprost, co sprawdzono, a czego nie (np. drzewka).
0b. Wyniki recommend_supports czytaj dosłownie. Podawaj tylko zamienniki z better_replacements (ich delta_pct
   to zysk DPS) i werdykt "zostaw" tam, gdzie tak wyszło. Nie wymyślaj progów, wag ani uzasadnień, których
   nie ma w wyniku. side_effects to zmiany w % (np. TotalEHP_pct); brak pola = brak zmiany.
0c. Mów wyłącznie o tym, co sprawdziły narzędzia w tej rozmowie. Nie pisz, że drzewko, jewele, flaski ani nic
   innego jest "w porządku", "optymalne" albo "zoptymalizowane", jeśli tego nie sprawdziłeś. Zamiast "build
   jest dobry" pisz "w sprawdzonym zakresie nie znalazłem nic lepszego". Pole better_but_excluded podawaj
   zawsze, gdy nie jest puste: to uniki lepsze w metryce celu, odrzucone kosztem przeżywalności, resistów albo
   atrybutów (powód w reason) i to jest ważna informacja dla użytkownika.
1. Żadnych liczb ani faktów o grze z pamięci. Każda liczba, nazwa gemu, uniqa czy moda w odpowiedzi
   musi pochodzić z wyniku narzędzia albo ze streszczenia buildu poniżej. Jeśli narzędzie czegoś nie dało,
   napisz, że tego nie wiesz.
2. Nie zgaduj nazw gemów. Jeśli nie jesteś pewien nazwy, użyj get_gem_info albo search_uniques.
3. Pytania o supporty, wolne sockety i zamienniki gemów rozwiązuj narzędziem recommend_supports, a o itemy
   (uniki, "czy zmienić hełm/pierścień/...") narzędziem recommend_items, o drzewko pasywne (zamiany nodów,
   "co zmienić w drzewku") narzędziem recommend_tree. Nie liczysz i nie porównujesz DPS sam.
4. W odpowiedzi podaj: rekomendację, liczby z narzędzia (zmiana DPS w %, efekty uboczne np. EHP),
   alternatywy i krótko zastrzeżenia z pola not_checked, jeśli mają znaczenie. Jeśli narzędzie wykluczyło
   lepszy DPS przez próg przeżywalności, napisz o tym.
5. Pole blind_spots_pob_sees_no_gain to gemy, którym PoB nie daje zysku, ale mogą mieć efekt, którego PoB
   nie modeluje. Gdy ma to znaczenie dla pytania, wywołaj community_info dla tych gemów. Jego wyniki (wiki, Reddit)
   zawsze opisuj jako "niezweryfikowane dane ze społeczności", osobno od liczb z PoB, i nigdy nie dopisuj z nich
   liczb do rankingu. Jeśli źródło jest niedostępne (sources_skipped), napisz to wprost.
6. Ceny i dostępność: tylko z community_info (pole price, orientacyjne, liga w polu league). Opinii graczy bez
   danych z tego narzędzia nie podawaj.

STRESZCZENIE WCZYTANEGO BUILDU (z PoB):
"""

PLANNER_PROMPT = """Jesteś asystentem do Path of Exile 1. Odpowiadasz po polsku, zwięźle i konkretnie.
Gra to Path of Exile 1, tylko PvE. PvP, tryby wieloosobowe i inne gry nie istnieją w tym kontekście: nigdy o nich nie pisz.
Użytkownik NIE wczytał buildu: pomagasz wybrać postać i zaplanować jej rozwój od zera.

ZASADY (bez wyjątków):
1. Żadnych liczb ani faktów o grze z pamięci. Nazwy klas, ascendancy i skilli muszą być prawdziwe: sprawdzaj je
   narzędziami (get_gem_info, make_leveling_plan zgłosi błąd przy złej nazwie). Nie zgaduj.
2. Gdy użytkownik nie wie w co grać ("gram melee", "chcę coś z minionami", "nie mam pomysłu"): najpierw wywołaj
   research_meta (zapytanie: klasa, ascendancy albo styl) i zaproponuj 2-3 opcje z wyniku (skill + ascendancy, udział
   wyświetleń). Opisz je jako NIEZWERYFIKOWANE, przybliżone dane z YouTube. Jeśli research_meta zwróci błąd (np.
   brak klucza), powiedz to wprost i zaproponuj opcje ze swojej wiedzy, zaznaczając, że to nie są dane z researchu.
3. Gdy użytkownik wybierze (albo prosi o "po prostu zrób"), wywołaj make_leveling_plan (klasa, ascendancy, skill). Klasa
   i ascendancy muszą do siebie pasować; jeśli nie wiesz, który skill pasuje do klasy, zapytaj albo użyj research_meta.
4. Po wygenerowaniu planu opisz sekcja po sekcji (akty, mapy): poziom, punkty (w tym z questów), kluczowe nody
   (keystone'y i mastery z efektem), supporty z contribution_pct, broń i rare'y poglądowe, oraz zastrzeżenia z
   not_checked. Plan jest do obejrzenia przyciskiem "Plan rozwoju" (drzewko). Jeśli użytkownik poda budżet, przekaż
   go jako budget_chaos; ceny pochodzą z poe.ninja (poe.trade już nie istnieje), rare'y poglądowe nie mają ceny.
5. Gdy użytkownik podaje skilla (np. "chcę grać pod Cyclone, prowadź mnie do endgame"): wywołaj research_meta z tym
   skillem, potem make_leveling_plan (w PoE 1 nie ma klasy "Warrior": dobierz klasę z bazy, np. Marauder, i zapytaj
   o ascendancy tylko jeśli research ich nie rozstrzyga). Nigdy nie opisuj buildu, gemów ani itemów z własnej wiedzy
   (to jest dokładnie to, czym ta aplikacja ma się różnić od zwykłego czatu). Gdy narzędzie zwróci błąd, napisz tylko,
   jaki to błąd, i nie uzupełniaj odpowiedzi domysłami.
6. Liczby z planu (poziomy, zyski %) pochodzą z make_leveling_plan; nie dodawaj własnych. Ceny: tylko community_info.
"""

_TOOL_TAG = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
_THINK = re.compile(r"<think>.*?</think>", re.S)


def scope_footer(checked_skills: list[str], checked_slots: list[str], tree_checked: bool = False,
                 goal_label: str | None = None, community: list[str] | None = None,
                 community_skipped: list[str] | None = None, plan_made: bool = False) -> str:
    """Zakres sprawdzenia budowany z faktycznych wywołań narzędzi, żeby model nie mógł go sam 'rozszerzyć'."""
    if not checked_skills and not checked_slots and not tree_checked and not community and not community_skipped and not plan_made:
        return ""
    done = []
    if checked_skills:
        done.append("supporty: " + ", ".join(checked_skills))
    if checked_slots:
        done.append("uniki w slotach: " + ", ".join(checked_slots))
    if tree_checked:
        done.append("zamiany nodów w drzewku i ascendancy (tylko notable'y i keystone'y w zasięgu)")
    if plan_made:
        done.append("plan rozwoju w sekcjach liczony w PoB (zachłanny wybór nodów, ekwipunek poglądowy zamiast prawdziwych itemów, założenia w opisie)")
    if community:
        done.append("dane ze społeczności (NIEZWERYFIKOWANE, poza obliczeniami PoB): " + ", ".join(community))
    todo = ["jewele", "flaski", "gemy innych skilli", "craft, rare i magiczne itemy"]
    if not community:
        todo.append("ceny")
    if not tree_checked:
        todo.insert(0, "drzewko pasywne")
    missing_slots = [x for x in SLOT_FILES if x not in checked_slots]
    if checked_slots and missing_slots:
        todo.insert(0, "sloty: " + ", ".join(missing_slots))
    goal = f" Cel rankingu: {goal_label}." if goal_label else ""
    skipped = f" Niedostępne źródła: {', '.join(community_skipped)}." if community_skipped else ""
    return "Zakres sprawdzenia: " + "; ".join(done) + ". Nie sprawdzono: " + ", ".join(todo) + "." + skipped + goal


def system_prompt(session: Session, goal: str | None = None) -> str:
    if not getattr(session, "has_build", True):
        return PLANNER_PROMPT
    chosen = ""
    if goal:
        chosen = (f'\nUżytkownik wybrał w interfejsie cel rankingu: "{goal}". Narzędzia liczą wtedy dla tego celu '
                  "niezależnie od tego, co sam podasz w parametrze goal. Opisując wynik, odwołuj się do tego celu "
                  "(pole goal_label).\n")
    head = SYSTEM_PROMPT.replace("STRESZCZENIE WCZYTANEGO BUILDU", chosen + "STRESZCZENIE WCZYTANEGO BUILDU", 1)
    return head + json.dumps(session.summary, ensure_ascii=False)


def extract_tool_calls(msg: dict[str, Any], tools: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Natywne tool_calls Ollamy; awaryjnie wywołanie zapisane tekstem (<tool_call>{...}</tool_call> lub czysty JSON)."""
    native = msg.get("tool_calls") or []
    calls = []
    for tc in native:
        fn = tc.get("function", {})
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        calls.append({"name": fn.get("name", ""), "arguments": args or {}})
    if calls:
        return calls
    content = (msg.get("content") or "").strip()
    candidates = _TOOL_TAG.findall(content)
    if not candidates and content.startswith("{") and content.endswith("}"):
        candidates = [content]
    known = {t["function"]["name"] for t in (tools or TOOLS)}
    for raw in candidates:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if obj.get("name") in known:
            calls.append({"name": obj["name"], "arguments": obj.get("arguments") or obj.get("parameters") or {}})
    return calls


def _brief(result: dict[str, Any]) -> str:
    if "error" in result:
        return f"błąd: {result['error']}"
    if "key_nodes_in_order" in result:
        return f"plan: {result['skill']} / {result['ascendancy']}, {result['steps_total']} kroków"
    if "videos_found" in result:
        top = (result.get("ranking") or [{}])[0]
        return f"{result['videos_matched']} filmów dopasowano" + (f", na czele {top.get('skill')}" if top else "")
    if "ranking" in result and result["ranking"] and "supports" in result["ranking"][0]:
        top = result["ranking"][0]
        return f"najlepsze: {' + '.join(top['supports'])} ({top['delta_pct']:+.1f}% {result['metric']})"
    if "replace_report" in result:
        return f"oceniono {len(result['replace_report'])} supportów"
    return "ok"


def _call_with_progress(toolbox: ToolBox, name: str, args: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Wywołuje narzędzie w osobnym wątku i na bieżąco przekazuje komunikaty postępu (strumień nie milknie przy długich planach)."""
    q: queue.Queue[tuple[str, float | None]] = queue.Queue()
    box: dict[str, Any] = {}
    toolbox.progress = lambda text, frac=None: q.put((text, frac))

    def work() -> None:
        try:
            box["result"] = toolbox.call(name, args)
        except Exception as e:  # noqa: BLE001
            box["result"] = {"error": f"Błąd narzędzia {name}: {e}"}

    t = threading.Thread(target=work, daemon=True)
    t.start()
    started = time.time()
    while t.is_alive() or not q.empty():
        try:
            text, frac = q.get(timeout=1.0)
            yield {"type": "progress", "text": text, "frac": frac, "elapsed": int(time.time() - started)}
        except queue.Empty:
            if time.time() - started >= 3:  # sygnał życia tylko przy dłuższych narzędziach
                yield {"type": "progress", "text": None, "frac": None, "elapsed": int(time.time() - started)}
    toolbox.progress = None
    return box["result"]


def run_agent(session: Session, llm: LLM, question: str, max_calls: int = MAX_TOOL_CALLS,
              goal: str | None = None) -> Iterator[dict[str, Any]]:
    """Generator zdarzeń: tool_call, tool_result, answer albo error. goal: cel wybrany w interfejsie (albo None)."""
    toolbox = ToolBox(session, goal if goal in GOALS else None)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt(session, goal if goal in GOALS else None)}]
    kb = Knowledge()
    taught = parse_teach(question)
    if taught:  # "zapamiętaj: ..." zapisuje fakt na stałe; od teraz wygrywa przy pasujących pytaniach
        n = kb.teach(taught)
        yield {"type": "answer", "text": f"Zapamiętane (poprawek w bazie: {n}). Będę z tego korzystać przy pasujących pytaniach: {taught}"}
        return
    ctx = kb.context(question)
    if ctx:
        messages.append({"role": "system", "content": ctx})
    messages += session.history + [{"role": "user", "content": question}]
    calls_made = 0
    checked_skills: list[str] = []
    checked_slots: list[str] = []
    tree_checked = False
    goal_label: str | None = None
    community: list[str] = []
    community_skipped: list[str] = []
    plan_made = False
    tools = available_tools(session)
    try:
        while True:
            force_answer = calls_made >= max_calls
            msg = llm.chat(messages, None if force_answer else tools)
            tool_calls = [] if force_answer else extract_tool_calls(msg, tools)
            if not tool_calls:
                answer = _THINK.sub("", msg.get("content") or "").strip() or "(model nie zwrócił odpowiedzi)"
                session.remember(question, answer)
                if calls_made == 0 and not getattr(session, "has_build", True):
                    answer = ("UWAGA: model nie użył żadnego narzędzia, więc ta odpowiedź NIE pochodzi z obliczeń ani z bazy gry "
                              "(niezweryfikowana). Poproś o plan rozwoju z klasą, ascendancy i skillem, żeby policzyć go w PoB.\n\n" + answer)
                footer = scope_footer(checked_skills, checked_slots, tree_checked, goal_label, community, community_skipped, plan_made)
                yield {"type": "answer", "text": answer + ("\n\n" + footer if footer else "")}
                return
            messages.append({
                "role": "assistant", "content": msg.get("content") or "",
                "tool_calls": [{"function": {"name": c["name"], "arguments": c["arguments"]}} for c in tool_calls],
            })
            for c in tool_calls:
                calls_made += 1
                yield {"type": "tool_call", "name": c["name"], "args": c["arguments"]}
                if calls_made > max_calls:
                    result: dict[str, Any] = {"error": "Limit wywołań narzędzi w jednej odpowiedzi został wyczerpany."}
                else:
                    result = yield from _call_with_progress(toolbox, c["name"], c["arguments"])
                if "error" not in result and isinstance(c["arguments"], dict):
                    if c["name"] == "recommend_supports" and result.get("skill") not in checked_skills:
                        checked_skills.append(str(result.get("skill")))
                    if c["name"] == "recommend_items" and result.get("slot") not in checked_slots:
                        checked_slots.append(str(result.get("slot")))
                    if c["name"] == "recommend_tree":
                        tree_checked = True
                    if c["name"] == "make_leveling_plan":
                        plan_made = True
                    if c["name"] == "community_info":
                        community += [x for x in result.get("sources_used", []) if x not in community]
                        community_skipped += [x for x in result.get("sources_skipped", []) if x not in community_skipped]
                    goal_label = goal_label or result.get("goal_label")
                yield {"type": "tool_result", "name": c["name"], "ok": "error" not in result, "brief": _brief(result)}
                if str(result.get("error", "")).startswith("Silnik PoB"):
                    # bez silnika nic nie jest policzone; model nie może wtedy improwizować odpowiedzi z własnej wiedzy
                    answer = (f"Nie mogę tego policzyć: {result['error']}\n\nNie podaję odpowiedzi z pamięci modelu, bo "
                              "byłaby niezweryfikowana. Sprawdź data\\app.log (linia „Silnik PoB”) "
                              "i połączenie z internetem przy pierwszym pobraniu PoB, potem zadaj pytanie ponownie.")
                    session.remember(question, answer)
                    yield {"type": "answer", "text": answer}
                    return
                messages.append({
                    "role": "tool", "tool_name": c["name"],
                    "content": json.dumps(result, ensure_ascii=False),
                })
            if calls_made >= max_calls:
                messages.append({"role": "user", "content": "Odpowiedz teraz na podstawie zebranych wyników."})
    except LLMError as e:
        yield {"type": "error", "text": str(e)}
