#!/usr/bin/env bash
# Quick dev-server launcher.
source venv/bin/activate
uvicorn pipeline.api.main:app --reload
