import logging
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import vectors
from app.api.reports import router as reports_router
from app.db import connect

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create the Qdrant collections on startup. Qdrant down = warn and start anyway
    (/healthz?db=1 will then show qdrant: false)."""
    try:
        vectors.ensure_collections()
    except Exception as e:  # noqa: BLE001 - any Qdrant failure must not stop the API
        log.warning("Qdrant not reachable at startup (%s); vector search is off", type(e).__name__)
    yield


app = FastAPI(title="Civic Router API", lifespan=lifespan)
app.include_router(reports_router)


@app.exception_handler(RequestValidationError)
def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's own 422s (bad float, bad uuid, ...) in our error shape."""
    fields = [".".join(str(p) for p in e["loc"]) for e in exc.errors()]
    return JSONResponse(
        {"error": {"code": "validation", "message_key": "invalid_input", "detail": {"fields": fields}}},
        status_code=422,
    )


@app.exception_handler(StarletteHTTPException)
def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Starlette's own errors (unknown route 404, 405, ...) in our error shape."""
    if exc.status_code == 404:
        body = {"code": "not_found", "message_key": "not_found"}
    else:
        body = {"code": "http_error", "message_key": "server_error"}
    return JSONResponse({"error": body}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(Exception)
def server_error(request: Request, exc: Exception) -> JSONResponse:
    """Any bug: our error shape, no details (they can hold SQL or secrets). The traceback
    still goes to the server log."""
    return JSONResponse({"error": {"code": "server_error", "message_key": "server_error"}},
                        status_code=500)


def check_db() -> dict:
    with connect() as conn:
        conn.execute("select 1")
        postgis = conn.execute("select 1 from pg_extension where extname = 'postgis'").fetchone()
    return {"select1": True, "postgis": postgis is not None}


def check_qdrant() -> bool:
    try:
        vectors.client().get_collections()
        return True
    except Exception:  # noqa: BLE001 - down, wrong key, timeout: all just mean "not healthy"
        return False


@app.get("/healthz")
def healthz(db: int = 0) -> dict:
    if not db:
        return {"ok": True}
    try:
        status = check_db()
    except psycopg.Error as e:
        # Only the error type: the full message can contain the connection string (password).
        return {"ok": False, "db": {"error": type(e).__name__}}
    status["qdrant"] = check_qdrant()
    return {"ok": status["postgis"] and status["qdrant"], "db": status}
