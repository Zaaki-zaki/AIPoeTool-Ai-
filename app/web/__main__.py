"""Start: uv run python -m app.web  ->  http://127.0.0.1:8000"""

import uvicorn

from ..env import load_env
from .server import create_app

if __name__ == "__main__":
    load_env()
    print("AIPoeTool: http://127.0.0.1:8000  (Ctrl+C kończy)")
    uvicorn.run(create_app(), host="127.0.0.1", port=8000, log_level="warning")
