import os

from fastapi import FastAPI

SERVICE_NAME = os.getenv("SERVICE_NAME", "catalog-service")
SERVICE_VERSION = os.getenv("SERVICE_VERSION", "0.1.0")

app = FastAPI(title=SERVICE_NAME, version=SERVICE_VERSION)

@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME, "version": SERVICE_VERSION}
