# apps/debug-puppet/src/main.py
from typing import List, Optional, Dict, Any
import asyncio
import json
import os
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import redis
import httpx

app = FastAPI(title="RARO Puppet Master")
templates = Jinja2Templates(directory="templates")

# Redis connection
redis_host = os.getenv("REDIS_HOST", "localhost")
redis_port = int(os.getenv("REDIS_PORT", "6379"))
kernel_url = os.getenv("KERNEL_URL", "http://kernel:3000")

# Sync Redis client for simple operations
try:
    redis_client = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
    redis_client.ping()
    print(f"✓ Connected to Redis at {redis_host}:{redis_port}")
except Exception as e:
    print(f"✗ Redis connection failed: {e}")
    redis_client = None

# Async Redis client for pub/sub
async_redis_client = None

# In-memory storage
pending_agents: Dict[str, Dict[str, Any]] = {}  # key: "run_id:agent_id" -> agent data
pre_configurations: Dict[str, str] = {}  # key: "run_id:agent_id" -> mock content
injection_history: List[Dict[str, Any]] = []

# SSE queues for different event types
pending_queues: List[asyncio.Queue] = []  # For pending agent notifications
update_queues: List[asyncio.Queue] = []   # For general updates


class MockInjection(BaseModel):
    run_id: str
    agent_id: str
    content: str
    force_tool_execution: bool = True


class PuppetResponse(BaseModel):
    run_id: str
    agent_id: str
    action: str  # "inject" or "skip"
    content: Optional[str] = None


class PreConfiguration(BaseModel):
    run_id: str
    agent_id: str
    content: str


@app.on_event("startup")
async def startup():
    """Initialize async Redis and start subscriber."""
    global async_redis_client

    try:
        # Create async Redis connection
        async_redis_client = redis.asyncio.Redis(
            host=redis_host,
            port=redis_port,
            decode_responses=True
        )
        await async_redis_client.ping()
        print("✓ Async Redis client connected")

        # Start background task for Redis subscriber
        asyncio.create_task(redis_subscriber())
        print("✓ Redis subscriber started")

    except Exception as e:
        print(f"✗ Failed to initialize async Redis: {e}")


@app.on_event("shutdown")
async def shutdown():
    """Clean up Redis connections."""
    if async_redis_client:
        await async_redis_client.close()


async def redis_subscriber():
    """Listen for pending agent events from Kernel."""
    if not async_redis_client:
        print("✗ Async Redis not available, subscriber disabled")
        return

    try:
        pubsub = async_redis_client.pubsub()
        await pubsub.subscribe("puppet:channel")

        print("🎭 Puppet subscriber listening for pending agents...")

        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    agent_key = f"{data['run_id']}:{data['agent_id']}"

                    # Check if there's a pre-configuration for this agent
                    preconfigured_content = pre_configurations.get(agent_key)
                    if preconfigured_content:
                        data["preconfigured"] = True
                        data["preconfigured_content"] = preconfigured_content
                    else:
                        data["preconfigured"] = False

                    # Store pending agent
                    pending_agents[agent_key] = data

                    # Broadcast to UI via SSE
                    event = {
                        "type": "pending_agent",
                        "data": data
                    }

                    for queue in pending_queues:
                        try:
                            await queue.put(event)
                        except:
                            pass

                    print(f"🎭 Agent pending: {data['agent_id']} in {data['run_id'][:8]} "
                          f"(preconfigured: {data['preconfigured']})")

                except Exception as e:
                    print(f"Error processing puppet message: {e}")

    except Exception as e:
        print(f"✗ Redis subscriber error: {e}")


@app.get("/", response_class=HTMLResponse)
async def puppet_dashboard(request: Request):
    """Render the Puppet Master control panel."""
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
    })


