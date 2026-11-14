import errno
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.routes import router
from app.core.config import get_settings
from app.database.session import SessionLocal, ensure_schema
from app.models.tables import ProcessingHistory, now
from app.services.knowledge import service as knowledge

logging.basicConfig(level=logging.INFO)
settings = get_settings()
settings.storage_dir.mkdir(parents=True, exist_ok=True)
ensure_schema()

# A job running when the process stopped will never finish; say so rather than show it as live.
with SessionLocal() as db:
    for job in db.scalars(select(ProcessingHistory).where(ProcessingHistory.status.in_(("queued", "running")))):
        job.status, job.error, job.finished_at = "failed", "Server restarted during processing", now()
    db.commit()
# Historical BOQs queued or mid-way when the server stopped are picked up again.
knowledge.resume_pending()

app = FastAPI(title="BOQ Component Mapping")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    """Give the page a reason it can show, rather than a bare 500."""
    logging.getLogger("app").exception("Unhandled error on %s", request.url.path)
    reason = str(exc) or type(exc).__name__
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == errno.ENOSPC or "disk is full" in reason.lower():
        reason = "The disk is full, so nothing could be saved. Free space on the C: drive and try again."
    return JSONResponse(status_code=500, content={"detail": f"Server error: {reason}"})


@app.get("/health")
def health():
    return {"status": "ok"}
