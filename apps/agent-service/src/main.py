# [[RARO]]/apps/agent-service/src/main.py
# Purpose: Main entry point for the Agent Service
# Architecture: Application Layer
# Dependencies: FastAPI, Core Logic

import json
import time
import asyncio
from typing import Dict, Any, List
from datetime import datetime
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from core.config import settings, gemini_client, redis_client, logger
from core.llm import call_gemini_with_context
from core.parsers import parse_delegation_request
from domain.protocol import AgentRequest, AgentResponse, WorkflowManifest, PatternDefinition, DelegationRequest
from intelligence.architect import ArchitectEngine
from intelligence.prompts import inject_delegation_capability

app = FastAPI(title="RARO Agent Service", version="0.4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Architect Engine
architect = ArchitectEngine(gemini_client) if gemini_client else None

# ============================================================================
# Custom Exception Handlers (Requested Validation Middleware)
# ============================================================================

@app.exception_handler(ValidationError)
async def pydantic_validation_exception_handler(request: Request, exc: ValidationError):
    """
    Catch-all for Pydantic validation errors, transforming them into 
    422 Unprocessable Entity responses for the client.
    """
    logger.error(f"Validation Error at {request.url.path}: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={
            "detail": exc.errors(),
            "message": "The system generated a plan or response that does not match the required schema.",
            "type": "SchemaMismatch"
        },
    )

# ============================================================================
# HTTP Endpoints (Synchronous/Request-Response)
# ============================================================================

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "llm_connected": gemini_client is not None,
        "redis_connected": redis_client is not None
    }

@app.post("/plan", response_model=WorkflowManifest)
async def create_plan(payload: Dict[str, str]):
    """
    Flow A: Orchestration Planning.
    Returns a DAG manifest based on user query.
    """
    if not architect:
        raise HTTPException(503, "Architect Engine unavailable")
    
    query = payload.get("text", "")
    if not query:
        raise HTTPException(400, "Query text is required")

    # The exception handler above will catch Pydantic errors from generate_plan
    # and return a 422 if the LLM output is structurally unsound.
    manifest = await architect.generate_plan(query)
    return manifest

@app.post("/compile-pattern", response_model=PatternDefinition)
async def compile_pattern(payload: dict):
    """Flow C: Safety"""
    if not architect: raise HTTPException(503, "LLM unavailable")
    return await architect.compile_pattern(payload.get("text", ""))

@app.post("/invoke", response_model=AgentResponse)
async def invoke_agent(request: AgentRequest):
    """Flow B: Execution (HTTP)"""
    return await _execute_agent_logic(request)

@app.post("/invoke/batch")
async def invoke_batch(requests: List[AgentRequest]):
    """
    Invoke multiple agents in parallel for improved performance.
    """
    logger.info(f"Batch invoke: {len(requests)} agents")

    results = []
    for req in requests:
        response = await invoke_agent(req)
        results.append(response)

    return results

@app.get("/agents/list")
async def list_agents():
    return {
        "agents": [
            {
                "id": "orchestrator",
                "role": "orchestrator",
                "model": settings.MODEL_REASONING, 
                "description": "Main coordinator - breaks down tasks and routes to specialists",
                "tools": ["plan_task", "route_agents", "synthesize_results"]
            },
            {
                "id": "researcher",
                "role": "worker",
                "model": settings.MODEL_FAST,
                "description": "Deep research and fact-finding",
                "tools": ["search_papers", "extract_citations"]
            },
            {
                "id": "extractor",
                "role": "worker",
                "model": settings.MODEL_FAST,
                "description": "Multimodal content extraction from PDFs and videos",
                "tools": ["extract_pdf", "parse_video", "extract_images"]
            },
            {
                "id": "analyst",
                "role": "worker",
                "model": settings.MODEL_FAST,
                "description": "Critical analysis and reasoning",
                "tools": ["analyze_data", "compare_sources", "validate_claims"]
            },
            {
                "id": "synthesizer",
                "role": "worker",
                "model": settings.MODEL_FAST,
                "description": "Combines results from multiple agents into coherent output",
                "tools": ["combine_results", "summarize", "format_report"]
            },
            {
                "id": "code_interpreter",
                "role": "worker",
                "model": settings.MODEL_FAST,
                "description": "Executes Python code for data analysis",
                "tools": ["execute_python", "plot_data", "run_analysis"]
            }
        ]
    }

