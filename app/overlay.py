"""Nakładka na grę (Tkinter, lekka): jednolinijkowy pasek czatu i małe okno cen przy kursorze po Ctrl+D.

Działa w osobnym wątku z własną pętlą Tk; reszta aplikacji rozmawia z nią przez kolejkę poleceń. Okna są bez ramki, zawsze
na wierzchu i (poza pisaniem w pasku) nie kradną fokusu grze. Widać je tylko, gdy aktywne jest okno gry (albo pasek jest
w użyciu). Gra musi działać w trybie "okno bez ramki" (windowed fullscreen), bo ekskluzywny pełny ekran zasłania wszystko.
Dane bierze z lokalnego serwera aplikacji (to samo API co okno główne).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import webbrowser
from datetime import datetime, timezone
from typing import Any, Callable

from . import hotkey

BG, BG2, LINE, TEXT, DIM, GOLD, OK, BAD, ACCENT = "#0b1214", "#132023", "#26373a", "#dbdfd6", "#8fa1a0", "#c8a45c", "#7fd36b", "#e5604a", "#5cc2c4"
POPUP_W = 420
POPUP_MAX_S = 25          # po tylu sekundach okno cen samo się chowa
POPUP_FAR_PX = 280        # albo gdy kursor odejdzie dalej od okna


# ---------------------------------------------------------------- czyste funkcje (testowane bez okna)
def ago(iso: str | None, now: datetime | None = None) -> str:
    """'2 h temu' z czasu wystawienia oferty (ISO 8601 z trade)."""
    if not iso:
        return "?"
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return "?"
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    sec = max(0, int(((now or datetime.now(timezone.utc)) - t).total_seconds()))
    for unit, n in (("d", 86400), ("h", 3600), ("min", 60)):
        if sec >= n:
            return f"{sec // n} {unit} temu"
    return "przed chwilą"


def chaos_txt(v: float | None) -> str:
    if v is None:
        return "?"
    return f"{v:.0f}c" if v >= 100 else f"{v:.1f}".rstrip("0").rstrip(".") + "c"


def first_line(text: str, limit: int = 140) -> str:
    """Pierwsza niepusta linia odpowiedzi bez znaczników markdown, przycięta do limitu."""
    for ln in (text or "").splitlines():
        ln = ln.strip().lstrip("#*-• ").replace("**", "").replace("`", "")
        if ln:
            return ln if len(ln) <= limit else ln[:limit - 1].rstrip() + "…"
    return ""


def popup_rows(result: dict[str, Any], n: int = 5) -> list[dict[str, str]]:
    """Wiersze okna cen: najtańsze oferty z czasem wystawienia i statusem sprzedawcy."""
    rows = []
    priced = sorted(result.get("listings", []), key=lambda l: (l.get("price_chaos") is None, l.get("price_chaos") or 0))
    for l in priced[:n]:
        rows.append({"price": l.get("price_text", "?"), "chaos": chaos_txt(l.get("price_chaos")) if l.get("currency") != "chaos" else "",
                     "seller": l.get("account") or "?", "online": "1" if l.get("online") else "", "age": ago(l.get("indexed"))})
    return rows


def estimate(elapsed: float, eta: float) -> int:
    """Szacowany postęp (%) dla odpowiedzi AI, gdy serwer nie podaje prawdziwego: zbliża się do 97%, nigdy nie dochodzi."""
    import math
    return int(97 * (1 - math.exp(-elapsed / max(eta * 0.6, 1.0))))


# ---------------------------------------------------------------- Windows
IS_WINDOWS = hotkey.IS_WINDOWS
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = -20, 0x08000000, 0x00000080
    u32.GetParent.restype = wintypes.HWND
    u32.GetForegroundWindow.restype = wintypes.HWND
    u32.FindWindowW.restype = wintypes.HWND
    u32.SetForegroundWindow.argtypes = [wintypes.HWND]
    u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    u32.MonitorFromWindow.restype = wintypes.HANDLE
    u32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    u32.MonitorFromPoint.restype = wintypes.HANDLE

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    u32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    u32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]


def cursor_pos() -> tuple[int, int]:
    if not IS_WINDOWS:
        return 200, 200
    p = wintypes.POINT()
    u32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def work_area(near: tuple[int, int] | None = None) -> tuple[int, int, int, int]:
    """Obszar roboczy monitora (bez paska zadań): ten, na którym jest gra, albo wskazany punkt; poza Windows 1280x800."""
    if not IS_WINDOWS:
        return 0, 0, 1280, 800
    if near is not None:
        mon = u32.MonitorFromPoint(wintypes.POINT(*near), 2)
    else:
        game = u32.FindWindowW(None, hotkey.GAME_TITLE)
        mon = u32.MonitorFromWindow(game, 2) if game else u32.MonitorFromPoint(wintypes.POINT(0, 0), 1)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    u32.GetMonitorInfoW(mon, ctypes.byref(mi))
    r = mi.rcWork
    return r.left, r.top, r.right, r.bottom


def _top_hwnd(win: Any) -> int:
    wid = win.winfo_id()
    return (u32.GetParent(wid) or wid) if IS_WINDOWS else wid


def set_noactivate(win: Any, on: bool) -> None:
    """Okno nie przejmuje fokusu przy kliknięciu i nie pokazuje się na pasku zadań (on=True)."""
    if not IS_WINDOWS:
        return
    h = _top_hwnd(win)
    style = u32.GetWindowLongW(h, GWL_EXSTYLE) | WS_EX_TOOLWINDOW
    style = (style | WS_EX_NOACTIVATE) if on else (style & ~WS_EX_NOACTIVATE)
    u32.SetWindowLongW(h, GWL_EXSTYLE, style)


def set_clickthrough(win: Any) -> None:
    """Kliknięcia przechodzą przez okno do gry (WS_EX_TRANSPARENT | WS_EX_LAYERED)."""
    if not IS_WINDOWS:
        return
    h = _top_hwnd(win)
    u32.SetWindowLongW(h, GWL_EXSTYLE, u32.GetWindowLongW(h, GWL_EXSTYLE) | 0x20 | 0x80000)


def tree_text(d: dict[str, Any]) -> str:
    """Tekst nakładki drzewka z odpowiedzi /api/plan/next."""
    if not d.get("ok"):
        return d.get("msg") or "Brak planu."
    mark = {"K": "★ keystone", "A": "◆ ascendancy", "T": "● notable"}
    lines = [f"Następny etap: {d['stage']} (poziom {d['level']})"]
    if d.get("game_level"):
        lines[0] += f", Ty: {d['game_level']}"
    lines.append(f"Do wzięcia: {d['points']} pkt. Szukaj w grze: Ctrl+F w drzewku")
    for n in d["nodes"][:14]:
        lines.append(f"{mark.get(n['type'], '·')}  {n['name']}" if n["type"] in mark else f"·  {n['name']}")
    if len(d["nodes"]) > 14:
        lines.append(f"… i {len(d['nodes']) - 14} drobnych")
    return "\n".join(lines)


def force_foreground(hwnd: int) -> None:
    """Przenosi fokus klawiatury do okna: krótkie wciśnięcie Alt omija blokadę kradzieży fokusu w Windows."""
    if not IS_WINDOWS or not hwnd:
        return
    u32.keybd_event(0x12, 0, 0, 0)
    u32.keybd_event(0x12, 0, 2, 0)
    u32.SetForegroundWindow(hwnd)


class Overlay:
    """base_url: adres lokalnego serwera aplikacji. strip: 'left' | 'center' | 'right' | 'off'."""

    def __init__(self, base_url: str, strip: str = "off", on_error: Callable[[str], None] | None = None) -> None:
        self.base = base_url.rstrip("/")
        self.strip_pos = strip if strip in ("left", "center", "right", "off") else "off"
        self.cmds: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.ready = threading.Event()
        self.error: str | None = None
        self.history: list[tuple[str, str]] = []
        self.eta = 20.0
        self._thread: threading.Thread | None = None
        self._popup_shown_at = 0.0
        self._chat_active = False
        self._chat_busy = False
        self._busy_since = 0.0
        self._frac: float | None = None
        self._prev_hwnd = 0
        self._popup_url = ""
        self._panel_open = False

    # ---- API wywoływane z innych wątków ----
    def start(self) -> bool:
        self._thread = threading.Thread(target=self._run, daemon=True, name="aipoe-overlay")
        self._thread.start()
        self.ready.wait(5)
        return self.error is None

    def show_price(self) -> None:
        self.cmds.put(("price", None))

    def focus_chat(self) -> None:
        self.cmds.put(("chat", None))

    def toggle_tree(self) -> None:
        self.cmds.put(("tree", None))

    def stop(self) -> None:
        self.cmds.put(("stop", None))

    # ---- pętla Tk ----
    def _run(self) -> None:
        try:
            import tkinter as tk
        except ImportError:
            self.error = "Brak Tkinter w tym Pythonie."
            self.ready.set()
            return
        try:
            self.tk = tk
            self.root = tk.Tk()
            self.root.withdraw()
            self._build_strip()
            self._build_popup()
            self._build_panel()
            self._build_tree()
            self.root.after(60, self._pump)
            self.root.after(400, self._watch)
        except Exception as e:  # noqa: BLE001 - brak ekranu itp.: aplikacja działa dalej bez nakładki
            self.error = f"Nie udało się utworzyć nakładki: {e}"
            logging.exception("Nakładka")
            self.ready.set()
            return
        self.ready.set()
        self.root.mainloop()

    def _toplevel(self) -> Any:
        w = self.tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=BG, highlightthickness=1, highlightbackground=LINE)
        w.withdraw()
        return w

    # ---- pasek czatu ----
    def _build_strip(self) -> None:
        tk = self.tk
        self.strip = self._toplevel()
        self.strip_lbl = tk.Label(self.strip, text="", bg=BG, fg=DIM, anchor="w", font=("Segoe UI", 10), cursor="hand2")
        self.strip_ent = tk.Entry(self.strip, bg=BG2, fg=TEXT, insertbackground=TEXT, relief="flat", font=("Segoe UI", 10),
                                  highlightthickness=0)
        self.strip_pct = tk.Label(self.strip, text="", bg=BG, fg=ACCENT, font=("Consolas", 10), width=5, anchor="e")
        self.strip_pct.pack(side="right", padx=(0, 6))
        self.strip_lbl.pack(side="left", fill="x", expand=True, padx=8)
        self.strip_lbl.bind("<Button-1>", lambda e: self._toggle_panel())
        self.strip_ent.bind("<Return>", lambda e: self._send())
        self.strip_ent.bind("<Escape>", lambda e: self._leave_chat())
        self._set_strip("Ctrl+Spacja: zapytaj AI · Ctrl+D: ceny", DIM)
        self.strip.update_idletasks()
        set_noactivate(self.strip, True)

    def _strip_geometry(self) -> None:
        l, t, r, b = work_area()
        w = min(640, max(360, (r - l) // 2))
        h = 28
        x = {"left": l + 8, "center": l + (r - l - w) // 2, "right": r - w - 8}[self.strip_pos if self.strip_pos != "off" else "left"]
        self.strip.geometry(f"{w}x{h}+{x}+{b - h - 8}")
        self._strip_box = (x, b - h - 8, w, h)

    def _set_strip(self, text: str, color: str = TEXT) -> None:
        self.strip_lbl.configure(text=text, fg=color)

    # ---- panel z historią (po kliknięciu paska) ----
    def _build_panel(self) -> None:
        tk = self.tk
        self.panel = self._toplevel()
        self.panel_txt = tk.Text(self.panel, bg=BG, fg=TEXT, wrap="word", relief="flat", font=("Segoe UI", 10), padx=10, pady=8,
                                 highlightthickness=0, cursor="arrow")
        sb = tk.Scrollbar(self.panel, command=self.panel_txt.yview)
        self.panel_txt.configure(yscrollcommand=sb.set, state="disabled")
        sb.pack(side="right", fill="y")
        self.panel_txt.pack(fill="both", expand=True)
        self.panel_txt.tag_configure("q", foreground=GOLD, font=("Segoe UI", 10, "bold"))
        self.panel.update_idletasks()
        set_noactivate(self.panel, True)

    def _toggle_panel(self) -> None:
        if self._panel_open:
            self.panel.withdraw()
            self._panel_open = False
            return
        if not self.history:
            return
        x, y, w, h = self._strip_box
        ph = 320
        self.panel.geometry(f"{w}x{ph}+{x}+{max(0, y - ph - 4)}")
        self.panel_txt.configure(state="normal")
        self.panel_txt.delete("1.0", "end")
        for q, a in self.history[-6:]:
            self.panel_txt.insert("end", f"› {q}\n", "q")
            self.panel_txt.insert("end", a.strip().replace("**", "").replace("`", "") + "\n\n")
        self.panel_txt.configure(state="disabled")
        self.panel_txt.see("end")
        self.panel.deiconify()
        self.panel.attributes("-topmost", True)
        self._panel_open = True

    # ---- okno cen ----
    def _build_popup(self) -> None:
        tk = self.tk
        self.popup = self._toplevel()
        self.pp_head = tk.Frame(self.popup, bg=BG)
        self.pp_head.pack(fill="x", padx=10, pady=(8, 2))
        self.pp_name = tk.Label(self.pp_head, text="", bg=BG, fg=GOLD, font=("Segoe UI", 11, "bold"), anchor="w")
        self.pp_name.pack(side="left", fill="x", expand=True)
        close = tk.Label(self.pp_head, text="✕", bg=BG, fg=DIM, cursor="hand2", font=("Segoe UI", 10))
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self._hide_popup())
        self.pp_sub = tk.Label(self.popup, text="", bg=BG, fg=DIM, font=("Segoe UI", 9), anchor="w")
        self.pp_sub.pack(fill="x", padx=10)
        self.pp_sum = tk.Label(self.popup, text="", bg=BG, fg=TEXT, font=("Consolas", 12, "bold"), anchor="w")
        self.pp_sum.pack(fill="x", padx=10, pady=(4, 4))
        self.pp_rows = tk.Frame(self.popup, bg=BG)
        self.pp_rows.pack(fill="x", padx=10)
        self.pp_foot = tk.Frame(self.popup, bg=BG)
        self.pp_foot.pack(fill="x", padx=10, pady=(6, 8))
        self.pp_msg = tk.Label(self.popup, text="", bg=BG, fg=DIM, font=("Segoe UI", 10), anchor="w", wraplength=POPUP_W - 24, justify="left")
        self.popup.update_idletasks()
        set_noactivate(self.popup, True)

    def _clear_rows(self) -> None:
        for f in (self.pp_rows, self.pp_foot):
            for c in f.winfo_children():
                c.destroy()

    def _popup_text(self, text: str, color: str = DIM) -> None:
        self._clear_rows()
        self.pp_sum.configure(text="")
        self.pp_sub.configure(text="")
        self.pp_msg.configure(text=text, fg=color)
        self.pp_msg.pack(fill="x", padx=10, pady=(0, 10), before=self.pp_foot)

    def _render_result(self, d: dict[str, Any]) -> None:
        tk = self.tk
        it = d["item"]
        self.pp_msg.pack_forget()
        self._clear_rows()
        self.pp_name.configure(text=it.get("name") or it.get("base") or "Item")
        base = it.get("base") if it.get("name") else ""
        self.pp_sub.configure(text=" · ".join(x for x in (it.get("rarity"), base, f"ilvl {it['ilvl']}" if it.get("ilvl") else "") if x))
        if d.get("min_chaos") is None and not d.get("listings"):
            self.pp_sum.configure(text="brak ofert", fg=BAD)
        else:
            self.pp_sum.configure(text=f"od {chaos_txt(d.get('min_chaos'))}   mediana {chaos_txt(d.get('median_chaos'))}   ofert {d.get('total', 0)}", fg=TEXT)
        for r in popup_rows(d):
            row = tk.Frame(self.pp_rows, bg=BG)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=r["price"], bg=BG, fg=TEXT, font=("Consolas", 10, "bold"), width=11, anchor="w").pack(side="left")
            tk.Label(row, text=r["chaos"] and f"≈{r['chaos']}", bg=BG, fg=DIM, font=("Consolas", 9), width=7, anchor="w").pack(side="left")
            tk.Label(row, text="●", bg=BG, fg=OK if r["online"] else DIM, font=("Segoe UI", 8)).pack(side="left")
            tk.Label(row, text=r["seller"][:16], bg=BG, fg=DIM, font=("Segoe UI", 9), width=15, anchor="w").pack(side="left")
            tk.Label(row, text=r["age"], bg=BG, fg=DIM, font=("Segoe UI", 9), anchor="e").pack(side="right")
        self._popup_url = d.get("trade_url", "")
        if self._popup_url:
            b = tk.Label(self.pp_foot, text="Otwórz na trade", bg=BG, fg=ACCENT, cursor="hand2", font=("Segoe UI", 9, "underline"))
            b.pack(side="left")
            b.bind("<Button-1>", lambda e: webbrowser.open(self._popup_url))
        tk.Label(self.pp_foot, text=d.get("league", ""), bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(side="right")
        self._fit_popup()

    def _fit_popup(self) -> None:
        self.popup.update_idletasks()
        h = self.popup.winfo_reqheight()
        x, y = self._popup_pos
        l, t, r, b = work_area((x, y))
        x = max(l, min(x, r - POPUP_W - 4))
        y = max(t, min(y, b - h - 4))
        self.popup.geometry(f"{POPUP_W}x{h}+{x}+{y}")
        self._popup_box = (x, y, POPUP_W, h)

    def _hide_popup(self) -> None:
        self.popup.withdraw()
        self._popup_shown_at = 0.0

    def _start_price_worker(self) -> None:
        def work() -> None:
            import httpx
            deadline = time.time() + 45
            while time.time() < deadline:
                try:
                    c = httpx.get(f"{self.base}/api/price/last", timeout=5).json()
                except Exception:  # noqa: BLE001
                    time.sleep(0.5)
                    continue
                if c.get("status") == "done":
                    self.cmds.put(("result", c["result"]))
                    return
                if c.get("status") == "error":
                    self.cmds.put(("perror", c.get("error", "Błąd")))
                    return
                time.sleep(0.3)
            self.cmds.put(("perror", "Trade nie odpowiedziało na czas."))
        threading.Thread(target=work, daemon=True).start()

    # ---- czat ----
    def _enter_chat(self) -> None:
        self._prev_hwnd = u32.GetForegroundWindow() if IS_WINDOWS else 0
        self._chat_active = True
        self._strip_geometry()
        self.strip.deiconify()
        set_noactivate(self.strip, False)
        self.strip_lbl.pack_forget()
        self.strip_ent.delete(0, "end")
        self.strip_ent.pack(side="left", fill="x", expand=True, padx=8, pady=3)
        self.strip.attributes("-topmost", True)
        force_foreground(_top_hwnd(self.strip))
        self.strip_ent.focus_force()

    def _leave_chat(self) -> None:
        self._chat_active = False
        self.strip_ent.pack_forget()
        self.strip_lbl.pack(side="left", fill="x", expand=True, padx=8)
        set_noactivate(self.strip, True)
        if IS_WINDOWS and self._prev_hwnd:
            force_foreground(self._prev_hwnd)

    def _send(self) -> None:
        q = self.strip_ent.get().strip()
        if not q or self._chat_busy:
            return
        self._leave_chat()
        self._chat_busy, self._busy_since, self._frac = True, time.time(), None
        self._set_strip(f"› {q}", DIM)

        def work() -> None:
            import httpx
            try:
                with httpx.stream("POST", f"{self.base}/api/chat", json={"question": q, "goal": None, "game": True}, timeout=httpx.Timeout(600, connect=5)) as r:
                    if r.status_code != 200:
                        self.cmds.put(("answer", (q, "Błąd: " + r.read().decode("utf-8", "replace")[:200], True)))
                        return
                    for line in r.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        e = json.loads(line[5:])
                        if e["type"] == "progress" and e.get("frac") is not None:
                            self.cmds.put(("frac", e["frac"]))
                        elif e["type"] == "answer":
                            self.cmds.put(("answer", (q, e["text"], False)))
                        elif e["type"] == "error":
                            self.cmds.put(("answer", (q, e["text"], True)))
            except Exception as ex:  # noqa: BLE001
                self.cmds.put(("answer", (q, f"Brak połączenia z serwerem: {ex}", True)))
        threading.Thread(target=work, daemon=True).start()

    # ---- pompa poleceń i obserwator ----
    # ---- lista węzłów drzewka (Ctrl+T): półprzezroczysta, klikalność przechodzi do gry ----
    def _build_tree(self) -> None:
        tk = self.tk
        self.tree = self.tk.Toplevel(self.root)
        self.tree.overrideredirect(True)
        self.tree.attributes("-topmost", True)
        self.tree.attributes("-alpha", 0.82)
        self.tree.configure(bg=BG)
        self.tree.withdraw()
        self.tree_lbl = tk.Label(self.tree, text="", bg=BG, fg=TEXT, justify="left", anchor="nw", font=("Segoe UI", 11), padx=12, pady=8)
        self.tree_lbl.pack()
        self.tree.update_idletasks()
        set_noactivate(self.tree, True)
        set_clickthrough(self.tree)
        self._tree_shown = False

    def _toggle_tree(self) -> None:
        if self._tree_shown:
            self.tree.withdraw()
            self._tree_shown = False
            return
        self.tree_lbl.configure(text="Ładuję plan…")
        self._place_tree()
        self.tree.deiconify()
        self._tree_shown = True

        def work() -> None:
            import httpx
            try:
                d = httpx.get(f"{self.base}/api/plan/next", timeout=10).json()
            except Exception as ex:  # noqa: BLE001
                d = {"ok": False, "msg": f"Brak połączenia z serwerem: {ex}"}
            self.cmds.put(("treedata", d))
        threading.Thread(target=work, daemon=True).start()

    def _place_tree(self) -> None:
        self.tree.update_idletasks()
        l, t, r, b = work_area()
        self.tree.geometry(f"+{r - self.tree.winfo_reqwidth() - 12}+{t + 80}")

    def _pump(self) -> None:
        try:
            while True:
                name, arg = self.cmds.get_nowait()
                if name == "stop":
                    self.root.quit()
                    return
                self._handle(name, arg)
        except queue.Empty:
            pass
        except Exception:  # noqa: BLE001
            logging.exception("Nakładka: błąd polecenia")
        self.root.after(60, self._pump)

    def _handle(self, name: str, arg: Any) -> None:
        if name == "price":
            self._popup_pos = (cursor_pos()[0] + 24, cursor_pos()[1] + 16)
            self.pp_name.configure(text="Sprawdzam cenę…")
            self._popup_text("Szukam ofert na trade…")
            self._fit_popup()
            self.popup.deiconify()
            self.popup.attributes("-topmost", True)
            self._popup_shown_at = time.time()
            self._start_price_worker()
        elif name == "result":
            self._render_result(arg)
        elif name == "perror":
            self.pp_name.configure(text="Cena niedostępna")
            self._popup_text(str(arg), BAD)
            self._fit_popup()
        elif name == "tree":
            self._toggle_tree()
        elif name == "treedata":
            self.tree_lbl.configure(text=tree_text(arg))
            self._place_tree()
        elif name == "chat":
            if self.strip_pos != "off" and not self._chat_busy:
                self._enter_chat()
        elif name == "frac":
            self._frac = float(arg)
        elif name == "answer":
            q, text, err = arg
            dur = time.time() - self._busy_since
            if not err:
                self.eta = self.eta * 0.6 + dur * 0.4
            self._chat_busy = False
            self.strip_pct.configure(text="")
            self.history.append((q, text))
            self._set_strip(first_line(text), BAD if err else TEXT)

    def _watch(self) -> None:
        """Co 400 ms: pokazuje/chowa pasek zależnie od aktywnego okna, odświeża procenty i chowa okno cen."""
        try:
            in_game = hotkey.game_is_foreground() if IS_WINDOWS else True
            if self.strip_pos != "off":
                if in_game or self._chat_active or self._chat_busy:
                    if str(self.strip.state()) == "withdrawn":
                        self._strip_geometry()
                        self.strip.deiconify()
                        set_noactivate(self.strip, not self._chat_active)
                    self.strip.attributes("-topmost", True)
                elif str(self.strip.state()) != "withdrawn":
                    self.strip.withdraw()
                    if self._panel_open:
                        self._toggle_panel()
            if self._tree_shown and IS_WINDOWS and not in_game:
                self._toggle_tree()
            if self._tree_shown:
                self.tree.attributes("-topmost", True)
            if self._chat_busy:
                p = int(self._frac * 100) if self._frac is not None else estimate(time.time() - self._busy_since, self.eta)
                self.strip_pct.configure(text=f"{min(p, 99)}%")
            if self._popup_shown_at:
                x, y, w, h = self._popup_box
                cx, cy = cursor_pos()
                far = max(x - cx, cx - (x + w), y - cy, cy - (y + h)) > POPUP_FAR_PX
                if far or time.time() - self._popup_shown_at > POPUP_MAX_S:
                    self._hide_popup()
        except Exception:  # noqa: BLE001
            logging.exception("Nakładka: błąd obserwatora")
        self.root.after(400, self._watch)
