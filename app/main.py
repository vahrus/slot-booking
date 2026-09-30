from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database.database import init_db
from app.routers import admin, public


BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
    """Prepare application resources for the server lifetime."""
    init_db()
    yield


app = FastAPI(title="Slotly", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(public.router)
app.include_router(admin.router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Report whether the application is running."""
    return {"status": "ok", "app": "Slotly"}
