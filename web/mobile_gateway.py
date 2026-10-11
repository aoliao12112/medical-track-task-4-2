"""Narrow LAN gateway for expiring patient previews only.

This process intentionally does not import or mount the main OCR/LLM API. It
only proxies token reads to the loopback-only main backend so both processes
share the main process's expiring in-memory preview cache.
"""

import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Header
from fastapi.responses import FileResponse, JSONResponse


DIST_ROOT = Path(__file__).resolve().parent / "dist"
app = FastAPI(
    title="Medical Care Mobile Preview",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def preview_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


@app.get("/health")
def health():
    return {"status": "ok", "service": "mobile-preview-only"}


@app.get("/api/mobile-preview")
def get_mobile_preview(x_preview_token: str = Header(min_length=20, max_length=200)):
    backend_base = os.getenv(
        "MOBILE_PREVIEW_BACKEND_BASE_URL", "http://127.0.0.1:8765"
    ).rstrip("/")
    try:
        response = httpx.get(
            f"{backend_base}/api/mobile-preview",
            headers={"X-Preview-Token": x_preview_token},
            timeout=10,
            trust_env=False,
        )
    except httpx.HTTPError:
        return JSONResponse(status_code=502, content={"detail": "手机预览主服务暂不可用"})
    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": "手机预览主服务返回了无效响应"}
    return JSONResponse(status_code=response.status_code, content=payload)


@app.get("/")
@app.get("/patient.html")
def patient_page():
    return FileResponse(DIST_ROOT / "patient.html", media_type="text/html")


@app.get("/patient.js")
def patient_script():
    return FileResponse(DIST_ROOT / "patient.js", media_type="text/javascript")


@app.get("/patient.css")
def patient_styles():
    return FileResponse(DIST_ROOT / "patient.css", media_type="text/css")
