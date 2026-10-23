"""Testy klienta Ollama — wszystkie operacje sieciowe są mockowane."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from src.ollama_client import OllamaClient, OllamaError


def _fake_response(payload: dict, status: int = 200) -> MagicMock:
    mock = MagicMock()
    mock.status = status
    mock.read.return_value = json.dumps(payload).encode("utf-8")
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False
    return mock


class TestOllamaClient(unittest.TestCase):
    def setUp(self) -> None:
        self.client = OllamaClient(host="http://localhost:11434", timeout=5)

    @patch("src.ollama_client.urllib.request.urlopen")
    def test_check_connection_true(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _fake_response({"models": []})
        self.assertTrue(self.client.check_connection())

    @patch("src.ollama_client.urllib.request.urlopen")
    def test_check_connection_uses_get_method(self, mock_urlopen: MagicMock) -> None:
        """Regresja: /api/tags musi wywoływać GET, nie POST."""
        mock_urlopen.return_value = _fake_response({"models": []})
        self.client.check_connection()
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.method, "GET")

    @patch("src.ollama_client.urllib.request.urlopen")
    def test_check_connection_false(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")
        self.assertFalse(self.client.check_connection())

    @patch("src.ollama_client.urllib.request.urlopen")
    def test_list_models(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _fake_response(
            {"models": [{"name": "qwen3-coder:30b"}, {"name": "llama3"}]}
        )
        models = self.client.list_models()
        self.assertIn("qwen3-coder:30b", models)
        self.assertIn("llama3", models)

    @patch("src.ollama_client.urllib.request.urlopen")
    def test_is_model_available(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _fake_response(
            {"models": [{"name": "qwen3-coder:30b"}]}
        )
        self.assertTrue(self.client.is_model_available("qwen3-coder:30b"))
        self.assertFalse(self.client.is_model_available("nieistniejący-model"))

    @patch("src.ollama_client.urllib.request.urlopen")
    def test_chat_returns_content(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _fake_response(
            {"message": {"role": "assistant", "content": "Cześć! Jestem asystentem PoE."}}
        )
        reply = self.client.chat(
            [{"role": "user", "content": "Cześć"}], model="qwen3-coder:30b"
        )
        self.assertEqual(reply, "Cześć! Jestem asystentem PoE.")

    @patch("src.ollama_client.urllib.request.urlopen")
    def test_chat_raises_on_connection_error(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")
        with self.assertRaises(OllamaError):
            self.client.chat(
                [{"role": "user", "content": "Cześć"}], model="qwen3-coder:30b"
            )

    @patch("src.ollama_client.urllib.request.urlopen")
    def test_chat_raises_on_invalid_json(self, mock_urlopen: MagicMock) -> None:
        mock = MagicMock()
        mock.read.return_value = b"to nie jest json"
        mock.__enter__.return_value = mock
        mock.__exit__.return_value = False
        mock_urlopen.return_value = mock
        with self.assertRaises(OllamaError):
            self.client.chat(
                [{"role": "user", "content": "Cześć"}], model="qwen3-coder:30b"
            )


if __name__ == "__main__":
    unittest.main()
