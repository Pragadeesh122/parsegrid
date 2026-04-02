"""ParseGrid API — FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_v1_router
from app.core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle — startup and shutdown."""
    # Startup: ensure S3 bucket exists
    try:
        from app.core.storage import get_s3_client

        client = get_s3_client()
        try:
            client.head_bucket(Bucket=settings.s3_bucket)
        except Exception:
            client.create_bucket(Bucket=settings.s3_bucket)
            print(f"Created S3 bucket: {settings.s3_bucket}")
    except Exception as e:
        print(f"Warning: Could not initialize S3 bucket: {e}")

    yield

    # Shutdown: cleanup if needed
    print("ParseGrid API shutting down.")


app = FastAPI(
    title=settings.app_name,
    description="AI-Powered Unstructured Data Extraction Pipeline",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — allow Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount v1 API routes
app.include_router(api_v1_router)


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": settings.app_name}
