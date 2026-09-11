"""
Frontend Static File Server
============================
Serves the client/ directory on FRONTEND_PORT (default 3000).
Run from the project root:  python client/serve.py
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

FRONTEND_PORT = int(os.environ.get("FRONTEND_PORT", 3000))
FRONTEND_TLS = os.environ.get("FRONTEND_TLS", "1") != "0"
CLIENT_DIR = Path(__file__).resolve().parent

if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    app = FastAPI()

    @app.get("/config.js")
    async def config_js():
        backend_port = int(os.environ.get("BACKEND_PORT", 8000))
        from fastapi.responses import Response
        return Response(
            content=f"window.BACKEND_PORT = {backend_port};",
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/")
    async def index():
        return FileResponse(str(CLIENT_DIR / "index.html"))


    app.mount("/static", StaticFiles(directory=str(CLIENT_DIR)), name="static")

    print("=" * 50)
    print("  Frontend Server")
    scheme = "https" if FRONTEND_TLS else "http"
    print(f"  URL: {scheme}://localhost:{FRONTEND_PORT}")
    print("=" * 50)
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    uvicorn_options = {
        "app": app,
        "host": "0.0.0.0",
        "port": FRONTEND_PORT,
    }
    if FRONTEND_TLS:
        uvicorn_options.update(
            ssl_keyfile=os.path.join(BASE_DIR, "key.pem"),
            ssl_certfile=os.path.join(BASE_DIR, "cert.pem"),
        )
    uvicorn.run(**uvicorn_options)
