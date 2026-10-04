"""
FastAPI app entrypoint.

Owner: Samir. Serves the API and the dashboard (dashboard/static at /).
Run with: uvicorn pipeline.api.main:app --reload
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from pipeline.api.routes import router

app = FastAPI(title="AI Network Architect Copilot")
app.include_router(router)


@app.middleware("http")
async def revalidate_every_load(request: Request, call_next):
    # Without this, browsers keep serving a stale dashboard after redesigns.
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


DASHBOARD_DIR = Path(__file__).resolve().parent.parent.parent / "dashboard" / "static"
app.mount("/", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")
