"""Klient komunikujący się z lokalnym API Ollama."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List


class OllamaError(Exception):
    """Błąd komunikacji z serwerem Ollama."""


class OllamaClient:
    """Prosty klient REST do lokalnego serwera Ollama."""

    def __init__(self, host: str = "http://localhost:11434", timeout: int = 60):
        self.host = host.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Wysyła żądanie POST do Ollama i zwraca sparstwowaną odpowiedź JSON."""
        url = f"{self.host}{path}"
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise OllamaError(
                f"Nie można połączyć się z Ollama pod {url}: {exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - zapisujemy nieoczekiwane błędy sieciowe
            raise OllamaError(f"Błąd podczas komunikacji z Ollama: {exc}") from exc

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"Nieprawidłowa odpowiedź JSON z Ollama: {exc}") from exc

    def _get(self, path: str) -> Dict[str, Any]:
        """Wysyła żądanie GET do Ollama i zwraca sparsowaną odpowiedź JSON."""
        url = f"{self.host}{path}"
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise OllamaError(
                f"Nie można połączyć się z Ollama pod {url}: {exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - zapisujemy nieoczekiwane błędy sieciowe
            raise OllamaError(f"Błąd podczas komunikacji z Ollama: {exc}") from exc

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"Nieprawidłowa odpowiedź JSON z Ollama: {exc}") from exc

    def check_connection(self) -> bool:
        """Sprawdza, czy serwer Ollama odpowiada (GET /api/tags)."""
        try:
            self._get("/api/tags")
            return True
        except OllamaError:
            return False

    def list_models(self) -> List[str]:
        """Zwraca listę nazw modeli dostępnych na serwerze Ollama (GET /api/tags)."""
        try:
            data = self._get("/api/tags")
        except OllamaError:
            return []
        models = data.get("models", [])
        return [model.get("name", "") for model in models if model.get("name")]

    def is_model_available(self, model: str) -> bool:
        """Sprawdza, czy wskazany model jest pobrany na serwerze Ollama."""
        return model in self.list_models()

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: str,
        stream: bool = False,
    ) -> str:
        """Wysyła listę wiadomości do modelu i zwraca całą odpowiedź tekstową.

        Dla stream=False Ollama zwraca pełną odpowiedź w jednym komunikacie,
        co wystarczy w Fazie 1.
        """
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        data = self._post("/api/chat", payload)
        message = data.get("message", {})
        return message.get("content", "")
