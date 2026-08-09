"""
workers/preloader_worker.py — Background VRAM preloader worker for Phantom AI.
"""

import time
import requests
from PyQt6.QtCore import QThread, pyqtSignal
from llm import OLLAMA_BASE_URL, OLLAMA_MODEL
from utils.logger import log_info, log_success, log_error


class ModelPreloaderWorker(QThread):
    """
    Background worker that preloads the local Ollama LLM into GPU VRAM
    at application startup and pins it in VRAM indefinitely using keep_alive: -1.
    """
    preload_finished = pyqtSignal(bool, str)  # (success, message)

    def run(self):
        log_info("Preloader", f"Initializing VRAM preload for local model '{OLLAMA_MODEL}'...")
        t0 = time.time()
        url = OLLAMA_BASE_URL.replace("/v1", "/api/generate")
        try:
            resp = requests.post(
                url,
                json={
                    "model": OLLAMA_MODEL,
                    "keep_alive": -1
                },
                timeout=90
            )
            elapsed = round(time.time() - t0, 2)
            if resp.status_code == 200:
                msg = f"Model '{OLLAMA_MODEL}' fully loaded in VRAM ({elapsed}s)."
                log_success("Preloader", msg)
                self.preload_finished.emit(True, msg)
            else:
                msg = f"Preload returned HTTP status {resp.status_code}"
                log_error("Preloader", msg)
                self.preload_finished.emit(False, msg)
        except Exception as e:
            elapsed = round(time.time() - t0, 2)
            msg = f"Connection error after {elapsed}s: {e}"
            log_error("Preloader", msg)
            self.preload_finished.emit(False, msg)

