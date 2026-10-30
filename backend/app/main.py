from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, candidates, chat, knowledge, knowledge_retrieval, employees, staffing, staffing_runs
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.init_db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    init_db()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(candidates.router)
app.include_router(chat.router)
app.include_router(knowledge.router)
app.include_router(knowledge_retrieval.router)
app.include_router(employees.router)
app.include_router(staffing.router)
app.include_router(staffing_runs.router)


@app.get("/health")
def health():
    return {"status": "ok", "service": settings.app_name}
