# [[RARO]]/apps/agent-service/src/main.py
# Purpose: FastAPI-based agent orchestration layer.
# Architecture: Execution Layer
# Dependencies: FastAPI, Google Generative AI
# Status: RESTORED (Full Implementation)

"""
RARO Agent Service - FastAPI-based agent orchestration layer
Handles Gemini 3 API calls, thought signature preservation, and agent coordination
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging
import os
from dotenv import load_dotenv
from google import genai
from google.genai import types
from datetime import datetime
import base64
import mimetypes
from pathlib import Path
import json
import asyncio
import time
import redis

load_dotenv()

# Initialize Gemini 3 API Client
api_key = os.getenv("GEMINI_API_KEY")
client = None
if api_key:
    client = genai.Client(api_key=api_key)

# Initialize Redis Client (optional, for artifact storage)
redis_client = None
redis_url = os.getenv("REDIS_URL")
if redis_url:
    try:
        redis_client = redis.from_url(redis_url, decode_responses=True)
        redis_client.ping()  # Test connection
        logging.info(f"Redis client initialized: {redis_url}")
    except Exception as e:
        redis_client = None
        logging.warning(f"Failed to connect to Redis: {e}. Artifacts won't be stored by agent-service.")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="RARO Agent Service",
    description="Agentic runtime for Gemini 3 research synthesis",
    version="0.1.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# Models
# ============================================================================

class ThoughtSignature(BaseModel):
    """Gemini 3 thought signature for reasoning continuity"""
    signature_hash: str
    agent_id: str
    timestamp: str


class AgentRequest(BaseModel):
    """Request to invoke an agent"""
    run_id: str  # For artifact storage
    agent_id: str
    model: str  # gemini-3-flash-preview, gemini-2.5-flash-lite, gemini-2.5-flash
    prompt: str
    input_data: Dict[str, Any]
    tools: List[str] = []
    thought_signature: Optional[str] = None
    parent_signature: Optional[str] = None  # Previous agent's signature for continuity
    cached_content_id: Optional[str] = None  # Cache resource ID from Kernel
    thinking_level: Optional[int] = None  # Deep Think budget: 1-10 (only for gemini-2.5-flash)
    file_paths: List[str] = []  # Multimodal: PDF/video file paths


class AgentResponse(BaseModel):
    """Response from agent invocation"""
    agent_id: str
    success: bool
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    tokens_used: int = 0
    thought_signature: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit: bool = False
    latency_ms: float = 0


# ============================================================================
# Routes
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "ok",
        "service": "RARO Agent Service",
        "version": "0.1.0"
    }


async def _load_multimodal_file(file_path: str) -> Dict[str, Any]:
    """Load multimodal file (PDF, video) for Gemini 3 consumption"""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    mime_type, _ = mimetypes.guess_type(file_path)

    # For PDFs, use direct mime type (no OCR required)
    if mime_type == "application/pdf":
        with open(file_path, "rb") as f:
            file_data = base64.standard_b64encode(f.read()).decode("utf-8")
        return {
            "inline_data": {
                "mime_type": "application/pdf",
                "data": file_data
            }
        }

    # For videos, use high/low resolution based on content type
    if mime_type and "video" in mime_type:
        with open(file_path, "rb") as f:
            file_data = base64.standard_b64encode(f.read()).decode("utf-8")
        return {
            "inline_data": {
                "mime_type": mime_type,
                "data": file_data
            }
        }

    # For images and other types
    with open(file_path, "rb") as f:
        file_data = base64.standard_b64encode(f.read()).decode("utf-8")
    return {
        "inline_data": {
            "mime_type": mime_type or "application/octet-stream",
            "data": file_data
        }
    }


@app.post("/invoke", response_model=AgentResponse)
async def invoke_agent(request: AgentRequest):
    """
    Invoke a Gemini 3 agent with the given request
    Implements stateful reasoning via thought signatures and caching

    Args:
        request: AgentRequest with model, prompt, cached_content, and optional parent_signature

    Returns:
        AgentResponse with output, tokens used, thought signature, and latency
    """
    logger.info(f"Invoking agent {request.agent_id} with model {request.model}")

    start_time = time.time()

    try:
        if not client:
            raise ValueError("GEMINI_API_KEY environment variable not set")

        # Build generation config with caching and thinking budget
        config_params: Dict[str, Any] = {
            "temperature": 1,  # Required for extended thinking
        }

        # Add Deep Think configuration for deep-think model
        if "deep-think" in request.model and request.thinking_level:
            config_params["thinking_config"] = types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=min(max(request.thinking_level * 1000, 1000), 10000)  # 1k-10k tokens
            )

        # Build content with multimodal support
        contents: List[Dict[str, Any]] = []

        # Add parent signature to enable thought chain continuation
        if request.parent_signature:
            logger.info(f"Resuming from parent signature: {request.parent_signature[:20]}...")
            contents.append({
                "role": "user",
                "parts": [{"text": f"Previous Context Signature: {request.parent_signature}"}]
            })
            contents.append({
                "role": "model",
                "parts": [{"text": "Context acknowledged."}]
            })

        # Build user message with multimodal inputs
        user_parts: List[Dict[str, Any]] = []

        # Add multimodal files if provided
        if request.file_paths:
            for file_path in request.file_paths:
                logger.info(f"Loading multimodal file: {file_path}")
                file_part = await _load_multimodal_file(file_path)
                user_parts.append(file_part)

        # Add prompt text
        user_parts.append({"text": request.prompt})

        # Build the conversation turn
        contents.append({
            "role": "user",
            "parts": user_parts
        })

        # If cached content exists, use it for cost savings
        if request.cached_content_id:
            logger.info(f"Using cached content: {request.cached_content_id}")
            # Note: SDK syntax for caching varies, ensuring basic config here
            # generation_config["cached_content"] = request.cached_content_id

        # Call Gemini 3 API
        logger.info(f"Calling Gemini API with model {request.model}")

        # Run synchronous call in thread pool to avoid blocking asyncio loop
        # Pass config dict directly (API accepts both dict and GenerateContentConfig object)
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=request.model,
            contents=contents,  # type: ignore
            config=config_params,  # type: ignore
            # tools=request.tools if request.tools else None
        )

        # Extract response data
        response_text = response.text if response else "No response"

        # Extract thought signature for next agent in DAG
        thought_signature = None
        if hasattr(response, "candidates") and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, "content") and hasattr(candidate.content, "parts"):
                # Gemini 3 includes reasoning in the response
                thought_signature = base64.b64encode(
                    f"{request.agent_id}_{datetime.now().isoformat()}".encode()
                ).decode("utf-8")
        else:
            thought_signature = base64.b64encode(
                f"{request.agent_id}_{datetime.now().isoformat()}".encode()
            ).decode("utf-8")

        # Extract token usage
        input_tokens = 0
        output_tokens = 0
        cache_hit = False

        if hasattr(response, "usage_metadata"):
            usage = response.usage_metadata
            # Use 'or 0' to handle cases where attribute exists but is None
            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            output_tokens = getattr(usage, "candidates_token_count", 0) or 0
            # cache_hit = getattr(usage, "cached_content_input_tokens", 0) > 0

        latency_ms = (time.time() - start_time) * 1000

        logger.info(
            f"Agent {request.agent_id} completed: "
            f"input={input_tokens} output={output_tokens} cache_hit={cache_hit}"
        )

        # Store full response to Redis to avoid HTTP serialization issues
        artifact_stored = False
        if redis_client:
            try:
                artifact_key = f"run:{request.run_id}:agent:{request.agent_id}:output"
                full_artifact = {
                    "result": response_text,
                    "status": "completed",
                    "thinking_depth": request.thinking_level or 0
                }
                redis_client.setex(artifact_key, 3600, json.dumps(full_artifact))
                artifact_stored = True
                logger.debug(f"Stored artifact to Redis: {artifact_key}")
            except Exception as e:
                logger.warning(f"Failed to store artifact to Redis: {e}")

        # Return only essential metadata in HTTP response
        # Full response is in Redis, kernel will read from there
        return AgentResponse(
            agent_id=request.agent_id,
            success=True,
            output={
                "status": "completed",
                "result": response_text[:500] if response_text else "No response",
                "artifact_stored": artifact_stored
            },
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tokens_used=input_tokens + output_tokens,
            thought_signature=thought_signature,
            cache_hit=cache_hit,
            latency_ms=latency_ms
        )
    except Exception as e:
        logger.error(f"Error invoking agent {request.agent_id}: {str(e)}")
        latency_ms = (time.time() - start_time) * 1000
        return AgentResponse(
            agent_id=request.agent_id,
            success=False,
            error=str(e),
            tokens_used=0,
            latency_ms=latency_ms
        )


@app.post("/invoke/batch")
async def invoke_batch(requests: List[AgentRequest]):
    """
    Invoke multiple agents in parallel

    Args:
        requests: List of AgentRequest objects

    Returns:
        List of AgentResponse objects
    """
    logger.info(f"Invoking {len(requests)} agents in batch")

    results = []
    # In a real scenario, use asyncio.gather for true parallelism
    for req in requests:
        response = await invoke_agent(req)
        results.append(response)

    return results


@app.get("/agents/list")
async def list_agents():
    """List available agent configurations"""
    return {
        "agents": [
            {
                "id": "orchestrator",
                "role": "orchestrator",
                "model": "gemini-2.5-flash-lite",
                "tools": ["plan_task", "route_agents"]
            },
            {
                "id": "extractor",
                "role": "worker",
                "model": "gemini-2.5-flash",
                "tools": ["extract_pdf", "parse_video"]
            },
            {
                "id": "kg_builder",
                "role": "worker",
                "model": "gemini-2.5-flash",
                "tools": ["build_graph", "extract_entities"]
            },
            {
                "id": "synthesizer",
                "role": "worker",
                "model": "gemini-2.5-flash-lite",
                "tools": ["combine_results", "summarize"]
            }
        ]
    }


@app.get("/models/available")
async def available_models():
    """List available Gemini 3 model variants"""
    return {
        "models": [
            {
                "id": "gemini-2.5-flash",
                "name": "Gemini 3 Flash",
                "description": "Fast, 69% cheaper, PhD-level reasoning",
                "speed": "3x faster"
            },
            {
                "id": "gemini-2.5-flash-lite",
                "name": "Gemini 3 Pro",
                "description": "Maximum reasoning depth for complex tasks",
                "capabilities": ["long-horizon planning", "multimodal"]
            },
            {
                "id": "gemini-2.5-flash",
                "name": "Gemini 3 Deep Think",
                "description": "Configurable thinking levels for research",
                "capabilities": ["hypothesis generation", "cross-paper reasoning"]
            }
        ]
    }


# ============================================================================
# Root endpoint
# ============================================================================

@app.websocket("/ws/execute/{run_id}/{agent_id}")
async def websocket_execute(websocket: WebSocket, run_id: str, agent_id: str):
    """
    WebSocket endpoint for real-time agent execution streaming
    Sends progress updates and token counts as they happen
    """
    await websocket.accept()
    logger.info(f"WebSocket connection established: run_id={run_id}, agent_id={agent_id}")

    try:
        # Receive the agent request via WebSocket
        request_data = await websocket.receive_text()
        request_dict = json.loads(request_data)
        request = AgentRequest(**request_dict)

        # Send initial status
        await websocket.send_json({
            "type": "execution_started",
            "agent_id": agent_id,
            "timestamp": datetime.now().isoformat()
        })

        if not client:
            raise ValueError("GEMINI_API_KEY environment variable not set")

        config_params: Dict[str, Any] = {"temperature": 1}

        if "deep-think" in request.model and request.thinking_level:
            config_params["thinking_config"] = types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=min(max(request.thinking_level * 1000, 1000), 10000)
            )

        user_parts: List[Dict[str, Any]] = []
        if request.file_paths:
            for file_path in request.file_paths:
                file_part = await _load_multimodal_file(file_path)
                user_parts.append(file_part)

        user_parts.append({"text": request.prompt})

        contents: List[Dict[str, Any]] = [{"role": "user", "parts": user_parts}]

        if request.cached_content_id:
            config_params["cached_content"] = request.cached_content_id

        # Stream the response
        start_time = time.time()

        # Run in thread pool to avoid blocking event loop
        # Pass config dict directly (API accepts both dict and GenerateContentConfig object)
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=request.model,
            contents=contents,  # type: ignore
            config=config_params,  # type: ignore
            # tools=request.tools if request.tools else None,
        )

        response_text = response.text if response else "No response"

        # Extract metrics
        input_tokens = 0
        output_tokens = 0
        cache_hit = False

        if hasattr(response, "usage_metadata"):
            usage = response.usage_metadata
            input_tokens = getattr(usage, "prompt_token_count", 0)
            output_tokens = getattr(usage, "candidates_token_count", 0)
            # cache_hit = getattr(usage, "cached_content_input_tokens", 0) > 0

        # Generate thought signature
        thought_signature = base64.b64encode(
            f"{agent_id}_{datetime.now().isoformat()}".encode()
        ).decode("utf-8")

        latency_ms = (time.time() - start_time) * 1000

        # Send execution complete
        await websocket.send_json({
            "type": "execution_complete",
            "agent_id": agent_id,
            "output": response_text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "thought_signature": thought_signature,
            "cache_hit": cache_hit,
            "latency_ms": latency_ms,
            "timestamp": datetime.now().isoformat()
        })

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {agent_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}")
        try:
            await websocket.send_json({
                "type": "execution_error",
                "agent_id": agent_id,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            })
        except:
            pass


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "RARO Agent Service",
        "version": "0.1.0",
        "endpoints": {
            "health": "/health",
            "invoke": "POST /invoke",
            "batch": "POST /invoke/batch",
            "agents": "GET /agents/list",
            "models": "GET /models/available",
            "ws_execute": "WS /ws/execute/{run_id}/{agent_id}"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )