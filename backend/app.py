"""FastAPI entrypoint.

Mounts /api/* via the routes router, and serves the front-end (mockup.html
plus any sibling static assets) from backend/static/.

Run:  python3 backend/app.py
Or:   python3 -m uvicorn backend.app:app --reload --port 8765
"""
from __future__ import annotations
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from . import db
from . import routes as api_routes

db.init_db()  # safe to call repeatedly — schema is idempotent

app = FastAPI(title="机票历史价参考 API", version="0.1.0")
app.include_router(api_routes.router)

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

# html=True → serves index.html on directory hit; trailing-slash friendly
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="info")
