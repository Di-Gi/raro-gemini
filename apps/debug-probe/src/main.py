# apps/debug-probe/src/main.py
from typing import List, Optional, Dict, Any
import asyncio
import json
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

app = FastAPI(title="RARO Debug Probe")
templates = Jinja2Templates(directory="templates")

# In-memory storage
captured_logs = []

# SSE notification queue
update_queues: List[asyncio.Queue] = []

class CapturePayload(BaseModel):
    id: str
    time: str
    agent_id: str
    run_id: str
    tools: Optional[List[str]] = []
    final_system_prompt: str
    final_user_message: str
    original_payload: Dict[str, Any]

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "captures": list(reversed(captured_logs))
    })

@app.post("/clear")
async def clear_logs():
    captured_logs.clear()
    return {"status": "cleared"}

# NEW: Passive Capture Endpoint
@app.post("/capture")
async def capture_debug_log(payload: CapturePayload):
    log_data = payload.model_dump()
    captured_logs.append(log_data)

    # Broadcast to all connected SSE clients
    for queue in update_queues:
        try:
            await queue.put(log_data)
        except:
            pass  # Client disconnected, ignore

    return {"status": "captured"}

# SSE endpoint for real-time updates
@app.get("/events")
async def stream_events(request: Request):
    """Server-Sent Events endpoint for real-time capture updates."""

    async def event_generator():
        # Create a queue for this client
        queue = asyncio.Queue()
        update_queues.append(queue)

        try:
            while True:
                # Check if client is still connected
                if await request.is_disconnected():
                    break

                try:
                    # Wait for new capture with timeout
                    log_data = await asyncio.wait_for(queue.get(), timeout=30.0)

                    # Format as SSE
                    yield f"data: {json.dumps(log_data)}\n\n"

                except asyncio.TimeoutError:
                    # Send keepalive comment every 30s
                    yield ": keepalive\n\n"

        finally:
            # Clean up when client disconnects
            update_queues.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable buffering for SSE
        }
    )
