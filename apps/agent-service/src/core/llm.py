# [[RARO]]/apps/agent-service/src/core/llm.py
# Purpose: LLM Wrapper with Multimodal, Parent Signature, Tool Handling & Streaming
# Architecture: Core Layer
# Dependencies: google.genai, pathlib, base64

from typing import Dict, Any, List, Optional, AsyncIterator, Union
import base64
import mimetypes
import json
import asyncio
import httpx
from pathlib import Path
from datetime import datetime
from google.genai import types
from core.config import gemini_client, logger, resolve_model, settings, redis_client
from intelligence.prompts import render_runtime_system_instruction

# Import Tooling Logic
try:
    from intelligence.tools import execute_tool_call
except ImportError:
    logger.warning("intelligence.tools not found, tool execution will be disabled")
    # FIX: Robust fallback signature that accepts keyword arguments
    execute_tool_call = lambda tool_name, args, run_id="default": {"error": "Tool execution unavailable"}

# Import Parser for Manual Tool Parsing
try:
    from core.parsers import parse_function_calls
except ImportError:
    logger.warning("core.parsers not found, manual function parsing will be disabled")
    parse_function_calls = lambda x: []

# ============================================================================
# Multimodal File Loading
# ============================================================================

async def load_multimodal_file(file_path: str) -> Dict[str, Any]:
    """
    Load multimodal file (PDF, video, image) for Gemini 3 consumption.

    Args:
        file_path: Path to the file to load

    Returns:
        Dict with inline_data structure for Gemini API

    Raises:
        FileNotFoundError: If file doesn't exist
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    mime_type, _ = mimetypes.guess_type(file_path)

    logger.debug(f"Loading multimodal file: {file_path} (type: {mime_type})")

    # Read file data once
    with open(file_path, "rb") as f:
        file_data = base64.standard_b64encode(f.read()).decode("utf-8")

    # Map to Gemini types
    final_mime = mime_type or "application/octet-stream"
    
    # Specific handling if needed, otherwise generic inline_data
    return {
        "inline_data": {
            "mime_type": final_mime,
            "data": file_data
        }
    }

# ============================================================================
# Live Telemetry Emission (Redis Pub/Sub)
# ============================================================================

def emit_telemetry(
    run_id: str,
    agent_id: str,
    category: str,
    message: str,
    meta: str,
    extra: dict = {}
):
    """
    Publish live log event to Redis for Kernel to forward to UI.

    Args:
        run_id: Current workflow run ID
        agent_id: Agent generating this log
        category: TOOL_CALL | TOOL_RESULT | THOUGHT
        message: Human-readable message
        meta: Short tag (IO_REQ, IO_OK, IO_ERR, PLANNING, etc.)
        extra: Additional structured data (tool_name, duration_ms, etc.)
    """
    if not redis_client:
        return  # Graceful degradation if Redis unavailable

    try:
        payload = {
            "run_id": run_id,
            "agent_id": agent_id,
            "category": category,
            "message": message,
            "metadata": meta,
            "timestamp": datetime.now().isoformat(),
            **extra
        }

        # Fire-and-forget publish (non-blocking)
        redis_client.publish("raro:live_logs", json.dumps(payload))

    except Exception as e:
        logger.warning(f"Telemetry emit failed: {e}")
        # Don't crash execution if telemetry fails

# ============================================================================
# Private Helper: Request Preparation
# ============================================================================

async def _prepare_gemini_request(
    model: str,
    prompt: str,
    agent_id: str,
    user_directive: str = "",
    input_data: Optional[Dict[str, Any]] = None,
    file_paths: Optional[List[str]] = None,
    parent_signature: Optional[str] = None,
    thinking_level: Optional[int] = None,
    tools: Optional[List[str]] = None,
    # [[NEW PARAMETERS]]
    allow_delegation: bool = False,
    graph_view: str = "",
) -> Dict[str, Any]:
    """
    Internal helper to build contents, config.
    NOTE: In Manual Parsing Mode, we do NOT pass 'tools' object to the API.
    We inject tool info into the system_instruction instead.

    Prompt Architecture (3-Layer):
        1. Base RARO Rules (from render_runtime_system_instruction)
        2. Node Persona (the 'prompt' field - "You are a Python specialist...")
        3. User Directive (the runtime task - "Analyze this CSV...")
    """

    # 1. Generate Base System Instruction
    base_instruction = render_runtime_system_instruction(agent_id, tools)

    # 2. Conditionally Inject Delegation Capability
    if allow_delegation:
        from intelligence.prompts import inject_delegation_capability
        base_instruction = inject_delegation_capability(base_instruction)
        logger.debug(f"Delegation capability granted to {agent_id}")

    # 3. Add Graph Awareness
    graph_context = f"\n\n[OPERATIONAL AWARENESS]\n{graph_view}\n" if graph_view else ""

    # 4. Combine: Base + Graph + Persona
    system_instruction = f"{base_instruction}{graph_context}\n\n[YOUR SPECIALTY]\n{prompt}"

    # 2. Build Generation Config
    config_params: Dict[str, Any] = {
        "temperature": 1.0,
        "system_instruction": system_instruction,
        # --- FIX: Explicitly disable native tools to prevent UNEXPECTED_TOOL_CALL ---
        # This forces the model to use the text-based ```json:function``` format
        # defined in our system prompts instead of attempting native function calls
        # "tool_config": {"function_calling_config": {"mode": "NONE"}}
    }

    # Add Deep Think configuration
    if "deep-think" in model and thinking_level:
        thinking_budget = min(max(thinking_level * 1000, 1000), 10000)
        config_params["thinking_config"] = types.ThinkingConfig(
            include_thoughts=True,
            thinking_budget=thinking_budget
        )
        logger.debug(f"Deep Think enabled: budget={thinking_budget}")

    # NOTE: We intentionally DO NOT set config_params["tools"] here.
    # The agent is instructed to use ```json:function``` text blocks.

    # 4. Build Conversation Contents
    contents: List[Dict[str, Any]] = []

    # Add parent signature logic
    if parent_signature:
        contents.append({
            "role": "user",
            "parts": [{"text": f"[CONTEXT CONTINUITY]\nPrevious Agent Signature: {parent_signature}"}]
        })
        contents.append({
            "role": "model",
            "parts": [{"text": "Context accepted. Maintaining reasoning chain."}]
        })

    # Build User Message
    user_parts: List[Dict[str, Any]] = []

    # 3. User Directive (if provided) - This is the runtime task
    if user_directive:
        user_parts.append({
            "text": f"[OPERATOR DIRECTIVE]\n{user_directive}\n\n"
        })

    # Multimodal files
    if file_paths:
        for file_path in file_paths:
            try:
                file_part = await load_multimodal_file(file_path)
                user_parts.append(file_part)
            except Exception as e:
                logger.error(f"Failed to load file {file_path}: {e}")
                user_parts.append({"text": f"[ERROR: Failed to load {file_path}]"})

    # Context Data (from parent nodes)
    if input_data:
        context_str = json.dumps(input_data, indent=2)
        user_parts.append({
            "text": f"[CONTEXT DATA]\n{context_str}\n\n"
        })

    # === PREVENT EMPTY REQUEST ===
    # Gemini throws 400 if 'parts' is empty. If we have no directive, file, or context,
    # we must provide a generic trigger to hand over control to the model.
    if not user_parts:
        user_parts.append({
            "text": "[SYSTEM] Ready. Execute based on system instructions."
        })

    contents.append({
        "role": "user",
        "parts": user_parts
    })

    return {
        "model": model,
        "contents": contents,
        "config": config_params
    }


async def probe_sink(
    params: Dict[str, Any],
    run_id: str,
    agent_id: str,
    tools: Optional[List[str]],
    model: str,
    prompt: str
) -> Optional[Dict[str, Any]]:
    """
    Intercepts the Agent execution if DEBUG_PROBE_URL is set.
    """
    # 1. Check if Probing is active
    if not settings.DEBUG_PROBE_URL:
        return None

    # logger.warning(...) <-- Optional: Comment this out to reduce log noise

    # 2. Extract Data for Visualization
    system_instruction = params["config"].get("system_instruction", "")
    
    # Extract user message parts safely
    user_parts = params["contents"][-1]["parts"] if params["contents"] else []
    formatted_user_msg = "\n".join([
        p.get("text", "[Binary Data/File]") for p in user_parts
        if "text" in p
    ])

    # 3. Fire-and-forget log payload to the Probe
    try:
        async with httpx.AsyncClient() as client:
            # We use a short timeout so we don't slow down the actual runtime significantly
            await client.post(
                f"{settings.DEBUG_PROBE_URL}/capture",
                json={
                    "id": run_id,
                    "time": datetime.now().isoformat(),
                    "agent_id": agent_id,
                    "run_id": run_id,
                    "tools": tools or [],
                    "final_system_prompt": system_instruction,
                    "final_user_message": formatted_user_msg,
                    "original_payload": {"model": model, "prompt": prompt}
                },
                timeout=0.5 
            )
    except Exception as e:
        logger.error(f"Failed to send debug capture: {e}")

    # 4. MODIFICATION: Return None to allow actual execution to proceed
    # Returning None tells 'call_gemini_with_context' to continue to the LLM
    return None


# ============================================================================
# Unified Gemini API Caller (Sync/Batch)
# ============================================================================
async def call_gemini_with_context(
    model: str,
    prompt: str,
    user_directive: str = "",
    input_data: Optional[Dict[str, Any]] = None,
    file_paths: Optional[List[str]] = None,
    parent_signature: Optional[str] = None,
    thinking_level: Optional[int] = None,
    tools: Optional[List[str]] = None,
    agent_id: Optional[str] = None,
    run_id: str = "default_run",
    # [[NEW PARAMETERS]]
    allow_delegation: bool = False,
    graph_view: str = "",
) -> Dict[str, Any]:
    """
    Execute Gemini interaction with full features: Multimodal, Context, and Tools.
    Handles the 'Tool Loop' (Model -> Function Call -> Execute -> Function Response -> Model).
    """
    if not gemini_client:
        raise ValueError("GEMINI_API_KEY not set")
    concrete_model = resolve_model(model)
    logger.debug(f"Resolved model alias '{model}' to '{concrete_model}'")

    safe_agent_id = agent_id or "unknown_agent"

    logger.info(
        f"\n{'#'*70}\n"
        f"AGENT INVOCATION: {safe_agent_id} (Manual Tooling)\n"
        f"Model: {concrete_model} | Run ID: {run_id}\n"
        f"Tools: {tools}\n"
        f"User Directive: {'Yes' if user_directive else 'No'}\n"
        f"{'#'*70}"
    )

    try:
        params = await _prepare_gemini_request(
            concrete_model, prompt, safe_agent_id, user_directive, input_data, file_paths,
            parent_signature, thinking_level, tools,
            # Pass new parameters
            allow_delegation=allow_delegation,
            graph_view=graph_view
        )

        # =========================================================
        # INTERCEPTION LAYER (Abstracted)
        # =========================================================
        probe_response = await probe_sink(
            params=params,
            run_id=run_id,
            agent_id=safe_agent_id,
            tools=tools,
            model=model,
            prompt=prompt
        )
        
        # If probe_sink returns a dictionary, it means we are in debug mode
        # and should return immediately, skipping the LLM call.
        if probe_response:
            return probe_response
        # =========================================================

        current_contents = params["contents"]
        max_turns = 10
        turn_count = 0
        final_response_text = ""

        # --- FIX START: Initialize variables before the loop ---
        response = None
        content_text = ""
        all_files_generated = []  # Accumulator for files from all tool calls
        # --- FIX END ---

        logger.debug(f"Agent {safe_agent_id}: Starting manual tool loop")

        while turn_count < max_turns:
            turn_count += 1

            # 1. Call LLM
            response = await asyncio.to_thread(
                gemini_client.models.generate_content,
                model=params["model"],
                contents=current_contents,
                config=params["config"]
            )

            logger.debug(f"Full Gemini Response: {response.model_dump_json(indent=2) if response else 'None'}")

            if not response.candidates:
                logger.error(f"Agent {agent_id}: API returned no candidates.")
                break

            # 2. Extract Text
            content_text = response.text or ""

            # Append model's response to history
            current_contents.append({
                "role": "model",
                "parts": [{"text": content_text}]
            })

            # 3. Parse for Manual Function Calls
            function_calls = parse_function_calls(content_text)

            if not function_calls:
                # No tools called, this is the final answer
                final_response_text = content_text
                break

            # 4. Process Tool Calls
            tool_outputs_text = ""

            for tool_name, tool_args in function_calls:
                # === EMIT: TOOL CALL START ===
                args_str = json.dumps(tool_args)
                emit_telemetry(
                    run_id=run_id,
                    agent_id=safe_agent_id,
                    category="TOOL_CALL",
                    message=f"{tool_name}({args_str[:50]}...)",  # Truncate for clean UI
                    meta="IO_REQ",
                    extra={"tool_name": tool_name, "args": tool_args}
                )

                logger.info(
                    f"[TOOL DETECTED] Agent: {agent_id} | Tool: {tool_name} | Args: {str(tool_args)[:100]}..."
                )

                # Measure execution time
                start_time = datetime.now()

                # Execute
                result_dict = execute_tool_call(tool_name, tool_args, run_id=run_id)

                duration_ms = (datetime.now() - start_time).total_seconds() * 1000

                # --- FIX START: Capture generated files ---
                if isinstance(result_dict, dict) and "files_generated" in result_dict:
                    files = result_dict["files_generated"]
                    if isinstance(files, list):
                        all_files_generated.extend(files)
                        logger.debug(f"Captured {len(files)} file(s) from {tool_name}: {files}")
                # --- FIX END ---

                # === EMIT: TOOL RESULT ===
                success = result_dict.get('success', True)

                # Generate smart summary
                output_summary = "Operation complete."
                if "result" in result_dict:
                    res = str(result_dict["result"])
                    output_summary = res[:100] + "..." if len(res) > 100 else res
                elif "error" in result_dict:
                    output_summary = result_dict["error"]

                emit_telemetry(
                    run_id=run_id,
                    agent_id=safe_agent_id,
                    category="TOOL_RESULT",
                    message=output_summary,
                    meta="IO_OK" if success else "IO_ERR",
                    extra={
                        "tool_name": tool_name,
                        "duration_ms": int(duration_ms)
                    }
                )

                logger.info(
                    f"[TOOL RESULT] Agent: {agent_id} | Status: {'✓' if success else '✗'}"
                )

                # Format Output for the Model
                tool_outputs_text += f"\n[SYSTEM: Tool '{tool_name}' Result]\n{json.dumps(result_dict, indent=2)}\n"

            # 5. Append Tool Outputs to History
            current_contents.append({
                "role": "user",
                "parts": [{"text": tool_outputs_text}]
            })

            logger.debug(f"Agent {agent_id}: Turning over with tool results...")

        # --- Finalization ---

        # Metadata extraction
        input_tokens = 0
        output_tokens = 0
        cache_hit = False

        if response and hasattr(response, "usage_metadata"):
            usage = response.usage_metadata
            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            output_tokens = getattr(usage, "candidates_token_count", 0) or 0
            cached_tokens = getattr(usage, "cached_content_token_count", 0) or 0
            cache_hit = cached_tokens > 0

        signature_data = f"{agent_id or 'unknown'}_{datetime.now().isoformat()}"
        thought_signature = base64.b64encode(signature_data.encode()).decode("utf-8")

        # Fallback if loop exhausted
        if not final_response_text:
            final_response_text = content_text

        logger.info(f"Agent {safe_agent_id} Completed. Tokens: {input_tokens}/{output_tokens} | Files: {len(all_files_generated)}")

        return {
            "text": final_response_text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "thought_signature": thought_signature,
            "cache_hit": cache_hit,
            "files_generated": all_files_generated  # Return captured files
        }

    except Exception as e:
        logger.error(
            f"\n{'!'*70}\n"
            f"AGENT FAILED: {safe_agent_id}\n"
            f"Error: {str(e)}\n"
            f"{'!'*70}\n",
            exc_info=True
        )
        raise

# ============================================================================
# Streaming Support
# ============================================================================

async def stream_gemini_response(
    model: str,
    prompt: str,
    user_directive: str = "",
    input_data: Optional[Dict[str, Any]] = None,
    file_paths: Optional[List[str]] = None,
    parent_signature: Optional[str] = None,
    thinking_level: Optional[int] = None,
    tools: Optional[List[str]] = None,
    agent_id: Optional[str] = None,
    allow_delegation: bool = False,
    graph_view: str = "",
    **kwargs
) -> AsyncIterator[str]:
    """
    Stream tokens from Gemini API in real-time.
    Supports simple tool execution flow within the stream.
    """
    if not gemini_client:
        raise ValueError("GEMINI_API_KEY not set")
    concrete_model = resolve_model(model)

    # Establish identity for System Instruction generation
    safe_agent_id = agent_id or "unknown_stream_agent"

    # Prepare context with the new System Instruction injection logic
    params = await _prepare_gemini_request(
        concrete_model, prompt, safe_agent_id, user_directive, input_data, file_paths,
        parent_signature, thinking_level, tools,
        # Pass new parameters
        allow_delegation=allow_delegation,
        graph_view=graph_view
    )
    
    current_contents = params["contents"]
    
    # Use the Async client for streaming to avoid blocking the event loop
    async_models = gemini_client.aio.models

    # Initiate the stream request
    stream = await async_models.generate_content_stream(
        model=params["model"],
        contents=current_contents,
        config=params["config"]
    )

    # Accumulate chunks to check for tool calls
    full_response_content = []
    
    async for chunk in stream:
        # Detect function calls in the stream candidates
        if (chunk.candidates and 
            chunk.candidates[0].content and 
            chunk.candidates[0].content.parts):
            
            part = chunk.candidates[0].content.parts[0]
            if part.function_call:
                # If a tool call is detected, we accumulate but do not yield text.
                # Note: Full tool loop recursion is not yet implemented in streaming mode;
                # this block currently serves to capture the intent.
                full_response_content.append(chunk.candidates[0].content)
                continue

        # Yield text content as it arrives
        if chunk.text:
            yield chunk.text

# ============================================================================
# Batch Processing Helper
# ============================================================================

async def call_gemini_batch(
    requests: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Process multiple Gemini API calls in parallel.
    """
    tasks = [
        call_gemini_with_context(**req)
        for req in requests
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    processed_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"Batch request {i} failed: {result}")
            processed_results.append({
                "text": "",
                "input_tokens": 0,
                "output_tokens": 0,
                "thought_signature": None,
                "cache_hit": False,
                "error": str(result)
            })
        else:
            processed_results.append(result)

    return processed_results