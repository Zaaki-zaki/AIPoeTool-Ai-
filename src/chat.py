"""Obsługa konwersacji, historii wiadomości i komend czatu."""

from __future__ import annotations

from typing import Dict, List, Optional

from .ollama_client import OllamaClient, OllamaError


class ChatSession:
    """Pojedyncza sesja rozmowy z modelem AI."""

    def __init__(
        self,
        client: OllamaClient,
        model: str,
        language: str = "pl",
        system_prompt: Optional[str] = None,
    ) -> None:
        self.client = client
        self.model = model
        self.language = language
        self.system_prompt = system_prompt or self._default_system_prompt()
        self.history: List[Dict[str, str]] = []
        if self.system_prompt:
            self.history.append({"role": "system", "content": self.system_prompt})

    def _default_system_prompt(self) -> str:
        return (
            "Jesteś pomocnym, lokalnym asystentem AI do Path of Exile 1. "
            "Odpowiadasz po polsku. Nie udawaj, że masz dostęp do aktualnego "
            "ekranu gry, plików buforów buildów ani cen przedmiotów, jeśli "
            "użytkownik ich nie udostępnił. Wszystkie odpowiedzi generuj "
            "lokalnie przez Ollama."
        )

    def reset(self) -> None:
        """Czyści historię i zaczyna nową konwersację (z promptem systemowym)."""
        self.history = []
        if self.system_prompt:
            self.history.append({"role": "system", "content": self.system_prompt})

    def send(self, user_text: str) -> str:
        """Wysyła wiadomość użytkownika do modelu i zwraca odpowiedź.

        Przy błędzie komunikacji wiadomość użytkownika jest usuwana z
        historii, aby nie zanieczyszczać kontekstu kolejną próbą.
        """
        self.history.append({"role": "user", "content": user_text})
        try:
            reply = self.client.chat(self.history, self.model)
        except OllamaError as exc:
            self.history.pop()
            return f"[Błąd] {exc}"
        self.history.append({"role": "assistant", "content": reply})
        return reply


def handle_command(text: str, session: ChatSession) -> Optional[str]:
    """Obsługuje komendy czatu.

    Zwraca tekst odpowiedzi do wyświetlenia, "__EXIT__" dla komendy wyjścia,
    lub None, jeśli przekazany tekst nie jest komendą.
    """
    command = text.strip().lower()

    if command == "/exit":
        return "__EXIT__"
    if command == "/reset":
        session.reset()
        return "Rozpocząłem nową konwersację. Kontekst został wyczyszczony."
    if command == "/help":
        return (
            "Dostępne komendy:\n"
            "  /exit  - zakończ rozmowę\n"
            "  /reset - rozpocznij nową konwersację (wyczyść kontekst)\n"
            "  /help  - pokaż tę pomoc\n"
        )
    return None
