"""
Inicia el servidor Robot 2026.
Requisito previo: haber ejecutado setup.bat una vez.
Uso: .venv\Scripts\python.exe server.py
"""
import sys
import webbrowser
import threading
import time
from pathlib import Path

import uvicorn

PORT = 8000
URL = f"http://localhost:{PORT}"
ROOT = Path(__file__).parent
DIST = ROOT / "frontend" / "index.html"


def open_browser():
    time.sleep(2)
    webbrowser.open(f"{URL}/?v={int(time.time())}")


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  Robot 2026 — Automatización de Reportes")
    print("=" * 50)

    if not DIST.exists():
        print("\n  ERROR: No se encuentra frontend/index.html\n")
        sys.exit(1)

    print(f"\n  Abriendo en: {URL}")
    print("  Presiona Ctrl+C para detener\n")

    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=PORT, reload=False)

