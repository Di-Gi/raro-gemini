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
from core.config import gemini_client, logger, resolve_model
from intelligence.prompts import render_runtime_system_instruction

# Import Tooling Logic
try:
    from intelligence.tools import get_tool_declarations, execute_tool_call
except ImportError:
    logger.warning("intelligence.tools not found, tool execution will be disabled")
    get_tool_declarations = lambda x: []
    # FIX: Robust fallback signature that accepts keyword arguments
    execute_tool_call = lambda tool_name, args, run_id="default": {"error": "Tool execution unavailable"}

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
    agent_id: str, # <--- NEW ARGUMENT
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
    
    # 1. Generate System Instruction (The Fix)
    # We define the "Rules of Engagement" here, separate from the user prompt.
    system_instruction = render_runtime_system_instruction(agent_id, tools)

    # 2. Build Generation Config
    config_params: Dict[str, Any] = {
        "temperature": 1.0,
        # REMOVED: "automatic_function_calling" - invalid for low-level generate_content API
        # The tool loop is manually handled in call_gemini_with_context
        "system_instruction": system_instruction
    }

    # Add Deep Think configuration
    if "deep-think" in model and thinking_level:
        thinking_budget = min(max(thinking_level * 1000, 1000), 10000)
        config_params["thinking_config"] = types.ThinkingConfig(
            include_thoughts=True,
            thinking_budget=thinking_budget
        )
        logger.debug(f"Deep Think enabled: budget={thinking_budget}")

    # 3. Prepare Tools (Inject into config)
    if tools:
        declarations = get_tool_declarations(tools)
        if declarations:
            tool_obj = types.Tool(function_declarations=declarations)
            config_params["tools"] = [tool_obj]

            # FIX: Explicitly set tool_config to AUTO mode
            # This tells the model it CAN call tools or generate text as needed
            # Prevents UNEXPECTED_TOOL_CALL errors when model attempts tool use
            config_params["tool_config"] = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.AUTO
                )
            )
            logger.debug(f"Tools enabled with AUTO mode: {tools}")

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
    agent_id: Optional[str] = None,
    run_id: str = "default_run"
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

    # Log invocation details
    logger.info(
        f"\n{'#'*70}\n"
        f"AGENT INVOCATION: {safe_agent_id}\n"
        f"Model: {concrete_model} | Run ID: {run_id}\n"
        f"Tools Available: {tools if tools else 'None'}\n"
        f"File Context: {len(file_paths) if file_paths else 0} files\n"
        f"{'#'*70}"
    )

    try:
        params = await _prepare_gemini_request(
            concrete_model, prompt, safe_agent_id, input_data, file_paths,
            parent_signature, thinking_level, tools
        )

        current_contents = params["contents"]
        max_turns = 10
        turn_count = 0
        final_response = None
        response = None

        logger.debug(f"Agent {safe_agent_id}: Starting tool execution loop (max {max_turns} turns)")

        while turn_count < max_turns:
            turn_count += 1
            
            response = await asyncio.to_thread(
                gemini_client.models.generate_content,
                model=params["model"],
                contents=current_contents,
                config=params["config"]
            )

            if not response.candidates:
                logger.error(f"Agent {agent_id}: API returned no candidates.")
                final_response = response
                break

            candidate = response.candidates[0]

            # === DEBUG: RAW CANDIDATE DUMP ===
            logger.info(f"Agent {agent_id} [Turn {turn_count}] RAW CANDIDATE:\n{candidate}")

            content = candidate.content

            # === ROBUST EXTRACTION: Extract function calls FIRST ===
            # This prevents confusion when content has no text but has function_call parts
            function_calls = []

            # 1. Extract function calls from content.parts if available
            if content and content.parts:
                for part in content.parts:
                    if part.function_call:
                        function_calls.append(part.function_call)

            # 2. Check finish_reason for tool-related stops
            finish_reason = candidate.finish_reason.name if candidate.finish_reason else "UNKNOWN"

            # 3. Handle the case where no function calls were extracted
            if not function_calls:
                # If finish_reason indicates tool call but we didn't extract any, log warning
                if finish_reason == "UNEXPECTED_TOOL_CALL":
                    logger.warning(
                        f"Agent {agent_id}: UNEXPECTED_TOOL_CALL finish reason but no function calls extracted. "
                        f"This may indicate a tool configuration mismatch."
                    )

                # No function calls means we're done with the tool loop
                final_response = response
                break

            logger.info(f"Agent {agent_id} triggered {len(function_calls)} tool calls (Turn {turn_count})")
            
            current_contents.append(candidate.content)

            function_responses = []
            for call in function_calls:
                tool_name = call.name
                tool_args = call.args

                # Log the tool call with appropriate detail level
                if tool_name == "execute_python":
                    code_preview = tool_args.get('code', '')
                    code_lines = len(code_preview.split('\n'))
                    logger.info(
                        f"\n{'='*60}\n"
                        f"[TOOL CALL: {tool_name}] Agent: {agent_id} | Turn: {turn_count}\n"
                        f"Code Length: {len(code_preview)} chars, {code_lines} lines\n"
                        f"{'='*60}\n"
                        f"{code_preview}\n"
                        f"{'='*60}"
                    )
                else:
                    logger.info(
                        f"[TOOL CALL: {tool_name}] Agent: {agent_id} | Turn: {turn_count} | "
                        f"Args: {json.dumps(tool_args, default=str)}"
                    )

                # Execute the tool
                result_dict = execute_tool_call(tool_name, tool_args, run_id=run_id)

                # Log the result with success/failure indication
                success = result_dict.get('success', True)  # Default to True for backwards compat
                if tool_name == "execute_python":
                    files_generated = result_dict.get('files_generated', [])
                    logger.info(
                        f"\n{'='*60}\n"
                        f"[TOOL RESULT: {tool_name}] Agent: {agent_id}\n"
                        f"Status: {'SUCCESS' if success else 'FAILED'}\n"
                        f"Files Generated: {len(files_generated)} - {files_generated}\n"
                        f"{'='*60}"
                    )
                    if not success:
                        logger.error(f"Execution Error: {result_dict.get('error', 'Unknown error')}")
                else:
                    status_icon = "✓" if success else "✗"
                    logger.info(
                        f"[TOOL RESULT: {tool_name}] Agent: {agent_id} | Status: {status_icon} "
                        f"{'SUCCESS' if success else 'FAILED'}"
                    )
                    if not success:
                        logger.warning(f"Tool Error: {result_dict.get('error', 'Unknown error')}")

                function_responses.append(types.Part.from_function_response(
                    name=tool_name,
                    response=result_dict
                ))

            current_contents.append(types.Content(
                role="function",
                parts=function_responses
            ))

        if not final_response:
            logger.warning(f"Agent {agent_id} hit max tool turns ({max_turns}) or failed to converge")
            final_response = response

        response_text = ""
        if final_response and final_response.text:
            response_text = final_response.text
        
        last_role = None
        if current_contents:
            last_item = current_contents[-1]
            if isinstance(last_item, dict):
                last_role = last_item.get("role")
            else:
                last_role = getattr(last_item, "role", None)

        if not response_text and last_role == "function":
                try:
                    last_part = current_contents[-1].parts[0]
                    last_result = last_part.function_response.response
                    
                    if 'result' in last_result:
                        response_text = f"[SYSTEM: Tool Output Used as Response] {last_result['result']}"
                    else:
                        response_text = f"[SYSTEM: Tool Output Used as Response] {str(last_result)}"
                    
                    logger.info(f"Agent {agent_id}: Model returned empty text. Promoted tool output to response.")
                except Exception as e:
                    logger.warning(f"Agent {agent_id}: Failed to promote tool output: {e}")
                    response_text = "[SYSTEM: Task completed with silent tool execution]"

        input_tokens = 0
        output_tokens = 0
        cache_hit = False
        
        if final_response and hasattr(final_response, "usage_metadata"):
            usage = final_response.usage_metadata
            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            output_tokens = getattr(usage, "candidates_token_count", 0) or 0
            cached_tokens = getattr(usage, "cached_content_token_count", 0) or 0
            cache_hit = cached_tokens > 0

        signature_data = f"{agent_id or 'unknown'}_{datetime.now().isoformat()}"
        thought_signature = base64.b64encode(signature_data.encode()).decode("utf-8")

        # Log completion summary
        total_tokens = input_tokens + output_tokens
        logger.info(
            f"\n{'#'*70}\n"
            f"AGENT COMPLETED: {safe_agent_id}\n"
            f"Turns Used: {turn_count}/{max_turns}\n"
            f"Tokens: {total_tokens} (Input: {input_tokens}, Output: {output_tokens})\n"
            f"Cache Hit: {cache_hit}\n"
            f"Response Length: {len(response_text)} chars\n"
            f"{'#'*70}\n"
        )

        return {
            "text": response_text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "thought_signature": thought_signature,
            "cache_hit": cache_hit
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
    input_data: Optional[Dict[str, Any]] = None,
    file_paths: Optional[List[str]] = None,
    parent_signature: Optional[str] = None,
    thinking_level: Optional[int] = None,
    tools: Optional[List[str]] = None,
    agent_id: Optional[str] = None,
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
        concrete_model, prompt, safe_agent_id, input_data, file_paths, 
        parent_signature, thinking_level, tools
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