@app.get("/models/available")
async def available_models():
    """
    Dynamically lists available Gemini models based on configuration.
    """
    # Start with the authoritative models from settings
    models = [
        {
            "id": settings.MODEL_FAST,
            "name": "Gemini 3 Flash",
            "description": "Fast, 69% cheaper, PhD-level reasoning",
            "speed": "3x faster than Pro",
            "use_cases": ["quick analysis", "extraction", "classification"],
            "cost_per_1m_tokens": 0.075
        },
        {
            "id": settings.MODEL_REASONING,
            "name": "Gemini 3 Pro",
            "description": "Maximum reasoning depth for complex tasks",
            "capabilities": ["long-horizon planning", "multimodal reasoning", "deep analysis"],
            "use_cases": ["research synthesis", "complex planning", "critical analysis"],
            "cost_per_1m_tokens": 0.30
        },
        {
            "id": settings.MODEL_THINKING,
            "name": "Gemini 3 Deep Think",
            "description": "Configurable thinking levels for research-intensive tasks",
            "capabilities": ["hypothesis generation", "cross-paper reasoning", "extended thinking"],
            "thinking_levels": "1-10 (maps to 1k-10k token budget)",
            "use_cases": ["PhD-level research", "hypothesis testing", "complex synthesis"],
            "cost_per_1m_tokens": 0.30
        }
    ]

    # Add the custom model if it's defined in the settings (from environment variables)
    if settings.MODEL_CUSTOM:
        models.append({
            "id": settings.MODEL_CUSTOM,
            "name": "Custom Model", # Generic name, as we don't know specifics
            "description": "User-defined model from configuration (MODEL_CUSTOM environment variable)",
            "use_cases": ["custom model integration"],
            "cost_per_1m_tokens": None # Cost is unknown for custom models
        })

    return {"models": models}




# ============================================================================
# WebSocket Endpoint (Streaming/Real-time)
# ============================================================================

@app.websocket("/ws/execute/{run_id}/{agent_id}")
async def websocket_execute(websocket: WebSocket, run_id: str, agent_id: str):
    await websocket.accept()
    logger.info(f"WS Connected: {agent_id}")

    try:
        # 1. Receive Request
        data = await websocket.receive_text()
        request_dict = json.loads(data)
        request = AgentRequest(**request_dict)

        # 2. Send Start Signal
        await websocket.send_json({
            "type": "execution_started",
            "agent_id": agent_id,
            "timestamp": datetime.now().isoformat()
        })

        # 3. Execute Logic
        response = await _execute_agent_logic(request)

        # 4. Send Completion
        await websocket.send_json({
            "type": "execution_complete",
            "agent_id": agent_id,
            "output": response.output.get("result") if response.output else "",
            "delegation": response.delegation.model_dump() if response.delegation else None,
            "tokens_used": response.tokens_used,
            "timestamp": datetime.now().isoformat()
        })

    except WebSocketDisconnect:
        logger.info(f"WS Disconnected: {agent_id}")
    except Exception as e:
        logger.error(f"WS Error: {e}")
        await websocket.send_json({
            "type": "execution_error",
            "error": str(e)
        })
        await websocket.close()

# ============================================================================
# Core Logic Helper (Shared by HTTP and WS)
# ============================================================================

