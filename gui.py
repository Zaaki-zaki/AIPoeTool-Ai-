"""Graficzny interfejs (Tkinter) dla AIPoeTool — Faza 2.

Uruchomienie:
    python gui.py

Interfejs jest dodatkowym sposobem uruchomienia aplikacji obok istniejącego
interfejsu konsolowego (main.py). Wszystka logika rozmowy i komunikacji z
Ollama znajduje się w istniejących modułach src.chat i src.ollama_client.
"""

from __future__ import annotations

import json
import threading
import tkinter as tk
from tkinter import scrolledtext, messagebox
from pathlib import Path

from src.chat import ChatSession, handle_command
from src.ollama_client import OllamaClient, OllamaError

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def load_config(path: Path) -> dict:
    """Wczytuje konfigurację z pliku JSON lub zwraca pusty słownik w razie braku."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}


class AIPoeToolGUI:
    """Okno aplikacji AIPoeTool z czatem tekstowym przez Ollama."""

    def __init__(
        self,
        root: tk.Tk,
        client: OllamaClient | None = None,
        session: ChatSession | None = None,
    ) -> None:
        self.root = root
        self.root.title("AIPoeTool — asystent AI do Path of Exile 1")
        self.root.geometry("820x640")
        self.root.minsize(600, 400)

        config = load_config(CONFIG_PATH)
        self.host = config.get("ollama_host", "http://localhost:11434")
        self.model = config.get("model", "qwen3-coder:30b")
        self.language = config.get("language", "pl")

        self.client = client or OllamaClient(host=self.host)
        self.session = session or ChatSession(
            client=self.client, model=self.model, language=self.language
        )

        self._generating = False

        self._build_menu()
        self._build_ui()
        self._check_connection_async()

    # ------------------------------------------------------------------ UI

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        plik_menu = tk.Menu(menubar, tearoff=0)
        plik_menu.add_command(label="Nowa rozmowa", command=self.new_conversation)
        plik_menu.add_separator()
        plik_menu.add_command(label="Zakończ", command=self.on_closing)
        menubar.add_cascade(label="Plik", menu=plik_menu)
        self.root.config(menu=menubar)

    def _build_ui(self) -> None:
        # --- Historia rozmowy ---
        self.history = scrolledtext.ScrolledText(
            self.root, wrap=tk.WORD, state=tk.DISABLED, font=("Segoe UI", 10)
        )
        self.history.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 5))

        self.history.tag_config("user", foreground="#0066cc", font=("Segoe UI", 10, "bold"))
        self.history.tag_config("assistant", foreground="#006600", font=("Segoe UI", 10))
        self.history.tag_config("system", foreground="#666666", font=("Segoe UI", 9, "italic"))
        self.history.tag_config("error", foreground="#cc0000", font=("Segoe UI", 10, "bold"))

        # --- Pole wpisania i przycisk Wyślij ---
        input_frame = tk.Frame(self.root)
        input_frame.pack(fill=tk.X, padx=10, pady=(0, 5))

        self.input_text = tk.Text(input_frame, height=3, wrap=tk.WORD, font=("Segoe UI", 10))
        self.input_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.input_text.bind("<Return>", self._on_enter)

        self.send_button = tk.Button(
            input_frame, text="Wyślij", command=self.send_message, width=10
        )
        self.send_button.pack(side=tk.RIGHT, padx=(5, 0))

        # --- Przyciski pomocnicze ---
        button_frame = tk.Frame(self.root)
        button_frame.pack(fill=tk.X, padx=10, pady=(0, 5))

        tk.Button(
            button_frame, text="Nowa rozmowa", command=self.new_conversation, width=14
        ).pack(side=tk.LEFT, padx=(0, 5))

        tk.Button(
            button_frame,
            text="Sprawdź połączenie",
            command=self.check_connection,
            width=18,
        ).pack(side=tk.LEFT, padx=(0, 5))

        tk.Button(
            button_frame, text="Zakończ", command=self.on_closing, width=10
        ).pack(side=tk.RIGHT)

        # --- Status ---
        self.status_var = tk.StringVar(value="Gotowy")
        self.status_label = tk.Label(
            self.root, textvariable=self.status_var, bd=1, relief=tk.SUNKEN, anchor=tk.W
        )
        self.status_label.pack(fill=tk.X, padx=10, pady=(0, 5))

    # ------------------------------------------------------------------ zdarzenia

    def _on_enter(self, event: tk.Event) -> str | None:
        """Enter wysyła wiadomość, Shift+Enter wstawia nową linię."""
        if event.state & 0x0001:  # Shift wciśnięty
            return None  # domyślne zachowanie: nowa linia
        self.send_message()
        return "break"  # zablokuj domyślne wstawienie nowej linii

    def send_message(self) -> None:
        """Pobiera tekst z pola wejścia, wysyła do modelu i pokazuje odpowiedź."""
        if self._generating:
            return

        text = self.input_text.get("1.0", tk.END).strip()
        if not text:
            return

        # Obsługa wbudowanych komend (/exit, /reset, /help)
        command_reply = handle_command(text, self.session)
        if command_reply is not None:
            if command_reply == "__EXIT__":
                self.on_closing()
                return
            self._append_message("Asystent", command_reply, "system")
            self.input_text.delete("1.0", tk.END)
            return

        self._append_message("Ty", text, "user")
        self.input_text.delete("1.0", tk.END)

        self._set_generating(True)
        thread = threading.Thread(target=self._worker, args=(text,), daemon=True)
        thread.start()

    def _schedule(self, callback, *args) -> None:
        """Wykonuje callback w wątku głównym Tkinter (lub bezpośrednio, jeśli brak pętli zdarzeń)."""
        try:
            self.root.after(0, callback, *args)
        except RuntimeError:
            # Brak głównej pętli Tkinter (np. w testach) — wykonaj od razu.
            callback(*args)

    def _worker(self, user_text: str) -> None:
        """Wywoływany w osobnym wątku — komunikacja z Ollama."""
        try:
            reply = self.session.send(user_text)
        except OllamaError as exc:
            reply = f"[Błąd] {exc}"
        except Exception as exc:  # noqa: BLE001 — nieoczekiwany błąd aplikacji
            reply = f"[Błąd] Nieoczekiwany błąd: {exc}"
        self._schedule(self._on_response, reply)

    def _on_response(self, reply: str) -> None:
        """Aktualizuje UI po otrzymaniu odpowiedzi z modelu (w wątku głównym)."""
        tag = "error" if reply.startswith("[Błąd]") else "assistant"
        self._append_message("Asystent", reply, tag)
        self._set_generating(False)

    def check_connection(self) -> None:
        """Sprawdza połączenie z serwerem Ollama w tle."""
        self._set_status("Sprawdzanie połączenia...")
        thread = threading.Thread(target=self._connection_worker, daemon=True)
        thread.start()

    def _connection_worker(self) -> None:
        try:
            ok = self.client.check_connection()
        except Exception:  # noqa: BLE001
            ok = False
        self._schedule(self._on_connect_result, ok)

    def _on_connect_result(self, ok: bool) -> None:
        if ok:
            self._set_status("Połączono z Ollama")
        else:
            self._set_status("Brak połączenia z Ollama")
            messagebox.showwarning(
                "Brak połączenia",
                "Nie można połączyć się z serwerem Ollama. "
                "Upewnij się, że Ollama jest uruchomiona "
                "(http://localhost:11434).",
            )

    def new_conversation(self) -> None:
        """Rozpoczyna nową konwersację — czyści kontekst i historię wyświetlania."""
        if self._generating:
            return
        self.session.reset()
        self.history.configure(state=tk.NORMAL)
        self.history.delete("1.0", tk.END)
        self.history.configure(state=tk.DISABLED)
        self._append_message(
            "System",
            "Nowa rozmowa rozpoczęta. Kontekst został wyczyszczony.",
            "system",
        )
        self._set_status("Gotowy")

    def on_closing(self) -> None:
        """Potwierdza zamknięcie aplikacji."""
        if messagebox.askokcancel("Zakończ", "Czy na pewno chcesz zamknąć AIPoeTool?"):
            self.root.destroy()

    # ------------------------------------------------------------------ pomocnicze

    def _append_message(self, sender: str, text: str, tag: str) -> None:
        self.history.configure(state=tk.NORMAL)
        self.history.insert(tk.END, f"{sender}: ", ("bold", tag))
        self.history.insert(tk.END, text + "\n\n", tag)
        self.history.configure(state=tk.DISABLED)
        self.history.see(tk.END)

    def _set_generating(self, generating: bool) -> None:
        self._generating = generating
        state = tk.DISABLED if generating else tk.NORMAL
        self.input_text.configure(state=state)
        self.send_button.configure(state=state)
        self._set_status("AI myśli…" if generating else "Gotowy")

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _check_connection_async(self) -> None:
        """Sprawdza połączenie przy starcie (nie blokuje okna)."""
        thread = threading.Thread(target=self._connection_worker, daemon=True)
        thread.start()


def main() -> None:
    root = tk.Tk()
    app = AIPoeToolGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
