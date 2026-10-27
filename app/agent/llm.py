"""Klient Ollamy (/api/chat z natywnym tool callingiem). Interfejs `chat` jest podmienialny w testach."""

from __future__ import annotations

import os
from typing import Any, Protocol

import httpx

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3-coder-32k"


class LLMError(RuntimeError):
    """Problem z modelem (Ollama niedostępna, brak modelu itp.) - komunikat po polsku."""


class LLM(Protocol):
    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        """Zwraca wiadomość asystenta: {'content': str, 'tool_calls': [...]?}."""
        ...


class OllamaClient:
    def __init__(self, model: str | None = None, url: str | None = None, timeout: float = 600.0,
                 num_ctx: int | None = None, temperature: float = 0.2, num_gpu: int | None = None) -> None:
        self.model = model or os.environ.get("AIPOE_MODEL", DEFAULT_MODEL)
        self.url = (url or os.environ.get("OLLAMA_URL", DEFAULT_URL)).rstrip("/")
        self.timeout = timeout
        # AIPOE_NUM_CTX: mniejszy kontekst = mniej pamięci (przy grze w tle); AIPOE_KEEP_ALIVE: jak długo model zostaje w VRAM po odpowiedzi
        self.options = {"num_ctx": num_ctx or int(os.environ.get("AIPOE_NUM_CTX", "32768")), "temperature": temperature}
        if num_gpu is not None:  # 0 = model liczy na CPU i nie rusza GPU gry
            self.options["num_gpu"] = num_gpu
        self.keep_alive = os.environ.get("AIPOE_KEEP_ALIVE", "2m")


def game_llm() -> OllamaClient:
    """Mały model na CPU do czatu w trakcie gry (AIPOE_GAME_MODEL, AIPOE_GAME_CTX, AIPOE_GAME_GPU=1 pozwala użyć GPU)."""
    gpu = None if os.environ.get("AIPOE_GAME_GPU") == "1" else 0
    c = OllamaClient(model=os.environ.get("AIPOE_GAME_MODEL", "qwen3:4b"), num_ctx=int(os.environ.get("AIPOE_GAME_CTX", "8192")), num_gpu=gpu)
    c.keep_alive = os.environ.get("AIPOE_GAME_KEEP_ALIVE", "10m")
    return c

    def available(self) -> bool:
        try:
            r = httpx.get(f"{self.url}/api/tags", timeout=3)
            return r.status_code == 200 and any(
                m.get("name", "").split(":")[0] == self.model.split(":")[0] for m in r.json().get("models", [])
            )
        except httpx.HTTPError:
            return False

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model, "messages": messages, "stream": False, "options": self.options, "keep_alive": self.keep_alive,
        }
        if tools:
            payload["tools"] = tools
        try:
            r = httpx.post(f"{self.url}/api/chat", json=payload, timeout=self.timeout)
        except httpx.ConnectError as e:
            raise LLMError(f"Nie mogę połączyć się z Ollamą pod {self.url}. Uruchom Ollamę i spróbuj ponownie.") from e
        except httpx.HTTPError as e:
            raise LLMError(f"Błąd połączenia z Ollamą: {e}") from e
        if r.status_code == 404:
            raise LLMError(f"Ollama nie zna modelu '{self.model}'. Sprawdź `ollama list` i ustaw AIPOE_MODEL.")
        if r.status_code != 200:
            raise LLMError(f"Ollama zwróciła błąd {r.status_code}: {r.text[:300]}")
        return r.json().get("message", {})