async def _execute_agent_logic(request: AgentRequest) -> AgentResponse:
    """
    Core agent execution logic shared by HTTP and WebSocket endpoints.
    Uses regex scanning to identify distinct 'json:delegation' blocks
    separately from standard content blocks.
    """
    start_time = time.time()

    if not gemini_client:
        raise ValueError("Gemini Client unavailable - check GEMINI_API_KEY")

    try:
        # 1. Prompt Enhancement (Flow B Support)
        # For non-deep-think models, inject delegation capability
        final_prompt = request.prompt
        if "deep-think" not in request.model:
            final_prompt = inject_delegation_capability(request.prompt)
            logger.debug(f"Delegation capability injected for agent {request.agent_id}")

        # 2. Call Unified LLM Module
        result = await call_gemini_with_context(
            model=request.model,
            prompt=final_prompt,
            user_directive=request.user_directive,  # Runtime task from operator
            input_data=request.input_data,
            file_paths=request.file_paths,
            parent_signature=request.parent_signature,
            thinking_level=request.thinking_level,
            tools=request.tools,
            agent_id=request.agent_id,
            run_id=request.run_id
        )

        response_text = result["text"]

        # --- FIX START: Extract files from LLM result ---
        files_generated = result.get("files_generated", [])
        logger.debug(f"Agent {request.agent_id} generated {len(files_generated)} file(s): {files_generated}")
        # --- FIX END ---

        # 3. Parse Delegation Request (Flow B)
        # Use centralized parser from core.parsers module
        delegation_request = None

        delegation_data = parse_delegation_request(response_text)

        if delegation_data:
            try:
                # Validate against schema
                delegation_request = DelegationRequest(**delegation_data)

                logger.info(
                    f"Delegation signal received via explicit tag: {len(delegation_request.new_nodes)} nodes. "
                    f"Reason: {delegation_request.reason[:50]}..."
                )
            except Exception as e:
                logger.warning(f"Failed to parse delegation request model: {e}")
        else:
            logger.debug("No explicit delegation tag found in response.")

        # 4. Store Artifact to Redis (if available)
        artifact_stored = False
        # Update condition: Store if we have text OR generated files
        if redis_client and (response_text or files_generated):
            try:
                key = f"run:{request.run_id}:agent:{request.agent_id}:output"
                artifact_data = {
                    # [[UPDATED]] Removed payload chunk to clean up frontend log stream.
                    # This prevents the raw prompt/response text from being re-rendered by the ArtifactCard,
                    # while preserving file metadata for downstream consumption.
                    # "result": response_text, 
                    "status": "completed",
                    "thinking_depth": request.thinking_level or 0,
                    "model": request.model,
                    # --- FIX START: Inject file metadata ---
                    "files_generated": files_generated,
                    "artifact_stored": len(files_generated) > 0
                    # --- FIX END ---
                }
                redis_client.setex(key, 3600, json.dumps(artifact_data))
                artifact_stored = True
                logger.debug(f"Artifact stored to Redis: {key} (files: {len(files_generated)})")
            except Exception as e:
                logger.warning(f"Redis write failed for {request.agent_id}: {e}")

        # 5. Calculate Latency
        latency_ms = (time.time() - start_time) * 1000

        # 6. Build Response
        return AgentResponse(
            agent_id=request.agent_id,
            success=True,
            output={
                "result": response_text,
                "artifact_stored": artifact_stored
            },
            delegation=delegation_request,
            input_tokens=result["input_tokens"],
            output_tokens=result["output_tokens"],
            tokens_used=result["input_tokens"] + result["output_tokens"],
            thought_signature=result["thought_signature"],
            cache_hit=result["cache_hit"],
            latency_ms=latency_ms
        )

    except Exception as e:
        logger.error(f"Execution failed for agent {request.agent_id}: {str(e)}", exc_info=True)
        latency_ms = (time.time() - start_time) * 1000

        return AgentResponse(
            agent_id=request.agent_id,
            success=False,
            error=str(e),
            tokens_used=0,
            latency_ms=latency_ms
        )

@app.get("/")
async def root():
    """Root endpoint with API documentation links"""
    return {
        "service": "RARO Agent Service",
        "version": "0.3.0",
        "features": ["multimodal", "dynamic-dag", "safety-compiler", "rfs-workspace"],
        "parsing_strategy": "explicit-tag (json:delegation)"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)