"""python -m relay  (from backend/, with: uv run --extra relay --extra actors python -m relay)"""
import logging

import uvicorn

from .config import CONFIG

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")

if __name__ == "__main__":
    uvicorn.run("relay.service:app", host="127.0.0.1", port=CONFIG.port, log_level="warning")
