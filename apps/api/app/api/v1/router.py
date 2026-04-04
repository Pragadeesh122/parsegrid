"""ParseGrid API — v1 Router aggregator."""

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.connections import router as connections_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.sse import router as sse_router
from app.api.v1.upload import router as upload_router

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(auth_router)
api_v1_router.include_router(jobs_router)
api_v1_router.include_router(upload_router)
api_v1_router.include_router(sse_router)
api_v1_router.include_router(connections_router)
