"""Testy GUI AIPoeTool — używają mocków zamiast prawdziwego Ollama i GUI."""

from __future__ import annotations

import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import tkinter as tk

from gui import AIPoeToolGUI, load_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


class TestGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_client.check_connection.return_value = True
        self.mock_session = MagicMock()
        self.mock_session.send.return_value = "Odpowiedź testowa modelu."
        self.app = AIPoeToolGUI(
            self.root, client=self.mock_client, session=self.mock_session
        )

    def tearDown(self):
        self.app.root.update_idletasks()

    def test_load_config_defaults(self):
        config = load_config(CONFIG_PATH)
        self.assertEqual(config.get("model"), "qwen3-coder:30b")
        self.assertEqual(config.get("ollama_host"), "http://localhost:11434")
        self.assertEqual(config.get("language"), "pl")

    def test_send_message_calls_session_send(self):
        self.app.input_text.insert("1.0", "Cześć, jak się masz?")
        self.app.send_message()
        # Pole wejścia powinno zostać wyczyszczone synchronicznie
        self.assertEqual(self.app.input_text.get("1.0", tk.END).strip(), "")
        self.assertTrue(self.app._generating)
        # Wątek powinien zdążyć wywołać session.send
        time.sleep(0.3)
        self.mock_session.send.assert_called_once_with("Cześć, jak się masz?")

    def test_send_message_blocks_when_generating(self):
        self.app._generating = True
        self.app.input_text.insert("1.0", "Cześć")
        self.app.send_message()
        self.mock_session.send.assert_not_called()
        self.assertEqual(self.app.input_text.get("1.0", tk.END).strip(), "Cześć")

    def test_exit_command_calls_on_closing(self):
        self.app.input_text.insert("1.0", "/exit")
        with patch.object(self.app, "on_closing") as mock_close:
            self.app.send_message()
            mock_close.assert_called_once()

    def test_reset_command_resets_session(self):
        self.app.input_text.insert("1.0", "/reset")
        self.app.send_message()
        self.mock_session.reset.assert_called_once()
        self.assertFalse(self.app._generating)

    def test_help_command_displays_in_history(self):
        self.app.input_text.insert("1.0", "/help")
        self.app.send_message()
        self.root.update_idletasks()
        content = self.app.history.get("1.0", tk.END)
        self.assertIn("/exit", content)
        self.assertIn("/reset", content)

    def test_new_conversation_clears_history(self):
        self.app.input_text.insert("1.0", "test")
        # Symulujemy stan po wysłaniu wiadomości, bez uruchamiania wątku
        # (operacje Tkinter z innego wątku są bezpieczne tylko z główną pętlą zdarzeń).
        self.app._append_message("Ty", "test", "user")
        self.app.input_text.delete("1.0", tk.END)
        self.app.new_conversation()
        self.mock_session.reset.assert_called()
        content = self.app.history.get("1.0", tk.END)
        self.assertIn("Nowa rozmowa rozpoczęta", content)

    def test_check_connection_calls_client(self):
        self.app.check_connection()
        time.sleep(0.3)
        self.mock_client.check_connection.assert_called()

    def test_on_response_displays_reply(self):
        self.app._on_response("Testowa odpowiedź.")
        self.root.update_idletasks()
        content = self.app.history.get("1.0", tk.END)
        self.assertIn("Testowa odpowiedź.", content)
        self.assertFalse(self.app._generating)

    def test_on_response_displays_error(self):
        self.app._on_response("[Błąd] Serwer niedostępny.")
        self.root.update_idletasks()
        content = self.app.history.get("1.0", tk.END)
        self.assertIn("[Błąd] Serwer niedostępny.", content)
        self.assertFalse(self.app._generating)

    def test_shift_enter_allows_newline(self):
        # Shift+Enter nie powinno wywoływać send_message
        self.app.input_text.insert("1.0", "Linia 1")

        class FakeEvent:
            state = 0x0001  # Shift wciśnięty

        result = self.app._on_enter(FakeEvent())
        self.assertIsNone(result)
        self.assertFalse(self.app._generating)


if __name__ == "__main__":
    unittest.main()
