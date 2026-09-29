"""
FastAPI app entrypoint.

Owner: Samir. Day 1 skeleton, wired up incrementally through Day 4.
Run with: uvicorn pipeline.api.main:app --reload
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from pipeline.api.routes import router

app = FastAPI(title="AI Network Architect Copilot")
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}


DASHBOARD_DIR = Path(__file__).resolve().parent.parent.parent / "dashboard" / "static"
app.mount("/", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")