@app.post("/respond")
async def respond_to_pending(payload: PuppetResponse):
    """
    User decision: inject mock OR skip (use LLM).
    Called when user clicks "Inject & Continue" or "Skip (Use LLM)".
    """
    if not redis_client:
        raise HTTPException(503, "Redis unavailable")

    run_id = payload.run_id
    agent_id = payload.agent_id
    action = payload.action
    agent_key = f"{run_id}:{agent_id}"

    if action == "inject":
        if not payload.content:
            raise HTTPException(400, "Content required for inject action")

        # Store mock in Redis (for agent-service to find)
        mock_key = f"mock:{run_id}:{agent_id}"
        mock_payload = {
            "content": payload.content,
            "force_tool_execution": True
        }
        redis_client.setex(mock_key, 600, json.dumps(mock_payload))

        # Record in history
        record = {
            "id": f"{run_id}_{agent_id}_{datetime.now().timestamp()}",
            "time": datetime.now().isoformat(),
            "run_id": run_id,
            "agent_id": agent_id,
            "content": payload.content[:200] + "..." if len(payload.content) > 200 else payload.content,
            "status": "injected",
            "action": "inject"
        }
        injection_history.append(record)

        print(f"💉 Mock injected for {agent_id}")
    else:
        # Record skip in history
        record = {
            "id": f"{run_id}_{agent_id}_{datetime.now().timestamp()}",
            "time": datetime.now().isoformat(),
            "run_id": run_id,
            "agent_id": agent_id,
            "content": "(skipped - using LLM)",
            "status": "skipped",
            "action": "skip"
        }
        injection_history.append(record)
        print(f"⏩ Skipped {agent_id} - using real LLM")

    # Signal Kernel to continue
    response_key = f"puppet:response:{run_id}:{agent_id}"
    redis_client.setex(response_key, 30, action)

    # Remove from pending
    pending_agents.pop(agent_key, None)

    # Remove pre-configuration if it exists
    pre_configurations.pop(agent_key, None)

    # Broadcast to update queues
    for queue in update_queues:
        try:
            await queue.put(record)
        except:
            pass

    return {"status": "acknowledged", "action": action, "record": record}


@app.post("/preconfigure")
async def preconfigure_mock(payload: PreConfiguration):
    """
    Pre-configure a mock for a future agent execution.
    Allows setting up mocks before the agent even starts.
    """
    agent_key = f"{payload.run_id}:{payload.agent_id}"
    pre_configurations[agent_key] = payload.content

    print(f"📝 Pre-configured mock for {payload.agent_id} in {payload.run_id[:8]}")

    return {
        "status": "preconfigured",
        "agent_key": agent_key,
        "content_length": len(payload.content)
    }


@app.get("/preconfigurations")
async def list_preconfigurations():
    """List all pre-configured mocks."""
    configs = []
    for key, content in pre_configurations.items():
        run_id, agent_id = key.split(":", 1)
        configs.append({
            "run_id": run_id,
            "agent_id": agent_id,
            "content": content[:100] + "..." if len(content) > 100 else content
        })
    return {"preconfigurations": configs}


@app.delete("/preconfigure/{run_id}/{agent_id}")
async def remove_preconfiguration(run_id: str, agent_id: str):
    """Remove a pre-configured mock."""
    agent_key = f"{run_id}:{agent_id}"
    if agent_key in pre_configurations:
        del pre_configurations[agent_key]
        return {"status": "removed", "agent_key": agent_key}
    return {"status": "not_found", "agent_key": agent_key}


@app.get("/pending")
async def list_pending():
    """List all currently pending agents."""
    return {"pending": list(pending_agents.values())}


@app.get("/topology/{run_id}")
async def get_topology(run_id: str):
    """Fetch DAG topology from Kernel for visualization."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{kernel_url}/runs/{run_id}/topology")
            if response.status_code == 200:
                return response.json()
            else:
                raise HTTPException(response.status_code, "Failed to fetch topology from Kernel")
    except Exception as e:
        print(f"Error fetching topology: {e}")
        raise HTTPException(503, f"Could not reach Kernel: {str(e)}")


@app.get("/injections")
async def list_injections():
    """List all injections in history."""
    return {"injections": list(reversed(injection_history))}


@app.post("/clear")
async def clear_history():
    """Clear the injection history."""
    injection_history.clear()
    return {"status": "cleared"}


@app.get("/status")
async def status():
    """Health check endpoint."""
    redis_ok = False
    if redis_client:
        try:
            redis_client.ping()
            redis_ok = True
        except:
            pass

    async_redis_ok = False
    if async_redis_client:
        try:
            await async_redis_client.ping()
            async_redis_ok = True
        except:
            pass

    return {
        "service": "puppet-master",
        "redis_sync": "connected" if redis_ok else "disconnected",
        "redis_async": "connected" if async_redis_ok else "disconnected",
        "pending_count": len(pending_agents),
        "preconfigured_count": len(pre_configurations),
        "injections_count": len(injection_history)
    }


@app.get("/stream/pending")
async def stream_pending(request: Request):
    """SSE stream for pending agents."""

    async def event_generator():
        queue = asyncio.Queue()
        pending_queues.append(queue)

        try:
            # Send current pending agents immediately
            for agent in pending_agents.values():
                event = {"type": "pending_agent", "data": agent}
                yield f"data: {json.dumps(event)}\n\n"

            # Stream new events
            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            pending_queues.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.get("/stream/updates")
async def stream_updates(request: Request):
    """SSE stream for general updates (injections, history)."""

    async def event_generator():
        queue = asyncio.Queue()
        update_queues.append(queue)

        try:
            while True:
                if await request.is_disconnected():
                    break

                try:
                    record = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(record)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            update_queues.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
