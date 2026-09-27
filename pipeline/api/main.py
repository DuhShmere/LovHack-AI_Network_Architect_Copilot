"""
FastAPI app entrypoint.

Owner: Samir. Day 1 skeleton, wired up incrementally through Day 4.
Run with: uvicorn pipeline.api.main:app --reload
"""

from fastapi import FastAPI
from pipeline.api.routes import router

app = FastAPI(title="AI Network Architect Copilot")
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
