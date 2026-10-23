"""Punkt wejścia aplikacji AIPoeTool (Faza 1 — tekstowy czat z Ollama)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from src.chat import ChatSession, handle_command
from src.ollama_client import OllamaClient

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def load_config(path: Path) -> dict:
    """Wczytuje konfigurację z pliku JSON lub zwraca pusty słownik w razie braku."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        print(f"[Uwaga] Nie znaleziono pliku konfiguracyjnego: {path}. Używam wartości domyślnych.")
        return {}
    except json.JSONDecodeError as exc:
        print(f"[Błąd] Nie można wczytać konfiguracji {path}: {exc}")
        sys.exit(1)


def main() -> int:
    config = load_config(CONFIG_PATH)
    host = config.get("ollama_host", "http://localhost:11434")
    model = config.get("model", "qwen3-coder:30b")
    language = config.get("language", "pl")

    client = OllamaClient(host=host)

    print("AIPoeTool — Faza 1: lokalny asystent AI do Path of Exile 1")
    print(f"Model: {model} | API Ollama: {host} | Język: {language}")
    print("Wpisz /exit aby zakończyć, /reset aby rozpocząć nową rozmowę, /help dla pomocy.")
    print("-" * 60)

    # Sprawdzenie połączenia z serwerem Ollama
    if not client.check_connection():
        print(
            "[Błąd] Nie można połączyć się z serwerem Ollama. "
            "Upewnij się, że Ollama jest uruchomiona (http://localhost:11434)."
        )
        return 1

    # Sprawdzenie dostępności wybranego modelu
    available_models = client.list_models()
    if model and model not in available_models:
        listed = ", ".join(available_models) if available_models else "(brak)"
        print(
            f"[Uwaga] Model '{model}' nie został znaleziony na serwerze Ollama. "
            f"Dostępne modele: {listed}"
        )
        print("Możesz pobrać model poleceniem: ollama pull <nazwa modelu>")
        print("-" * 60)

    session = ChatSession(client=client, model=model, language=language)

    while True:
        try:
            user_input = input("\nTy: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nDo widzenia!")
            return 0

        if not user_input:
            continue

        command_reply = handle_command(user_input, session)
        if command_reply is not None:
            if command_reply == "__EXIT__":
                print("Do widzenia!")
                return 0
            print(f"\nAsystent: {command_reply}")
            continue

        reply = session.send(user_input)
        print(f"\nAsystent: {reply}")


if __name__ == "__main__":
    sys.exit(main())
