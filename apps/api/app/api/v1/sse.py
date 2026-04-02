"""ParseGrid API — SSE streaming endpoint for real-time job status.

Uses FastAPI StreamingResponse with text/event-stream content type.
Subscribes to Redis PubSub channel for the specific job.
The Next.js client listens using the native browser EventSource API.

NO WebSocket. NO socket.io.
"""

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.core.config import settings
from app.core.security import TokenPayload
from app.models.job import Job, JobStatus

router = APIRouter(prefix="/jobs", tags=["SSE"])


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

            # Send keepalive comment every 15 seconds to prevent timeout
            yield ": keepalive\n\n"
            await asyncio.sleep(1)

    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await redis_client.close()


def _format_sse(data: dict, event: str = "message") -> str:
    """Format a dict as an SSE event string."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.get(
    "/{job_id}/stream",
    summary="SSE stream for real-time job status updates",
    response_class=StreamingResponse,
)
async def stream_job_status(
    job_id: str,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Server-Sent Events endpoint. Connect with EventSource in the browser.

    Usage (client-side):
    ```javascript
    const es = new EventSource('/api/v1/jobs/{id}/stream', {
      headers: { Authorization: 'Bearer <token>' }
    });
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
