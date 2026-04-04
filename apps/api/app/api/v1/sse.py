"""ParseGrid API — SSE streaming endpoint for real-time job status.

Uses FastAPI StreamingResponse with text/event-stream content type.
Subscribes to Redis PubSub channel for the specific job.
The Next.js client listens using the native browser EventSource API.

Authentication: The browser EventSource API cannot send custom headers.
Instead, the request goes through the Next.js rewrite proxy (same-origin),
so the Auth.js session cookie is sent automatically. This endpoint reads
the JWT from that cookie.

NO WebSocket. NO socket.io. NO query-param tokens.
"""

import asyncio
import json
from collections.abc import AsyncGenerator

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.core.config import settings
from app.core.security import TokenPayload
from app.models.job import Job, JobStatus

router = APIRouter(prefix="/jobs", tags=["SSE"])


# --- SSE-specific auth dependency (cookie-based) ---


async def verify_sse_cookie(request: Request) -> TokenPayload:
    """Read the Auth.js session JWT from the cookie and verify it.

    Checks both cookie names:
    - "authjs.session-token"           (local HTTP / development)
    - "__Secure-authjs.session-token"  (production HTTPS)

    This is used ONLY for the SSE endpoint because the browser EventSource
    API cannot send Authorization headers.
    """
    token = (
        request.cookies.get("authjs.session-token")
        or request.cookies.get("__Secure-authjs.session-token")
    )

    if not token:
        raise HTTPException(status_code=401, detail="Missing session cookie")

    try:
        payload = jwt.decode(
            token,
            settings.auth_secret,
            algorithms=[settings.jwt_algorithm],
            options={"verify_aud": False},
        )
        return TokenPayload(payload)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


# --- SSE event generator ---


async def _event_generator(
    job_id: str,
    user_id: str,
    db: AsyncSession,
) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE events for a given job.

    1. Sends the current state immediately
    2. Subscribes to Redis PubSub channel `job:{job_id}:status`
    3. Yields events until the job reaches a terminal state
    """
    import redis.asyncio as aioredis

    # Send current state as the first event
    query = select(Job).where(Job.id == job_id, Job.user_id == user_id)
    result = await db.execute(query)
    job = result.scalar_one_or_none()
    if not job:
        yield _format_sse({"error": "Job not found"}, event="error")
        return

    yield _format_sse(
        {
            "status": job.status.value,
            "progress": job.progress,
            "connection_string": job.connection_string,
        },
        event="status",
    )

    # If already terminal, close the stream
    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
        return

    # Subscribe to Redis PubSub for live updates
    redis_client = aioredis.from_url(settings.redis_url)
    pubsub = redis_client.pubsub()
    channel = f"job:{job_id}:status"

    try:
        await pubsub.subscribe(channel)

        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=1.0,
            )
            if message and message["type"] == "message":
                data = json.loads(message["data"])
                yield _format_sse(data, event="status")

                # Close stream on terminal states
                if data.get("status") in (
                    JobStatus.COMPLETED.value,
                    JobStatus.FAILED.value,
                ):
                    break

            # Send keepalive comment every cycle to prevent timeout
            yield ": keepalive\n\n"
            await asyncio.sleep(1)

    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await redis_client.close()


def _format_sse(data: dict, event: str = "message") -> str:
    """Format a dict as an SSE event string."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# --- Endpoint ---


@router.get(
    "/{job_id}/stream",
    summary="SSE stream for real-time job status updates",
    response_class=StreamingResponse,
)
async def stream_job_status(
    job_id: str,
    user: TokenPayload = Depends(verify_sse_cookie),
    db: AsyncSession = Depends(get_db_session),
):
    """Server-Sent Events endpoint. Connect with EventSource in the browser.

    Authentication is via the Auth.js session cookie (sent automatically
    when the request goes through the Next.js rewrite proxy at same-origin).

    Usage (client-side):
    ```javascript
    const es = new EventSource('/api/v1/jobs/{id}/stream');
    es.addEventListener('status', (e) => {
      const data = JSON.parse(e.data);
      console.log(data.status, data.progress);
    });
    ```
    """
    # Verify job exists and belongs to user
    query = select(Job).where(Job.id == job_id, Job.user_id == user.sub)
    result = await db.execute(query)
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return StreamingResponse(
        _event_generator(job_id, user.sub, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
