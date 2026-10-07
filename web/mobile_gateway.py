"""Narrow LAN gateway for expiring patient previews only.

This process intentionally does not import or mount the main OCR/LLM API.
"""

from pathlib import Path

from fastapi import FastAPI, Header
from fastapi.responses import FileResponse, JSONResponse

from medical_claims.storage import CaseStorage, WorkflowItemNotFound


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


@app.exception_handler(WorkflowItemNotFound)
async def preview_not_found_handler(request, exc):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.get("/health")
def health():
    return {"status": "ok", "service": "mobile-preview-only"}


@app.get("/api/mobile-preview")
def get_mobile_preview(x_preview_token: str = Header(min_length=20, max_length=200)):
    return CaseStorage().get_mobile_preview(x_preview_token)


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
