"""Testy sesji czatu i komend — używają mocków zamiast prawdziwego Ollama."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from src.chat import ChatSession, handle_command
from src.ollama_client import OllamaError


class TestChatSession(unittest.TestCase):
    def setUp(self) -> None:
        self.client = MagicMock()
        self.client.chat.return_value = "To jest testowa odpowiedź modelu."
        self.session = ChatSession(client=self.client, model="qwen3-coder:30b", language="pl")

    def test_send_adds_user_and_assistant_messages(self) -> None:
        reply = self.session.send("Co to jest unikalny przedmiot?")
        self.assertEqual(reply, "To jest testowa odpowiedź modelu.")
        self.client.chat.assert_called_once()
        args, _ = self.client.chat.call_args
        messages = args[0]
        self.assertEqual(
            messages[-2], {"role": "user", "content": "Co to jest unikalny przedmiot?"}
        )
        self.assertEqual(
            messages[-1],
            {"role": "assistant", "content": "To jest testowa odpowiedź modelu."},
        )

    def test_reset_clears_history(self) -> None:
        self.session.send("Pierwsza wiadomość")
        self.session.reset()
        self.assertEqual(len(self.session.history), 1)
        self.assertEqual(self.session.history[0]["role"], "system")

    def test_handle_exit(self) -> None:
        self.assertEqual(handle_command("/exit", self.session), "__EXIT__")

    def test_handle_reset(self) -> None:
        self.session.send("Wiadomość przed resetem")
        result = handle_command("/reset", self.session)
        self.assertIn("nową konwersację", result)
        self.assertEqual(len(self.session.history), 1)

    def test_handle_help(self) -> None:
        result = handle_command("/help", self.session)
        self.assertIn("/exit", result)
        self.assertIn("/reset", result)

    def test_handle_unknown_returns_none(self) -> None:
        self.assertIsNone(handle_command("Zwykła wiadomość", self.session))

    def test_chat_error_is_handled(self) -> None:
        self.client.chat.side_effect = OllamaError("serwer niedostępny")
        reply = self.session.send("Cośtam")
        self.assertIn("[Błąd]", reply)
        # Wiadomość użytkownika powinna zostać usunięta z historii przy błędzie
        self.assertEqual(len(self.session.history), 1)


if __name__ == "__main__":
    unittest.main()
