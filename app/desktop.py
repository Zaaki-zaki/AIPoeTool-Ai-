"""Aplikacja okienkowa: serwer startuje w tle, UI w natywnym oknie (WebView2), bez przeglądarki i konsoli.

Start: uv run python -m app.desktop   (bez konsoli: uv run pythonw -m app.desktop)
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser

import httpx
import uvicorn

from .env import load_env
from .web.server import create_app

TITLE = "AIPoeTool"
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "app.log")


def _setup_logging() -> None:
    """pythonw nie ma stdout/stderr, więc wszystko (też uvicorn) idzie do data/app.log; bez tego okno potrafi nie wstać po cichu."""
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")


def _error_box(text: str) -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, "AIPoeTool – błąd", 0x10)
        except Exception:  # noqa: BLE001
            pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ollama_up(url: str) -> bool:
    try:
        return httpx.get(url, timeout=1.5).status_code < 500
    except httpx.HTTPError:
        return False


def ensure_ollama() -> None:
    """Jeśli Ollama nie odpowiada, uruchamia ją w tle (best effort; UI i tak pokaże status)."""
    url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    if _ollama_up(url) or not shutil.which("ollama"):
        return
    subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=CREATE_NO_WINDOW)
    for _ in range(20):
        if _ollama_up(url):
            return
        time.sleep(0.5)


def ensure_docker() -> None:
    """Jeśli demon Dockera nie działa, odpala Docker Desktop (start trwa zwykle 20-60 s; silnik PoB czeka na niego)."""
    if not shutil.which("docker"):
        return
    ok = subprocess.run(["docker", "info"], capture_output=True, creationflags=CREATE_NO_WINDOW).returncode == 0
    if ok:
        return
    exe = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Docker", "Docker", "Docker Desktop.exe")
    if os.path.exists(exe):
        kw: dict = {}
        if sys.platform == "win32":  # start zminimalizowany (SW_SHOWMINNOACTIVE), żeby okno Dockera nie wyskakiwało na pulpit
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 7
            kw["startupinfo"] = si
        subprocess.Popen([exe], **kw)


def prepare_engine() -> None:
    """Silnik natywny: pobiera PoB w tle przy pierwszym starcie. Bez pakietu lupa zostaje Docker (stary tryb)."""
    from .calc.engine import native_available
    if native_available() and not os.environ.get("POB_ENGINE_CMD"):
        from .calc import setup
        try:
            setup.ensure(logging.info)
        except Exception:  # noqa: BLE001
            logging.exception("Pobieranie PoB nie powiodło się")
    elif os.environ.get("AIPOE_ALLOW_DOCKER") == "1":
        ensure_docker()


def main() -> None:
    load_env()
    threading.Thread(target=ensure_ollama, daemon=True).start()
    threading.Thread(target=prepare_engine, daemon=True).start()
    port = free_port()

    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    listener = overlay = None
    url = f"http://127.0.0.1:{port}"
    if os.environ.get("AIPOE_OVERLAY") != "off":
        from .overlay import Overlay
        overlay = Overlay(url, os.environ.get("AIPOE_STRIP", "off"))
        if overlay.start():
            app.state.overlay = True
        else:
            logging.warning("Nakładka wyłączona: %s", overlay.error)
            overlay = None
    if os.environ.get("AIPOE_NO_HOTKEY") != "1":
        from .hotkey import HotkeyListener

        def on_item(text: str) -> None:
            app.state.push_capture(text)
            if overlay:
                overlay.show_price()

        listener = HotkeyListener(on_item, os.environ.get("AIPOE_PRICE_KEY", "D"),
                                  on_tree=overlay.toggle_tree if overlay else None,
                                  tree_key=os.environ.get("AIPOE_TREE_KEY", "T"))
        logging.info("Skrót cen Ctrl+%s, drzewko Ctrl+%s: %s", listener.key, listener.tree_key,
                     "aktywne" if listener.start() else listener.error)
    logging.info("Okno: %s (serwer wystartował: %s)", url, server.started)
    try:
        import webview
    except ImportError:  # brak pywebview: zwykła przeglądarka, serwer żyje do Ctrl+C
        print("Brak pywebview (uv add pywebview). Otwieram przeglądarkę:", url)
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        webview.create_window(TITLE, url, width=1280, height=860, min_size=(900, 600))
        webview.start()
    if listener:
        listener.stop()
    if overlay:
        overlay.stop()
    server.should_exit = True  # zamyka też silnik PoB (lifespan)
    time.sleep(1)


if __name__ == "__main__":
    main()
