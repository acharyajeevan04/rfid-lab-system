import os
import sys
import time
import threading
import webbrowser
from pathlib import Path

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR = get_base_dir()
os.chdir(BASE_DIR)

def open_browser():
    time.sleep(2)
    webbrowser.open("http://127.0.0.1:8000")

threading.Thread(target=open_browser, daemon=True).start()

import uvicorn
from main import app

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info"
    )