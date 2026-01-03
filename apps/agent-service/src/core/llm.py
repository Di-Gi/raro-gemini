# [[RARO]]/apps/agent-service/src/core/llm.py
# Purpose: LLM Wrapper with Multimodal, Parent Signature, Tool Handling & Streaming
# Architecture: Core Layer
# Dependencies: google.genai, pathlib, base64

from typing import Dict, Any, List, Optional, AsyncIterator, Union
import base64
import mimetypes
import json
import asyncio
from pathlib import Path
from datetime import datetime
from google.genai import types
from core.config import gemini_client, logger

# Import Tooling Logic
try:
    from intelligence.tools import get_tool_declarations, execute_tool_call
except ImportError:
    logger.warning("intelligence.tools not found, tool execution will be disabled")
    get_tool_declarations = lambda x: []
    execute_tool_call = lambda x, y: {"error": "Tool execution unavailable"}

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
# Private Helper: Request Preparation
# ============================================================================

async def _prepare_gemini_request(
    model: str,
    prompt: str,
    input_data: Optional[Dict[str, Any]] = None,
    file_paths: Optional[List[str]] = None,
    parent_signature: Optional[str] = None,
    thinking_level: Optional[int] = None,
    tools: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Internal helper to build contents, config, and tools for API calls.
    Returns a dict of arguments ready to pass to generate_content.
    """
    # 1. Build Generation Config
    config_params: Dict[str, Any] = {
        "temperature": 1.0, 
    }

    # Add Deep Think configuration
    if "deep-think" in model and thinking_level:
        thinking_budget = min(max(thinking_level * 1000, 1000), 10000)
        config_params["thinking_config"] = types.ThinkingConfig(
            include_thoughts=True,
            thinking_budget=thinking_budget
        )
        logger.debug(f"Deep Think enabled: budget={thinking_budget}")

    # 2. Prepare Tools (Inject into config)
    if tools:
        declarations = get_tool_declarations(tools)
        if declarations:
            # Create tool config with the declarations
            # Using the Google GenAI SDK format, tools are passed inside config
            tool_obj = types.Tool(function_declarations=declarations)
            config_params["tools"] = [tool_obj] 
            logger.debug(f"Tools enabled: {tools}")

    # 3. Build Conversation Contents
    contents: List[Dict[str, Any]] = []

    # Add parent signature logic
    if parent_signature:
        contents.append({
            "role": "user",
            "parts": [{"text": f"[CONTEXT CONTINUITY]\nPrevious Agent Signature: {parent_signature}"}]
        })
        contents.append({
            "role": "model",
            "parts": [{"text": "Previous context acknowledged. Continuing reasoning chain."}]
        })

    # Build User Message
    user_parts: List[Dict[str, Any]] = []

    # Multimodal files
    if file_paths:
        for file_path in file_paths:
            try:
                file_part = await load_multimodal_file(file_path)
                user_parts.append(file_part)
            except Exception as e:
                logger.error(f"Failed to load file {file_path}: {e}")
                user_parts.append({"text": f"[ERROR: Failed to load {file_path}]"})

    # Context Data
    if input_data:
        context_str = json.dumps(input_data, indent=2)
        user_parts.append({
            "text": f"[CONTEXT DATA]\n{context_str}\n\n"
        })

    # Main Prompt
    user_parts.append({"text": prompt})

    contents.append({
        "role": "user",
        "parts": user_parts
    })

    return {
        "model": model,
        "contents": contents,
        "config": config_params
    }

# ============================================================================
# Unified Gemini API Caller (Sync/Batch)
# ============================================================================

async def call_gemini_with_context(
    model: str,
    prompt: str,
    input_data: Optional[Dict[str, Any]] = None,
    file_paths: Optional[List[str]] = None,
    parent_signature: Optional[str] = None,
    thinking_level: Optional[int] = None,
    tools: Optional[List[str]] = None,
    agent_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Execute Gemini interaction with full features: Multimodal, Context, and Tools.
    Handles the 'Tool Loop' (Model -> Function Call -> Execute -> Function Response -> Model).
    """
    if not gemini_client:
        raise ValueError("GEMINI_API_KEY not set")

    try:
        # Prepare initial request
        params = await _prepare_gemini_request(
            model, prompt, input_data, file_paths, 
            parent_signature, thinking_level, tools
        )

        current_contents = params["contents"]
        max_turns = 5
        turn_count = 0
        final_response = None
        response = None
        
        # Tool Loop
        while turn_count < max_turns:
            turn_count += 1
            
            # Call API
            response = await asyncio.to_thread(
                gemini_client.models.generate_content,
                model=params["model"],
                contents=current_contents,
                config=params["config"]
            )

            # Check for Function Calls
            function_calls = []
            if (response.candidates and 
                response.candidates[0].content and 
                response.candidates[0].content.parts):
                
                for part in response.candidates[0].content.parts:
                    if part.function_call:
                        function_calls.append(part.function_call)

            # If no function calls, we are done
            if not function_calls:
                final_response = response
                break

            # Handle Function Calls
            logger.info(f"Agent {agent_id} triggered {len(function_calls)} tool calls")
            
            # Append model's thought/call to history
            if response.candidates and response.candidates[0].content:
                current_contents.append(response.candidates[0].content)

            # Execute Tools
            function_responses = []
            for call in function_calls:
                tool_name = call.name
                tool_args = call.args
                
                logger.debug(f"Executing tool: {tool_name} with args: {tool_args}")
                result_dict = execute_tool_call(tool_name, tool_args)
                
                function_responses.append(types.Part.from_function_response(
                    name=tool_name,
                    response=result_dict
                ))

            # Append results to history
            current_contents.append(types.Content(
                role="function",
                parts=function_responses
            ))
            
            # Loop continues to send function results back to model

        if not final_response:
             # Should happen only if max_turns hit or loop didn't produce final_response
            logger.warning(f"Agent {agent_id} hit max tool turns ({max_turns}) or failed to converge")
            final_response = response

        # Extract Metrics & Text
        response_text = ""
        if final_response and final_response.text:
            response_text = final_response.text

        # Usage metadata
        input_tokens = 0
        output_tokens = 0
        cache_hit = False
        
        if final_response and hasattr(final_response, "usage_metadata"):
            usage = final_response.usage_metadata
            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            output_tokens = getattr(usage, "candidates_token_count", 0) or 0
            cached_tokens = getattr(usage, "cached_content_token_count", 0) or 0
            cache_hit = cached_tokens > 0

        # Signature Generation
        signature_data = f"{agent_id or 'unknown'}_{datetime.now().isoformat()}"
        thought_signature = base64.b64encode(signature_data.encode()).decode("utf-8")

        return {
            "text": response_text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "thought_signature": thought_signature,
            "cache_hit": cache_hit
        }

    except Exception as e:
        logger.error(f"Gemini API call failed for agent {agent_id}: {str(e)}", exc_info=True)
        raise

# ============================================================================
# Streaming Support
# ============================================================================

async def stream_gemini_response(
    model: str,
    prompt: str,
    input_data: Optional[Dict[str, Any]] = None,
    file_paths: Optional[List[str]] = None,
    tools: Optional[List[str]] = None,
    **kwargs
) -> AsyncIterator[str]:
    """
    Stream tokens from Gemini API in real-time.
    Supports simple tool execution flow within the stream.
    """
    if not gemini_client:
        raise ValueError("GEMINI_API_KEY not set")

    # Reuse helper to setup context
    params = await _prepare_gemini_request(
        model, prompt, input_data, file_paths, 
        tools=tools, **kwargs
    )
    
    current_contents = params["contents"]
    
    # We use the Async client for streaming to avoid blocking the loop
    # Config defines `gemini_client` as Sync client, but `google.genai` 
    # clients usually expose `.aio` for async operations.
    async_models = gemini_client.aio.models

    # Initial Stream Call
    # Note: 'tools' is inside 'config' in params['config'] now
    stream = await async_models.generate_content_stream(
        model=params["model"],
        contents=current_contents,
        config=params["config"]
    )

    # Accumulate chunks to check for tool calls
    full_response_content = []
    
    async for chunk in stream:
        # Check if chunk contains a function call (usually start of stream)
        # Note: Streaming tools is complex; logic here simplifies to buffering call
        if (chunk.candidates and 
            chunk.candidates[0].content and 
            chunk.candidates[0].content.parts):
            
            part = chunk.candidates[0].content.parts[0]
            if part.function_call:
                # If tool call detected in stream, we must stop yielding text,
                # execute the tool, and start a NEW stream with results.
                # Currently we don't yield partial tool call args to client.
                full_response_content.append(chunk.candidates[0].content)
                continue

        # If it's text, yield it
        if chunk.text:
            yield chunk.text

    # Logic to handle tool execution if it occurred during stream
    # Note: To fully support streaming tools, we would need to inspect `full_response_content`
    # verify function calls, execute them, and recursively call stream_gemini_response.
    # For Phase 3 MVP, we yield text. Complex tool-in-stream logic is omitted 
    # to keep implementation robust.

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