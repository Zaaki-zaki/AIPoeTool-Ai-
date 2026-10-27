"""Globalny skrót (domyślnie Ctrl+D) do sprawdzania cen: w grze kopiuje item pod kursorem i oddaje tekst aplikacji.

Tylko Windows (ctypes, bez dodatkowych pakietów). Skrót działa wyłącznie przy aktywnym oknie "Path of Exile";
w każdym innym oknie wciśnięcie jest przekazywane dalej, więc Ctrl+D nadal działa np. w przeglądarce.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

IS_WINDOWS = os.name == "nt"
GAME_TITLE = os.environ.get("AIPOE_GAME_TITLE", "Path of Exile")  # PoE 2 ma tytuł "Path of Exile 2", więc nie pasuje

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]

    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):  # tylko po to, by unia miała właściwy rozmiar
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]

    WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
    MOD_CONTROL, MOD_NOREPEAT = 0x0002, 0x4000
    KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0002, 0x0008
    CF_UNICODETEXT = 13
    _ROWS = (("QWERTYUIOP", 0x10), ("ASDFGHJKL", 0x1E), ("ZXCVBNM", 0x2C))
    SCAN = {ch: base + i for row, base in _ROWS for i, ch in enumerate(row)}  # kody skanowania układu QWERTY
    SCAN["SPACE"] = 0x39
    VK = {**{ch: ord(ch) for ch in SCAN if len(ch) == 1}, "SPACE": 0x20}


def foreground_title() -> str:
    if not IS_WINDOWS:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(user32.GetForegroundWindow(), buf, 256)
    return buf.value


def game_is_foreground() -> bool:
    return foreground_title().strip() == GAME_TITLE


def _key(scan: int, up: bool) -> "INPUT":
    return INPUT(type=1, ki=KEYBDINPUT(0, scan, KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0), 0, 0))


def _send(keys: list[tuple[int, bool]]) -> None:
    arr = (INPUT * len(keys))(*[_key(s, u) for s, u in keys])
    user32.SendInput(len(keys), arr, ctypes.sizeof(INPUT))


def clipboard_sequence() -> int:
    return int(user32.GetClipboardSequenceNumber()) if IS_WINDOWS else 0


def read_clipboard() -> str:
    """Tekst ze schowka (pusty string, gdy schowek jest zajęty albo nie ma tekstu)."""
    if not IS_WINDOWS:
        return ""
    for _ in range(10):
        if user32.OpenClipboard(None):
            try:
                h = user32.GetClipboardData(CF_UNICODETEXT)
                if not h:
                    return ""
                p = kernel32.GlobalLock(h)
                if not p:
                    return ""
                try:
                    return ctypes.wstring_at(p)
                finally:
                    kernel32.GlobalUnlock(h)
            finally:
                user32.CloseClipboard()
        time.sleep(0.02)
    return ""


def copy_item_from_game(timeout: float = 1.2) -> str:
    """Wysyła Ctrl+C do gry i czeka na zmianę schowka. Ctrl jest już wciśnięty (skrót Ctrl+D), więc dosyłamy samo C."""
    before = clipboard_sequence()
    _send([(SCAN["C"], False), (SCAN["C"], True)])
    end = time.time() + timeout
    while time.time() < end:
        if clipboard_sequence() != before:
            time.sleep(0.03)
            return read_clipboard()
        time.sleep(0.02)
    return ""


def _valid_key(key: str | None, default: str) -> str:
    k = (key or "").upper()
    return k if (len(k) == 1 and k in "QWERTYUIOPASDFGHJKLZXCVBNM") or k == "SPACE" else default


class HotkeyListener:
    """Wątek z pętlą komunikatów Windows. on_item(text) dostaje tekst itemu skopiowany w grze (Ctrl+klawisz cen);
    on_tree() wołane po Ctrl+klawisz listy węzłów drzewka (domyślnie Ctrl+T), też tylko przy aktywnej grze."""

    def __init__(self, on_item: Callable[[str], None], key: str = "D", on_tree: Callable[[], None] | None = None,
                 tree_key: str = "T") -> None:
        self.on_item = on_item
        self.on_tree = on_tree
        self.key = _valid_key(key, "D")
        self.tree_key = _valid_key(tree_key, "T")
        self._thread: threading.Thread | None = None
        self._tid = 0
        self.error: str | None = None

    def start(self) -> bool:
        if not IS_WINDOWS:
            self.error = "Skrót działa tylko w Windows."
            return False
        ready = threading.Event()
        self._thread = threading.Thread(target=self._loop, args=(ready,), daemon=True, name="aipoe-hotkey")
        self._thread.start()
        ready.wait(3)
        return self.error is None

    def stop(self) -> None:
        if IS_WINDOWS and self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)

    def _keys(self) -> list[tuple[int, str]]:
        keys = [(1, self.key)]
        if self.on_tree and self.tree_key != self.key:
            keys.append((2, self.tree_key))
        return keys

    def _register(self, only: int | None = None) -> bool:
        ok = True
        for hid, k in self._keys():
            if only in (None, hid):
                ok = bool(user32.RegisterHotKey(None, hid, MOD_CONTROL | MOD_NOREPEAT, VK[k])) and ok
        return ok

    def _loop(self, ready: threading.Event) -> None:
        self._tid = kernel32.GetCurrentThreadId()
        if not self._register():
            self.error = f"Nie udało się zarejestrować Ctrl+{self.key} (zajęty przez inny program?)."
            logging.warning(self.error)
            ready.set()
            return
        ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message != WM_HOTKEY:
                continue
            hid = int(msg.wParam)
            try:
                if game_is_foreground():
                    if hid == 1:
                        text = copy_item_from_game()
                        if text.strip():
                            self.on_item(text)
                    elif self.on_tree:
                        self.on_tree()
                else:  # nie w grze: oddajemy skrót aplikacji, która jest na wierzchu
                    key = dict(self._keys()).get(hid, self.key)
                    user32.UnregisterHotKey(None, hid)
                    _send([(SCAN[key], False), (SCAN[key], True)])
                    time.sleep(0.05)
                    self._register(only=hid)
            except Exception:  # noqa: BLE001 - pętla ma przeżyć błąd pojedynczego skrótu
                logging.exception("Błąd obsługi skrótu cen")
        for hid, _ in self._keys():
            user32.UnregisterHotKey(None, hid)
