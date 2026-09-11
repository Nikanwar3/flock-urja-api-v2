"""Entry point: `uvicorn app.main:app --reload`.

A clean REST API in front of the "Urja Meter Ops" legacy portal. See
README.md for what this is and PROTOCOL.md for how the portal itself
works.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import get_settings
from app.portal_errors import PortalAuthError, PortalUnavailableError
from app.routers import health, hierarchy, meters, transformers
from app.service import UrjaService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    service = UrjaService(settings)
    app.state.service = service
    try:
        yield
    finally:
        await service.aclose()


app = FastAPI(
    title="Urja Meter Ops API",
    description=(
        "A clean, documented REST API in front of the Urja Meter Ops utility portal. "
        "See /docs for interactive exploration, and the project's PROTOCOL.md for how "
        "the underlying portal actually works."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(meters.router)
app.include_router(transformers.router)
app.include_router(hierarchy.router)
app.include_router(health.router)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Anyone hitting the bare URL is almost certainly a human looking for
    the docs, not an API client — a 404 there just looks broken."""
    return RedirectResponse(url="/docs")


@app.exception_handler(PortalUnavailableError)
async def portal_unavailable_handler(request: Request, exc: PortalUnavailableError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"error": "portal_unavailable", "message": str(exc)})


@app.exception_handler(PortalAuthError)
async def portal_auth_handler(request: Request, exc: PortalAuthError) -> JSONResponse:
    # This means *our* service credentials are wrong, not the caller's —
    # 500 is more honest here than 401 (the caller didn't do anything wrong).
    return JSONResponse(status_code=500, content={"error": "portal_auth_failed", "message": str(exc)})
