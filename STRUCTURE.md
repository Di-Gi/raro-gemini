# Project Structure with Import Analysis

- apps/
  - agent-service/
    - src/
      - core/
        - config.py
            # [[RARO]]/apps/agent-service/src/core/config.py
            # Purpose: Centralized Configuration & Client Management
            # Architecture: Core Layer providing singleton access to LLM and Cache clients.
            # Dependencies: pydantic-settings, google-genai, redis
            
            import os
            import logging
            from typing import Optional, Dict
            from pydantic_settings import BaseSettings
            from google import genai
            import redis
            
            class Settings(BaseSettings):
                """
                Application settings loaded from environment variables.
                Defaults are provided for local development.
                """
                GEMINI_API_KEY: Optional[str] = None
                E2B_API_KEY: Optional[str] = None
                TAVILY_API_KEY: Optional[str] = None
            
                REDIS_URL: str = "redis://localhost:6379"
                LOG_LEVEL: str = "INFO"
            
                # NEW: If set, we skip Gemini calls and send prompt dumps here
                DEBUG_PROBE_URL: Optional[str] = None
                
                # === MODEL AUTHORITY ===
                # Change specific versions here to propagate across the entire system.
                MODEL_FAST: str = "gemini-2.5-flash-lite"
                MODEL_REASONING: str = "gemini-2.5-flash-lite"
                MODEL_THINKING: str = "gemini-2.5-flash-lite"
                # THE MAPPING LAYER
                # The system sends keys (left), we use values (right).
                MODEL_ALIASES: Dict[str, str] = {
                    "fast": MODEL_FAST,
                    "reasoning": MODEL_REASONING,
                    "thinking": MODEL_THINKING,
                }
                MODEL_CUSTOM: Optional[str] = None
            
                class Config:
                    env_file = ".env"
                    case_sensitive = True
            # Initialize Settings
            settings = Settings()
            
            # Configure Logging
            logging.basicConfig(
                level=settings.LOG_LEVEL,
                format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            logger = logging.getLogger("raro.agent")
            
            
            def resolve_model(alias: str) -> str:
                """
                Resolves a semantic alias (e.g., 'fast') to a concrete model ID.
                If no alias is found, assumes the string is already a concrete ID (passthrough).
                """
                # Normalize input
                key = alias.lower().strip()
                
                if key in settings.MODEL_ALIASES:
                    return settings.MODEL_ALIASES[key]
                
                # Allow custom passthrough (e.g. if user specifically requests 'gemini-1.5-pro')
                return alias
            
            def get_gemini_client() -> Optional[genai.Client]:
                """
                Initializes and returns the Google GenAI client.
                Returns None if the API key is missing to allow for graceful failure in non-LLM paths.
                """
                if not settings.GEMINI_API_KEY:
                    logger.error("GEMINI_API_KEY is missing. LLM features will be disabled.")
                    return None
                try:
                    return genai.Client(api_key=settings.GEMINI_API_KEY)
                except Exception as e:
                    logger.error(f"Failed to initialize Gemini Client: {e}")
                    return None
            
            def get_redis_client() -> Optional[redis.Redis]:
                """
                Initializes and validates the Redis connection.
                """
                try:
                    r = redis.from_url(settings.REDIS_URL, decode_responses=True)
                    r.ping()
                    logger.info(f"Connected to Redis at {settings.REDIS_URL}")
                    return r
                except Exception as e:
                    logger.warning(f"Redis connection failed: {e}. State persistence will be disabled.")
                    return None
            
            # Global instances for service-wide import
            gemini_client = get_gemini_client()
            redis_client = get_redis_client()
            
            # Integration: Imported by src/main.py, src/core/llm.py, and src/intelligence/architect.py.
            # Notes: Ensure .env file contains a valid GEMINI_API_KEY for full functionality.
        - llm.py
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
            import re  # Added for reasoning extraction regex
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
                Load multimodal file for Gemini consumption.
                Handles text files (JSON, CSV, Code) by reading as text.
                Handles media files (Images, PDF) by reading as binary inline_data.
            
                Args:
                    file_path: Path to the file to load
            
                Returns:
                    Dict with 'text' or 'inline_data' structure for Gemini API
                """
                path = Path(file_path)
                if not path.exists():
                    raise FileNotFoundError(f"File not found: {file_path}")
            
                mime_type, _ = mimetypes.guess_type(file_path)
                logger.debug(f"Loading multimodal file: {file_path} (type: {mime_type})")
            
                # === FIX: TEXT MODE DETECTION ===
                # List of mime types/extensions that should be treated as text context
                # to avoid 500 errors from passing them as binary blobs.
                text_mimes = {
                    'application/json',
                    'application/xml',
                    'text/csv',
                    'text/plain',
                    'text/markdown',
                    'text/html',
                    'text/x-python',
                    'application/javascript',
                    'application/x-yaml'
                }
                
                text_extensions = {
                    '.json', '.csv', '.txt', '.md', '.py', '.js', '.xml', '.yml', '.yaml', '.sh', '.rs', '.ts', '.svelte'
                }
            
                is_text = False
                
                # 1. Check Mime
                if mime_type and (mime_type.startswith('text/') or mime_type in text_mimes):
                    is_text = True
                
                # 2. Check Extension (Fallback if mime is None or octet-stream)
                if not is_text:
                    ext = path.suffix.lower()
                    if ext in text_extensions:
                        is_text = True
            
                # === MODE A: TEXT INJECTION ===
                if is_text:
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f.read()
                        
                        # Truncate if massive to prevent context overflow (e.g. > 1MB text)
                        if len(content) > 500_000:
                            content = content[:500_000] + "\n...[TRUNCATED: File too large]..."
                        
                        # Return as explicit text part with clear delimiters
                        return {
                            "text": f"\n[FILE_CONTEXT: {path.name}]\n{content}\n[END_FILE_CONTEXT]\n"
                        }
                    except UnicodeDecodeError:
                        logger.warning(f"File {file_path} identified as text but failed UTF-8 decode. Falling back to binary.")
                        # Fall through to binary mode
                    except Exception as e:
                        logger.error(f"Failed to read text file {file_path}: {e}")
                        return {"text": f"[ERROR: Could not read file {path.name}]"}
            
                # === MODE B: BINARY BLOB (Images, PDF, Audio) ===
                try:
                    with open(file_path, "rb") as f:
                        file_data = base64.standard_b64encode(f.read()).decode("utf-8")
            
                    # Map to Gemini types
                    final_mime = mime_type or "application/octet-stream"
                    
                    return {
                        "inline_data": {
                            "mime_type": final_mime,
                            "data": file_data
                        }
                    }
                except Exception as e:
                    logger.error(f"Failed to load binary file {file_path}: {e}")
                    return {"text": f"[ERROR: Failed to load binary file {path.name}]"}
            
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
                            # Load file (text or binary)
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
                    _seen_files = set()  # Track files to prevent duplicates during retries
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
            
                        # === PATCH: Emit Reasoning ===
                        # If there is text before/around the tool call, treat it as a thought
                        # We strip out the ```json:function ... ``` block to isolate the reasoning
                        reasoning_text = re.sub(r"```json:function[\s\S]*?```", "", content_text).strip()
                        
                        if reasoning_text:
                            emit_telemetry(
                                run_id=run_id,
                                agent_id=safe_agent_id,
                                category="REASONING",
                                message=reasoning_text,
                                meta="PLANNING"
                            )
                        # =============================
            
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
            
                            # --- FIX START: Capture generated files with deduplication ---
                            if isinstance(result_dict, dict) and "files_generated" in result_dict:
                                files = result_dict["files_generated"]
                                if isinstance(files, list):
                                    for f in files:
                                        if f not in _seen_files:
                                            _seen_files.add(f)
                                            all_files_generated.append(f)
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
        - parsers.py
            # [[RARO]]/apps/agent-service/src/core/parsers.py
            # Purpose: Unified Parser Module for Markdown Code Block Extraction
            # Architecture: Core Layer - Shared parsing utilities
            # Dependencies: re, json, typing
            
            import re
            import json
            from typing import Optional, List, Dict, Any, Tuple
            from core.config import logger
            
            
            class ParsedBlock:
                """
                Represents a parsed code block with its type and data.
                """
                def __init__(self, block_type: str, data: Dict[str, Any], raw_json: str):
                    self.block_type = block_type
                    self.data = data
                    self.raw_json = raw_json
            
                def __repr__(self):
                    return f"ParsedBlock(type={self.block_type}, keys={list(self.data.keys())})"
            
            
            def _repair_json_string(json_str: str) -> str:
                """
                Attempts to repair common JSON errors made by LLMs when embedding Python code.
                
                Specifically fixes invalid escape sequences (e.g., '\d', '\s' in regex) by 
                doubling backslashes that are not part of valid JSON control characters.
                
                Args:
                    json_str: The raw JSON string extracted from the LLM output.
                    
                Returns:
                    The sanitized string with double backslashes where appropriate.
                """
                # Regex logic: Find a backslash that is NOT followed by a valid JSON escape char.
                # Valid JSON escapes are: " \ / b f n r t u
                # If we find a backslash followed by anything else (like 'd' in '\d'), double it.
                pattern = r'\\(?![/u"\\bfnrt])'
                
                # Replace single backslash with double backslash
                return re.sub(pattern, r'\\\\', json_str)
            
            
            def _parse_with_repair(json_str: str, block_type: str) -> Optional[Dict[str, Any]]:
                """
                Helper to parse JSON with a fallback repair mechanism.
                
                1. Tries standard json.loads().
                2. If that fails, applies _repair_json_string() and tries again.
                3. Logs warnings/errors appropriately.
                """
                try:
                    # Attempt 1: Standard Parse
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    try:
                        # Attempt 2: Auto-Repair (Fix regex backslashes)
                        logger.warning(f"Initial JSON parse failed for ```json:{block_type}```, attempting regex repair...")
                        
                        repaired_json = _repair_json_string(json_str)
                        data = json.loads(repaired_json)
                        
                        logger.info(f"JSON repair successful for ```json:{block_type}```.")
                        return data
                    except json.JSONDecodeError as e:
                        # Attempt 3: Final Failure
                        logger.error(f"Failed to parse ```json:{block_type}``` block even after repair: {e}")
                        logger.debug(f"Failed JSON content: {json_str[:200]}...") # Log partial content for debug
                        return None
            
            
            def extract_code_block(text: str, block_type: str) -> Optional[ParsedBlock]:
                """
                Extract and parse a single code block of specified type from text.
            
                Args:
                    text: The text to search for code blocks
                    block_type: The type identifier (e.g., 'function', 'delegation')
            
                Returns:
                    ParsedBlock if found and valid, None otherwise
                """
                # Pattern matches: ```json:TYPE \n { ... } \n ```
                # [\s\S]*? matches any character including newlines, non-greedy
                pattern = rf"```json:{re.escape(block_type)}\s*(\{{[\s\S]*?\}})\s*```"
            
                match = re.search(pattern, text, re.IGNORECASE)
            
                if not match:
                    return None
            
                json_str = match.group(1)
                
                # Use robust parsing helper
                data = _parse_with_repair(json_str, block_type)
            
                if data:
                    return ParsedBlock(block_type=block_type, data=data, raw_json=json_str)
                
                return None
            
            
            def extract_all_code_blocks(text: str, block_type: str) -> List[ParsedBlock]:
                """
                Extract and parse ALL code blocks of specified type from text.
            
                Args:
                    text: The text to search for code blocks
                    block_type: The type identifier (e.g., 'function', 'delegation')
            
                Returns:
                    List of ParsedBlock objects (empty list if none found)
                """
                pattern = rf"```json:{re.escape(block_type)}\s*(\{{[\s\S]*?\}})\s*```"
            
                matches = re.finditer(pattern, text, re.IGNORECASE)
                blocks = []
            
                for match in matches:
                    json_str = match.group(1)
                    
                    # Use robust parsing helper
                    data = _parse_with_repair(json_str, block_type)
                    
                    if data:
                        blocks.append(ParsedBlock(block_type=block_type, data=data, raw_json=json_str))
                    else:
                        # Error already logged in _parse_with_repair
                        pass
            
                return blocks
            
            
            # ============================================================================
            # Specialized Parsers for Delegation and Function Calls
            # ============================================================================
            
            def parse_delegation_request(text: str) -> Optional[Dict[str, Any]]:
                """
                Parse a delegation request from agent output.
            
                Searches for ```json:delegation blocks and extracts the structured data.
            
                Args:
                    text: The agent's output text
            
                Returns:
                    Dictionary with delegation data if found, None otherwise
                """
                block = extract_code_block(text, 'delegation')
                return block.data if block else None
            
            
            def parse_function_calls(text: str) -> List[Tuple[str, Dict[str, Any]]]:
                """
                Parse function call requests from agent output.
            
                Searches for ```json:function blocks and extracts tool invocations.
            
                Args:
                    text: The agent's output text
            
                Returns:
                    List of tuples: [(tool_name, args), ...]
                """
                blocks = extract_all_code_blocks(text, 'function')
            
                function_calls = []
                for block in blocks:
                    tool_name = block.data.get('name')
                    tool_args = block.data.get('args', {})
            
                    if not tool_name:
                        logger.warning(f"Function block missing 'name' field: {block.raw_json[:100]}")
                        continue
            
                    function_calls.append((tool_name, tool_args))
            
                return function_calls
            
            
            def has_delegation_request(text: str) -> bool:
                """
                Quick check if text contains a delegation request.
            
                Args:
                    text: The text to check
            
                Returns:
                    True if delegation block is present, False otherwise
                """
                pattern = r"```json:delegation\s*\{[\s\S]*?\}\s*```"
                return bool(re.search(pattern, text, re.IGNORECASE))
            
            
            def has_function_calls(text: str) -> bool:
                """
                Quick check if text contains function call requests.
            
                Args:
                    text: The text to check
            
                Returns:
                    True if function block is present, False otherwise
                """
                pattern = r"```json:function\s*\{[\s\S]*?\}\s*```"
                return bool(re.search(pattern, text, re.IGNORECASE))
      - domain/
        - protocol.py
            # [[RARO]]/apps/agent-service/src/domain/protocol.py
            # Purpose: Shared Data Models and Domain Enums (DDD Value Objects)
            # Architecture: Domain Layer defining the contract between Agent Service and Kernel.
            # Dependencies: pydantic
            
            from enum import Enum
            from pydantic import BaseModel, Field
            from typing import List, Dict, Optional, Any, Literal
            from core.config import settings
            
            # ============================================================================
            # Enums (Expanded for robust error handling and Pydantic mapping)
            # ============================================================================
            
            class AgentRole(str, Enum):
                ORCHESTRATOR = "orchestrator"
                WORKER = "worker"
                OBSERVER = "observer"
            
            class DelegationStrategy(str, Enum):
                CHILD = "child"
                SIBLING = "sibling"
            
            class TriggerType(str, Enum):
                NODE_CREATED = "NodeCreated"
                TOOL_CALL = "ToolCall"
                AGENT_FAILED = "AgentFailed"
            
            class ActionType(str, Enum):
                INTERRUPT = "Interrupt"
                REQUEST_APPROVAL = "RequestApproval"
                SPAWN_AGENT = "SpawnAgent"
            
            # ============================================================================
            # Core Definitions
            # ============================================================================
            
            class AgentNodeConfig(BaseModel):
                """Configuration for a single agent node within a DAG workflow."""
                id: str = Field(..., description="Unique ID for the agent node")
                role: AgentRole = Field(AgentRole.WORKER, description="Structural role in the graph")
                specialty: str = Field("generalist", description="Functional description, e.g., 'Analyst'")
                
                # UPDATED: Default value now comes from config.py
                model: str = Field(settings.MODEL_FAST, description="Gemini model identifier")
                
                prompt: str = Field(..., description="The system instruction for this specific node")
                tools: List[str] = Field(default_factory=list, description="Capabilities enabled for this agent")
                depends_on: List[str] = Field(default_factory=list, description="IDs of nodes this agent waits for")
                input_schema: Dict[str, Any] = Field(default_factory=dict, description="Expected input JSON structure")
                output_schema: Dict[str, Any] = Field(default_factory=dict, description="Expected output JSON structure")
                accepts_directive: bool = False 
                
            class WorkflowManifest(BaseModel):
                """The complete DAG definition generated by the Architect."""
                name: str = Field(..., description="Descriptive name of the workflow")
                agents: List[AgentNodeConfig] = Field(..., description="The sequence/graph of agents to execute")
            
            class DelegationRequest(BaseModel):
                """Payload for an agent requesting dynamic graph expansion."""
                reason: str = Field(..., description="Justification for the delegation")
                strategy: DelegationStrategy = Field(DelegationStrategy.CHILD)
                new_nodes: List[AgentNodeConfig] = Field(..., description="Sub-agents to be spliced into the graph")
            
            # ============================================================================
            # Safety Patterns
            # ============================================================================
            
            class PatternTrigger(BaseModel):
                type: TriggerType
                condition: str = Field(..., description="Logic filter, e.g., 'tool == fs_delete'")
            
            class PatternAction(BaseModel):
                type: ActionType
                reason: str
            
            class PatternDefinition(BaseModel):
                """Definition of a safety guardrail compiled from natural language policy."""
                id: str
                name: str
                trigger: PatternTrigger
                action: PatternAction
            
            # ============================================================================
            # Transport Layer
            # ============================================================================
            
            class AgentRequest(BaseModel):
                """Request from the Kernel to execute a specific agent node."""
                agent_id: str
                model: str
                prompt: str
                input_data: Dict[str, Any]
            
                # === NEW FIELD ===
                # Required for RFS WorkspaceManager to locate the session folder
                run_id: str
            
                # === DIRECTIVE FIELD ===
                # Separate from prompt to preserve persona/directive distinction
                # Prompt = "You are a Python specialist..." (System Instruction)
                # user_directive = "Analyze this CSV..." (User Message)
                user_directive: str = ""
            
                tools: List[str] = []
                thought_signature: Optional[str] = None
                parent_signature: Optional[str] = None
                cached_content_id: Optional[str] = None
                thinking_level: Optional[int] = None
                file_paths: List[str] = []
            
                # [[NEW FIELDS]]
                allow_delegation: bool = False
                graph_view: str = "Context unavailable"
            
            class AgentResponse(BaseModel):
                """Result of an agent execution returned to the Kernel."""
                agent_id: str
                success: bool
                tokens_used: int = 0
                input_tokens: int = 0
                output_tokens: int = 0
                cache_hit: bool = False
                latency_ms: float = 0.0
                thought_signature: Optional[str] = None
                output: Optional[Dict[str, Any]] = None
                error: Optional[str] = None
                delegation: Optional[DelegationRequest] = None
            
            # Integration: Central source of truth for all Pydantic validation across the service.
            # Notes: Added run_id to AgentRequest to support RFS integration.
      - intelligence/
        - architect.py
            # [[RARO]]/apps/agent-service/src/intelligence/architect.py
            # Purpose: Core Planning Engine (Flow A & C)
            # Architecture: Intelligence Layer responsible for high-level reasoning and planning.
            # Dependencies: google-genai, domain.protocol
            
            # [[RARO]]/apps/agent-service/src/intelligence/architect.py
            import json
            import logging
            from typing import Optional
            from google import genai
            # Import types to use for configuration
            from google.genai import types 
            from pydantic import ValidationError
            from domain.protocol import WorkflowManifest, PatternDefinition, AgentRole
            from intelligence.prompts import render_architect_prompt, render_safety_compiler_prompt
            from core.config import logger, settings
            
            class ArchitectEngine:
                """
                Engine that uses LLM reasoning to generate structured workflow plans 
                and safety patterns. Uses strict low-temperature settings for JSON consistency.
                """
                def __init__(self, client: genai.Client):
                    self.client = client
                    self.model = settings.MODEL_REASONING
                    
                    self.generation_config = types.GenerateContentConfig(
                        temperature=0.1,
                        response_mime_type="application/json"
                    )
                async def generate_plan(self, user_query: str) -> WorkflowManifest:
                    """
                    Translates a natural language user query into a WorkflowManifest (DAG).
                    """
                    if not self.client:
                        raise ValueError("Gemini client is not initialized")
            
                    prompt = render_architect_prompt(user_query)
                    
                    try:
                        logger.info(f"Generating workflow plan for query: {user_query[:50]}...")
                        
                        # Pylance will now recognize self.generation_config as a valid type
                        response = self.client.models.generate_content(
                            model=self.model,
                            contents=prompt,
                            config=self.generation_config
                        )
                        
                        raw_text = response.text or "{}"
                        data = json.loads(raw_text)
            
                        # Defensive Coercion: Ensure roles match the Enum
                        if "agents" in data:
                            valid_roles = [role.value for role in AgentRole]
                            for agent in data["agents"]:
                                if agent.get("role") not in valid_roles:
                                    logger.warning(f"Coercing invalid role '{agent.get('role')}' to 'worker' for agent {agent.get('id')}")
                                    agent["role"] = AgentRole.WORKER.value
            
                        manifest = WorkflowManifest(**data)
                        return manifest
            
                    except json.JSONDecodeError as e:
                        logger.error(f"Architect produced invalid JSON: {e}")
                        raise ValueError(f"Failed to parse architect response as JSON: {str(e)}")
                    except ValidationError as e:
                        logger.error(f"Architect plan failed schema validation: {e}")
                        raise e
                    except Exception as e:
                        logger.error(f"Unexpected architect failure: {e}", exc_info=True)
                        raise
            
                async def compile_pattern(self, policy_rule: str) -> PatternDefinition:
                    """
                    Translates a safety policy rule into a machine-readable PatternDefinition.
                    """
                    prompt = render_safety_compiler_prompt(policy_rule)
                    
                    try:
                        logger.info(f"Compiling safety pattern: {policy_rule[:50]}...")
                        
                        response = self.client.models.generate_content(
                            model=self.model,
                            contents=prompt,
                            config=self.generation_config
                        )
                        
                        raw_text = response.text or "{}"
                        data = json.loads(raw_text)
                        
                        pattern = PatternDefinition(**data)
                        return pattern
                                  
                    except Exception as e:
                        logger.error(f"Pattern compilation failure: {e}")
                        raise
            
            # Integration: Utilized by /plan and /compile-pattern endpoints in main.py.
            # Notes: Uses temperature 0.1 to ensure output strictly follows the Pydantic schema.
        - prompts.py
            # [[RARO]]/apps/agent-service/src/intelligence/prompts.py
            
            import json
            from typing import Optional, List
            from domain.protocol import WorkflowManifest, DelegationRequest, PatternDefinition
            try:
                from intelligence.tools import get_tool_definitions_for_prompt
            except ImportError:
                get_tool_definitions_for_prompt = lambda x: "[]"
            
            def get_schema_instruction(model_class) -> str:
                """
                Extracts a clean JSON schema from a Pydantic model to inject into prompts.
                This guarantees the LLM knows the EXACT JSON format we require.
                """
                try:
                    schema = model_class.model_json_schema()
                    return json.dumps(schema, indent=2)
                except Exception:
                    return "{}"
            
            # === ARCHITECT PROMPT (Flow A) ===
            def render_architect_prompt(user_query: str) -> str:
                schema = get_schema_instruction(WorkflowManifest)
                return f"""
            ROLE: System Architect
            GOAL: Design a multi-agent Directed Acyclic Graph (DAG) to solve the user's request.
            
            USER REQUEST: "{user_query}"
            
            INSTRUCTIONS:
            1. Break the request into atomic steps.
            2. For each agent, you must use one of these STRUCTURAL ROLES:
               - 'worker': For standard tasks (Research, Analysis, Coding).
               - 'orchestrator': Only for complex sub-management.
               - 'observer': For monitoring/logging.
            3. Use the 'id' field to define the functional role (e.g., 'web_researcher', 'data_analyst').
            4. Define dependencies (e.g., 'data_analyst' depends_on ['web_researcher']).
            5. Select model: 'gemini-2.5-flash' (speed) or 'gemini-2.5-flash-lite' (reasoning).
            6. TOOL ASSIGNMENT RULES (CRITICAL):
               Available Tools: ['execute_python', 'web_search', 'read_file', 'write_file', 'list_files']
            
               ASSIGNMENT GUIDELINES:
               - 'execute_python': REQUIRED for ANY agent that needs to:
                 * Create files (images, graphs, PDFs, CSV, JSON)
                 * Perform calculations or data analysis
                 * Process or transform data
                 * Generate visualizations
                 When in doubt, INCLUDE this tool - it's the most versatile.
            
               - 'web_search': REQUIRED for agents that need:
                 * Real-time information or current events
                 * Fact verification
                 * Research from the internet
            
               - 'read_file', 'write_file', 'list_files':
                 * Baseline tools are auto-assigned by the system
                 * You CAN explicitly include them, but it's optional
            
               - IMPORTANT: Be GENEROUS with tool assignments. If an agent MIGHT need a tool, assign it.
                 Better to over-assign than under-assign (prevents UNEXPECTED_TOOL_CALL errors).
            
            7. PROMPT CONSTRUCTION:
               - For agents with 'execute_python', write prompts like: "Write and EXECUTE Python code to..."
               - Do NOT ask agents to "output code" or "describe the approach"
               - Ask for RESULTS, not explanations
            
            8. STRICT OUTPUT PROTOCOL:
               - Agents MUST NOT output Python code in Markdown blocks (```python).
               - Agents MUST use the 'execute_python' tool for all logic.
               - The pipeline relies on the *Tool Result* to pass data to the next agent. Markdown text is ignored by the compiler.
            
            OUTPUT REQUIREMENT:
            You must output PURE JSON matching this schema:
            {schema}
            
            IMPORTANT: The 'role' field MUST be exactly 'worker', 'orchestrator', or 'observer'.
            """
            #
            # def render_architect_prompt(user_query: str) -> str:
            #     schema = get_schema_instruction(WorkflowManifest)
            #     return f"""
            # ROLE: System Architect
            # GOAL: Design a multi-agent Directed Acyclic Graph (DAG) for: "{user_query}"
            
            # INSTRUCTIONS:
            # 1. **Structural Role**: The 'role' field MUST be exactly one of: ['orchestrator', 'worker', 'observer']. 
            #    - Use 'worker' for almost all tasks.
            # 2. **Specialty**: Use the 'specialty' field for the functional title (e.g., 'Analyst', 'Researcher', 'Coder').
            # 3. **ID**: Use unique slug-style IDs (e.g., 'research_node_1').
            
            # OUTPUT REQUIREMENT:
            # Output PURE JSON matching this schema:
            # {schema}
            
            # IMPORTANT: If you put 'Analyst' in the 'role' field, the system will crash. Put 'worker' in 'role' and 'Analyst' in 'specialty'.
            # """
            
            
            # === WORKER PROMPT (Flow B Support) ===
            def inject_delegation_capability(base_prompt: str) -> str:
                schema = get_schema_instruction(DelegationRequest)
                return f"""
            {base_prompt}
            
            [SYSTEM CAPABILITY: DYNAMIC DELEGATION]
            If the task is too complex, missing data, or requires sub-tasks:
            You are authorized to spawn sub-agents.
            
            To delegate, output a JSON object wrapped in a SPECIAL code block.
            You MUST use the tag `json:delegation` for the system to recognize it.
            
            Example Format:
            ```json:delegation
            {schema}
            ```
            
            The system will:
            1. Pause your execution.
            2. Run these new agents.
            3. Return their results to you as context.
            """
            
            # === SAFETY COMPILER PROMPT (Flow C) ===
            def render_safety_compiler_prompt(policy_rule: str) -> str:
                schema = get_schema_instruction(PatternDefinition)
                return f"""
            ROLE: Cortex Safety Compiler
            GOAL: Translate a natural language safety policy into a Machine-Readable Pattern.
            
            POLICY RULE: "{policy_rule}"
            
            INSTRUCTIONS:
            1. Identify the trigger event (e.g., ToolCall, AgentFailed).
            2. Define the condition logic.
            3. Determine the enforcement action (Interrupt, RequestApproval).
            
            OUTPUT REQUIREMENT:
            Output PURE JSON matching this schema:
            
            {schema}
            """
            
            def render_runtime_system_instruction(agent_id: str, tools: Optional[List[str]]) -> str:
                """
                Generates the high-priority System Instruction for the Runtime Loop (Flow B).
                Uses MANUAL PARSING MODE with json:function blocks.
                """
                instruction = f"""
            SYSTEM IDENTITY:
            You are Agent '{agent_id}', an autonomous execution node within the RARO Kernel.
            You are running in a headless environment. Your outputs are consumed programmatically.
            
            OPERATIONAL CONSTRAINTS:
            1. NO CHAT: Do not output conversational filler.
            2. DIRECT ACTION: If the user request implies an action, use a tool immediately.
            3. FAIL FAST: If you cannot complete the task, return a clear error.
            """
            
                if tools:
                    tool_schemas = get_tool_definitions_for_prompt(tools)
            
                    instruction += f"""
            [SYSTEM CAPABILITY: TOOL USE]
            You have access to the following tools. 
            To use a tool, you MUST output a specific Markdown code block. 
            DO NOT use native function calling mechanisms.
            
            AVAILABLE TOOLS (Reference):
            {tool_schemas}
            
            [CRITICAL PROTOCOL: MANUAL CALLING]
            The system does not support native function calling. 
            You must MANUALLY type the tool call using the `json:function` tag.
            
            CORRECT FORMAT:
            ```json:function
            {{
              "name": "tool_name",
              "args": {{
                "parameter_name": "value"
              }}
            }}
            ```
            
            [ONE-SHOT EXAMPLE]
            User: "Calculate 25 * 4 using python"
            Assistant:
            ```json:function
            {{
              "name": "execute_python",
              "args": {{
                "code": "print(25 * 4)"
              }}
            }}
            ```
            
            INCORRECT FORMATS (FORBIDDEN):
            - No standard ```json``` blocks.
            - No ```python``` blocks for code execution.
            - No native tool objects.
            """
            
                    # Specific guidance for Python
                    if "execute_python" in tools:
                        instruction += """
            [TOOL NOTE: execute_python]
            You have a secure Python sandbox.
            To run code, you MUST use the `execute_python` tool.
            Do NOT output ```python ... ``` text blocks; the system ignores them.
            [TOOL NOTE: execute_python vs read_file]
            - Use `read_file` for: Inspecting file contents, checking headers, or reading small logs. It is fast and free.
            - Use `execute_python` for: Heavy data transformation, math, creating charts/images, or processing large files. 
              NOTE: Files created by previous agents are automatically available in your Python environment.
            """
                else:
                    instruction += "\nNOTE: You have NO tools available. Provide analysis based solely on the provided context.\n"
            
                return instruction
        - tools.py
            # [[RARO]]/apps/agent-service/src/intelligence/tools.py
            # Purpose: Tool definitions and Secure Workspace Execution Logic
            # Architecture: Intelligence Layer bridge to E2B and Tavily
            # Dependencies: google-genai, e2b-code-interpreter, tavily-python
            
            import os
            import base64
            import logging
            from datetime import datetime
            from typing import List, Dict, Any, Optional, Union
            from google.genai import types
            from core.config import settings, logger, redis_client
            import json
            
            # --- OPTIONAL DEPENDENCY LOADING ---
            try:
                from e2b_code_interpreter import Sandbox
                from tavily import TavilyClient
            except ImportError:
                logger.warning("E2B or Tavily libraries not found. Advanced tools will be disabled.")
                Sandbox = None
                TavilyClient = None
            
            # Hard anchor to prevent agents from breaking out of the sandbox
            RFS_BASE = "/app/storage"
            
            class WorkspaceManager:
                """
                Secure file system abstraction that scopes all I/O operations to a specific 
                run session ID. Prevents path traversal and unauthorized access.
                """
                def __init__(self, run_id: str):
                    self.run_id = run_id
                    self.session_root = os.path.join(RFS_BASE, "sessions", run_id)
                    self.input_dir = os.path.join(self.session_root, "input")
                    self.output_dir = os.path.join(self.session_root, "output")
                    
                    os.makedirs(self.input_dir, exist_ok=True)
                    os.makedirs(self.output_dir, exist_ok=True)
            
                def _get_secure_path(self, filename: str) -> Optional[str]:
                    clean_name = os.path.basename(filename) 
                    out_path = os.path.join(self.output_dir, clean_name)
                    if os.path.exists(out_path): return out_path
                    in_path = os.path.join(self.input_dir, clean_name)
                    if os.path.exists(in_path): return in_path
                    return None
            
                def read(self, filename: str) -> str:
                    path = self._get_secure_path(filename)
                    if not path: return f"Error: File '{filename}' not found."
                    try:
                        with open(path, 'r', encoding='utf-8') as f:
                            content = f.read()
                            if len(content) > 50000: return content[:50000] + "\n...[TRUNCATED]..."
                            return content
                    except Exception as e: return f"Error reading file: {str(e)}"
            
                def write(self, filename: str, content: Union[str, bytes]) -> str:
                    clean_name = os.path.basename(filename)
                    path = os.path.join(self.output_dir, clean_name)
                    try:
                        mode = 'wb' if isinstance(content, bytes) else 'w'
                        encoding = None if isinstance(content, bytes) else 'utf-8'
                        
                        with open(path, mode, encoding=encoding) as f:
                            f.write(content)
                            # === FIX: FORCE DISK SYNC ===
                            # Ensures OS flushes buffer to Docker volume immediately.
                            # Prevents 0-byte files when Kernel tries to read immediately after.
                            f.flush()
                            os.fsync(f.fileno())
                            
                        return f"Successfully saved to {clean_name}"
                    except Exception as e: return f"Error writing file: {str(e)}"
            
                def list_contents(self) -> str:
                    try:
                        inputs = os.listdir(self.input_dir)
                        outputs = os.listdir(self.output_dir)
                        return f"FILES:\nInputs: {inputs}\nOutputs: {outputs}"
                    except Exception as e: return f"Error: {str(e)}"
            
            # ============================================================================
            # SANDBOX SESSION MANAGEMENT
            # ============================================================================
            
            class SandboxSession:
                """
                Manages persistent E2B sandboxes across multiple agent steps.
                Stores the E2B Sandbox ID in Redis keyed by the RARO run_id.
                """
                @staticmethod
                def get_redis_key(run_id: str) -> str:
                    return f"raro:e2b_session:{run_id}"
            
                @classmethod
                def get_or_create(cls, run_id: str) -> Optional[Any]:
                    if not Sandbox or not settings.E2B_API_KEY:
                        return None
            
                    key = cls.get_redis_key(run_id)
            
                    # 1. Try to recover existing session
                    if redis_client:
                        stored_sandbox_id = redis_client.get(key)
                        if stored_sandbox_id:
                            try:
                                # FIX: Explicit cast to str to satisfy Pylance/Type Checker
                                # Redis get returns ResponseT which matches str|None at runtime
                                sandbox_id_str = str(stored_sandbox_id)
                                
                                logger.info(f"Reconnecting to existing E2B sandbox: {sandbox_id_str} for run {run_id}")
                                # Connect to existing session
                                sandbox = Sandbox.connect(sandbox_id_str, api_key=settings.E2B_API_KEY)
                                return sandbox
                            except Exception as e:
                                logger.warning(f"Failed to reconnect to sandbox {stored_sandbox_id}: {e}. Creating new one.")
                                redis_client.delete(key)
            
                    # 2. Create new session if none exists or connection failed
                    try:
                        logger.info(f"Creating NEW E2B sandbox for run {run_id}")
                        # Set a longer timeout (e.g., 10 minutes) so it survives between agent thoughts
                        sandbox = Sandbox.create(api_key=settings.E2B_API_KEY, timeout=600)
            
                        if redis_client:
                            # Store ID for future steps. Expire after 1 hour to prevent leaks.
                            redis_client.setex(key, 3600, sandbox.sandbox_id)
            
                        return sandbox
                    except Exception as e:
                        logger.error(f"Failed to create E2B sandbox: {e}")
                        return None
            
                @classmethod
                def kill_session(cls, run_id: str):
                    """Explicitly kill the sandbox when the run is finished."""
                    if not redis_client or not Sandbox: return
            
                    key = cls.get_redis_key(run_id)
                    stored_sandbox_id = redis_client.get(key)
            
                    if stored_sandbox_id:
                        try:
                            # FIX: Explicit cast to str
                            sandbox_id_str = str(stored_sandbox_id)
                            logger.info(f"Killing E2B sandbox {sandbox_id_str} for completed run {run_id}")
                            Sandbox.kill(sandbox_id_str, api_key=settings.E2B_API_KEY)
                        except Exception as e:
                            logger.warning(f"Error killing sandbox: {e}")
                        finally:
                            redis_client.delete(key)
            
            # --- EXECUTION LOGIC ---
            
            def _run_e2b_sandbox(code: str, ws: WorkspaceManager) -> Dict[str, Any]:
                if Sandbox is None: return {"error": "E2B library missing."}
            
                # 1. Acquire Persistent Sandbox
                sandbox = SandboxSession.get_or_create(ws.run_id)
                if not sandbox:
                    return {"error": "Failed to initialize E2B Sandbox connection."}
            
                try:
                    # 2. SYNC FILES (Smart Sync)
                    if os.path.exists(ws.input_dir):
                        for filename in os.listdir(ws.input_dir):
                            file_path = os.path.join(ws.input_dir, filename)
                            if os.path.isfile(file_path):
                                try:
                                    with open(file_path, "rb") as f:
                                        # Use /home/user explicitly to ensure we are in the cwd
                                        sandbox.files.write(f"/home/user/{filename}", f.read())
                                except Exception as e:
                                    logger.warning(f"Failed to upload {filename}: {e}")
            
                    # 3. Execute Code
                    logger.info(f"E2B: Executing code ({len(code)} chars)")
                    execution = sandbox.run_code(code)
            
                    output_log = []
                    if execution.logs.stdout: output_log.append(f"STDOUT:\n{''.join(execution.logs.stdout)}")
                    if execution.logs.stderr: output_log.append(f"STDERR:\n{''.join(execution.logs.stderr)}")
            
                    # 4. CAPTURE ARTIFACTS
                    artifacts_created = []
            
                    # A. Plot/Image Results (from plt.show() or implicit display)
                    for result in execution.results:
                        if hasattr(result, 'png') and result.png:
                            timestamp = datetime.now().strftime("%H%M%S")
                            img_filename = f"plot_{ws.run_id}_{timestamp}_{len(artifacts_created)}.png"
                            ws.write(img_filename, base64.b64decode(result.png))
                            artifacts_created.append(img_filename)
                            output_log.append(f"\n[SYSTEM: Generated Image saved to '{img_filename}']")
            
                    # B. File System Artifacts (from plt.savefig() or open(..., 'w'))
                    try:
                        # FIX: Explicitly check the standard working directory
                        # list(".") sometimes returns root or behaves unexpectedly in some versions
                        files_in_sandbox = sandbox.files.list("/home/user")
                        
                        logger.debug(f"Sandbox Files Scan: {[f.name for f in files_in_sandbox]}")
            
                        for remote_file in files_in_sandbox:
                            if remote_file.name.startswith(".") or remote_file.name == "__pycache__": continue
            
                            try:
                                # FIX: Explicit path read
                                file_bytes = sandbox.files.read(f"/home/user/{remote_file.name}", format="bytes")
                                
                                if file_bytes is not None and len(file_bytes) > 0:
                                    ws.write(remote_file.name, file_bytes)
                                    if remote_file.name not in artifacts_created:
                                        artifacts_created.append(remote_file.name)
                                else:
                                    # Don't log warning for directory entries or empty files unless specific
                                    pass
                                    
                            except Exception as read_err:
                                # Often fails if it's a directory, ignore specific errors
                                logger.debug(f"Skipping artifact capture for {remote_file.name}: {read_err}")
                    except Exception as list_err:
                        logger.warning(f"Failed to list sandbox files: {list_err}")
            
                    # 5. Handle Errors (But DO NOT close sandbox)
                    if execution.error:
                        error_msg = f"RUNTIME ERROR: {execution.error.name}: {execution.error.value}"
                        if execution.error.traceback: error_msg += f"\n{execution.error.traceback}"
                        return {"success": False, "error": error_msg, "logs": "\n".join(output_log)}
            
                    logs_text = "\n".join(output_log)
                    if artifacts_created:
                        logs_text += f"\n[SYSTEM: The following files were generated/updated: {artifacts_created}]"
            
                    return {
                        "success": True,
                        "result": logs_text if logs_text else "Execution successful (No stdout).",
                        "files_generated": artifacts_created
                    }
            
                except Exception as e:
                    logger.error(f"E2B failure: {e}", exc_info=True)
                    return {"success": False, "error": f"Sandbox failed: {str(e)}"}
            
            def _run_tavily_search(query: str) -> Dict[str, Any]:
                if TavilyClient is None or not settings.TAVILY_API_KEY:
                    return {"success": False, "error": "Search unavailable (Missing Lib or Key)."}
                try:
                    tavily = TavilyClient(api_key=settings.TAVILY_API_KEY)
                    context = tavily.get_search_context(query=query, search_depth="advanced", max_tokens=2000)
                    return {"success": True, "result": context}
                except Exception as e:
                    return {"success": False, "error": f"Search failed: {str(e)}"}
            
            # --- TOOL DEFINITIONS ---
            
            def get_tool_definitions_for_prompt(tool_names: List[str]) -> str:
                """
                Returns a RICH JSON string of tool definitions.
                Explicitly formats schemas to guide the LLM's JSON generation.
                """
                registry = {
                    'web_search': {
                        "name": "web_search",
                        "description": "Perform a real-time web search. Use for current events, fact verification, or technical documentation.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string", 
                                    "description": "The search keywords or question."
                                }
                            },
                            "required": ["query"]
                        }
                    },
                    'execute_python': {
                        "name": "execute_python",
                        "description": "Run Python code in a secure sandbox. REQUIRED for: calculations, data analysis (pandas), and generating files/plots (matplotlib).",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "code": {
                                    "type": "string", 
                                    "description": "The complete Python script to execute. Must be valid, self-contained code."
                                }
                            },
                            "required": ["code"]
                        }
                    },
                    'read_file': {
                        "name": "read_file",
                        "description": "Read text content from a file in your workspace.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "filename": {
                                    "type": "string", 
                                    "description": "The exact name of the file (e.g. 'data.csv')."
                                }
                            },
                            "required": ["filename"]
                        }
                    },
                    'write_file': {
                        "name": "write_file",
                        "description": "Create or overwrite a text file in your workspace.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "filename": {
                                    "type": "string", 
                                    "description": "The destination filename."
                                },
                                "content": {
                                    "type": "string", 
                                    "description": "The text content to write."
                                }
                            },
                            "required": ["filename", "content"]
                        }
                    },
                    'list_files': {
                        "name": "list_files",
                        "description": "List all files currently available in your workspace (inputs and outputs).",
                        "parameters": {
                            "type": "object",
                            "properties": {}
                        }
                    }
                }
            
                definitions = []
                for name in tool_names:
                    if name in registry:
                        definitions.append(registry[name])
            
                # Return clean JSON string
                return json.dumps(definitions, indent=2)
            
            def execute_tool_call(tool_name: str, args: Dict[str, Any], run_id: str = "default_run") -> Dict[str, Any]:
                """Dispatcher"""
                ws = WorkspaceManager(run_id)
                try:
                    if tool_name == 'read_file':
                        return {'success': True, 'result': ws.read(args.get('filename', ''))}
                    
                    elif tool_name == 'write_file':
                        filename = args.get('filename', '')
                        result = ws.write(filename, args.get('content', ''))
                        # === FIX: EXPLICITLY RETURN FILES_GENERATED ===
                        # This ensures the Kernel logic picks up the file for promotion/mounting.
                        return {
                            'success': True, 
                            'result': result, 
                            'files_generated': [filename] 
                        }
                        
                    elif tool_name == 'list_files':
                        return {'success': True, 'result': ws.list_contents()}
                    elif tool_name == 'web_search':
                        return _run_tavily_search(args.get('query', ''))
                    elif tool_name == 'execute_python':
                        return _run_e2b_sandbox(args.get('code', ''), ws)
                    
                    return {'success': False, 'error': f"Unknown tool: {tool_name}"}
                except Exception as e:
                    return {'success': False, 'error': f"Tool execution error: {str(e)}"}
      - utils/
        - schema_formatter.py
            # [[RARO]]/apps/agent-service/src/utils/schema_formatter.py
            # Purpose: JSON Schema Extraction Helper
            # Architecture: Utility Layer
            
            import json
            from pydantic import BaseModel
            from typing import Type
            
            def get_clean_schema_json(model_class: Type[BaseModel]) -> str:
                """
                Extracts a clean JSON schema from a Pydantic model.
                Removes extraneous 'definitions' if possible to save tokens.
                """
                try:
                    # Generate the schema
                    schema = model_class.model_json_schema()
                    
                    # Serialize to pretty JSON
                    return json.dumps(schema, indent=2)
                except Exception as e:
                    return f"{{ 'error': 'Schema generation failed: {str(e)}' }}"
      - main.py
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
          from intelligence.tools import SandboxSession
          
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
          
          @app.delete("/runtime/{run_id}/cleanup")
          async def cleanup_runtime(run_id: str):
              """
              Called by Kernel when a workflow completes or fails.
              Destroys the persistent E2B sandbox to save resources.
              """
              logger.info(f"Received cleanup request for run {run_id}")
              try:
                  SandboxSession.kill_session(run_id)
                  return {"status": "cleaned", "run_id": run_id}
              except Exception as e:
                  logger.error(f"Cleanup failed: {e}")
                  return JSONResponse(status_code=500, content={"error": str(e)})
          
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
                  # 1. Call Unified LLM Module
                  # NOTE: Delegation injection now happens in llm.py based on allow_delegation flag
                  result = await call_gemini_with_context(
                      model=request.model,
                      prompt=request.prompt,  # Pass raw prompt, injection handled conditionally
                      user_directive=request.user_directive,  # Runtime task from operator
                      input_data=request.input_data,
                      file_paths=request.file_paths,
                      parent_signature=request.parent_signature,
                      thinking_level=request.thinking_level,
                      tools=request.tools,
                      agent_id=request.agent_id,
                      run_id=request.run_id,
                      # [[NEW PARAMETERS FROM KERNEL]]
                      allow_delegation=request.allow_delegation,
                      graph_view=request.graph_view,
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
                              "result": response_text, 
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
                          "artifact_stored": artifact_stored,
                          "files_generated": files_generated  # Pass files back so Kernel can promote them
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
    - .dockerignore
        __pycache__
        venv
        .env
        .git
        .DS_Store
    - Dockerfile
        FROM python:3.11-slim
        
        WORKDIR /app
        
        COPY requirements.txt .
        RUN pip install --no-cache-dir -r requirements.txt
        
        COPY src .
        
        EXPOSE 8000
        
        CMD ["python", "main.py"]
    - package.json
        {
          "name": "raro-agent-service",
          "version": "0.1.0",
          "private": true,
          "description": "Python-based agent service for RARO"
        }
    - requirements.txt
        fastapi>=0.104.1
        uvicorn>=0.24.0
        pydantic>=2.5.0
        pydantic-settings>=2.1.0
        google-genai>=0.0.0
        python-dotenv>=1.0.0
        aiohttp>=3.9.1
        grpcio>=1.59.0
        grpcio-tools>=1.59.0
        protobuf>=4.25.1
        httpx>=0.25.1
        redis>=0.0.0
        e2b_code_interpreter>=2.4.1
        tavily_python>=0.7.17
  - debug-probe/
    - src/
      - templates/
        - dashboard.html
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Debug Probe</title>
                <style>
                    * {
                        margin: 0;
                        padding: 0;
                        box-sizing: border-box;
                    }
            
                    :root {
                        --bg: #fafafa;
                        --surface: #ffffff;
                        --border: #e5e5e5;
                        --text: #171717;
                        --text-muted: #737373;
                        --accent: #0ea5e9;
                        --success: #22c55e;
                        --mono: ui-monospace, 'Cascadia Code', 'Source Code Pro', Menlo, Consolas, 'DejaVu Sans Mono', monospace;
                    }
            
                    @media (prefers-color-scheme: dark) {
                        :root {
                            --bg: #0a0a0a;
                            --surface: #171717;
                            --border: #262626;
                            --text: #fafafa;
                            --text-muted: #a3a3a3;
                        }
                    }
            
                    body {
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif;
                        background: var(--bg);
                        color: var(--text);
                        line-height: 1.6;
                        padding: 0;
                        margin: 0;
                    }
            
                    /* Header */
                    header {
                        background: var(--surface);
                        border-bottom: 1px solid var(--border);
                        padding: 1.5rem 2rem;
                        display: flex;
                        justify-content: space-between;
                        align-items: center;
                        position: sticky;
                        top: 0;
                        z-index: 100;
                        backdrop-filter: blur(8px);
                    }
            
                    .header-left h1 {
                        font-size: 1.125rem;
                        font-weight: 600;
                        color: var(--text);
                        margin-bottom: 0.25rem;
                    }
            
                    .status {
                        display: flex;
                        align-items: center;
                        gap: 0.5rem;
                        font-size: 0.8125rem;
                        color: var(--text-muted);
                        font-family: var(--mono);
                    }
            
                    .status-dot {
                        width: 6px;
                        height: 6px;
                        border-radius: 50%;
                        background: var(--text-muted);
                    }
            
                    .status-dot.live {
                        background: var(--success);
                        box-shadow: 0 0 8px var(--success);
                    }
            
                    button {
                        background: transparent;
                        border: 1px solid var(--border);
                        color: var(--text);
                        padding: 0.5rem 1rem;
                        border-radius: 6px;
                        cursor: pointer;
                        font-size: 0.875rem;
                        font-weight: 500;
                        transition: all 0.15s;
                    }
            
                    button:hover {
                        background: var(--bg);
                        border-color: var(--text-muted);
                    }
            
                    /* Main Content */
                    main {
                        max-width: 1400px;
                        margin: 0 auto;
                        padding: 2rem;
                    }
            
                    /* Capture Card */
                    .capture {
                        background: var(--surface);
                        border: 1px solid var(--border);
                        border-radius: 8px;
                        margin-bottom: 1.5rem;
                        overflow: hidden;
                        transition: border-color 0.2s;
                    }
            
                    .capture:hover {
                        border-color: var(--text-muted);
                    }
            
                    .capture-header {
                        padding: 1rem 1.5rem;
                        border-bottom: 1px solid var(--border);
                        display: flex;
                        justify-content: space-between;
                        align-items: center;
                        background: var(--bg);
                    }
            
                    .capture-meta {
                        display: flex;
                        gap: 1rem;
                        align-items: center;
                        font-size: 0.8125rem;
                        font-family: var(--mono);
                    }
            
                    .meta-item {
                        display: flex;
                        align-items: center;
                        gap: 0.375rem;
                        color: var(--text-muted);
                    }
            
                    .meta-label {
                        color: var(--text-muted);
                        text-transform: uppercase;
                        font-size: 0.6875rem;
                        letter-spacing: 0.05em;
                    }
            
                    .meta-value {
                        color: var(--text);
                        font-weight: 500;
                    }
            
                    .agent-id {
                        color: var(--accent);
                        font-weight: 600;
                    }
            
                    .tools-badge {
                        display: flex;
                        gap: 0.375rem;
                        flex-wrap: wrap;
                    }
            
                    .tool {
                        background: var(--bg);
                        border: 1px solid var(--border);
                        padding: 0.125rem 0.5rem;
                        border-radius: 4px;
                        font-size: 0.6875rem;
                        font-family: var(--mono);
                        color: var(--text-muted);
                    }
            
                    /* Capture Body */
                    .capture-body {
                        display: grid;
                        grid-template-columns: 1fr 1fr;
                        gap: 0;
                    }
            
                    .section {
                        padding: 1.5rem;
                    }
            
                    .section.left {
                        border-right: 1px solid var(--border);
                    }
            
                    .section-title {
                        font-size: 0.6875rem;
                        text-transform: uppercase;
                        letter-spacing: 0.05em;
                        color: var(--text-muted);
                        margin-bottom: 1rem;
                        font-weight: 600;
                    }
            
                    .content {
                        background: var(--bg);
                        border: 1px solid var(--border);
                        border-radius: 6px;
                        padding: 1rem;
                        overflow-x: auto;
                    }
            
                    pre {
                        font-family: var(--mono);
                        font-size: 0.8125rem;
                        line-height: 1.6;
                        color: var(--text);
                        white-space: pre-wrap;
                        word-wrap: break-word;
                        margin: 0;
                    }
            
                    /* Empty State */
                    .empty {
                        text-align: center;
                        padding: 4rem 2rem;
                        color: var(--text-muted);
                    }
            
                    .empty-icon {
                        font-size: 2.5rem;
                        margin-bottom: 1rem;
                        opacity: 0.3;
                    }
            
                    .empty-text {
                        font-size: 0.875rem;
                    }
            
                    /* Responsive */
                    @media (max-width: 1024px) {
                        .capture-body {
                            grid-template-columns: 1fr;
                        }
            
                        .section.left {
                            border-right: none;
                            border-bottom: 1px solid var(--border);
                        }
                    }
            
                    @media (max-width: 640px) {
                        header {
                            padding: 1rem;
                        }
            
                        main {
                            padding: 1rem;
                        }
            
                        .capture-header {
                            flex-direction: column;
                            align-items: flex-start;
                            gap: 0.75rem;
                        }
            
                        .capture-meta {
                            flex-wrap: wrap;
                            gap: 0.5rem;
                        }
                    }
            
                    /* Animations */
                    @keyframes slideIn {
                        from {
                            opacity: 0;
                            transform: translateY(-8px);
                        }
                        to {
                            opacity: 1;
                            transform: translateY(0);
                        }
                    }
            
                    .capture.new {
                        animation: slideIn 0.3s ease-out;
                    }
                </style>
            </head>
            <body>
                <header>
                    <div class="header-left">
                        <h1>Debug Probe</h1>
                        <div class="status">
                            <span class="status-dot" id="status-dot"></span>
                            <span id="status-text">Connecting...</span>
                        </div>
                    </div>
                    <button onclick="clearLogs()">Clear</button>
                </header>
            
                <main>
                    {% if captures %}
                        {% for log in captures %}
                        <div class="capture">
                            <div class="capture-header">
                                <div class="capture-meta">
                                    <div class="meta-item">
                                        <span class="meta-label">Agent</span>
                                        <span class="agent-id">{{ log.agent_id }}</span>
                                    </div>
                                    <div class="meta-item">
                                        <span class="meta-label">Run</span>
                                        <span class="meta-value">{{ log.run_id[:8] }}</span>
                                    </div>
                                    <div class="meta-item">
                                        <span class="meta-label">Time</span>
                                        <span class="meta-value">{{ log.time.split('T')[1].split('.')[0] if 'T' in log.time else log.time }}</span>
                                    </div>
                                </div>
                                {% if log.tools %}
                                <div class="tools-badge">
                                    {% for tool in log.tools %}
                                    <span class="tool">{{ tool }}</span>
                                    {% endfor %}
                                </div>
                                {% endif %}
                            </div>
                            <div class="capture-body">
                                <div class="section left">
                                    <div class="section-title">System Instruction</div>
                                    <div class="content">
                                        <pre>{{ log.final_system_prompt }}</pre>
                                    </div>
                                </div>
                                <div class="section">
                                    <div class="section-title">User Message</div>
                                    <div class="content">
                                        <pre>{{ log.final_user_message }}</pre>
                                    </div>
                                </div>
                            </div>
                        </div>
                        {% endfor %}
                    {% else %}
                    <div class="empty">
                        <div class="empty-icon">⏳</div>
                        <div class="empty-text">Waiting for captures...</div>
                    </div>
                    {% endif %}
                </main>
            
                <script>
                    const eventSource = new EventSource('/events');
                    let connectionAttempts = 0;
            
                    eventSource.onopen = function() {
                        connectionAttempts = 0;
                        updateStatus(true, 'Live');
                    };
            
                    eventSource.onmessage = function(event) {
                        const capture = JSON.parse(event.data);
                        addCapture(capture);
                    };
            
                    eventSource.onerror = function() {
                        connectionAttempts++;
                        if (connectionAttempts > 1) {
                            updateStatus(false, 'Reconnecting...');
                        }
                    };
            
                    function updateStatus(isLive, text) {
                        const dot = document.getElementById('status-dot');
                        const statusText = document.getElementById('status-text');
            
                        if (isLive) {
                            dot.classList.add('live');
                        } else {
                            dot.classList.remove('live');
                        }
            
                        if (statusText) {
                            statusText.textContent = text;
                        }
                    }
            
                    function addCapture(data) {
                        const main = document.querySelector('main');
            
                        // Remove empty state
                        const empty = main.querySelector('.empty');
                        if (empty) empty.remove();
            
                        // Format time
                        const time = data.time.includes('T')
                            ? data.time.split('T')[1].split('.')[0]
                            : data.time;
            
                        // Create capture card
                        const capture = document.createElement('div');
                        capture.className = 'capture new';
                        capture.innerHTML = `
                            <div class="capture-header">
                                <div class="capture-meta">
                                    <div class="meta-item">
                                        <span class="meta-label">Agent</span>
                                        <span class="agent-id">${escape(data.agent_id)}</span>
                                    </div>
                                    <div class="meta-item">
                                        <span class="meta-label">Run</span>
                                        <span class="meta-value">${escape(data.run_id.substring(0, 8))}</span>
                                    </div>
                                    <div class="meta-item">
                                        <span class="meta-label">Time</span>
                                        <span class="meta-value">${escape(time)}</span>
                                    </div>
                                </div>
                                ${data.tools && data.tools.length > 0 ? `
                                <div class="tools-badge">
                                    ${data.tools.map(tool => `<span class="tool">${escape(tool)}</span>`).join('')}
                                </div>
                                ` : ''}
                            </div>
                            <div class="capture-body">
                                <div class="section left">
                                    <div class="section-title">System Instruction</div>
                                    <div class="content">
                                        <pre>${escape(data.final_system_prompt)}</pre>
                                    </div>
                                </div>
                                <div class="section">
                                    <div class="section-title">User Message</div>
                                    <div class="content">
                                        <pre>${escape(data.final_user_message)}</pre>
                                    </div>
                                </div>
                            </div>
                        `;
            
                        main.insertBefore(capture, main.firstChild);
                    }
            
                    function escape(text) {
                        const div = document.createElement('div');
                        div.textContent = text;
                        return div.innerHTML;
                    }
            
                    async function clearLogs() {
                        await fetch('/clear', { method: 'POST' });
                        location.reload();
                    }
                </script>
            </body>
            </html>
      - main.py
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
    - Dockerfile
        FROM python:3.11-slim
        WORKDIR /app
        COPY requirements.txt .
        RUN pip install --no-cache-dir -r requirements.txt
        COPY src .
        EXPOSE 8080
        CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
    - requirements.txt
        fastapi
        uvicorn
        jinja2
        pydantic
  - kernel-server/
    - config/
      - cortex_patterns.json
          [
            {
              "id": "guard_fs_delete",
              "name": "Prevent File Deletion",
              "trigger_event": "ToolCall",
              "condition": "fs_delete",
              "action": {
                "Interrupt": {
                  "reason": "Safety Violation: File deletion is prohibited by system policy."
                }
              }
            },
            {
              "id": "guard_max_failures",
              "name": "Max Failure Guard",
              "trigger_event": "AgentFailed",
              "condition": "*",
              "action": {
                "RequestApproval": {
                  "reason": "Agent failed. Requesting human intervention before retry."
                }
              }
            },
            {
              "id": "guard_sudo_access",
              "name": "Prevent Root Access",
              "trigger_event": "ToolCall",
              "condition": "sudo",
              "action": {
                "Interrupt": {
                  "reason": "Root access attempts are strictly forbidden."
                }
              }
            }
          ]
    - src/
      - server/
        - handlers.rs
            // [[RARO]]/apps/kernel-server/src/server/handlers.rs
            // Purpose: API Handlers. updated to allow async spawning of workflows.
            // Architecture: API Layer
            // Dependencies: Axum, Runtime
            
            use axum::{
                extract::{Path, State, Json, Query, Multipart, ws::{WebSocket, WebSocketUpgrade}},
                http::StatusCode,
                response::IntoResponse,
            };
            use serde_json::json;
            use std::sync::Arc;
            use futures::{sink::SinkExt, stream::StreamExt};
            use axum::extract::ws::Message;
            use axum::body::Body;
            use tokio_util::io::ReaderStream; // You might need: cargo add tokio-util
            use redis::AsyncCommands;
            
            use crate::models::*;
            use crate::runtime::{RARORuntime, InvocationPayload};
            use crate::fs_manager::{WorkspaceInitializer, ArtifactMetadata}; // Import the manager and metadata
            
            use tokio::fs; // For listing library files
            
            #[derive(serde::Deserialize)]
            pub struct RunQuery {
                run_id: Option<String>,
            }
            
            #[derive(serde::Serialize)]
            pub struct HealthResponse {
                status: String,
                message: String,
            }
            
            pub async fn health() -> Json<HealthResponse> {
                Json(HealthResponse {
                    status: "ok".to_string(),
                    message: "RARO Kernel Server is running".to_string(),
                })
            }
            
            // GET /runtime/:run_id/files/:filename
            pub async fn serve_session_file(
                Path((run_id, filename)): Path<(String, String)>,
            ) -> Result<impl IntoResponse, StatusCode> {
                // 1. Sanitize (Basic security)
                if filename.contains("..") || filename.starts_with("/") {
                    return Err(StatusCode::FORBIDDEN);
                }
            
                // 2. Construct Path (Targeting the RFS Output directory)
                let file_path = format!("/app/storage/sessions/{}/output/{}", run_id, filename);
                let path = std::path::Path::new(&file_path);
            
                // 3. Verify Existence
                if !path.exists() {
                    return Err(StatusCode::NOT_FOUND);
                }
            
                // 4. Open and Stream
                let file = match tokio::fs::File::open(path).await {
                    Ok(file) => file,
                    Err(_) => return Err(StatusCode::INTERNAL_SERVER_ERROR),
                };
            
                let stream = ReaderStream::new(file);
                let body = Body::from_stream(stream);
            
                // 5. Determine Content Type (Simple guess)
                let content_type = if filename.ends_with(".png") { "image/png" }
                else if filename.ends_with(".jpg") { "image/jpeg" }
                else if filename.ends_with(".csv") { "text/csv" }
                else if filename.ends_with(".txt") { "text/plain" }
                else { "application/octet-stream" };
            
                let headers = [
                    ("Content-Type", content_type),
                    ("Cache-Control", "public, max-age=3600"),
                ];
            
                Ok((headers, body))
            }
            
            // === NEW HANDLER: LIST LIBRARY FILES ===
            // GET /runtime/library
            pub async fn list_library_files() -> Result<Json<serde_json::Value>, StatusCode> {
                // Hardcoded path to the library volume
                let path = "/app/storage/library";
                // Check if directory exists, if not, create it
                if !std::path::Path::new(path).exists() {
                    tracing::info!("Library directory missing. Creating: {}", path);
                    fs::create_dir_all(path).await.map_err(|e| {
                        tracing::error!("Failed to create library dir: {}", e);
                        StatusCode::INTERNAL_SERVER_ERROR
                    })?;
                }
                // Read dir
                let mut entries = fs::read_dir(path).await.map_err(|e| {
                    tracing::error!("Failed to read library dir: {}", e);
                    // If dir doesn't exist, try to create it silently or return empty
                    StatusCode::INTERNAL_SERVER_ERROR
                })?;
            
                let mut files = Vec::new();
            
                while let Ok(Some(entry)) = entries.next_entry().await {
                    if let Ok(file_type) = entry.file_type().await {
                        if file_type.is_file() {
                            if let Ok(name) = entry.file_name().into_string() {
                                // Filter out hidden files like .keep
                                if !name.starts_with('.') {
                                    files.push(name);
                                }
                            }
                        }
                    }
                }
            
                Ok(Json(serde_json::json!({
                    "files": files
                })))
            }
            
            // === NEW HANDLER: UPLOAD FILE ===
            // POST /runtime/library/upload
            pub async fn upload_library_file(
                mut multipart: Multipart
            ) -> Result<Json<serde_json::Value>, StatusCode> {
                while let Some(field) = multipart.next_field().await.map_err(|_| StatusCode::BAD_REQUEST)? {
                    let name = field.file_name().unwrap_or("unknown_file").to_string();
                    
                    // Read the bytes
                    let data = field.bytes().await.map_err(|e| {
                        tracing::error!("Failed to read upload bytes: {}", e);
                        StatusCode::INTERNAL_SERVER_ERROR
                    })?;
            
                    // Save using fs_manager
                    if let Err(e) = WorkspaceInitializer::save_to_library(&name, &data).await {
                        tracing::error!("Failed to write file to disk: {}", e);
                        return Err(StatusCode::INTERNAL_SERVER_ERROR);
                    }
                }
            
                Ok(Json(serde_json::json!({
                    "success": true,
                    "message": "Upload complete"
                })))
            }
            
            pub async fn start_workflow(
                State(runtime): State<Arc<RARORuntime>>,
                Json(config): Json<WorkflowConfig>,
            ) -> Result<Json<serde_json::Value>, StatusCode> {
                // start_workflow now spawns the task internally and returns the run_id immediately
                match runtime.start_workflow(config) {
                    Ok(run_id) => Ok(Json(json!({
                        "success": true,
                        "run_id": run_id
                    }))),
                    Err(e) => {
                        tracing::error!("Failed to start workflow: {}", e);
                        Err(StatusCode::BAD_REQUEST)
                    }
                }
            }
            
            pub async fn resume_run(
                State(runtime): State<Arc<RARORuntime>>,
                Path(run_id): Path<String>
            ) -> StatusCode {
                // 1. Verify currently paused
                let is_paused = runtime.get_state(&run_id)
                    .map(|s| s.status == RuntimeStatus::AwaitingApproval)
                    .unwrap_or(false);
            
                if !is_paused {
                    tracing::warn!("Resume called on non-paused run: {}", run_id);
                    return StatusCode::BAD_REQUEST;
                }
            
                // 2. Flip to Running
                runtime.set_run_status(&run_id, RuntimeStatus::Running);
            
                // 3. RESPAWN THE EXECUTION LOOP
                // This is the critical piece. We fire the engine again.
                let rt_clone = runtime.clone();
                let rid_clone = run_id.clone();
                tokio::spawn(async move {
                    rt_clone.execute_dynamic_dag(rid_clone).await;
                });
            
                // 4. Emit event for UI to update logs
                runtime.emit_event(crate::events::RuntimeEvent::new(
                    &run_id,
                    crate::events::EventType::SystemIntervention,
                    None,
                    serde_json::json!({ "action": "resume", "reason": "User approved execution" })
                ));
            
                tracing::info!("Run {} resumed by user", run_id);
                StatusCode::OK
            }
            
            pub async fn stop_run(
                State(runtime): State<Arc<RARORuntime>>, 
                Path(run_id): Path<String>
            ) -> StatusCode {
                runtime.fail_run(&run_id, "OPERATOR", "Manual Stop").await;
                StatusCode::OK
            }
            
            
            pub async fn get_runtime_state(
                State(runtime): State<Arc<RARORuntime>>,
                Query(query): Query<RunQuery>,
            ) -> Result<Json<RuntimeState>, StatusCode> {
                let run_id = query.run_id.ok_or(StatusCode::BAD_REQUEST)?;
            
                runtime
                    .get_state(&run_id)
                    .ok_or(StatusCode::NOT_FOUND)
                    .map(Json)
            }
            
            pub async fn invoke_agent(
                State(runtime): State<Arc<RARORuntime>>,
                Path((run_id, agent_id)): Path<(String, String)>,
            ) -> Result<Json<InvocationPayload>, StatusCode> {
                tracing::info!("Preparing invocation for agent: {} in run: {}", agent_id, run_id);
            
                // CHANGE: Added .await
                runtime
                    .prepare_invocation_payload(&run_id, &agent_id)
                    .await 
                    .map(Json)
                    .map_err(|e| {
                        tracing::error!("Failed to prepare invocation: {}", e);
                        StatusCode::NOT_FOUND
                    })
            }
            
            pub async fn get_signatures(
                State(runtime): State<Arc<RARORuntime>>,
                Query(query): Query<RunQuery>,
            ) -> Result<Json<serde_json::Value>, StatusCode> {
                let run_id = query.run_id.ok_or(StatusCode::BAD_REQUEST)?;
            
                let signatures = runtime
                    .get_all_signatures(&run_id)
                    .ok_or(StatusCode::NOT_FOUND)?;
            
                Ok(Json(json!({
                    "run_id": run_id,
                    "signatures": signatures.signatures
                })))
            }
            
            pub async fn get_artifact(
                State(runtime): State<Arc<RARORuntime>>,
                Path((run_id, agent_id)): Path<(String, String)>,
            ) -> Result<Json<serde_json::Value>, StatusCode> {
                tracing::debug!("Fetching artifact for run={}, agent={}", run_id, agent_id);
            
                let client = runtime
                    .redis_client
                    .as_ref()
                    .ok_or(StatusCode::SERVICE_UNAVAILABLE)?;
            
                let key = format!("run:{}:agent:{}:output", run_id, agent_id);
            
                let mut con = client
                    .get_async_connection()
                    .await
                    .map_err(|e| {
                        tracing::error!("Redis connection failed: {}", e);
                        StatusCode::INTERNAL_SERVER_ERROR
                    })?;
            
                let data: String = con.get(&key).await.map_err(|e| {
                    tracing::warn!("Artifact not found in Redis: {} ({})", key, e);
                    StatusCode::NOT_FOUND
                })?;
            
                let json_val: serde_json::Value = serde_json::from_str(&data).map_err(|e| {
                    tracing::error!("Failed to parse artifact JSON: {}", e);
                    StatusCode::INTERNAL_SERVER_ERROR
                })?;
            
                Ok(Json(json_val))
            }
            
            pub async fn ws_runtime_stream(
                State(runtime): State<Arc<RARORuntime>>,
                Path(run_id): Path<String>,
                ws: WebSocketUpgrade,
            ) -> impl IntoResponse {
                ws.on_upgrade(move |socket| handle_runtime_stream(socket, runtime, run_id))
            }
            
            async fn handle_runtime_stream(
                socket: WebSocket,
                runtime: Arc<RARORuntime>,
                run_id: String,
            ) {
                let (mut sender, mut receiver) = socket.split();
            
                // Wait briefly for state to be initialized if called immediately after start
                if runtime.get_state(&run_id).is_none() {
                    tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                }
            
                // Verify run exists
                if runtime.get_state(&run_id).is_none() {
                    let _ = sender
                        .send(Message::Text(
                            json!({"error": "Run not found"}).to_string(),
                        ))
                        .await;
                    return;
                }
            
                // Send initial state
                if let Some(state) = runtime.get_state(&run_id) {
                    let _ = sender
                        .send(Message::Text(
                            serde_json::to_string(&json!({
                                "type": "state_update",
                                "state": state,
                                "timestamp": chrono::Utc::now().to_rfc3339()
                            }))
                            .unwrap(),
                        ))
                        .await;
                }
            
                // Stream updates
                let mut interval = tokio::time::interval(std::time::Duration::from_millis(250));
            
                // Subscribe to event bus for real-time logs
                let mut bus_rx = runtime.event_bus.subscribe();
            
                loop {
                    tokio::select! {
                        // Check for client disconnect
                        msg = receiver.next() => {
                            if msg.is_none() {
                                tracing::info!("Client disconnected from runtime stream: {}", run_id);
                                break;
                            }
                        }
            
                        // Send periodic updates
                        _ = interval.tick() => {
                            if let Some(state) = runtime.get_state(&run_id) {
                                
                                // === NEW: Fetch Topology ===
                                let topology = runtime.get_topology_snapshot(&run_id);
                                
                                let update = json!({
                                    "type": "state_update",
                                    "state": state,
                                    "signatures": runtime.get_all_signatures(&run_id).map(|s| s.signatures),
                                    "topology": topology, // <--- THE BRIDGE
                                    "timestamp": chrono::Utc::now().to_rfc3339()
                                });
            
                                if sender.send(Message::Text(update.to_string())).await.is_err() {
                                    tracing::info!("Failed to send state update, client disconnected");
                                    break;
                                }
                                
                                // === FIX START ===
                                // Check for terminal states to auto-close connection
                                if state.status == RuntimeStatus::Completed || state.status == RuntimeStatus::Failed {
                                    tracing::info!("Run {} reached terminal state: {:?}. Closing stream.", run_id, state.status);
                                    
                                    // Optional: Small delay to ensure client processes the final message before close frame
                                    tokio::time::sleep(std::time::Duration::from_millis(500)).await;
                                    
                                    // Send a Close frame explicitly (optional, breaking loop also works)
                                    let _ = sender.close().await;
                                    break;
                                }
                                // === FIX END ===
                            }
                        }
            
                        // Forward real-time events from event bus
                        Ok(event) = bus_rx.recv() => {
                            // Only forward events for THIS run
                            if event.run_id == run_id {
                                // Filter for intermediate logs
                                if let crate::events::EventType::IntermediateLog = event.event_type {
                                    let ws_msg = json!({
                                        "type": "log_event",
                                        "agent_id": event.agent_id,
                                        "payload": event.payload,
                                        "timestamp": event.timestamp
                                    });
            
                                    if sender.send(Message::Text(ws_msg.to_string())).await.is_err() {
                                        tracing::info!("Failed to send log event, client disconnected");
                                        break;
                                    }
                                }
                            }
                        }
                    }
                }
            }
            
            // === ARTIFACT STORAGE HANDLERS ===
            
            /// GET /runtime/artifacts
            /// Lists all artifact runs with their metadata
            pub async fn list_all_artifacts() -> Result<Json<serde_json::Value>, StatusCode> {
                let runs = WorkspaceInitializer::list_artifact_runs()
                    .await
                    .map_err(|e| {
                        tracing::error!("Failed to list artifact runs: {}", e);
                        StatusCode::INTERNAL_SERVER_ERROR
                    })?;
            
                let mut artifacts = Vec::new();
            
                for run_id in runs {
                    if let Ok(metadata) = WorkspaceInitializer::get_artifact_metadata(&run_id).await {
                        artifacts.push(json!({
                            "run_id": run_id,
                            "metadata": metadata
                        }));
                    }
                }
            
                Ok(Json(json!({ "artifacts": artifacts })))
            }
            
            /// GET /runtime/artifacts/:run_id
            /// Gets metadata for a specific run's artifacts
            pub async fn get_run_artifacts(
                Path(run_id): Path<String>,
            ) -> Result<Json<ArtifactMetadata>, StatusCode> {
                WorkspaceInitializer::get_artifact_metadata(&run_id)
                    .await
                    .map(Json)
                    .map_err(|e| {
                        tracing::warn!("Artifact metadata not found for run {}: {}", run_id, e);
                        StatusCode::NOT_FOUND
                    })
            }
            
            /// GET /runtime/artifacts/:run_id/files/:filename
            /// Serves a specific artifact file from persistent storage
            pub async fn serve_artifact_file(
                Path((run_id, filename)): Path<(String, String)>,
            ) -> Result<impl IntoResponse, StatusCode> {
                // 1. Sanitize (prevent path traversal)
                if filename.contains("..") || filename.starts_with("/") {
                    tracing::warn!("Blocked suspicious artifact filename: {}", filename);
                    return Err(StatusCode::FORBIDDEN);
                }
            
                // 2. Construct path to artifacts storage
                let file_path = format!("/app/storage/artifacts/{}/{}", run_id, filename);
                let path = std::path::Path::new(&file_path);
            
                // 3. Verify existence
                if !path.exists() {
                    tracing::debug!("Artifact file not found: {}", file_path);
                    return Err(StatusCode::NOT_FOUND);
                }
            
                // 4. Open and stream
                let file = tokio::fs::File::open(path).await
                    .map_err(|e| {
                        tracing::error!("Failed to open artifact file {}: {}", file_path, e);
                        StatusCode::INTERNAL_SERVER_ERROR
                    })?;
            
                let stream = ReaderStream::new(file);
                let body = Body::from_stream(stream);
            
                // 5. Determine content type
                let content_type = if filename.ends_with(".png") { "image/png" }
                else if filename.ends_with(".jpg") || filename.ends_with(".jpeg") { "image/jpeg" }
                else if filename.ends_with(".csv") { "text/csv" }
                else if filename.ends_with(".json") { "application/json" }
                else if filename.ends_with(".md") { "text/markdown" }
                else if filename.ends_with(".txt") { "text/plain" }
                else if filename.ends_with(".json") { "application/json" }
                else { "application/octet-stream" };
            
            
                let headers = [
                    ("Content-Type", content_type),
                    ("Cache-Control", "public, max-age=86400"), // 24-hour cache
                ];
            
                Ok((headers, body))
            }
            
            /// DELETE /runtime/artifacts/:run_id
            /// Deletes all artifacts for a specific run
            pub async fn delete_artifact_run(
                Path(run_id): Path<String>,
            ) -> Result<StatusCode, StatusCode> {
                let path = format!("/app/storage/artifacts/{}", run_id);
            
                tokio::fs::remove_dir_all(&path)
                    .await
                    .map_err(|e| {
                        tracing::error!("Failed to delete artifact run {}: {}", run_id, e);
                        StatusCode::INTERNAL_SERVER_ERROR
                    })?;
            
                tracing::info!("Deleted artifact run: {}", run_id);
                Ok(StatusCode::NO_CONTENT)
            }
            
            /// POST /runtime/artifacts/:run_id/files/:filename/promote
            /// Promotes an artifact to permanent library storage
            pub async fn promote_artifact_to_library(
                Path((run_id, filename)): Path<(String, String)>,
            ) -> Result<StatusCode, StatusCode> {
                // Sanitize filename
                if filename.contains("..") || filename.starts_with("/") {
                    return Err(StatusCode::FORBIDDEN);
                }
            
                let src = format!("/app/storage/artifacts/{}/{}", run_id, filename);
                let dst = format!("/app/storage/library/{}", filename);
            
                // Check if source exists
                if !std::path::Path::new(&src).exists() {
                    return Err(StatusCode::NOT_FOUND);
                }
            
                // Copy to library
                tokio::fs::copy(&src, &dst)
                    .await
                    .map_err(|e| {
                        tracing::error!("Failed to promote artifact {} to library: {}", filename, e);
                        StatusCode::INTERNAL_SERVER_ERROR
                    })?;
            
                tracing::info!("Promoted artifact {} from run {} to library", filename, run_id);
                Ok(StatusCode::CREATED)
            }
      - dag.rs
          // [[RARO]]/apps/kernel-server/src/dag.rs
          // Purpose: DAG Data Structure. Updated with mutation methods for dynamic graph splicing.
          // Architecture: Core Data Structure
          // Dependencies: std, thiserror
          
          use std::collections::{HashMap, HashSet, VecDeque};
          use thiserror::Error;
          
          #[derive(Error, Debug)]
          pub enum DAGError {
              #[error("Cycle detected in DAG")]
              CycleDetected,
              #[error("Invalid node: {0}")]
              InvalidNode(String),
              #[error("Dependency not found: {0}")]
              DependencyNotFound(String),
              #[error("Edge not found: {0} -> {1}")]
              EdgeNotFound(String, String),
          }
          
          #[derive(Clone, Debug)] // Added Clone/Debug for easier state management
          pub struct DAG {
              nodes: HashSet<String>,
              edges: HashMap<String, Vec<String>>, // Adjacency list: Source -> [Targets]
          }
          
          impl DAG {
              pub fn new() -> Self {
                  DAG {
                      nodes: HashSet::new(),
                      edges: HashMap::new(),
                  }
              }
          
              /// Add a node to the DAG
              pub fn add_node(&mut self, node_id: String) -> Result<(), DAGError> {
                  self.nodes.insert(node_id);
                  Ok(())
              }
          
              /// Add an edge from source to target
              pub fn add_edge(&mut self, from: String, to: String) -> Result<(), DAGError> {
                  if !self.nodes.contains(&from) {
                      return Err(DAGError::InvalidNode(from));
                  }
                  if !self.nodes.contains(&to) {
                      return Err(DAGError::InvalidNode(to));
                  }
          
                  // Check for cycle before adding
                  if self.would_create_cycle(&from, &to) {
                      return Err(DAGError::CycleDetected);
                  }
          
                  self.edges.entry(from).or_insert_with(Vec::new).push(to);
                  Ok(())
              }
          
              /// Remove an edge from source to target (Required for splicing)
              pub fn remove_edge(&mut self, from: &str, to: &str) -> Result<(), DAGError> {
                  if let Some(targets) = self.edges.get_mut(from) {
                      if let Some(pos) = targets.iter().position(|x| x == to) {
                          targets.remove(pos);
                          return Ok(());
                      }
                  }
                  Err(DAGError::EdgeNotFound(from.to_string(), to.to_string()))
              }
          
              /// Get all direct children (dependents) of a node
              pub fn get_children(&self, node_id: &str) -> Vec<String> {
                  self.edges.get(node_id).cloned().unwrap_or_default()
              }
          
              /// Check if adding edge would create a cycle
              fn would_create_cycle(&self, from: &str, to: &str) -> bool {
                  // DFS from 'to' to see if we can reach 'from'
                  let mut visited = HashSet::new();
                  self.has_path_dfs(to, from, &mut visited)
              }
          
              fn has_path_dfs(
                  &self,
                  current: &str,
                  target: &str,
                  visited: &mut HashSet<String>,
              ) -> bool {
                  if current == target {
                      return true;
                  }
          
                  if visited.contains(current) {
                      return false;
                  }
          
                  visited.insert(current.to_string());
          
                  if let Some(neighbors) = self.edges.get(current) {
                      for neighbor in neighbors {
                          if self.has_path_dfs(neighbor, target, visited) {
                              return true;
                          }
                      }
                  }
          
                  false
              }
          
              /// Compute topological order for execution
              /// This is now used dynamically to recalculate the path after mutation
              pub fn topological_sort(&self) -> Result<Vec<String>, DAGError> {
                  let mut in_degree: HashMap<String, usize> = self.nodes.iter().map(|n| (n.clone(), 0)).collect();
          
                  for neighbors in self.edges.values() {
                      for neighbor in neighbors {
                          *in_degree.get_mut(neighbor).unwrap() += 1;
                      }
                  }
          
                  let mut queue: VecDeque<String> = in_degree
                      .iter()
                      .filter(|(_, &degree)| degree == 0)
                      .map(|(node, _)| node.clone())
                      .collect();
          
                  let mut result = Vec::new();
          
                  while let Some(node) = queue.pop_front() {
                      result.push(node.clone());
          
                      if let Some(neighbors) = self.edges.get(&node) {
                          for neighbor in neighbors {
                              let degree = in_degree.get_mut(neighbor).unwrap();
                              *degree -= 1;
                              if *degree == 0 {
                                  queue.push_back(neighbor.clone());
                              }
                          }
                      }
                  }
          
                  if result.len() != self.nodes.len() {
                      return Err(DAGError::CycleDetected);
                  }
          
                  Ok(result)
              }
          
              /// Get dependencies for a given node (Reverse lookup)
              pub fn get_dependencies(&self, node_id: &str) -> Vec<String> {
                  let mut deps = Vec::new();
                  for (source, targets) in &self.edges {
                      if targets.contains(&node_id.to_string()) {
                          deps.push(source.clone());
                      }
                  }
                  deps
              }
              
              /// Export edges as a flat vector for UI visualization
              pub fn export_edges(&self) -> Vec<(String, String)> {
                  let mut edge_list = Vec::new();
                  for (source, targets) in &self.edges {
                      for target in targets {
                          edge_list.push((source.clone(), target.clone()));
                      }
                  }
                  edge_list
              }
          
              /// Export all known node IDs
              pub fn export_nodes(&self) -> Vec<String> {
                  self.nodes.iter().cloned().collect()
              }
          
              /// Get dependents for a given node
              pub fn get_dependents(&self, node_id: &str) -> Option<Vec<String>> {
                  self.edges.get(node_id).cloned()
              }
          }
          
          #[cfg(test)]
          mod tests {
              use super::*;
          
              #[test]
              fn test_topological_sort() {
                  let mut dag = DAG::new();
                  dag.add_node("a".to_string()).unwrap();
                  dag.add_node("b".to_string()).unwrap();
                  dag.add_node("c".to_string()).unwrap();
          
                  dag.add_edge("a".to_string(), "b".to_string()).unwrap();
                  dag.add_edge("b".to_string(), "c".to_string()).unwrap();
          
                  let order = dag.topological_sort().unwrap();
                  assert_eq!(order, vec!["a", "b", "c"]);
              }
          
              #[test]
              fn test_cycle_detection() {
                  let mut dag = DAG::new();
                  dag.add_node("a".to_string()).unwrap();
                  dag.add_node("b".to_string()).unwrap();
          
                  dag.add_edge("a".to_string(), "b".to_string()).unwrap();
                  let result = dag.add_edge("b".to_string(), "a".to_string());
          
                  assert!(result.is_err());
              }
          }
      - events.rs
          // [[RARO]]/apps/kernel-server/src/events.rs
          // Purpose: Event definitions for the Nervous System (Pattern Engine).
          // Architecture: Domain Event Layer
          // Dependencies: Serde, Chrono, Uuid
          
          use serde::{Deserialize, Serialize};
          use serde_json::Value;
          use chrono::Utc;
          
          #[derive(Debug, Clone, Serialize, Deserialize)]
          pub enum EventType {
              /// A new agent node has been added to the DAG (static or dynamic)
              NodeCreated,
              /// An agent started execution
              AgentStarted,
              /// An agent completed successfully
              AgentCompleted,
              /// An agent failed
              AgentFailed,
              /// An agent requested a tool (e.g., shell, python)
              ToolCall,
              /// A human/system intervention
              SystemIntervention,
              /// Real-time intermediate log from agent (tool calls, thoughts)
              IntermediateLog,
          }
          
          #[derive(Debug, Clone, Serialize, Deserialize)]
          pub struct RuntimeEvent {
              pub id: String,
              pub run_id: String,
              pub event_type: EventType,
              pub agent_id: Option<String>,
              pub timestamp: String,
              pub payload: Value,
          }
          
          impl RuntimeEvent {
              pub fn new(run_id: &str, event_type: EventType, agent_id: Option<String>, payload: Value) -> Self {
                  Self {
                      id: uuid::Uuid::new_v4().to_string(),
                      run_id: run_id.to_string(),
                      event_type,
                      agent_id,
                      timestamp: Utc::now().to_rfc3339(),
                      payload,
                  }
              }
          }
      - fs_manager.rs
          // [[RARO]]/apps/kernel-server/src/fs_manager.rs
          // Purpose: Manages file system operations for RFS (Raro File System).
          // Architecture: Infrastructure Helper Layer.
          // Dependencies: std::fs, std::path
          
          use std::fs;
          use std::path::Path;
          use std::io;
          use std::io::Write;
          use serde::{Serialize, Deserialize};
          use chrono::Utc; 
          
          // Hard anchor to prevent escaping the storage volume
          const STORAGE_ROOT: &str = "/app/storage";
          
          /// Metadata for artifact storage - tracks all files generated during a workflow run
          #[derive(Serialize, Deserialize, Clone)]
          pub struct ArtifactMetadata {
              pub run_id: String,
              pub workflow_id: String,
              pub user_directive: String,
              pub created_at: String,
              pub expires_at: String,
              pub artifacts: Vec<ArtifactFile>,
              pub status: String,
          }
          
          /// Individual file metadata within an artifact collection
          #[derive(Serialize, Deserialize, Clone)]
          pub struct ArtifactFile {
              pub filename: String,
              pub agent_id: String,
              pub generated_at: String,
              pub size_bytes: u64,
              pub content_type: String,
          }
          
          pub struct WorkspaceInitializer;
          
          impl WorkspaceInitializer {
              /// Initializes a new session workspace for a given run_id.
              /// Creates directory structure and copies requested files from the library.
              pub fn init_run_session(run_id: &str, library_files: Vec<String>) -> io::Result<()> {
                  let session_path = format!("{}/sessions/{}", STORAGE_ROOT, run_id);
                  let input_path = format!("{}/input", session_path);
                  let output_path = format!("{}/output", session_path);
          
                  // 1. Create Directories (Idempotent)
                  fs::create_dir_all(&input_path)?;
                  fs::create_dir_all(&output_path)?;
                  
                  tracing::info!("Created workspace for run {}: {}", run_id, session_path);
          
                  // 2. Copy requested files from Library -> Session Input
                  for filename in library_files {
                      let src = format!("{}/library/{}", STORAGE_ROOT, filename);
                      let dest = format!("{}/{}", input_path, filename);
                      
                      if Path::new(&src).exists() {
                          // We copy to ensure the run is an isolated snapshot
                          // Changes in session don't affect library
                          match fs::copy(&src, &dest) {
                              Ok(_) => tracing::info!("Attached file {} to run {}", filename, run_id),
                              Err(e) => tracing::error!("Failed to copy {}: {}", filename, e),
                          }
                      } else {
                          tracing::warn!("Requested file {} not found in library", filename);
                          // We log warning but don't fail the run; agent might handle missing file gracefully
                      }
                  }
          
                  Ok(())
              }
              
              /// Securely saves a byte buffer to the Library folder.
              pub async fn save_to_library(filename: &str, data: &[u8]) -> io::Result<()> {
                  // 1. Sanitize Filename (Basic)
                  let safe_name = Path::new(filename).file_name()
                      .ok_or(io::Error::new(io::ErrorKind::InvalidInput, "Invalid filename"))?
                      .to_string_lossy();
          
                  if safe_name.contains("..") || safe_name.starts_with("/") {
                       return Err(io::Error::new(io::ErrorKind::PermissionDenied, "Invalid path"));
                  }
          
                  // 2. Ensure Library Dir Exists
                  let lib_path = format!("{}/library", STORAGE_ROOT);
                  fs::create_dir_all(&lib_path)?;
          
                  // 3. Write File
                  let target_path = format!("{}/{}", lib_path, safe_name);
                  let mut file = fs::File::create(&target_path)?;
                  file.write_all(data)?;
                  
                  tracing::info!("Uploaded file to library: {}", target_path);
                  Ok(())
              }
          
              /// Optional: Cleanup routine for old sessions (commented until used)
              // pub fn cleanup_run(run_id: &str) -> io::Result<()> {
              //     let path = format!("{}/sessions/{}", STORAGE_ROOT, run_id);
              //     if Path::new(&path).exists() {
              //          fs::remove_dir_all(path)?;
              //          tracing::info!("Cleaned up workspace for run {}", run_id);
              //     }
              //     Ok(())
              // }
          
              /// Promotes agent-generated file from session output to persistent artifacts storage
              pub async fn promote_artifact_to_storage(
                  run_id: &str,
                  workflow_id: &str,
                  agent_id: &str,
                  filename: &str,
                  user_directive: &str,
              ) -> io::Result<()> {
                  // 1. Source: Session output
                  let src_path = format!("{}/sessions/{}/output/{}", STORAGE_ROOT, run_id, filename);
          
                  // 2. Destination: Artifacts directory (organized by run)
                  let artifacts_dir = format!("{}/artifacts/{}", STORAGE_ROOT, run_id);
                  fs::create_dir_all(&artifacts_dir)?;
          
                  let dest_path = format!("{}/{}", artifacts_dir, filename);
          
                  if !Path::new(&src_path).exists() {
                      return Err(io::Error::new(
                          io::ErrorKind::NotFound,
                          format!("Artifact {} not found in session output", filename)
                      ));
                  }
          
                  // 3. Copy file (keep session copy for integrity)
                  fs::copy(&src_path, &dest_path)?;
                  tracing::info!("Promoted artifact: {} → {}", src_path, dest_path);
          
                  // 4. Update/Create Metadata
                  let metadata_path = format!("{}/metadata.json", artifacts_dir);
                  let mut metadata = if Path::new(&metadata_path).exists() {
                      let data = fs::read_to_string(&metadata_path)?;
                      serde_json::from_str::<ArtifactMetadata>(&data)
                          .unwrap_or_else(|_| Self::create_new_metadata(run_id, workflow_id, user_directive))
                  } else {
                      Self::create_new_metadata(run_id, workflow_id, user_directive)
                  };
          
                  // 5. Add file entry
                  let file_meta = fs::metadata(&dest_path)?;
                  metadata.artifacts.push(ArtifactFile {
                      filename: filename.to_string(),
                      agent_id: agent_id.to_string(),
                      generated_at: Utc::now().to_rfc3339(),
                      size_bytes: file_meta.len(),
                      content_type: Self::guess_content_type(filename),
                  });
          
                  // 6. Write metadata
                  let json = serde_json::to_string_pretty(&metadata)?;
                  let mut meta_file = fs::File::create(&metadata_path)?;
                  meta_file.write_all(json.as_bytes())?;
          
                  Ok(())
              }
          
              /// Creates new artifact metadata for a workflow run
              fn create_new_metadata(run_id: &str, workflow_id: &str, user_directive: &str) -> ArtifactMetadata {
                  let now = Utc::now();
                  let expires = now + chrono::Duration::days(7); // 7-day retention
          
                  ArtifactMetadata {
                      run_id: run_id.to_string(),
                      workflow_id: workflow_id.to_string(),
                      user_directive: user_directive.to_string(),
                      created_at: now.to_rfc3339(),
                      expires_at: expires.to_rfc3339(),
                      artifacts: Vec::new(),
                      status: "active".to_string(),
                  }
              }
          
              /// Guesses MIME type from file extension
              fn guess_content_type(filename: &str) -> String {
                  if filename.ends_with(".png") { "image/png" }
                  else if filename.ends_with(".jpg") || filename.ends_with(".jpeg") { "image/jpeg" }
                  else if filename.ends_with(".csv") { "text/csv" }
                  else if filename.ends_with(".json") { "application/json" }
                  else if filename.ends_with(".md") { "text/markdown" }
                  else if filename.ends_with(".txt") { "text/plain" }
                  else { "application/octet-stream" }
                  .to_string()
              }
          
              /// List all artifact runs
              pub async fn list_artifact_runs() -> io::Result<Vec<String>> {
                  let artifacts_root = format!("{}/artifacts", STORAGE_ROOT);
                  if !Path::new(&artifacts_root).exists() {
                      return Ok(Vec::new());
                  }
          
                  let entries = fs::read_dir(&artifacts_root)?;
                  let mut runs = Vec::new();
          
                  for entry in entries {
                      if let Ok(entry) = entry {
                          if entry.file_type()?.is_dir() {
                              if let Ok(name) = entry.file_name().into_string() {
                                  runs.push(name);
                              }
                          }
                      }
                  }
          
                  Ok(runs)
              }
          
              /// Get metadata for a specific run's artifacts
              pub async fn get_artifact_metadata(run_id: &str) -> io::Result<ArtifactMetadata> {
                  let path = format!("{}/artifacts/{}/metadata.json", STORAGE_ROOT, run_id);
                  let data = fs::read_to_string(&path)?;
                  serde_json::from_str(&data)
                      .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))
              }
          }
      - main.rs
          // [[RARO]]/apps/kernel-server/src/main.rs
          // Purpose: Entry point. Invokes state hydration before starting the server.
          // Architecture: Application Boot
          // Dependencies: Axum, Tower, Tokio
          
          mod dag;
          mod models;
          mod server;
          mod runtime;
          mod observability;
          mod events;
          mod registry;
          mod fs_manager; // Register new module
          
          use axum::{
              Router,
              routing::{get, post},
              http::Method,
          };
          use std::sync::Arc;
          use tower_http::cors::{CorsLayer, Any};
          use tracing_subscriber;
          use futures::StreamExt;  // For Redis PubSub stream
          
          use crate::runtime::RARORuntime;
          use crate::server::handlers;
          
          #[tokio::main]
          async fn main() {
              // Initialize tracing
              tracing_subscriber::fmt()
                  .with_env_filter(
                      tracing_subscriber::EnvFilter::from_default_env()
                          .add_directive("raro_kernel=debug".parse().unwrap())
                          .add_directive("tower_http=trace".parse().unwrap()),
                  )
                  .init();
          
              tracing::info!("Initializing RARO Kernel...");
          
              let runtime = Arc::new(RARORuntime::new());
          
              // === PERSISTENCE RECOVERY ===
              // Attempt to load previous run states from Redis into memory
              runtime.rehydrate_from_redis().await;
          
              // === CORTEX: Pattern Engine ===
              // Subscribe to the event bus and spawn background pattern matcher
              let mut rx = runtime.event_bus.subscribe();
              let runtime_ref = runtime.clone();
          
              tokio::spawn(async move {
                  tracing::info!("Cortex Pattern Engine started");
                  loop {
                      if let Ok(event) = rx.recv().await {
                          // 1. Find matching patterns
                          let patterns = runtime_ref.pattern_registry.get_patterns_for_trigger(&format!("{:?}", event.event_type));
          
                          for pattern in patterns {
                              // 2. Evaluate Condition (Simple string match for MVP)
                              // In Phase 4, we use a real JSONPath engine here.
                              let condition_met = if pattern.condition == "*" {
                                  true
                              } else {
                                  // Very basic check: Does payload string contain the condition keyword?
                                  event.payload.to_string().contains(&pattern.condition)
                              };
          
                              if condition_met {
                                  tracing::info!("⚠️  Pattern Triggered: {} on Agent {}", pattern.name, event.agent_id.as_deref().unwrap_or("?"));
          
                                  // 3. Execute Action
                                  match pattern.action {
                                      crate::registry::PatternAction::Interrupt { reason } => {
                                          if let Some(agent) = &event.agent_id {
                                              // Direct call to fail_run (simulating interrupt)
                                              runtime_ref.fail_run(&event.run_id, agent, &reason).await;
                                          }
                                      }
                                      crate::registry::PatternAction::RequestApproval { reason } => {
                                          tracing::warn!("✋ Safety Pattern Triggered: Approval Required - {}", reason);
          
                                          // CALL THE NEW PAUSE METHOD
                                          if let Some(agent) = &event.agent_id {
                                              runtime_ref.request_approval(&event.run_id, Some(agent), &reason).await;
                                          } else {
                                              runtime_ref.request_approval(&event.run_id, None, &reason).await;
                                          }
                                      }
                                      crate::registry::PatternAction::SpawnAgent { .. } => {
                                          tracing::warn!("SpawnAgent action not yet implemented in Cortex");
                                      }
                                  }
                              }
                          }
                      }
                  }
              });
          
              // === REDIS LIVE LOG SUBSCRIBER ===
              // Listens to "raro:live_logs" channel and bridges messages to internal event bus
              if let Some(redis_client) = &runtime.redis_client {
                  let client = redis_client.clone();
                  let event_bus = runtime.event_bus.clone();
          
                  tokio::spawn(async move {
                      tracing::info!("🎧 Started Redis Log Subscriber on 'raro:live_logs'");
          
                      // Establish PubSub connection
                      let mut pubsub_conn = match client.get_async_connection().await {
                          Ok(conn) => conn.into_pubsub(),
                          Err(e) => {
                              tracing::error!("Failed to connect to Redis for PubSub: {}", e);
                              return;
                          }
                      };
          
                      // Subscribe to the channel
                      if let Err(e) = pubsub_conn.subscribe("raro:live_logs").await {
                          tracing::error!("Failed to subscribe to 'raro:live_logs': {}", e);
                          return;
                      }
          
                      // Stream incoming messages
                      let mut stream = pubsub_conn.on_message();
          
                      while let Some(msg) = stream.next().await {
                          let payload_str: String = msg.get_payload().unwrap_or_default();
          
                          // Parse JSON payload from Python
                          if let Ok(data) = serde_json::from_str::<serde_json::Value>(&payload_str) {
                              let run_id = data["run_id"].as_str().unwrap_or_default();
                              let agent_id = data["agent_id"].as_str();
                              let message = data["message"].as_str().unwrap_or("");
                              let metadata = data["metadata"].as_str().unwrap_or("INFO");
                              let category = data["category"].as_str().unwrap_or("INFO");
          
                              // Bridge to internal Event Bus (which WebSockets subscribe to)
                              let _ = event_bus.send(crate::events::RuntimeEvent::new(
                                  run_id,
                                  crate::events::EventType::IntermediateLog,
                                  agent_id.map(|s| s.to_string()),
                                  serde_json::json!({
                                      "message": message,
                                      "metadata": metadata,
                                      "category": category
                                  })
                              ));
                          } else {
                              tracing::warn!("Failed to parse Redis log payload: {}", payload_str);
                          }
                      }
          
                      tracing::warn!("Redis log subscriber exited unexpectedly");
                  });
              } else {
                  tracing::warn!("Redis client not available - live logs disabled");
              }
          
              // Configure CORS
              let cors = CorsLayer::new()
                  .allow_origin(Any)
                  .allow_methods([Method::GET, Method::POST, Method::DELETE])
                  .allow_headers(Any);
          
              // Build router
              let app = Router::new()
                  .route("/health", get(handlers::health))
                  .route("/runtime/start", post(handlers::start_workflow))
                  .route("/runtime/state", get(handlers::get_runtime_state))
                  .route("/runtime/:run_id/agent/:agent_id/invoke", post(handlers::invoke_agent))
                  .route("/runtime/signatures", get(handlers::get_signatures))
                  .route("/runtime/:run_id/artifact/:agent_id", get(handlers::get_artifact))
                  .route("/runtime/:run_id/resume", post(handlers::resume_run))
                  .route("/runtime/:run_id/stop", post(handlers::stop_run))
                  .route("/runtime/library", get(handlers::list_library_files))
                  .route("/runtime/library/upload", post(handlers::upload_library_file))
                  .route("/runtime/:run_id/files/:filename", get(handlers::serve_session_file))
                  // Artifact Storage Routes
                  .route("/runtime/artifacts", get(handlers::list_all_artifacts))
                  .route("/runtime/artifacts/:run_id", get(handlers::get_run_artifacts))
                  .route("/runtime/artifacts/:run_id", axum::routing::delete(handlers::delete_artifact_run))
                  .route("/runtime/artifacts/:run_id/files/:filename", get(handlers::serve_artifact_file))
                  .route("/runtime/artifacts/:run_id/files/:filename/promote", post(handlers::promote_artifact_to_library))
                  // WebSocket
                  .route("/ws/runtime/:run_id", axum::routing::get(handlers::ws_runtime_stream))
                  .layer(cors)
                  .with_state(runtime);
          
              let port = std::env::var("KERNEL_PORT").unwrap_or_else(|_| "3000".to_string());
              let addr = format!("0.0.0.0:{}", port);
              let listener = tokio::net::TcpListener::bind(&addr)
                  .await
                  .expect("Failed to bind to port");
          
              tracing::info!("RARO Kernel Server listening on http://{}", addr);
          
              axum::serve(listener, app)
                  .await
                  .expect("Server error");
          }
      - models.rs
          // [[RARO]]/apps/kernel-server/src/models.rs
          // Purpose: Core data models. Updated with attached_files support for RFS.
          // Architecture: Shared Data Layer
          // Dependencies: Serde
          
          use serde::{Deserialize, Serialize};
          use std::collections::HashMap;
          
          #[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
          #[serde(rename_all = "lowercase")] // Serializes to "fast", "reasoning", etc.
          pub enum ModelVariant {
              Fast,       // Cheap, quick
              Reasoning,  // Standard "Pro" level
              Thinking,   // Deep think / o1-style
              
              // Allow an escape hatch for specific IDs if absolutely needed
              #[serde(untagged)] 
              Custom(String), 
          }
          impl Default for ModelVariant {
              fn default() -> Self {
                  ModelVariant::Fast
              }
          }
          #[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
          pub enum AgentRole {
              #[serde(rename = "orchestrator")]
              Orchestrator,
              #[serde(rename = "worker")]
              Worker,
              #[serde(rename = "observer")]
              Observer,
          }
          
          /// Configuration for a single agent node.
          /// Used in both static workflow definitions and dynamic delegations.
          #[derive(Debug, Clone, Serialize, Deserialize)]
          pub struct AgentNodeConfig {
              pub id: String,
              pub role: AgentRole,
              pub model: ModelVariant,
              pub tools: Vec<String>,
              #[serde(default)]
              pub input_schema: serde_json::Value,
              #[serde(default)]
              pub output_schema: serde_json::Value,
              #[serde(default = "default_cache_policy")]
              pub cache_policy: String,
              // Dependencies relative to the context (Workflow or Subgraph)
              #[serde(default)]
              pub depends_on: Vec<String>,
              pub prompt: String,
              pub position: Option<Position>,
              #[serde(default)]
              pub accepts_directive: bool,
              #[serde(default)]
              pub user_directive: String,  // Runtime task from operator
          
              // [[NEW FIELD]]
              #[serde(default)]
              pub allow_delegation: bool,
          }
          
          fn default_cache_policy() -> String {
              "ephemeral".to_string()
          }
          
          #[derive(Debug, Clone, Serialize, Deserialize)]
          pub struct Position {
              pub x: f64,
              pub y: f64,
          }
          
          #[derive(Debug, Clone, Serialize, Deserialize)]
          pub struct WorkflowConfig {
              pub id: String,
              pub name: String,
              pub agents: Vec<AgentNodeConfig>,
              pub max_token_budget: usize,
              pub timeout_ms: u64,
              
              // === RFS Integration ===
              // List of filenames from the Library to attach to this run's context
              #[serde(default)]
              pub attached_files: Vec<String>, 
          }
          
          // === NEW: DYNAMIC GRAPH STRUCTURES ===
          
          /// A request from an active agent to spawn new sub-agents.
          /// This supports Flow B (Recursive Fork).
          #[derive(Debug, Clone, Serialize, Deserialize)]
          pub struct DelegationRequest {
              /// The intent/reason for this delegation (for logging/patterns)
              pub reason: String,
              
              /// The new nodes to inject into the graph
              pub new_nodes: Vec<AgentNodeConfig>,
              
              /// How these nodes relate to the delegating agent.
              /// Default: "child" (Parent -> New Nodes -> Original Children)
              #[serde(default = "default_strategy")]
              pub strategy: DelegationStrategy,
          }
          
          fn default_strategy() -> DelegationStrategy {
              DelegationStrategy::Child
          }
          
          #[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
          #[serde(rename_all = "lowercase")]
          pub enum DelegationStrategy {
              /// New nodes become children of the current node. 
              /// Current node's original children are re-parented to these new nodes.
              Child,
              /// New nodes are siblings (parallel execution), not blocking dependent flow.
              Sibling,
          }
          
          /// The standardized response from the Remote Agent Service.
          /// Moved here from runtime.rs to centralize the contract.
          #[derive(Debug, Clone, Serialize, Deserialize)]
          pub struct RemoteAgentResponse {
              pub agent_id: String,
              pub success: bool,
              pub output: Option<serde_json::Value>,
              pub error: Option<String>,
              pub tokens_used: usize,
              pub thought_signature: Option<String>,
              pub input_tokens: usize,
              pub output_tokens: usize,
              pub cache_hit: bool,
              pub latency_ms: f64,
              
              // === NEW: The payload for dynamic graph changes ===
              pub delegation: Option<DelegationRequest>,
          }
          
          // === RUNTIME STATE ===
          
          #[derive(Debug, Clone, Serialize, Deserialize)]
          pub struct AgentInvocation {
              pub id: String,
              pub agent_id: String,
              pub model_variant: ModelVariant,
              pub thought_signature: Option<String>,
              pub tools_used: Vec<String>,
              pub tokens_used: usize,
              pub latency_ms: u64,
              pub status: InvocationStatus,
              pub timestamp: String,
              pub artifact_id: Option<String>,
              pub error_message: Option<String>, 
          }
          
          #[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
          #[serde(rename_all = "lowercase")]
          pub enum InvocationStatus {
              Pending,
              Running,
              Success,
              Failed,
              Paused, // Added for Human-in-the-Loop or Delegation pauses
          }
          
          #[derive(Debug, Clone, Serialize, Deserialize)]
          pub struct RuntimeState {
              pub run_id: String,
              pub workflow_id: String,
              pub status: RuntimeStatus,
              pub active_agents: Vec<String>,
              pub completed_agents: Vec<String>,
              pub failed_agents: Vec<String>,
              pub invocations: Vec<AgentInvocation>,
              pub total_tokens_used: usize,
              pub start_time: String,
              pub end_time: Option<String>,
          }
          
          #[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
          #[serde(rename_all = "lowercase")]
          pub enum RuntimeStatus {
              Idle,
              Running,
              Completed,
              Failed,
              AwaitingApproval, // Added for Flow C (Safety)
          }
          
          #[derive(Debug, Clone, Serialize, Deserialize)]
          pub struct ThoughtSignatureStore {
              pub signatures: HashMap<String, String>,
          }
      - observability.rs
          use serde::{Deserialize, Serialize};
          
          #[derive(Debug, Clone, Serialize, Deserialize)]
          pub struct Metrics {
              pub p99_latency_ms: u64,
              pub cache_hit_percentage: f64,
              pub cost_per_run: f64,
              pub total_errors: usize,
              pub average_tokens_per_invocation: usize,
          }
          
          impl Default for Metrics {
              fn default() -> Self {
                  Metrics {
                      p99_latency_ms: 0,
                      cache_hit_percentage: 0.0,
                      cost_per_run: 0.0,
                      total_errors: 0,
                      average_tokens_per_invocation: 0,
                  }
              }
          }
          
          #[derive(Debug, Clone, Serialize, Deserialize)]
          pub struct TraceEvent {
              pub timestamp: String,
              pub level: String,
              pub message: String,
              pub agent_id: Option<String>,
              pub metadata: serde_json::Value,
          }
      - registry.rs
          // [[RARO]]/apps/kernel-server/src/registry.rs
          // Purpose: Pattern Registry. Stores active Event-Condition-Action rules.
          // Architecture: Cortex Layer
          // Dependencies: DashMap, Models
          
          use dashmap::DashMap;
          use serde::{Deserialize, Serialize};
          use std::fs; // Import FS
          use crate::models::AgentNodeConfig;
          
          #[derive(Debug, Clone, Serialize, Deserialize)]
          pub struct Pattern {
              pub id: String,
              pub name: String,
              pub trigger_event: String, 
              pub condition: String,
              pub action: PatternAction,
          }
          
          #[derive(Debug, Clone, Serialize, Deserialize)]
          pub enum PatternAction {
              // Serde will automatically handle the JSON structure {"Interrupt": {"reason": "..."}}
              Interrupt { reason: String },
              RequestApproval { reason: String },
              SpawnAgent { config: AgentNodeConfig },
          }
          
          pub struct PatternRegistry {
              patterns: DashMap<String, Pattern>,
          }
          
          impl PatternRegistry {
              pub fn new() -> Self {
                  let registry = Self {
                      patterns: DashMap::new(),
                  };
                  
                  // CHANGED: Load from file instead of hardcoded function
                  registry.load_patterns_from_disk("config/cortex_patterns.json");
                  
                  registry
              }
          
              pub fn register(&self, pattern: Pattern) {
                  tracing::info!("Registering Safety Pattern: [{}] {}", pattern.id, pattern.name);
                  self.patterns.insert(pattern.id.clone(), pattern);
              }
          
              pub fn get_patterns_for_trigger(&self, event_type: &str) -> Vec<Pattern> {
                  self.patterns
                      .iter()
                      .filter(|p| {
                          // Loose string matching against EventType enum output (e.g., "ToolCall")
                          p.trigger_event == event_type || 
                          // Handle Rust enum debug formatting which might be "ToolCall" or "EventType::ToolCall"
                          event_type.contains(&p.trigger_event) 
                      })
                      .map(|p| p.value().clone())
                      .collect()
              }
          
              /// NEW: Hydration Logic
              fn load_patterns_from_disk(&self, path: &str) {
                  match fs::read_to_string(path) {
                      Ok(data) => {
                          match serde_json::from_str::<Vec<Pattern>>(&data) {
                              Ok(patterns) => {
                                  for p in patterns {
                                      self.register(p);
                                  }
                              },
                              Err(e) => tracing::error!("Failed to parse patterns file: {}", e),
                          }
                      },
                      Err(_) => {
                          tracing::warn!("Pattern file not found at '{}'. Loading fallback defaults.", path);
                          self.register_fallback_patterns();
                      }
                  }
              }
          
              /// Keep fallbacks just in case file is missing
              fn register_fallback_patterns(&self) {
                  self.register(Pattern {
                      id: "guard_fs_delete".to_string(),
                      name: "Prevent File Deletion (Fallback)".to_string(),
                      trigger_event: "ToolCall".to_string(),
                      condition: "fs_delete".to_string(), 
                      action: PatternAction::Interrupt { 
                          reason: "Safety Violation: File deletion is prohibited.".to_string() 
                      },
                  });
              }
          }
      - runtime.rs
          // [[RARO]]/apps/kernel-server/src/runtime.rs
          // Purpose: Core orchestration logic with Redis Persistence added.
          // Architecture: Domain Logic Layer
          // Dependencies: reqwest, dashmap, tokio, redis, serde_json
          
          use crate::dag::DAG;
          use crate::models::*;
          use crate::events::{RuntimeEvent, EventType};
          use crate::registry::PatternRegistry;
          use chrono::Utc;
          use dashmap::DashMap;
          use uuid::Uuid;
          use serde::{Deserialize, Serialize};
          use std::sync::Arc;
          use std::env;
          use redis::AsyncCommands;
          use tokio::sync::broadcast;
          use crate::fs_manager;
          /// Payload for invoking an agent with signature routing and caching
          #[derive(Debug, Clone, Serialize, Deserialize)]
          pub struct InvocationPayload {
              pub run_id: String,
              pub agent_id: String,
              pub model: String,
              pub prompt: String,
              pub user_directive: String,  // Runtime task from operator
              pub input_data: serde_json::Value,
              pub parent_signature: Option<String>,
              pub cached_content_id: Option<String>,
              pub thinking_level: Option<i32>,
              pub file_paths: Vec<String>,
              pub tools: Vec<String>,
          
              // [[NEW FIELDS]]
              pub allow_delegation: bool,
              pub graph_view: String,
          }
          
          pub struct RARORuntime {
              workflows: DashMap<String, WorkflowConfig>,
              runtime_states: DashMap<String, RuntimeState>,
              thought_signatures: DashMap<String, ThoughtSignatureStore>,
              dag_store: DashMap<String, DAG>,
              cache_resources: DashMap<String, String>, // run_id -> cached_content_id
              http_client: reqwest::Client,
              pub redis_client: Option<redis::Client>,
              pub event_bus: broadcast::Sender<RuntimeEvent>,
              pub pattern_registry: Arc<PatternRegistry>,
          }
          
          impl RARORuntime {
              pub fn new() -> Self {
                  // Initialize Redis Client (optional, non-blocking)
                  let redis_client = match env::var("REDIS_URL") {
                      Ok(url) => {
                          match redis::Client::open(url.as_str()) {
                              Ok(client) => {
                                  tracing::info!("Redis client initialized: {}", url);
                                  Some(client)
                              }
                              Err(e) => {
                                  tracing::warn!("Failed to create Redis client: {}. Persistence disabled.", e);
                                  None
                              }
                          }
                      }
                      Err(_) => {
                          tracing::warn!("REDIS_URL not set. Running without persistence.");
                          None
                      }
                  };
          
                  // Initialize Event Bus for Cortex
                  let (tx, _) = broadcast::channel(100); // Buffer 100 events
          
                  RARORuntime {
                      workflows: DashMap::new(),
                      runtime_states: DashMap::new(),
                      thought_signatures: DashMap::new(),
                      dag_store: DashMap::new(),
                      cache_resources: DashMap::new(),
                      // http_client: reqwest::Client::new(),
                      http_client: reqwest::Client::builder()
                          .pool_max_idle_per_host(0) // Disable pooling
                          .build()
                          .unwrap_or_else(|_| reqwest::Client::new()),
                      redis_client,
                      event_bus: tx,
                      pattern_registry: Arc::new(PatternRegistry::new()),
                  }
              }
          
              // === PERSISTENCE LAYER ===
          
              /// Saves the current state of a run to Redis and manages the active index
              async fn persist_state(&self, run_id: &str) {
                  if let Some(client) = &self.redis_client {
                      if let Some(state) = self.runtime_states.get(run_id) {
                          let state_key = format!("run:{}:state", run_id);
                          let active_set_key = "sys:active_runs";
                          
                          match serde_json::to_string(&*state) {
                              Ok(json) => {
                                  match client.get_async_connection().await {
                                      Ok(mut con) => {
                                          // 1. Save State JSON
                                          let _: redis::RedisResult<()> = con.set(&state_key, json).await;
                                          
                                          // 2. Manage Index
                                          // If Completed or Failed, remove from active set. Otherwise add.
                                          if state.status == RuntimeStatus::Completed || state.status == RuntimeStatus::Failed {
                                              let _: redis::RedisResult<()> = con.srem(active_set_key, run_id).await;
                                              // Optional: Set expiry on the state key so old runs eventually clean up (e.g., 24 hours)
                                              let _: redis::RedisResult<()> = con.expire(&state_key, 86400).await;
                                          } else {
                                              let _: redis::RedisResult<()> = con.sadd(active_set_key, run_id).await;
                                          }
                                      },
                                      Err(e) => tracing::error!("Redis connection failed during persist: {}", e),
                                  }
                              },
                              Err(e) => tracing::error!("Failed to serialize state for {}: {}", run_id, e),
                          }
                      }
                  }
              }
          
              /// Rehydrate state from Redis on boot
              pub async fn rehydrate_from_redis(&self) {
                  if let Some(client) = &self.redis_client {
                      tracing::info!("Attempting to rehydrate state from Redis...");
                      match client.get_async_connection().await {
                          Ok(mut con) => {
                              // 1. Get all active run IDs
                              let active_ids: Vec<String> = con.smembers("sys:active_runs").await.unwrap_or_default();
                              tracing::info!("Found {} active runs in persistence layer.", active_ids.len());
          
                              for run_id in active_ids {
                                  let state_key = format!("run:{}:state", run_id);
                                  let state_json: Option<String> = con.get(&state_key).await.unwrap_or(None);
          
                                  if let Some(json) = state_json {
                                      match serde_json::from_str::<RuntimeState>(&json) {
                                          Ok(mut state) => {
                                              // IMPORTANT: On recovery, we might find a run that was "Running" 
                                              // when the server crashed. We should probably mark it as "Failed" 
                                              // or "Interrupted" so the UI knows it's not actually processing anymore.
                                              // For now, we will leave it as is to allow for potential resume logic later,
                                              // but logging it is essential.
                                              tracing::warn!("Rehydrating run: {} (Status: {:?})", state.run_id, state.status);
                                              
                                              // Restore DAG store if possible (Note: DAG structure isn't currently persisted in this simple implementation, 
                                              // so complex resume isn't possible without rebuilding DAG from workflow config. 
                                              // We will mark orphan runs as Failed for safety in this iteration).
                                              
                                              if state.status == RuntimeStatus::Running {
                                                  state.status = RuntimeStatus::Failed; 
                                                  // We treat crash recovery as failure for now
                                                  state.invocations.push(AgentInvocation {
                                                       id: Uuid::new_v4().to_string(),
                                                       agent_id: "KERNEL".to_string(),
                                                       model_variant: ModelVariant::Fast,
                                                       thought_signature: None,
                                                       tools_used: vec![],
                                                       tokens_used: 0,
                                                       latency_ms: 0,
                                                       status: InvocationStatus::Failed,
                                                       timestamp: Utc::now().to_rfc3339(),
                                                       artifact_id: None,
                                                       error_message: Some("Kernel restarted unexpectedly. Workflow terminated.".to_string()),
                                                  });
                                              }
          
                                              self.runtime_states.insert(run_id.clone(), state);
                                          },
                                          Err(e) => tracing::error!("Failed to deserialize state for {}: {}", run_id, e),
                                      }
                                  }
                              }
                          },
                          Err(e) => tracing::error!("Failed to connect to Redis for rehydration: {}", e),
                      }
                  }
              }
          
              // === EVENT EMISSION ===
          
              /// Emit an event to the event bus for Cortex pattern matching
              pub(crate) fn emit_event(&self, event: RuntimeEvent) {
                  // Broadcast to subscribers (Observers, WebSocket, PatternEngine)
                  let _ = self.event_bus.send(event);
              }
          
              // === RESOURCE CLEANUP ===
          
              /// Notify Agent Service to clean up resources (E2B Sandboxes)
              async fn trigger_remote_cleanup(&self, run_id: &str) {
                  let host = env::var("AGENT_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
                  let port = env::var("AGENT_PORT").unwrap_or_else(|_| "8000".to_string());
                  let scheme = if host.contains("localhost") || host == "127.0.0.1" { "http" } else { "http" };
          
                  let url = format!("{}://{}:{}/runtime/{}/cleanup", scheme, host, port, run_id);
          
                  tracing::info!("Triggering resource cleanup for run: {}", run_id);
          
                  // Fire and forget - we don't block the kernel if cleanup fails
                  let client = self.http_client.clone();
                  tokio::spawn(async move {
                      match client.delete(&url).send().await {
                          Ok(res) => {
                              if !res.status().is_success() {
                                  tracing::warn!("Cleanup request failed: Status {}", res.status());
                              }
                          },
                          Err(e) => tracing::warn!("Failed to send cleanup request: {}", e),
                      }
                  });
              }
          
              // === APPROVAL CONTROL ===
          
              /// Request approval from user, pausing execution
              pub async fn request_approval(&self, run_id: &str, agent_id: Option<&str>, reason: &str) {
                  if let Some(mut state) = self.runtime_states.get_mut(run_id) {
                      state.status = RuntimeStatus::AwaitingApproval;
          
                      // Log the intervention event
                      self.emit_event(RuntimeEvent::new(
                          run_id,
                          EventType::SystemIntervention,
                          agent_id.map(|s| s.to_string()),
                          serde_json::json!({
                              "action": "pause",
                              "reason": reason
                          }),
                      ));
                  }
                  self.persist_state(run_id).await;
                  tracing::info!("Run {} PAUSED for approval: {}", run_id, reason);
              }
          
              // === EXECUTION LOGIC ===
          
              /// Start a new workflow execution
              pub fn start_workflow(self: &Arc<Self>, config: WorkflowConfig) -> Result<String, String> {
                  // Validate workflow structure
                  let mut dag = DAG::new();
          
                  // Add all nodes
                  for agent in &config.agents {
                      dag.add_node(agent.id.clone())
                          .map_err(|e| format!("Failed to add node: {}", e))?;
                  }
          
                  // Add edges based on dependencies
                  for agent in &config.agents {
                      for dep in &agent.depends_on {
                          dag.add_edge(dep.clone(), agent.id.clone())
                              .map_err(|e| format!("Failed to add edge: {}", e))?;
                      }
                  }
          
                  // Verify topological sort (catches cycles)
                  let _execution_order = dag
                      .topological_sort()
                      .map_err(|e| format!("Invalid workflow: {}", e))?;
          
                  let workflow_id = config.id.clone();
                  let run_id = Uuid::new_v4().to_string();
          
                  // === RFS INITIALIZATION ===
                  // Create the session folder and copy files
                  if let Err(e) = fs_manager::WorkspaceInitializer::init_run_session(&run_id, config.attached_files.clone()) {
                       tracing::error!("Failed to initialize workspace for {}: {}", run_id, e);
                       return Err(format!("FileSystem Initialization Error: {}", e));
                  }
          
                  // Store workflow and DAG
                  self.workflows.insert(workflow_id.clone(), config.clone());
                  self.dag_store.insert(run_id.clone(), dag);
          
                  // Initialize runtime state
                  let state = RuntimeState {
                      run_id: run_id.clone(),
                      workflow_id: workflow_id.clone(),
                      status: RuntimeStatus::Running,
                      active_agents: Vec::new(),
                      completed_agents: Vec::new(),
                      failed_agents: Vec::new(),
                      invocations: Vec::new(),
                      total_tokens_used: 0,
                      start_time: Utc::now().to_rfc3339(),
                      end_time: None,
                  };
          
                  self.runtime_states.insert(run_id.clone(), state);
          
                  // Initialize thought signature store
                  self.thought_signatures.insert(
                      run_id.clone(),
                      ThoughtSignatureStore {
                          signatures: Default::default(),
                      },
                  );
          
                  // Spawn the execution task (Fire and Forget)
                  let runtime_clone = self.clone();
                  let run_id_clone = run_id.clone();
          
                  tokio::spawn(async move {
                      runtime_clone.persist_state(&run_id_clone).await;
                      runtime_clone.execute_dynamic_dag(run_id_clone).await;
                  });
          
                  Ok(run_id)
              }
          
              /// DYNAMIC EXECUTION LOOP
              /// Keeps pulling 'ready' nodes from the DAG until completion or failure.
              /// Handles graph mutations (delegation) mid-flight.
              pub(crate) async fn execute_dynamic_dag(&self, run_id: String) {
                  tracing::info!("Starting DYNAMIC DAG execution for run_id: {}", run_id);
          
                  // We use a simplified loop: Re-calculate topology, filter for uncompleted, take the next one.
                  // In a real high-throughput system, we'd use a proper ready-queue, but re-calculating topology
                  // on a small graph (<100 nodes) is negligible and safer for consistency.
                  loop {
                      // 1. Check if Run is still valid/active or paused
                      if let Some(state) = self.runtime_states.get(&run_id) {
                          // Check for pause state
                          if state.status == RuntimeStatus::AwaitingApproval {
                              tracing::info!("Execution loop for {} suspending (Awaiting Approval).", run_id);
                              break;
                          }
                          // Check for terminal states
                          if state.status == RuntimeStatus::Failed || state.status == RuntimeStatus::Completed {
                              break;
                          }
                      } else {
                          // Run vanished
                          break;
                      }
          
                      // 2. Determine Next Agent(s)
                      // We get the full topological sort, then find the first node that is NOT complete and NOT running.
                      let next_agent_opt = {
                          // Scope for locks
                          let dag = match self.dag_store.get(&run_id) {
                              Some(d) => d,
                              None => {
                                  tracing::error!("DAG not found for run {}", run_id);
                                  break;
                              }
                          };
          
                          let execution_order = match dag.topological_sort() {
                              Ok(order) => order,
                              Err(e) => {
                                  self.fail_run(&run_id, "SYSTEM", &format!("DAG cycle detected during execution: {}", e)).await;
                                  break;
                              }
                          };
          
                          let state = self.runtime_states.get(&run_id).unwrap(); // Safe due to check above
          
                          // Find first node that isn't done and isn't currently running
                          execution_order.into_iter().find(|agent_id| {
                              !state.completed_agents.contains(agent_id) &&
                              !state.failed_agents.contains(agent_id) &&
                              !state.active_agents.contains(agent_id)
                          })
                      };
          
                      // 3. If no next agent, check if we are done
                      let agent_id = match next_agent_opt {
                          Some(id) => id,
                          None => {
                              // No agents ready. Are any running?
                              let running_count = self.runtime_states.get(&run_id)
                                  .map(|s| s.active_agents.len())
                                  .unwrap_or(0);
          
                              if running_count > 0 {
                                  // Wait for them to finish (simple polling for this implementation)
                                  tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                                  continue;
                              } else {
                                  // Nothing running, nothing ready -> We are done!
                                  if let Some(mut state) = self.runtime_states.get_mut(&run_id) {
                                      state.status = RuntimeStatus::Completed;
                                      state.end_time = Some(Utc::now().to_rfc3339());
                                  }
                                  self.persist_state(&run_id).await;
          
                                  // Trigger Cleanup
                                  self.trigger_remote_cleanup(&run_id).await;
          
                                  tracing::info!("Workflow run {} completed successfully (Dynamic)", run_id);
                                  break;
                              }
                          }
                      };
          
                      // 4. Verify Dependencies
                      // The topo sort gives us order, but we must ensure parents are actually *completed*.
                      let can_run = {
                          let dag = self.dag_store.get(&run_id).unwrap();
                          let deps = dag.get_dependencies(&agent_id);
                          let state = self.runtime_states.get(&run_id).unwrap();
                          deps.iter().all(|d| state.completed_agents.contains(d))
                      };
          
                      if !can_run {
                          // If dependencies aren't met, but topological sort put us here,
                          // it means dependencies are still running or failed.
                          // We wait.
                          tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                          continue;
                      }
          
                      // 5. Execute Agent
                      tracing::info!("Processing agent: {}", agent_id);
                      self.update_agent_status(&run_id, &agent_id, InvocationStatus::Running).await;
          
                      // Emit AgentStarted event
                      self.emit_event(RuntimeEvent::new(
                          &run_id,
                          EventType::AgentStarted,
                          Some(agent_id.clone()),
                          serde_json::json!({"agent_id": agent_id}),
                      ));
          
                      let payload_res = self.prepare_invocation_payload(&run_id, &agent_id).await;
                      if let Err(e) = payload_res {
                          self.fail_run(&run_id, &agent_id, &e).await;
                          self.trigger_remote_cleanup(&run_id).await;
                          continue;
                      }
                      let payload = payload_res.unwrap();
          
                      let response = self.invoke_remote_agent(&payload).await;
          
                      // 6. Handle Result & Potential Delegation
                      match response {
                          Ok(res) => {
                              if res.success {
                                  // A. Check for Delegation (Dynamic Splicing)
                                  if let Some(delegation) = res.delegation {
                                      tracing::info!("Agent {} requested delegation: {}", agent_id, delegation.reason);
          
                                      // Verify agent has delegation privilege (Defense in Depth)
                                      let workflow_id = self.runtime_states.get(&run_id)
                                          .map(|s| s.workflow_id.clone());
          
                                      if let Some(wf_id) = workflow_id {
                                          if let Some(workflow) = self.workflows.get(&wf_id) {
                                              let agent_config = workflow.agents.iter()
                                                  .find(|a| a.id == agent_id);
          
                                              if let Some(config) = agent_config {
                                                  if !config.allow_delegation {
                                                      tracing::warn!("Agent {} attempted delegation without permission. Ignoring.", agent_id);
                                                      // Continue without processing delegation - treat as normal completion
                                                  } else {
                                                      // Agent has permission - process delegation
                                                      match self.handle_delegation(&run_id, &agent_id, delegation).await {
                                                          Ok(_) => {
                                                              tracing::info!("Delegation processed. Graph updated.");
                                                          }
                                                          Err(e) => {
                                                              tracing::error!("Delegation failed: {}", e);
                                                              self.fail_run(&run_id, &agent_id, &format!("Delegation error: {}", e)).await;
                                                              continue;
                                                          }
                                                      }
                                                  }
                                              }
                                          }
                                      }
                                  }
          
                                  // B. Standard Completion Logic
                                  if let Some(sig) = res.thought_signature {
                                      let _ = self.set_thought_signature(&run_id, &agent_id, sig);
                                  }
          
                                  // Store Artifact
                                  let artifact_id = if let Some(output_data) = &res.output {
          
                                      // === AUTO-PROMOTE ARTIFACTS TO PERSISTENT STORAGE ===
                                      // When agents generate files, automatically copy them from ephemeral session
                                      // storage to persistent artifacts storage with metadata tracking
                                      if let Some(files_array) = output_data.get("files_generated").and_then(|v| v.as_array()) {
                                          // Extract workflow_id from runtime state
                                          let workflow_id = self.runtime_states.get(&run_id)
                                              .map(|s| s.workflow_id.clone())
                                              .unwrap_or_default();
          
                                          // Extract user_directive by cloning the workflow and finding the agent
                                          let user_directive = {
                                              if let Some(workflow) = self.workflows.get(&workflow_id) {
                                                  workflow.agents.iter()
                                                      .find(|a| a.id == agent_id)
                                                      .map(|a| a.user_directive.clone())
                                                      .unwrap_or_default()
                                              } else {
                                                  String::new()
                                              }
                                          };
          
                                          for file_val in files_array {
                                              if let Some(filename) = file_val.as_str() {
                                                  let rid = run_id.clone();
                                                  let wid = workflow_id.clone();
                                                  let aid = agent_id.clone();
                                                  let fname = filename.to_string();
                                                  let directive = user_directive.clone();
          
                                                  // Fire-and-forget promotion (don't block execution on IO)
                                                  tokio::spawn(async move {
                                                      match fs_manager::WorkspaceInitializer::promote_artifact_to_storage(
                                                          &rid, &wid, &aid, &fname, &directive
                                                      ).await {
                                                          Ok(_) => tracing::info!("✓ Artifact '{}' promoted to persistent storage", fname),
                                                          Err(e) => tracing::error!("✗ Failed to promote artifact '{}': {}", fname, e),
                                                      }
                                                  });
                                              }
                                          }
                                      }
                                      // ============================================
          
                                      let agent_stored_flag = output_data.get("artifact_stored")
                                          .and_then(|v| v.as_bool())
                                          .unwrap_or(false);
          
                                      if agent_stored_flag {
                                          Some(format!("run:{}:agent:{}:output", run_id, agent_id))
                                      } else {
                                          self.store_artifact(&run_id, &agent_id, output_data).await
                                      }
                                  } else { None };
          
                                  // Record Metrics
                                  let invocation = AgentInvocation {
                                      id: Uuid::new_v4().to_string(),
                                      agent_id: agent_id.clone(),
                                      model_variant: match payload.model.as_str() {
                                          "gemini-2.5-flash" => ModelVariant::Fast,
                                          _ => ModelVariant::Reasoning,
                                      },
                                      thought_signature: None,
                                      tools_used: payload.tools.clone(),
                                      tokens_used: res.tokens_used,
                                      latency_ms: res.latency_ms as u64,
                                      status: InvocationStatus::Success,
                                      timestamp: Utc::now().to_rfc3339(),
                                      artifact_id,
                                      error_message: None,
                                  };
          
                                  let _ = self.record_invocation(&run_id, invocation).await;
          
                                  // Emit AgentCompleted event
                                  self.emit_event(RuntimeEvent::new(
                                      &run_id,
                                      EventType::AgentCompleted,
                                      Some(agent_id.clone()),
                                      serde_json::json!({"agent_id": agent_id, "tokens_used": res.tokens_used}),
                                  ));
                              } else {
                                  // Failure
                                  let error = res.error.unwrap_or_else(|| "Unknown error".to_string());
          
                                  // Emit AgentFailed event
                                  self.emit_event(RuntimeEvent::new(
                                      &run_id,
                                      EventType::AgentFailed,
                                      Some(agent_id.clone()),
                                      serde_json::json!({"agent_id": agent_id, "error": error}),
                                  ));
          
                                  self.fail_run(&run_id, &agent_id, &error).await;
                                  self.trigger_remote_cleanup(&run_id).await;
                              }
                          }
                          Err(e) => {
                              // Emit AgentFailed event for network errors
                              self.emit_event(RuntimeEvent::new(
                                  &run_id,
                                  EventType::AgentFailed,
                                  Some(agent_id.clone()),
                                  serde_json::json!({"agent_id": agent_id, "error": e.to_string()}),
                              ));
          
                              self.fail_run(&run_id, &agent_id, &e.to_string()).await;
                              self.trigger_remote_cleanup(&run_id).await;
                          }
                      }
                  }
              }
          
              /// Handles the "Graph Surgery" when an agent requests delegation
              async fn handle_delegation(&self, run_id: &str, parent_id: &str, req: DelegationRequest) -> Result<(), String> {
                  // 1. Get State to identify Workflow ID
                  let state = self.runtime_states.get(run_id).ok_or("Run not found")?;
                  let workflow_id = state.workflow_id.clone();
                  drop(state); // Drop read lock
          
                  // 2. PRE-FETCH DEPENDENTS
                  // We need to know who currently depends on the parent to update their config.
                  // We do this before locking the workflow to avoid complex lock interleaving.
                  let existing_dependents: Vec<String> = if let Some(dag) = self.dag_store.get(run_id) {
                      dag.get_children(parent_id)
                  } else {
                      return Err("DAG not found for pre-fetch".to_string());
                  };
          
                  // 3. MUTATE WORKFLOW CONFIG (CRITICAL STEP FOR CONTEXT)
                  // We must add new nodes AND update existing nodes' dependencies so they look for the right context.
                  if let Some(mut workflow) = self.workflows.get_mut(&workflow_id) {
                      // A. Register new agents
                      for node in &req.new_nodes {
                          // In production, ensure unique IDs here
                          workflow.agents.push(node.clone());
                      }
          
                      // B. Rewire Configuration Dependencies (The Fix)
                      // If we insert nodes as "Children", the original dependents must now depend on the new nodes,
                      // not the original parent. This ensures prepare_invocation_payload loads the new nodes' output.
                      if req.strategy == DelegationStrategy::Child {
                          for dep_id in &existing_dependents {
                              // Find the config entry for the dependent agent (e.g., 'research_planner')
                              if let Some(dep_agent) = workflow.agents.iter_mut().find(|a| a.id == *dep_id) {
                                  
                                  // 1. Remove the old parent (e.g., 'image_analyzer') from depends_on
                                  dep_agent.depends_on.retain(|p| p != parent_id);
          
                                  // 2. Add the new nodes (e.g., 'vision_analyst') as dependencies
                                  for new_node in &req.new_nodes {
                                      if !dep_agent.depends_on.contains(&new_node.id) {
                                          dep_agent.depends_on.push(new_node.id.clone());
                                      }
                                  }
                                  tracing::info!("Rewired Config: Agent {} now depends on {:?}", dep_id, dep_agent.depends_on);
                              }
                          }
                      }
                  } else {
                      return Err("Workflow config not found".to_string());
                  }
          
                  // 4. MUTATE DAG TOPOLOGY (EXECUTION ORDER)
                  if let Some(mut dag) = self.dag_store.get_mut(run_id) {
                      
                      // A. Add New Nodes & Edges from Parent
                      for node in &req.new_nodes {
                          dag.add_node(node.id.clone()).map_err(|e| e.to_string())?;
          
                          // Parent -> New Node (so New Node can see Parent's context)
                          dag.add_edge(parent_id.to_string(), node.id.clone()).map_err(|e| e.to_string())?;
          
                          // B. Connect New Nodes -> Existing Dependents
                          // If strategy is Child, new nodes block the original dependents.
                          if req.strategy == DelegationStrategy::Child {
                              for dep in &existing_dependents {
                                  // Add edge New Node -> Dependent
                                  dag.add_edge(node.id.clone(), dep.clone()).map_err(|e| e.to_string())?;
                              }
                          }
                      }
          
                      // C. Remove Old Edges (Parent -> Dependents)
                      // Only required for Child strategy to force linear flow (Parent -> New -> Dependent)
                      if req.strategy == DelegationStrategy::Child {
                          for dep in &existing_dependents {
                              // It's okay if this fails (edge might not exist), but logic says it should.
                              let _ = dag.remove_edge(parent_id, dep);
                          }
                      }
          
                      // Validate Cycle (Rollback is hard, so we just check and error if bad)
                      if let Err(e) = dag.topological_sort() {
                          // If we broke the graph, we are in trouble.
                          tracing::error!("Delegation created a cycle: {:?}", e);
                          return Err("Delegation created a cycle in DAG".to_string());
                      }
                  } else {
                      return Err("DAG not found".to_string());
                  }
          
                  Ok(())
              }
          
          
          
              /// Helper to fail the run and update state (Async + Persistent)
              pub async fn fail_run(&self, run_id: &str, agent_id: &str, error: &str) {
                  if let Some(mut state) = self.runtime_states.get_mut(run_id) {
                      state.status = RuntimeStatus::Failed;
                      state.end_time = Some(Utc::now().to_rfc3339());
                      state.failed_agents.push(agent_id.to_string());
                      
                      // Remove from active if present
                      state.active_agents.retain(|a| a != agent_id);
                      
                      // Record failed invocation
                      state.invocations.push(AgentInvocation {
                          id: Uuid::new_v4().to_string(),
                          agent_id: agent_id.to_string(),
                          model_variant: ModelVariant::Fast, // Fallback
                          thought_signature: None,
                          tools_used: vec![],
                          tokens_used: 0,
                          latency_ms: 0,
                          status: InvocationStatus::Failed,
                          timestamp: Utc::now().to_rfc3339(),
                          artifact_id: None,
                          error_message: Some(error.to_string()), 
                      });
                  }
                  
                  self.persist_state(run_id).await;
                  tracing::error!("Run {} failed at agent {}: {}", run_id, agent_id, error);
              }
          
              /// Helper to update status to Running (Async + Persistent)
              async fn update_agent_status(&self, run_id: &str, agent_id: &str, status: InvocationStatus) {
                   let mut changed = false;
                   if let Some(mut state) = self.runtime_states.get_mut(run_id) {
                      match status {
                          InvocationStatus::Running => {
                               if !state.active_agents.contains(&agent_id.to_string()) {
                                   state.active_agents.push(agent_id.to_string());
                                   changed = true;
                               }
                          },
                          _ => {}
                      }
                   }
                   
                   if changed {
                       self.persist_state(run_id).await;
                   }
              }
          
              /// Perform the actual HTTP request to the Agent Service
              async fn invoke_remote_agent(&self, payload: &InvocationPayload) -> Result<RemoteAgentResponse, reqwest::Error> {
                  // Resolve Agent Host from Env or Default
                  let host = env::var("AGENT_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
                  let port = env::var("AGENT_PORT").unwrap_or_else(|_| "8000".to_string());
                  let scheme = if host.contains("localhost") || host == "127.0.0.1" { "http" } else { "http" };
                  
                  let url = format!("{}://{}:{}/invoke", scheme, host, port);
          
                  tracing::debug!("Sending invocation request to: {}", url);
          
                  let response = self.http_client
                      .post(&url)
                      .json(payload)
                      .send()
                      .await?;
          
                  response.json::<RemoteAgentResponse>().await
              }
          
              /// Store agent output to Redis with TTL
              async fn store_artifact(
                  &self,
                  run_id: &str,
                  agent_id: &str,
                  output: &serde_json::Value,
              ) -> Option<String> {
                  let artifact_id = format!("run:{}:agent:{}:output", run_id, agent_id);
          
                  let json_str = match serde_json::to_string(output) {
                      Ok(s) => s,
                      Err(e) => {
                          tracing::error!("Failed to serialize artifact for {}: {}", agent_id, e);
                          return None;
                      }
                  };
          
                  if let Some(client) = &self.redis_client {
                      match client.get_async_connection().await {
                          Ok(mut con) => {
                              match con.set_ex::<_, _, ()>(&artifact_id, json_str, 3600).await {
                                  Ok(_) => {
                                      tracing::debug!("Stored artifact: {}", artifact_id);
                                      return Some(artifact_id);
                                  }
                                  Err(e) => {
                                      tracing::error!("Failed to write artifact to Redis: {}", e);
                                  }
                              }
                          }
                          Err(e) => {
                              tracing::error!("Failed to get Redis connection: {}", e);
                          }
                      }
                  } else {
                      tracing::debug!("No Redis client available, artifact not stored");
                  }
          
                  None
              }
          
              /// Get current runtime state
              pub fn get_state(&self, run_id: &str) -> Option<RuntimeState> {
                  self.runtime_states.get(run_id).map(|r| (*r).clone())
              }
          
              /// Record an agent invocation (Async + Persistent)
              pub async fn record_invocation(&self, run_id: &str, invocation: AgentInvocation) -> Result<(), String> {
                  {
                      let mut state = self
                          .runtime_states
                          .get_mut(run_id)
                          .ok_or_else(|| "Run not found".to_string())?;
          
                      state.invocations.push(invocation.clone());
                      state.total_tokens_used += invocation.tokens_used;
          
                      match invocation.status {
                          InvocationStatus::Running => {
                              if !state.active_agents.contains(&invocation.agent_id) {
                                  state.active_agents.push(invocation.agent_id.clone());
                              }
                          }
                          InvocationStatus::Success => {
                              state.active_agents.retain(|a| a != &invocation.agent_id);
                              state.completed_agents.push(invocation.agent_id.clone());
                          }
                          InvocationStatus::Failed => {
                              state.active_agents.retain(|a| a != &invocation.agent_id);
                              state.failed_agents.push(invocation.agent_id.clone());
                          }
                          _ => {}
                      }
                  } // Drop write lock before persisting
          
                  self.persist_state(run_id).await;
          
                  Ok(())
              }
          
              /// Store or retrieve thought signature
              pub fn set_thought_signature(&self, run_id: &str, agent_id: &str, signature: String) -> Result<(), String> {
                  let mut store = self
                      .thought_signatures
                      .get_mut(run_id)
                      .ok_or_else(|| "Run not found".to_string())?;
          
                  store.signatures.insert(agent_id.to_string(), signature);
                  Ok(())
              }
          
              pub fn get_thought_signature(&self, run_id: &str, agent_id: &str) -> Option<String> {
                  self.thought_signatures
                      .get(run_id)
                      .and_then(|store| store.signatures.get(agent_id).cloned())
              }
          
              pub fn get_all_signatures(&self, run_id: &str) -> Option<ThoughtSignatureStore> {
                  self.thought_signatures.get(run_id).map(|s| (*s).clone())
              }
          
              /// Generate a contextual graph view based on agent's delegation privilege.
              ///
              /// - **detailed=true**: Returns JSON array with full topology (for orchestrators)
              /// - **detailed=false**: Returns linear text view (for workers)
              fn generate_graph_context(&self, run_id: &str, current_agent_id: &str, detailed: bool) -> String {
                  let state = match self.runtime_states.get(run_id) {
                      Some(s) => s,
                      None => return "Graph state unavailable.".to_string(),
                  };
          
                  let dag = match self.dag_store.get(run_id) {
                      Some(d) => d,
                      None => return "Graph topology unavailable.".to_string(),
                  };
          
                  if detailed {
                      // DETAILED VIEW: JSON topology for orchestrators
                      // Useful for making informed delegation decisions
                      let nodes: Vec<serde_json::Value> = dag.export_nodes().iter().map(|node_id| {
                          let status = if state.completed_agents.contains(node_id) { "completed" }
                          else if state.failed_agents.contains(node_id) { "failed" }
                          else if state.active_agents.contains(node_id) { "running" }
                          else { "pending" };
          
                          serde_json::json!({
                              "id": node_id,
                              "status": status,
                              "is_you": node_id == current_agent_id,
                              "dependencies": dag.get_dependencies(node_id)
                          })
                      }).collect();
          
                      return serde_json::to_string_pretty(&nodes).unwrap_or_default();
                  } else {
                      // LINEAR VIEW: High-level progress indicator for workers
                      // Shows position in pipeline: [n1:COMPLETE] -> [n2:RUNNING(YOU)] -> [n3:PENDING]
                      match dag.topological_sort() {
                          Ok(order) => {
                              let parts: Vec<String> = order.iter().map(|node_id| {
                                  let status = if state.completed_agents.contains(node_id) { "COMPLETE" }
                                  else if state.failed_agents.contains(node_id) { "FAILED" }
                                  else if state.active_agents.contains(node_id) { "RUNNING" }
                                  else { "PENDING" };
          
                                  if node_id == current_agent_id {
                                      format!("[{}:{}(YOU)]", node_id, status)
                                  } else {
                                      format!("[{}:{}]", node_id, status)
                                  }
                              }).collect();
                              return parts.join(" -> ");
                          },
                          Err(_) => return "Cycle detected in graph view.".to_string()
                      }
                  }
              }
          
              /// Prepare invocation payload with signature routing AND artifact context
              pub async fn prepare_invocation_payload(
                  &self,
                  run_id: &str,
                  agent_id: &str,
              ) -> Result<InvocationPayload, String> {
                  // 1. Retrieve Runtime State
                  let state = self
                      .runtime_states
                      .get(run_id)
                      .ok_or_else(|| "Run not found".to_string())?;
          
                  // 2. Retrieve Workflow Configuration
                  let workflow = self
                      .workflows
                      .get(&state.workflow_id)
                      .ok_or_else(|| "Workflow not found".to_string())?;
          
                  // 3. Find Specific Agent Config
                  let agent_config = workflow
                      .agents
                      .iter()
                      .find(|a| a.id == agent_id)
                      .ok_or_else(|| format!("Agent {} not found", agent_id))?;
          
                  // 4. Fetch Parent Signatures (Chain of Thought Continuity)
                  let parent_signature = if !agent_config.depends_on.is_empty() {
                      agent_config
                          .depends_on
                          .iter()
                          .find_map(|parent_id| self.get_thought_signature(run_id, parent_id))
                  } else {
                      None
                  };
          
                  // 5. Context & Artifact Retrieval (Redis)
                  let mut context_prompt_appendix = String::new();
                  let mut input_data_map = serde_json::Map::new();
                  let mut dynamic_file_mounts: Vec<String> = Vec::new();
          
                  if !agent_config.depends_on.is_empty() {
                      if let Some(client) = &self.redis_client {
                          // Use a separate async connection to avoid borrowing issues
                          match client.get_async_connection().await {
                              Ok(mut con) => {
                                  for parent_id in &agent_config.depends_on {
                                      let key = format!("run:{}:agent:{}:output", run_id, parent_id);
                                      
                                      // Attempt to fetch artifact from Redis
                                      let data: Option<String> = con.get(&key).await.unwrap_or(None);
          
                                      if let Some(json_str) = data {
                                          if let Ok(val) = serde_json::from_str::<serde_json::Value>(&json_str) {
                                              
                                              // A. Structured Input Data
                                              input_data_map.insert(parent_id.clone(), val.clone());
          
                                              // B. Text Context (The "Result" string)
                                              let content = val.get("result")
                                                  .and_then(|v| v.as_str())
                                                  .or_else(|| val.get("output").and_then(|v| v.as_str()))
                                                  .unwrap_or("No text output");
          
                                              context_prompt_appendix.push_str(&format!("\n\n=== CONTEXT FROM AGENT {} ===\n{}\n", parent_id, content));
          
                                              // C. Dynamic File Mounting (Manifest Pattern)
                                              // We look for the 'files_generated' array created by the Python tool logic.
                                              // If found, we explicitly mount these files from the session OUTPUT directory
                                              // so the current agent can see/analyze them (Multimodal Vision).
                                              if let Some(files_array) = val.get("files_generated").and_then(|v| v.as_array()) {
                                                  for file_val in files_array {
                                                      if let Some(filename) = file_val.as_str() {
                                                          // Construct absolute path to the RFS session output
                                                          let mount_path = format!("/app/storage/sessions/{}/output/{}", run_id, filename);
          
                                                          // Deduplication: Only add if not already in the list
                                                          // This prevents the same file from being mounted multiple times to Gemini
                                                          if !dynamic_file_mounts.contains(&mount_path) {
                                                              dynamic_file_mounts.push(mount_path);
                                                          }
                                                      }
                                                  }
                                              }
                                          }
                                      }
                                  }
                              },
                              Err(e) => tracing::warn!("Could not connect to Redis for context fetching: {}", e),
                          }
                      } else {
                          tracing::warn!("Redis client unavailable. Context fetching skipped.");
                      }
                  }
          
                  // 6. Construct Final Prompt
                  let mut final_prompt = agent_config.prompt.clone();
                  if !context_prompt_appendix.is_empty() {
                      final_prompt.push_str(&context_prompt_appendix);
                  }
          
                  // 7. Get Cached Content ID (if applicable)
                  let cached_content_id = self.cache_resources.get(run_id).map(|c| (*c).clone());
          
                  // 8. Determine Model Variant String
                  let model_string = match &agent_config.model {
                      ModelVariant::Fast => "fast".to_string(),
                      ModelVariant::Reasoning => "reasoning".to_string(),
                      ModelVariant::Thinking => "thinking".to_string(),
                      ModelVariant::Custom(s) => s.clone(),
                  };
          
                  // 9. Configure Thinking Budget
                  let thinking_level = if matches!(agent_config.model, ModelVariant::Thinking) {
                      Some(5) // Default budget level for Thinking models
                  } else {
                      None
                  };
          
                  // 10. Construct File Paths List
                  // Start with files explicitly attached to the workflow (Input Dir)
                  let mut full_file_paths: Vec<String> = workflow.attached_files.iter()
                      .map(|f| format!("/app/storage/sessions/{}/input/{}", run_id, f))
                      .collect();
          
                  // Track if we have dynamic artifacts (before moving the vector)
                  let has_dynamic_artifacts = !dynamic_file_mounts.is_empty();
                  let dynamic_artifact_count = dynamic_file_mounts.len();
          
                  // Append files generated by parent agents (Output Dir)
                  if has_dynamic_artifacts {
                      tracing::info!("Mounting {} dynamic artifacts for agent {}", dynamic_artifact_count, agent_id);
                      full_file_paths.extend(dynamic_file_mounts);
                  }
          
                  // 11. SMART TOOL ACCESS (Prevents UNEXPECTED_TOOL_CALL)
                  // Strategy: Start with Architect's assignments, then add baseline guarantees
                  let mut tools = agent_config.tools.clone();
          
                  // Baseline tools that ALL agents should have access to
                  // These prevent UNEXPECTED_TOOL_CALL when agents need to inspect their workspace
                  let baseline_tools = vec!["read_file", "list_files", "write_file"];
                  for baseline in baseline_tools {
                      if !tools.contains(&baseline.to_string()) {
                          tools.push(baseline.to_string());
                          tracing::debug!("Agent {}: Added baseline tool '{}'", agent_id, baseline);
                      }
                  }
          
                  // Smart Enhancement: If agent receives files from parents, give it execution capability
                  // This prevents failures when an agent needs to analyze/process generated artifacts
                  if has_dynamic_artifacts && !tools.contains(&"execute_python".to_string()) {
                      tools.push("execute_python".to_string());
                      tracing::info!(
                          "Agent {}: Added 'execute_python' tool (has {} dynamic artifacts to process)",
                          agent_id,
                          dynamic_artifact_count
                      );
                  }
          
                  // Smart Enhancement: If agent has write_file, it likely needs execute_python too
                  // (Most file generation happens via Python execution)
                  if tools.contains(&"write_file".to_string()) && !tools.contains(&"execute_python".to_string()) {
                      tools.push("execute_python".to_string());
                      tracing::debug!("Agent {}: Added 'execute_python' (has write_file capability)", agent_id);
                  }
          
                  // 11. Generate Graph Context (NEW)
                  // Give orchestrators detailed JSON, workers a simple linear view
                  let graph_view = self.generate_graph_context(
                      run_id,
                      agent_id,
                      agent_config.allow_delegation
                  );
          
                  // 12. Return Payload
                  Ok(InvocationPayload {
                      run_id: run_id.to_string(),
                      agent_id: agent_id.to_string(),
                      model: model_string,
                      prompt: final_prompt,
                      user_directive: agent_config.user_directive.clone(),  // Pass operator directive
                      input_data: serde_json::Value::Object(input_data_map),
                      parent_signature,
                      cached_content_id,
                      thinking_level,
                      file_paths: full_file_paths,
                      tools, // Now contains Architect's choices + smart baseline guarantees
          
                      // [[NEW FIELDS]]
                      allow_delegation: agent_config.allow_delegation,
                      graph_view,
                  })
              }
          
              pub fn set_run_status(&self, run_id: &str, status: RuntimeStatus) {
                  if let Some(mut state) = self.runtime_states.get_mut(run_id) {
                      state.status = status;
                      // Trigger async persistence here
                  }
              }
              
              /// Returns the current topology (nodes and edges) for visualization
              pub fn get_topology_snapshot(&self, run_id: &str) -> Option<serde_json::Value> {
                  if let Some(dag) = self.dag_store.get(run_id) {
                      let edges = dag.export_edges();
                      let nodes = dag.export_nodes();
                      
                      // Convert to the JSON structure the frontend expects
                      Some(serde_json::json!({
                          "nodes": nodes,
                          "edges": edges.into_iter().map(|(from, to)| {
                              serde_json::json!({ "from": from, "to": to })
                          }).collect::<Vec<_>>()
                      }))
                  } else {
                      None
                  }
              }
          
              pub fn set_cache_resource(&self, run_id: &str, cached_content_id: String) -> Result<(), String> {
                  self.cache_resources.insert(run_id.to_string(), cached_content_id);
                  Ok(())
              }
          
              pub fn get_cache_resource(&self, run_id: &str) -> Option<String> {
                  self.cache_resources.get(run_id).map(|c| c.clone())
              }
          
              pub fn has_dag(&self, run_id: &str) -> bool {
                  self.dag_store.contains_key(run_id)
              }
          }
      - server.rs
          pub mod handlers;
    - .dockerignore
        node_modules
        dist
        .env
        .git
        .DS_Store
    - Cargo.toml
        [package]
        name = "raro-kernel"
        version = "0.1.0"
        edition = "2021"
        authors = ["RARO Team"]
        license = "MIT"
        
        [dependencies]
        tokio = { version = "1.35", features = ["full"] }
        axum = { version = "0.7", features = ["ws", "multipart"] }
        tower = "0.4"
        tower-http = { version = "0.5", features = ["cors", "trace"] }
        serde = { version = "1.0", features = ["derive"] }
        serde_json = "1.0"
        tracing = "0.1"
        tracing-subscriber = { version = "0.3", features = ["env-filter"] }
        uuid = { version = "1.6", features = ["v4", "serde"] }
        thiserror = "1.0"
        anyhow = "1.0"
        dashmap = "5.5"
        async-trait = "0.1"
        futures = "0.3"
        tungstenite = "0.20"
        tokio-tungstenite = "0.20"
        chrono = { version = "0.4", features = ["serde"] }
        reqwest = { version = "0.11", features = ["json"] }
        redis = { version = "0.24", features = ["tokio-comp", "connection-manager"] }
        tokio-util = "0.7.18"
        
        [profile.release]
        opt-level = 3
        lto = true
    - Dockerfile
        FROM rust:latest AS builder
        
        WORKDIR /usr/src/raro
        
        COPY Cargo.toml Cargo.toml
        # Copy lock file if it exists, otherwise cargo will generate a new one
        COPY Cargo.lock* Cargo.lock
        COPY src src
        
        RUN cargo build --release
        
        FROM debian:bookworm-slim
        
        RUN apt-get update && apt-get install -y ca-certificates curl openssl && rm -rf /var/lib/apt/lists/*
        
        COPY --from=builder /usr/src/raro/target/release/raro-kernel /usr/local/bin/
        
        EXPOSE 3000
        
        CMD ["raro-kernel"]
  - web-console/
    - src/
      - components/
        - sub/
          - ApprovalCard.svelte
              <!-- [[RARO]]/apps/web-console/src/components/sub/ApprovalCard.svelte -->
              <!-- Purpose: HITL Intervention UI. Styled as a physical security ticket/interrupt. -->
              
              <script lang="ts">
                import { resumeRun, stopRun } from '$lib/stores';
                import { fade, slide } from 'svelte/transition';
              
                let { reason, runId }: { reason: string, runId: string } = $props();
                
                let processing = $state(false);
                let decision = $state<'APPROVED' | 'DENIED' | null>(null);
              
                async function handleApprove() {
                    processing = true;
                    await resumeRun(runId);
                    decision = 'APPROVED';
                    processing = false;
                }
              
                async function handleDeny() {
                    processing = true;
                    await stopRun(runId);
                    decision = 'DENIED';
                    processing = false;
                }
              </script>
              
              <div 
                class="security-ticket {decision ? decision.toLowerCase() : 'pending'}" 
                transition:slide={{ duration: 300, axis: 'y' }}
              >
                
                <!-- 1. HAZARD STRIP (Visual Indicator) -->
                <div class="hazard-strip"></div>
              
                <div class="ticket-body">
                  
                  <!-- 2. METADATA COLUMN -->
                  <div class="col-meta">
                    <div class="meta-row">
                      <span class="label">TYPE</span>
                      <span class="value">INTERRUPT</span>
                    </div>
                    <div class="meta-row">
                      <span class="label">CODE</span>
                      <span class="value warn">SEC_01</span>
                    </div>
                    <div class="icon-zone">
                      {#if decision === 'APPROVED'}
                         <span class="status-icon success">✓</span>
                      {:else if decision === 'DENIED'}
                         <span class="status-icon fail">✕</span>
                      {:else}
                         <span class="status-icon blink">!</span>
                      {/if}
                    </div>
                  </div>
              
                  <!-- 3. CONTENT COLUMN -->
                  <div class="col-content">
                    <div class="content-header">
                      <span class="sys-msg">SYSTEM_PAUSE // AUTHORIZATION_REQUIRED</span>
                    </div>
                    
                    <div class="reason-block">
                      <span class="reason-label">TRIGGER_REASON:</span>
                      <p class="reason-text">"{reason}"</p>
                    </div>
              
                    <!-- 4. ACTION DECK -->
                    <div class="action-deck">
                      {#if decision}
                          <!-- STAMP RESULT -->
                          <div class="stamp-container" in:fade>
                              <div class="rubber-stamp {decision === 'APPROVED' ? 'stamp-ok' : 'stamp-fail'}">
                                  {decision === 'APPROVED' ? 'AUTHORIZED' : 'TERMINATED'}
                              </div>
                              <span class="stamp-meta">OP_ID: {runId.slice(-6).toUpperCase()}</span>
                          </div>
                      {:else}
                          <!-- INTERACTIVE BUTTONS -->
                          <button class="btn-action deny" onclick={handleDeny} disabled={processing}>
                              <span class="btn-bracket">[</span> ABORT <span class="btn-bracket">]</span>
                          </button>
                          
                          <button class="btn-action approve" onclick={handleApprove} disabled={processing}>
                              {#if processing}
                                  <span class="blink">PROCESSING...</span>
                              {:else}
                                  <span class="btn-bracket">[</span> EXECUTE <span class="btn-bracket">]</span>
                              {/if}
                          </button>
                      {/if}
                    </div>
                  </div>
              
                </div>
              </div>
              
              <style>
                /* === CHASSIS === */
                .security-ticket {
                    margin: 20px 0;
                    background: var(--paper-bg);
                    border: 1px solid var(--paper-line);
                    border-radius: 2px;
                    font-family: var(--font-code);
                    position: relative;
                    overflow: hidden;
                    box-shadow: 0 4px 20px rgba(0,0,0,0.05);
                    display: flex;
                    flex-direction: column;
                    transition: border-color 0.3s, opacity 0.3s;
                }
              
                /* State Modifiers */
                .security-ticket.pending { border-color: var(--alert-amber); }
                .security-ticket.approved { border-color: var(--paper-line); opacity: 0.8; }
                .security-ticket.denied { border-color: #d32f2f; opacity: 0.8; }
              
                /* === HAZARD STRIP === */
                .hazard-strip {
                    height: 4px;
                    width: 100%;
                    background-image: repeating-linear-gradient(
                        -45deg,
                        var(--alert-amber),
                        var(--alert-amber) 10px,
                        transparent 10px,
                        transparent 20px
                    );
                    border-bottom: 1px solid var(--paper-line);
                }
                
                /* Strip Colors per State */
                .approved .hazard-strip {
                    background-image: repeating-linear-gradient(-45deg, var(--signal-success), var(--signal-success) 10px, transparent 10px, transparent 20px);
                }
                .denied .hazard-strip {
                    background-image: repeating-linear-gradient(-45deg, #d32f2f, #d32f2f 10px, transparent 10px, transparent 20px);
                }
              
                /* === BODY LAYOUT === */
                .ticket-body { display: flex; min-height: 100px; }
              
                /* Left Meta Column */
                .col-meta {
                    width: 80px;
                    background: var(--paper-surface);
                    border-right: 1px dashed var(--paper-line);
                    padding: 12px;
                    display: flex;
                    flex-direction: column;
                    gap: 12px;
                    flex-shrink: 0;
                }
              
                .meta-row { display: flex; flex-direction: column; gap: 2px; }
                .label { font-size: 8px; color: var(--paper-line); font-weight: 700; letter-spacing: 0.5px; }
                .value { font-size: 10px; color: var(--paper-ink); font-weight: 700; }
                .value.warn { color: var(--alert-amber); }
              
                .icon-zone {
                    margin-top: auto;
                    display: flex; justify-content: center; align-items: center;
                    height: 32px; width: 32px;
                    border: 1px solid var(--paper-line);
                    border-radius: 50%;
                    align-self: center;
                    background: var(--paper-bg);
                }
                .status-icon { font-weight: 900; font-size: 14px; }
                .status-icon.blink { color: var(--alert-amber); animation: blink 1s infinite; }
                .status-icon.success { color: var(--signal-success); }
                .status-icon.fail { color: #d32f2f; }
              
                /* Right Content Column */
                .col-content { flex: 1; display: flex; flex-direction: column; }
              
                .content-header {
                    padding: 8px 16px;
                    border-bottom: 1px solid var(--paper-line);
                    background: color-mix(in srgb, var(--paper-surface), transparent 50%);
                }
                .sys-msg {
                    font-size: 9px; font-weight: 700; letter-spacing: 1px;
                    color: var(--paper-line); text-transform: uppercase;
                }
              
                .reason-block { padding: 16px; flex: 1; }
                .reason-label {
                    font-size: 9px; color: var(--paper-ink); font-weight: 700;
                    background: var(--paper-surface); padding: 2px 6px;
                    margin-right: 8px;
                }
                .reason-text {
                    display: inline;
                    font-size: 12px; line-height: 1.5; color: var(--paper-ink);
                    font-style: italic;
                }
              
                /* === ACTION DECK === */
                .action-deck {
                    padding: 12px 16px;
                    border-top: 1px dashed var(--paper-line);
                    background: var(--paper-bg);
                    display: flex; justify-content: flex-end; align-items: center;
                    min-height: 50px;
                }
              
                /* Buttons */
                .btn-action {
                    background: transparent;
                    border: 1px solid transparent;
                    padding: 8px 16px;
                    font-family: var(--font-code);
                    font-size: 11px; font-weight: 700; letter-spacing: 1px;
                    cursor: pointer;
                    color: var(--paper-line);
                    transition: all 0.2s;
                    display: flex; align-items: center; gap: 4px;
                }
                .btn-bracket { opacity: 0.5; transition: opacity 0.2s; }
                
                /* Deny Style */
                .btn-action.deny:hover { color: #d32f2f; }
                .btn-action.deny:hover .btn-bracket { opacity: 1; color: #d32f2f; }
              
                /* Approve Style */
                .btn-action.approve { color: var(--paper-ink); border: 1px solid var(--paper-line); margin-left: 12px; background: var(--paper-surface); }
                .btn-action.approve:hover { background: var(--paper-ink); color: var(--paper-bg); border-color: var(--paper-ink); }
                .btn-action.approve:disabled { opacity: 0.5; cursor: wait; background: transparent; color: var(--paper-line); }
              
                /* === RUBBER STAMP === */
                .stamp-container { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
                
                .rubber-stamp {
                    font-size: 14px; font-weight: 900; letter-spacing: 2px;
                    padding: 4px 12px;
                    border: 3px solid currentColor;
                    border-radius: 4px;
                    text-transform: uppercase;
                    transform: rotate(-8deg);
                    mask-image: url("data:image/svg+xml;utf8,<svg width='100%' height='100%' xmlns='http://www.w3.org/2000/svg'><filter id='noise'><feTurbulence type='fractalNoise' baseFrequency='1.5' numOctaves='3' stitchTiles='stitch'/></filter><rect width='100%' height='100%' fill='white'/><rect width='100%' height='100%' filter='url(%23noise)' opacity='0.5'/></svg>");
                    mix-blend-mode: multiply; /* Looks like ink on paper */
                }
                /* Phosphor Mode Override for Stamp Blend */
                :global(.mode-phosphor) .rubber-stamp { mix-blend-mode: normal; opacity: 0.9; }
              
                .stamp-ok { color: var(--signal-success); }
                .stamp-fail { color: #d32f2f; }
              
                .stamp-meta { font-size: 8px; color: var(--paper-line); font-weight: 700; margin-right: 4px; }
              
                @keyframes blink { 50% { opacity: 0; } }
              </style>
          - ArtifactCard.svelte
              <!-- [[RARO]]/apps/web-console/src/components/sub/ArtifactCard.svelte -->
              <script lang="ts">
                import { fade } from 'svelte/transition';
                import { USE_MOCK } from '$lib/api';
                import { getMockGeneratedFile } from '$lib/mock-api';
              
                let { filenames, runId }: { filenames: string[], runId: string } = $props();
              
                let currentIndex = $state(0);
              
                // Derived state based on the current index
                let currentFilename = $derived(filenames[currentIndex]);
                
                // Regex to check file types
                let isImage = $derived(/\.(png|jpg|jpeg|svg|gif|webp)$/i.test(currentFilename));
                
                let src = $derived(
                  USE_MOCK
                    ? (getMockGeneratedFile(currentFilename) || `/api/runtime/${runId}/files/${currentFilename}`)
                    : `/api/runtime/${runId}/files/${currentFilename}`
                );
              
                let isLoading = $state(true);
                let hasError = $state(false);
                let textContent = $state<string | null>(null);
              
                // Reset and Load Content when file changes
                $effect(() => {
                  // Dependency tracking
                  const _f = currentFilename; 
                  const _s = src;
              
                  hasError = false;
                  textContent = null;
                  isLoading = true; // Start loading
              
                  if (!isImage) {
                      fetchTextContent(_s);
                  }
                  // Note: If it IS an image, the <img> tag in the markup handles the loading trigger
                });
              
                async function fetchTextContent(url: string) {
                    try {
                        const res = await fetch(url);
                        if (!res.ok) throw new Error('Fetch failed');
                        
                        let text = await res.text();
              
                        // Pretty Print JSON if applicable
                        if (currentFilename.endsWith('.json')) {
                            try {
                                const json = JSON.parse(text);
                                text = JSON.stringify(json, null, 2);
                            } catch (e) {
                                // Keep original text if parse fails
                            }
                        }
                        
                        textContent = text;
                    } catch (e) {
                        console.error(e);
                        hasError = true;
                    } finally {
                        isLoading = false;
                    }
                }
              
                function handleImageLoad() {
                  isLoading = false;
                }
              
                function handleImageError() {
                  isLoading = false;
                  hasError = true;
                }
              
                function nextFile() {
                  currentIndex = (currentIndex + 1) % filenames.length;
                }
              
                function prevFile() {
                  currentIndex = (currentIndex - 1 + filenames.length) % filenames.length;
                }
              </script>
              
              <div class="artifact-card" transition:fade={{ duration: 200 }}>
                <!-- Header -->
                <div class="card-header">
                  <div class="header-left">
                    <div class="header-title">
                      <span class="icon">▚</span>
                      <span>ARTIFACT_DECK</span>
                    </div>
                    <!-- File Counter -->
                    {#if filenames.length > 1}
                      <div class="counter-badge">
                        {currentIndex + 1} / {filenames.length}
                      </div>
                    {/if}
                  </div>
                  
                  <div class="meta-tag" title={currentFilename}>{currentFilename}</div>
                </div>
              
                <!-- Viewport -->
                <div class="card-viewport" class:text-mode={!isImage}>
                  
                  <!-- NAVIGATION CONTROLS (Overlay) -->
                  {#if filenames.length > 1}
                    <button class="nav-btn prev" onclick={prevFile} title="Previous Asset">‹</button>
                    <button class="nav-btn next" onclick={nextFile} title="Next Asset">›</button>
                  {/if}
              
                  <!-- CONTENT RENDERER -->
                  {#key currentFilename} 
                    
                    <!-- 1. LOADING OVERLAY (Independent) -->
                    {#if isLoading}
                        <div class="state-msg">
                          <div class="spinner"></div>
                          <span>FETCHING STREAM...</span>
                        </div>
                    {/if}
              
                    <!-- 2. ERROR STATE -->
                    {#if hasError}
                        <div class="error-state">
                          <span>ERR_LOAD_FAILED // 404</span>
                        </div>
                    
                    <!-- 3. IMAGE RENDERER -->
                    {:else if isImage}
                        <!-- 
                           CRITICAL FIX: The image must be rendered immediately so the browser fetches it.
                           We hide it via CSS class until 'isLoading' is false.
                        -->
                        <img 
                          {src} 
                          alt="Agent Output" 
                          onload={handleImageLoad} 
                          onerror={handleImageError}
                          class:hidden={isLoading}
                        />
              
                    <!-- 4. TEXT RENDERER -->
                    {:else if textContent !== null}
                        <div class="code-viewer">
                          <pre><code>{textContent}</code></pre>
                        </div>
                    {/if}
              
                  {/key}
                </div>
                
                <!-- Footer / Actions -->
                <div class="card-footer">
                  <div class="footer-info">
                    {#if filenames.length > 1}
                      <span class="nav-hint">USE ARROWS TO NAVIGATE</span>
                    {/if}
                  </div>
                  <a href={src} target="_blank" download={currentFilename} class="action-btn">
                    DOWNLOAD [↓]
                  </a>
                </div>
              </div>
              
              <style>
                .artifact-card {
                  margin: 16px 0;
                  border: 1px solid var(--paper-line);
                  background: var(--paper-bg);
                  border-radius: 2px;
                  font-family: var(--font-code);
                  overflow: hidden;
                  max-width: 100%;
                  display: flex;
                  flex-direction: column;
                  box-shadow: 0 4px 12px rgba(0,0,0,0.05);
                  position: relative;
                }
              
                /* HEADER */
                .card-header {
                  background: var(--paper-surface);
                  border-bottom: 1px solid var(--paper-line);
                  padding: 8px 12px;
                  display: flex;
                  justify-content: space-between;
                  align-items: center;
                  height: 32px;
                }
              
                .header-left { display: flex; align-items: center; gap: 12px; }
              
                .header-title {
                  font-size: 10px; font-weight: 700; color: var(--paper-ink);
                  display: flex; align-items: center; gap: 8px; letter-spacing: 1px;
                }
              
                .counter-badge {
                  font-size: 9px; font-weight: 700; color: var(--paper-bg);
                  background: var(--paper-ink); padding: 1px 6px; border-radius: 2px;
                }
              
                .icon { color: var(--arctic-cyan); }
              
                .meta-tag {
                  font-size: 9px; color: var(--paper-line); text-transform: uppercase;
                  max-width: 200px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
                }
              
                /* VIEWPORT */
                .card-viewport {
                  position: relative;
                  min-height: 200px;
                  max-height: 400px; /* Cap height specifically for code scrolling */
                  
                  /* Technical Grid Background */
                  background-image: 
                      linear-gradient(var(--paper-line) 1px, transparent 1px),
                      linear-gradient(90deg, var(--paper-line) 1px, transparent 1px);
                  background-size: 20px 20px;
                  background-position: center;
                  background-color: color-mix(in srgb, var(--paper-bg), var(--paper-surface) 50%);
                  
                  display: flex; justify-content: center; align-items: center;
                  overflow: hidden;
                }
              
                /* Special mode for text to align content top-left and allow scrolling */
                .card-viewport.text-mode {
                    display: block; 
                    overflow: auto;
                    align-items: flex-start;
                    justify-content: flex-start;
                }
              
                img {
                  max-width: 100%; max-height: 300px; display: block;
                  border: 1px solid var(--paper-line);
                  box-shadow: 0 8px 24px rgba(0,0,0,0.1);
                  background: white; /* Transparency checkerboard substitute */
                  margin: 16px; /* Spacing from edges in flex mode */
                }
                
                img.hidden { opacity: 0; position: absolute; }
              
                /* CODE / TEXT VIEWER */
                .code-viewer {
                    padding: 16px;
                    width: 100%;
                    height: 100%;
                    box-sizing: border-box;
                }
              
                pre {
                    margin: 0;
                    white-space: pre-wrap;
                    word-break: break-all;
                    font-family: var(--font-code);
                    font-size: 11px;
                    line-height: 1.5;
                    color: var(--paper-ink);
                }
              
                /* NAVIGATION BUTTONS */
                .nav-btn {
                  position: absolute; top: 50%; transform: translateY(-50%);
                  width: 32px; height: 32px;
                  background: var(--paper-surface); border: 1px solid var(--paper-line);
                  color: var(--paper-ink); font-size: 18px; line-height: 1;
                  cursor: pointer; z-index: 10;
                  display: flex; align-items: center; justify-content: center;
                  transition: all 0.2s; opacity: 0.7;
                }
                .nav-btn:hover { opacity: 1; background: var(--paper-ink); color: var(--paper-bg); }
                .nav-btn.prev { left: 8px; }
                .nav-btn.next { right: 8px; }
              
                /* STATES */
                .state-msg {
                  display: flex; flex-direction: column; align-items: center; gap: 8px;
                  color: var(--paper-line); font-size: 9px; font-weight: 700; letter-spacing: 1px;
                  padding-top: 80px; /* Center generic state vertically roughly */
                  position: absolute; /* Overlay on top */
                  top: 0; left: 0; width: 100%; height: 100%;
                  justify-content: center; padding-top: 0;
                  background: color-mix(in srgb, var(--paper-bg), transparent 20%);
                  z-index: 5;
                }
              
                .error-state {
                  color: #d32f2f; font-size: 10px; font-weight: 700;
                  border: 1px dashed #d32f2f; padding: 8px 16px;
                  background: rgba(211, 47, 47, 0.05);
                  margin: auto;
                }
              
                .spinner {
                  width: 16px; height: 16px;
                  border: 2px solid var(--paper-line); border-top-color: var(--paper-ink);
                  border-radius: 50%; animation: spin 1s linear infinite;
                }
              
                /* FOOTER */
                .card-footer {
                  padding: 8px 12px;
                  border-top: 1px solid var(--paper-line);
                  background: var(--paper-surface);
                  display: flex; justify-content: space-between; align-items: center;
                }
              
                .nav-hint { font-size: 8px; color: var(--paper-line); opacity: 0.8; letter-spacing: 0.5px; }
              
                .action-btn {
                  font-size: 9px; font-weight: 700; color: var(--paper-ink);
                  text-decoration: none; padding: 6px 12px;
                  border: 1px solid var(--paper-line); background: var(--paper-bg);
                  transition: all 0.2s; text-transform: uppercase; letter-spacing: 0.5px;
                }
                .action-btn:hover { border-color: var(--paper-ink); background: var(--paper-ink); color: var(--paper-bg); }
              
                @keyframes spin { to { transform: rotate(360deg); } }
              </style>
          - CodeBlock.svelte
              <!-- // [[RARO]]/apps/web-console/src/components/sub/CodeBlock.svelte -->
              
              <script lang="ts">
                import { fade } from 'svelte/transition';
                import { highlight } from '$lib/syntax-lite';
                
                let { 
                  code, 
                  language, 
                  activeCursor = false 
                }: { 
                  code: string, 
                  language: string, 
                  activeCursor?: boolean 
                } = $props();
              
                let copied = $state(false);
                let timeout: any;
              
                // 1. NEW: Handle escaped newlines (e.g. from JSON logs) to ensure <pre> breaks lines correctly
                let cleanCode = $derived(code ? code.replace(/\\n/g, '\n') : '');
              
                // 2. Highlight logic (pass the cleaned code)
                let highlightedCode = $derived(highlight(cleanCode, language));
              
                function copyToClipboard() {
                  // Copy the cleaned code (actual newlines), not the escaped version
                  navigator.clipboard.writeText(cleanCode);
                  copied = true;
                  clearTimeout(timeout);
                  timeout = setTimeout(() => copied = false, 2000);
                }
              </script>
              
              <div class="code-chassis" transition:fade={{ duration: 200 }}>
                <div class="code-header">
                  <div class="lang-tag">
                    <div class="status-dot"></div>
                    {language || 'TXT'}
                  </div>
                  
                  <button class="action-copy" onclick={copyToClipboard} class:success={copied}>
                    {#if copied} COPIED {:else} COPY_ {/if}
                  </button>
                </div>
              
                <div class="code-viewport">
                  <pre><code class="language-{language}"><!-- 
                    --><span class="code-inner">{@html highlightedCode}</span><!--
                    -->{#if activeCursor}<span class="cursor-block">▋</span>{/if}<!-- 
                  --></code></pre>
                </div>
              </div>
              
              <style>
                /* ... Existing styles remain unchanged ... */
                
                .code-chassis {
                  margin: 16px 0;
                  border: 1px solid var(--paper-line);
                  background: color-mix(in srgb, var(--paper-bg), var(--paper-ink) 3%);
                  border-radius: 2px;
                  overflow: hidden;
                  font-family: var(--font-code);
                  transition: border-color 0.3s;
                  display: block; 
                  width: 100%;
                }
              
                .code-chassis:hover { border-color: var(--paper-ink); }
                .code-header {
                  display: flex; justify-content: space-between; align-items: center;
                  padding: 6px 12px; border-bottom: 1px solid var(--paper-line);
                  background: var(--paper-surface); user-select: none;
                }
                .lang-tag {
                  font-size: 9px; font-weight: 700; text-transform: uppercase;
                  color: var(--paper-ink); display: flex; align-items: center; gap: 6px;
                }
                .status-dot { width: 4px; height: 4px; background: var(--alert-amber); border-radius: 50%; }
                .action-copy {
                  background: transparent; border: none; font-family: var(--font-code);
                  font-size: 9px; font-weight: 600; color: var(--paper-line); cursor: pointer;
                }
                .action-copy:hover { color: var(--paper-ink); }
                .action-copy.success { color: var(--signal-success); }
                
                .code-viewport { padding: 16px; overflow-x: auto; font-size: 11px; line-height: 1.5; }
                pre { margin: 0; font-family: var(--font-code); }
              
                :global(.token-kw) { color: var(--arctic-cyan); font-weight: 700; }
                :global(.mode-archival .token-kw) { color: #005cc5; }
                :global(.token-str) { color: #a5d6ff; }
                :global(.mode-archival .token-str) { color: #032f62; }
                :global(.token-comment) { color: var(--paper-line); font-style: italic; }
                :global(.token-num), :global(.token-bool) { color: var(--alert-amber); }
                :global(.mode-archival .token-num), :global(.mode-archival .token-bool) { color: #d73a49; }
              
                .cursor-block {
                  display: inline-block;
                  color: var(--arctic-cyan);
                  margin-left: 1px;
                  vertical-align: text-bottom;
                  line-height: 1;
                  animation: blink 1s infinite;
                }
                @keyframes blink { 50% { opacity: 0; } }
              </style>
          - DelegationCard.svelte
              <!-- // [[RARO]]/apps/web-console/src/components/sub/DelegationCard.svelte -->
              <script lang="ts">
                import { fade } from 'svelte/transition';
                import Spinner from './Spinner.svelte';
              
                let { rawJson, loading = false }: { rawJson: string, loading?: boolean } = $props();
              
                let data = $derived.by(() => {
                  try {
                      if (loading) return null;
                      return JSON.parse(rawJson);
                  } catch (e) {
                      return null; // Invalid or incomplete JSON
                  }
                });
              </script>
              
              <div class="delegation-card" transition:fade={{ duration: 200 }}>
                
                <!-- Header -->
                <div class="card-header">
                  <div class="header-title">
                      <span class="icon">⑃</span>
                      <span>GRAPH MUTATION DETECTED</span>
                  </div>
                  
                  {#if !loading && data}
                    <div class="strategy-badge" transition:fade>
                        STRATEGY: {data.strategy || 'CHILD'}
                    </div>
                  {/if}
                </div>
              
                <!-- Body -->
                <div class="card-body">
                  
                  {#if loading}
                      <!-- LOADING STATE -->
                      <div class="state-loading">
                          <Spinner />
                          <span>CALCULATING SHARD DELEGATION...</span>
                      </div>
                  {:else if data}
                      <!-- DATA STATE -->
                      <div class="section">
                          <div class="label">REASONING</div>
                          <div class="content reasoning">"{data.reason || 'No reason provided'}"</div>
                      </div>
              
                      {#if data.new_nodes && Array.isArray(data.new_nodes)}
                          <div class="section">
                              <div class="label">INJECTING NODES ({data.new_nodes.length})</div>
                              <div class="node-list">
                                  {#each data.new_nodes as node}
                                      <div class="node-chip">
                                          <div class="chip-role">{node.role || 'WORKER'}</div>
                                          <div class="chip-id">{node.id}</div>
                                          <div class="chip-model">{node.model}</div>
                                      </div>
                                  {/each}
                              </div>
                          </div>
                      {/if}
                  {:else}
                      <!-- ERROR / RAW STATE -->
                      <div class="section">
                           <div class="label" style="color: var(--alert-amber)">MALFORMED DELEGATION DATA</div>
                           <div class="content raw">{rawJson}</div>
                      </div>
                  {/if}
              
                </div>
              
              </div>
              
              <style>
                .delegation-card {
                  margin: 16px 0;
                  border: 1px solid var(--arctic-lilac);
                  background: color-mix(in srgb, var(--paper-bg), var(--arctic-lilac) 5%);
                  border-radius: 2px;
                  font-family: var(--font-code);
                  overflow: hidden;
                  box-shadow: 0 4px 12px rgba(113, 113, 242, 0.1);
                }
              
                .card-header {
                  background: color-mix(in srgb, var(--paper-surface), var(--arctic-lilac) 10%);
                  border-bottom: 1px solid var(--arctic-lilac);
                  padding: 8px 12px;
                  display: flex;
                  justify-content: space-between;
                  align-items: center;
                  height: 32px;
                }
              
                .header-title {
                  color: var(--arctic-lilac);
                  font-weight: 700;
                  font-size: 10px;
                  letter-spacing: 1px;
                  display: flex;
                  align-items: center;
                  gap: 8px;
                }
              
                .icon { font-size: 14px; line-height: 0; }
              
                .strategy-badge {
                  font-size: 8px;
                  background: var(--paper-bg);
                  border: 1px solid var(--paper-line);
                  padding: 2px 6px;
                  border-radius: 2px;
                  color: var(--paper-ink);
                  text-transform: uppercase;
                }
              
                .card-body {
                  padding: 16px;
                  display: flex;
                  flex-direction: column;
                  gap: 16px;
                  min-height: 60px; /* Prevent collapse during load */
                  justify-content: center;
                }
              
                /* LOADING STATE */
                .state-loading {
                  display: flex;
                  align-items: center;
                  gap: 12px;
                  color: var(--paper-line);
                  font-size: 10px;
                  font-weight: 700;
                  letter-spacing: 1px;
                  animation: pulse 1s infinite alternate;
                }
              
                .label {
                  font-size: 8px;
                  color: var(--paper-line);
                  text-transform: uppercase;
                  font-weight: 700;
                  margin-bottom: 6px;
                  letter-spacing: 0.5px;
                }
              
                .reasoning {
                  font-size: 12px;
                  color: var(--paper-ink);
                  font-style: italic;
                  line-height: 1.4;
                  padding-left: 8px;
                  border-left: 2px solid var(--paper-line);
                }
              
                .raw {
                  font-size: 10px;
                  opacity: 0.7;
                  white-space: pre-wrap;
                  word-break: break-all;
                }
              
                .node-list {
                  display: flex;
                  flex-wrap: wrap;
                  gap: 8px;
                }
              
                .node-chip {
                  display: flex;
                  align-items: center;
                  border: 1px solid var(--paper-line);
                  background: var(--paper-surface);
                  border-radius: 2px;
                  overflow: hidden;
                }
              
                .chip-role {
                  background: var(--paper-line);
                  color: var(--paper-bg);
                  font-size: 8px;
                  padding: 4px 6px;
                  text-transform: uppercase;
                  font-weight: 700;
                }
              
                .chip-id {
                  padding: 4px 8px;
                  font-size: 10px;
                  font-weight: 700;
                  color: var(--paper-ink);
                  border-right: 1px dashed var(--paper-line);
                }
              
                .chip-model {
                  padding: 4px 8px;
                  font-size: 9px;
                  color: var(--paper-line);
                }
              
                @keyframes pulse { from { opacity: 0.6; } to { opacity: 1; } }
              </style>
              
          - SmartText.svelte
              <!-- // [[RARO]]/apps/web-console/src/components/sub/SmartText.svelte -->
              
              <script lang="ts">
                import CodeBlock from './CodeBlock.svelte';
                import DelegationCard from './DelegationCard.svelte'; 
                import { parseMarkdown } from '$lib/markdown';
              
                let { text }: { text: string } = $props();
              
                function parseContent(input: string) {
                  const regex = /```([a-zA-Z0-9:_-]+)?\n([\s\S]*?)```/g;
                  const parts = [];
                  let lastIndex = 0;
                  let match;
              
                  while ((match = regex.exec(input)) !== null) {
                    if (match.index > lastIndex) {
                      parts.push({
                        type: 'text',
                        content: input.slice(lastIndex, match.index)
                      });
                    }
              
                    parts.push({
                      type: 'code',
                      lang: match[1] || 'text',
                      content: match[2]
                    });
              
                    lastIndex = regex.lastIndex;
                  }
              
                  if (lastIndex < input.length) {
                    parts.push({
                      type: 'text',
                      content: input.slice(lastIndex)
                    });
                  }
              
                  return parts;
                }
              
                let blocks = $derived(parseContent(text));
              </script>
              
              <div class="smart-text-wrapper">
                {#each blocks as block}
                  {#if block.type === 'code'}
                    <!-- ROUTING LOGIC -->
                    {#if block.lang === 'json:delegation'}
                      <DelegationCard rawJson={block.content} />
                    {:else}
                      <CodeBlock code={block.content} language={block.lang || 'text'} />
                    {/if}
                  {:else}
                    <!-- 
                      Pass text segments through Marked.
                      The wrapper div handles the CSS for the generated HTML.
                    -->
                    <div class="markdown-body">
                      {@html parseMarkdown(block.content)}
                    </div>
                  {/if}
                {/each}
              </div>
              
              <style>
                .smart-text-wrapper {
                  display: flex;
                  flex-direction: column;
                  width: 100%;
                  gap: 8px; 
                }
              
                /* === MARKDOWN TYPOGRAPHY SYSTEM === */
              
                :global(.markdown-body) {
                  font-size: 13px;
                  line-height: 1.6;
                  color: var(--paper-ink);
                  /* FIX: Force wrapping to prevent horizontal scroll */
                  overflow-wrap: break-word;
                  word-break: break-word;
                }
              
                /* HEADERS */
                :global(.markdown-body h1), 
                :global(.markdown-body h2), 
                :global(.markdown-body h3) {
                  margin-top: 24px;
                  margin-bottom: 12px;
                  font-weight: 700;
                  letter-spacing: -0.5px;
                  color: var(--paper-ink);
                }
              
                :global(.markdown-body h1) { font-size: 18px; border-bottom: 1px solid var(--paper-line); padding-bottom: 8px; }
                :global(.markdown-body h2) { font-size: 16px; }
                :global(.markdown-body h3) { font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; opacity: 0.8; }
              
                /* PARAGRAPHS */
                :global(.markdown-body p) {
                  margin-bottom: 12px;
                }
                :global(.markdown-body p:last-child) {
                  margin-bottom: 0;
                }
              
                /* LISTS */
                :global(.markdown-body ul), 
                :global(.markdown-body ol) {
                  padding-left: 20px;
                  margin-bottom: 12px;
                }
                :global(.markdown-body li) {
                  margin-bottom: 4px;
                  padding-left: 4px;
                }
                :global(.markdown-body li::marker) {
                  color: var(--paper-line);
                }
              
                /* INLINE ELEMENTS */
                :global(.markdown-body strong) {
                  font-weight: 700;
                  color: var(--paper-ink);
                }
                
                :global(.markdown-body em) {
                  font-style: italic;
                  opacity: 0.8;
                }
              
                :global(.markdown-body code) {
                  font-family: var(--font-code);
                  font-size: 11px;
                  padding: 2px 4px;
                  background: var(--paper-surface);
                  border: 1px solid var(--paper-line);
                  border-radius: 2px;
                  color: var(--arctic-cyan);
                }
                
                :global(.mode-archival .markdown-body code) {
                  color: #e36209;
                }
              
                /* LINKS (Configured in markdown.ts) */
                :global(.md-link) {
                  color: var(--arctic-lilac);
                  text-decoration: none;
                  border-bottom: 1px dotted var(--arctic-lilac);
                  transition: all 0.2s;
                }
                :global(.md-link:hover) {
                  background: var(--arctic-lilac-lite);
                  border-bottom-style: solid;
                }
              
                /* BLOCKQUOTES (Configured in markdown.ts) */
                :global(.md-quote) {
                  margin: 16px 0;
                  padding: 8px 16px;
                  border-left: 3px solid var(--paper-line);
                  background: var(--paper-surface);
                  font-style: italic;
                  color: var(--paper-line);
                }
                
                /* TABLES */
                :global(.markdown-body table) {
                  width: 100%;
                  border-collapse: collapse;
                  margin: 16px 0;
                  font-family: var(--font-code);
                  font-size: 11px;
                }
                
                :global(.markdown-body th) {
                  text-align: left;
                  padding: 8px;
                  border-bottom: 1px solid var(--paper-line);
                  color: var(--paper-line);
                  text-transform: uppercase;
                  font-weight: 600;
                }
                
                :global(.markdown-body td) {
                  padding: 8px;
                  border-bottom: 1px dashed var(--paper-line);
                  color: var(--paper-ink);
                }
              </style>
          - Spinner.svelte
              <!-- // [[RARO]]/apps/web-console/src/components/sub/Spinner.svelte
              // Purpose: Reusable CSS loading spinner.
              // Architecture: UI Atom
              // Dependencies: None -->
              
              <script lang="ts">
                // Simple CSS-based loading spinner
              </script>
              
              <div class="spinner"></div>
              
              <style>
                .spinner {
                  width: 12px;
                  height: 12px;
                  border: 2px solid rgba(0, 0, 0, 0.1);
                  border-left-color: var(--paper-ink);
                  border-radius: 50%;
                  animation: spin 0.8s linear infinite;
                  display: block; /* Changed from inline-block for better layout control in parents */
                }
              
                @keyframes spin {
                  to { transform: rotate(360deg); }
                }
              </style>
          - ToolExecutionCard.svelte
              <!-- [[RARO]]/apps/web-console/src/components/sub/ToolExecutionCard.svelte -->
              <script lang="ts">
                import { fade, slide } from 'svelte/transition';
                import Spinner from './Spinner.svelte';
              
                // DEFINITION: Explicitly define the props interface
                let {
                  category,
                  message,
                  metadata,
                  agentId,
                  // New Props for "Merged" state
                  isComplete = false,
                  toolResult = null,
                  toolStatus = 'success'
                }: {
                  category: string,
                  message: string,
                  metadata: string,
                  agentId: string,
                  isComplete?: boolean,
                  toolResult?: string | null,
                  toolStatus?: 'success' | 'error'
                } = $props();
              
                // Parsing logic for the "Call" part (always present in 'message')
                let toolName = $derived.by(() => {
                  const match = message.match(/^([a-z_]+)\(/);
                  return match ? match[1] : 'unknown';
                });
              
                let argsPreview = $derived.by(() => {
                  const match = message.match(/\(([\s\S]*)\)$/);
                  if (!match) return '';
                  const args = match[1];
                  return args.length > 60 ? args.substring(0, 60) + '...' : args;
                });
              
                // State
                let isExpanded = $state(false);
                let isError = $derived(toolStatus === 'error' || metadata === 'IO_ERR');
              
                // Auto-expand on error to show traceback
                $effect(() => {
                  if (isError) isExpanded = true;
                });
              </script>
              
              <div 
                class="tool-card {isComplete ? 'complete' : 'executing'} {isError ? 'error' : ''}"
                transition:fade={{ duration: 150 }}
              >
                <!-- HEADER -->
                <div 
                  class="card-header" 
                  onclick={() => { if(isComplete) isExpanded = !isExpanded; }}
                  role="button"
                  tabindex="0"
                  onkeydown={(e) => e.key === 'Enter' && isComplete && (isExpanded = !isExpanded)}
                >
                  <div class="header-main">
                      <div class="meta-badges">
                          <span class="agent-badge">{agentId}</span>
                          <span class="arrow">→</span>
                          <span class="tool-name">{toolName}</span>
                      </div>
                      {#if !isExpanded}
                          <span class="args-preview" transition:fade>({argsPreview})</span>
                      {/if}
                  </div>
              
                  <div class="header-status">
                      {#if !isComplete}
                          <!-- STATE: EXECUTING -->
                          <div class="status-active">
                              <Spinner />
                              <span class="status-text">EXECUTING</span>
                          </div>
                      {:else}
                          <!-- STATE: DONE -->
                          <div class="status-done" class:err={isError}>
                              {#if isError}
                                  <span class="icon">✕</span> FAILED
                              {:else}
                                  <span class="icon">✓</span> DONE
                              {/if}
                          </div>
                          <div class="chevron {isExpanded ? 'up' : 'down'}">▼</div>
                      {/if}
                  </div>
                </div>
              
                <!-- BODY: Expanded Content -->
                {#if isExpanded}
                  <div class="card-body" transition:slide={{ duration: 200 }}>
                      <!-- Input Arguments -->
                      <div class="section">
                          <div class="label">INPUT_PAYLOAD</div>
                          <div class="code-block input">{message}</div>
                      </div>
              
                      <!-- Output Result -->
                      {#if isComplete && toolResult}
                          <div class="section result-section" class:err-section={isError}>
                              <div class="label">{isError ? 'ERROR_TRACE' : 'OUTPUT_DATA'}</div>
                              <div class="code-block output {isError ? 'text-err' : 'text-ok'}">
                                  {toolResult}
                              </div>
                          </div>
                      {/if}
                  </div>
                {/if}
              </div>
              
              <style>
                .tool-card { margin: 8px 0; font-family: var(--font-code); font-size: 11px; border: 1px solid var(--paper-line); background: var(--paper-bg); border-radius: 2px; overflow: hidden; transition: all 0.3s; }
                .tool-card.executing { border-left: 3px solid var(--alert-amber); background: color-mix(in srgb, var(--paper-bg), var(--alert-amber) 2%); }
                .tool-card.complete { border-left: 3px solid var(--paper-line); }
                .tool-card.complete.error { border-left: 3px solid #d32f2f; border-color: #d32f2f; background: color-mix(in srgb, var(--paper-bg), #d32f2f 3%); }
                
                .card-header { display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; background: var(--paper-surface); cursor: default; user-select: none; }
                .tool-card.complete .card-header { cursor: pointer; }
                .tool-card.complete .card-header:hover { background: color-mix(in srgb, var(--paper-surface), var(--paper-ink) 5%); }
              
                .header-main { display: flex; align-items: center; gap: 8px; flex: 1; overflow: hidden; }
                .meta-badges { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
                .agent-badge { font-size: 9px; font-weight: 700; color: var(--paper-ink); background: var(--paper-bg); padding: 2px 6px; border-radius: 2px; border: 1px solid var(--paper-line); }
                .arrow { color: var(--paper-line); font-size: 10px; }
                .tool-name { color: var(--arctic-cyan); font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }
                :global(.mode-phosphor) .tool-name { color: #00ff66; }
                :global(.mode-archival) .tool-name { color: #005cc5; }
              
                .args-preview { color: var(--paper-line); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 10px; }
              
                .header-status { display: flex; align-items: center; gap: 8px; }
                .status-active { display: flex; align-items: center; gap: 6px; color: var(--paper-line); }
                .status-text { font-size: 9px; font-weight: 700; letter-spacing: 0.5px; animation: pulse 1s infinite; }
                .status-done { display: flex; align-items: center; gap: 4px; font-size: 9px; font-weight: 700; color: var(--signal-success); background: color-mix(in srgb, var(--signal-success), transparent 90%); padding: 2px 6px; border-radius: 2px; }
                .status-done.err { color: #d32f2f; background: color-mix(in srgb, #d32f2f, transparent 90%); }
                .chevron { font-size: 8px; color: var(--paper-line); transition: transform 0.2s; }
                .chevron.up { transform: rotate(180deg); }
              
                .card-body { padding: 12px; border-top: 1px solid var(--paper-line); background: var(--paper-bg); display: flex; flex-direction: column; gap: 12px; }
                .section { display: flex; flex-direction: column; gap: 4px; }
                .label { font-size: 8px; font-weight: 700; color: var(--paper-line); text-transform: uppercase; letter-spacing: 0.5px; }
                .code-block { background: var(--paper-surface); padding: 8px; border-radius: 2px; font-family: var(--font-code); font-size: 10px; white-space: pre-wrap; word-break: break-all; line-height: 1.4; border: 1px solid transparent; }
                .code-block.input { color: var(--paper-line); }
                .code-block.output { color: var(--paper-ink); border-color: var(--paper-line); }
                .result-section.err-section .code-block { background: color-mix(in srgb, #d32f2f, transparent 95%); border-color: #d32f2f; color: #d32f2f; }
              
                @keyframes pulse { 50% { opacity: 0.5; } }
              </style>
          - Typewriter.svelte
              <!-- // [[RARO]]/apps/web-console/src/components/sub/Typewriter.svelte -->
              
              <script lang="ts">
                import Spinner from './Spinner.svelte';
                import CodeBlock from './CodeBlock.svelte';
                import DelegationCard from './DelegationCard.svelte'; 
              
                let { text, onComplete }: { text: string, onComplete?: () => void } = $props();
              
                let displayedText = $state('');
                let isTyping = $state(true);
                let showCursor = $state(true);
                
                // Telemetry
                let charCount = $state(0);
                let charSpeed = $state(0);
                let lastFrameTime = 0;
                
                // Internal State
                let currentIndex = 0;
                let timer: any;
              
              
              
                // === 1. LIVE PARSER ===
                let segments = $derived(parseStream(displayedText));
              
              
              
                function parseStream(input: string) {
                  const parts = [];
                  const closedBlockRegex = /```([a-zA-Z0-9:_-]+)?\n([\s\S]*?)```/g;
                  let lastIndex = 0;
                  let match;
              
                  // 1. Fully closed blocks
                  while ((match = closedBlockRegex.exec(input)) !== null) {
                    if (match.index > lastIndex) {
                      parts.push({ type: 'text', content: input.slice(lastIndex, match.index) });
                    }
                    parts.push({ 
                        type: 'code', 
                        lang: match[1] || 'text', 
                        content: match[2],
                        isOpen: false 
                    });
                    lastIndex = closedBlockRegex.lastIndex;
                  }
              
                  // 2. The "Tail" (Potentially open block)
                  const tail = input.slice(lastIndex);
                  const openBlockMatch = /```([a-zA-Z0-9:_-]+)?(?:\n)?([\s\S]*)$/.exec(tail);
              
                  if (openBlockMatch) {
                     if (openBlockMatch.index > 0) {
                       parts.push({ type: 'text', content: tail.slice(0, openBlockMatch.index) });
                     }
                     parts.push({ 
                       type: 'code', 
                       lang: openBlockMatch[1] || 'text', 
                       content: openBlockMatch[2] || '', 
                       isOpen: true // Flag to indicate loading/incomplete
                     });
                  } else {
                     if (tail.length > 0) {
                       parts.push({ type: 'text', content: tail });
                     }
                  }
                  
                  return parts;
                }
              
                // === 2. STANDARD TYPEWRITER LOGIC ===
                
                $effect(() => {
                  return () => clearTimeout(timer);
                });
              
                $effect(() => {
                  if (!isTyping) { showCursor = false; return; }
                  const blinkInterval = setInterval(() => {
                      if (Date.now() - lastFrameTime > 100) showCursor = !showCursor;
                      else showCursor = true;
                  }, 500);
                  return () => clearInterval(blinkInterval);
                });
              
                $effect(() => {
                  if (text && text.length > currentIndex) {
                    isTyping = true;
                    typeNext();
                  } else if (text && text.length === currentIndex) {
                      isTyping = false;
                      if (onComplete) onComplete();
                  }
                });
              
                function typeNext() {
                  clearTimeout(timer);
                  
                  if (currentIndex < text.length) {
                    const now = Date.now();
                    if (lastFrameTime) {
                        const delta = now - lastFrameTime;
                        charSpeed = Math.floor(1000 / delta); 
                    }
                    lastFrameTime = now;
              
                    const remaining = text.length - currentIndex;
                    let chunk = 1;
                    let delay = 20;
              
                    // HTML Tag Skip
                    if (text[currentIndex] === '<') {
                        const closeIdx = text.indexOf('>', currentIndex);
                        if (closeIdx !== -1) {
                            chunk = (closeIdx - currentIndex) + 1;
                            delay = 0; 
                        }
                    } 
                    // Speed up for code blocks
                    else if (text.slice(currentIndex, currentIndex+3) === '```') {
                         chunk = 3; delay = 10;
                    }
                    else if (remaining > 500) { chunk = 25; delay = 2; }
                    else if (remaining > 100) { chunk = 5; delay = 10; }
                    
                    const nextIndex = Math.min(currentIndex + chunk, text.length);
                    displayedText = text.substring(0, nextIndex);
                    currentIndex = nextIndex;
                    charCount = currentIndex;
                    
                    timer = setTimeout(typeNext, delay);
                  } else {
                    isTyping = false;
                    if (onComplete) onComplete();
                  }
                }
              
                function escapeHtml(unsafe: string) {
                    return unsafe
                        .replace(/&/g, "&amp;")
                        .replace(/</g, "&lt;")
                        .replace(/>/g, "&gt;")
                        .replace(/"/g, "&quot;")
                        .replace(/'/g, "&#039;");
                }
              
              </script>
              
              <div class="typewriter-container">
                
                <div class="stream-content">
                  {#each segments as segment, i}
                    {#if segment.type === 'code'}
                      <!-- ROUTER -->
                      {#if segment.lang === 'json:delegation'}
                          <DelegationCard 
                              rawJson={segment.content} 
                              loading={segment.isOpen} 
                          />
                      {:else}
                          <CodeBlock 
                              code={segment.content} 
                              language={segment.lang || 'text'} 
                              activeCursor={isTyping && i === segments.length - 1} 
                          />
                      {/if}
                    {:else}
                      <!--
                         1. Escape HTML (So "<Button>" shows as text, not a hidden tag)
                         2. Replace newlines with actual line breaks for pre-wrap
                      -->
                      <span class="text-body">{@html escapeHtml(segment.content).replace(/\\n/g, '\n')}</span>
                      {#if isTyping && i === segments.length - 1}
                         <span class="cursor" style:opacity={showCursor ? 1 : 0}>▋</span>
                      {/if}
                    {/if}
                  {/each}
                  
                  {#if isTyping && segments.length === 0}
                      <span class="cursor" style:opacity={showCursor ? 1 : 0}>▋</span>
                  {/if}
                </div>
                
                {#if isTyping}
                  <div class="telemetry-footer">
                    <div class="stat-group">
                      <span class="label">SPEED</span>
                      <span class="value">{charSpeed} CPS</span>
                    </div>
                    <div class="stat-group">
                      <span class="label">SIZE</span>
                      <span class="value">{charCount} B</span>
                    </div>
                    <div class="stat-group right-aligned">
                      <span class="label ingress">DATA_INGRESS</span>
                      <Spinner />
                    </div>
                  </div>
                {/if}
              </div>
              
              <style>
                .typewriter-container {
                  position: relative;
                  width: 100%;
                  font-family: var(--font-code);
                }
              
                .stream-content {
                  display: block; 
                  line-height: 1.6;
                  word-break: break-word; /* Ensure long non-breaking strings don't scroll */
                  overflow-wrap: break-word;
                  color: var(--paper-ink);
                }
              
                .text-body {
                  white-space: pre-wrap; 
                  display: inline;
                }
              
                .telemetry-footer {
                  display: flex; align-items: center; gap: 16px; margin-top: 12px; padding-top: 8px;
                  border-top: 1px dashed rgba(0,0,0,0.1); font-size: 9px; color: #888; user-select: none;
                  animation: fadeIn 0.3s ease;
                }
                .stat-group { display: flex; align-items: center; gap: 6px; }
                .right-aligned { margin-left: auto; color: var(--paper-ink); }
                .label { font-weight: 600; opacity: 0.6; letter-spacing: 0.5px; }
                .value { font-family: var(--font-code); font-weight: 400; }
                .ingress { color: var(--paper-line); font-weight: 700; letter-spacing: 1px; animation: pulse 1s infinite alternate; }
                @keyframes pulse { from { opacity: 0.6; } to { opacity: 1; } }
                @keyframes fadeIn { from { opacity: 0; transform: translateY(-5px); } to { opacity: 1; transform: translateY(0); } }
              
                .cursor {
                  display: inline-block;
                  color: var(--arctic-cyan);
                  margin-left: 1px;
                  vertical-align: text-bottom;
                  line-height: 1;
                  font-weight: 900;
                }
              </style>
        - ArtifactViewer.svelte
            <!-- [[RARO]]/apps/web-console/src/components/ArtifactViewer.svelte -->
            <!-- Purpose: Overlay component for previewing and managing artifacts -->
            <!-- Architecture: UI Component -->
            <!-- Dependencies: api -->
            
            <script lang="ts">
              import { getArtifactFileUrl, promoteArtifactToLibrary, type ArtifactFile, type ArtifactMetadata } from '$lib/api';
              import { addLog } from '$lib/stores';
            
              let {
                artifact,
                runMetadata,
                onClose
              }: {
                artifact: ArtifactFile | null;
                runMetadata: ArtifactMetadata | null;
                onClose: () => void;
              } = $props();
            
              let isPromoting = $state(false);
            
              function handleOverlayClick(e: MouseEvent) {
                if (e.target === e.currentTarget) {
                  onClose();
                }
              }
            
              function handleKeydown(e: KeyboardEvent) {
                if (e.key === 'Escape') {
                  onClose();
                }
              }
            
              async function handlePromoteToLibrary() {
                if (!artifact || !runMetadata) return;
            
                isPromoting = true;
                try {
                  await promoteArtifactToLibrary(runMetadata.run_id, artifact.filename);
                  addLog('SYSTEM', `Promoted ${artifact.filename} to library`, 'IO_OK');
                } catch (err) {
                  addLog('SYSTEM', `Failed to promote artifact: ${err}`, 'IO_ERR');
                } finally {
                  isPromoting = false;
                }
              }
            
              function getFileIcon(contentType: string): string {
                if (contentType.includes('image')) return '📊';
                if (contentType.includes('json')) return '📋';
                if (contentType.includes('csv')) return '📈';
                if (contentType.includes('markdown')) return '📝';
                if (contentType.includes('pdf')) return '📄';
                return '📄';
              }
            
              function formatBytes(bytes: number): string {
                if (bytes < 1024) return bytes + ' B';
                if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
                return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
              }
            
              function formatTimestamp(iso: string): string {
                const date = new Date(iso);
                return date.toLocaleString();
              }
            
              function isImageType(contentType: string): boolean {
                return contentType.includes('image/png') ||
                       contentType.includes('image/jpeg') ||
                       contentType.includes('image/svg');
              }
            
              function isTextType(contentType: string): boolean {
                return contentType.includes('text/') ||
                       contentType.includes('json') ||
                       contentType.includes('markdown');
              }
            
              let fileUrl = $derived(artifact && runMetadata ? getArtifactFileUrl(runMetadata.run_id, artifact.filename) : '');
            </script>
            
            <svelte:window onkeydown={handleKeydown} />
            
            {#if artifact && runMetadata}
              <div 
                class="overlay" 
                role="dialog" 
                aria-modal="true" 
                tabindex="-1"
                onclick={handleOverlayClick} 
                onkeydown={handleKeydown}
              >
                
                <div class="viewer-container">
            
                  <!-- Header -->
                  <div class="header">
                    <div class="title-section">
                      <span class="icon">{getFileIcon(artifact.content_type)}</span>
                      <div class="title-info">
                        <h2 class="filename">{artifact.filename}</h2>
                        <div class="meta">
                          Generated by <strong>{artifact.agent_id}</strong> · {formatBytes(artifact.size_bytes)}
                        </div>
                      </div>
                    </div>
                    <button class="btn-close" onclick={onClose} title="Close (Esc)">✕</button>
                  </div>
            
                  <!-- Content Preview -->
                  <div class="content-area">
                    {#if isImageType(artifact.content_type)}
                      <!-- Image Preview -->
                      <div class="preview-image">
                        <img src={fileUrl} alt={artifact.filename} />
                      </div>
                    {:else if isTextType(artifact.content_type)}
                      <!-- Text/Code Preview (iframe for simplicity) -->
                      <iframe src={fileUrl} title={artifact.filename} class="preview-text"></iframe>
                    {:else}
                      <!-- Generic File Preview -->
                      <div class="preview-generic">
                        <div class="file-icon-large">{getFileIcon(artifact.content_type)}</div>
                        <p class="preview-message">Preview not available for this file type</p>
                        <p class="preview-hint">{artifact.content_type}</p>
                      </div>
                    {/if}
                  </div>
            
                  <!-- Metadata Panel -->
                  <div class="metadata-panel">
                    <div class="metadata-grid">
                      <div class="metadata-item">
                        <span class="metadata-label">Run ID</span>
                        <span class="metadata-value" title={runMetadata.run_id}>{runMetadata.run_id.slice(0, 12)}...</span>
                      </div>
                      <div class="metadata-item">
                        <span class="metadata-label">Workflow</span>
                        <span class="metadata-value">{runMetadata.workflow_id}</span>
                      </div>
                      <div class="metadata-item">
                        <span class="metadata-label">Generated</span>
                        <span class="metadata-value">{formatTimestamp(artifact.generated_at)}</span>
                      </div>
                      <div class="metadata-item">
                        <span class="metadata-label">Content Type</span>
                        <span class="metadata-value">{artifact.content_type}</span>
                      </div>
                    </div>
                  </div>
            
                  <!-- Actions -->
                  <div class="actions">
                    <a href={fileUrl} download={artifact.filename} class="btn-action primary">
                      ⬇ Download
                    </a>
                    <button
                      class="btn-action secondary"
                      onclick={handlePromoteToLibrary}
                      disabled={isPromoting}
                    >
                      {#if isPromoting}
                        Promoting...
                      {:else}
                        ⭐ Save to Library
                      {/if}
                    </button>
                    <a href={fileUrl} target="_blank" class="btn-action secondary">
                      ↗ Open in New Tab
                    </a>
                  </div>
            
                </div>
              </div>
            {/if}
            
            <style>
              .overlay {
                position: fixed;
                top: 0;
                left: 0;
                width: 100vw;
                height: 100vh;
                background: rgba(0, 0, 0, 0.8);
                backdrop-filter: blur(8px);
                display: flex;
                align-items: center;
                justify-content: center;
                z-index: 1000;
                animation: fadeIn 0.2s ease;
              }
            
              @keyframes fadeIn {
                from { opacity: 0; }
                to { opacity: 1; }
              }
            
              .viewer-container {
                width: 90%;
                max-width: 900px;
                max-height: 90vh;
                background: var(--paper-surface);
                border: 2px solid var(--paper-line);
                box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
                display: flex;
                flex-direction: column;
                animation: slideUp 0.3s var(--ease-snap);
              }
            
              @keyframes slideUp {
                from {
                  opacity: 0;
                  transform: translateY(20px);
                }
                to {
                  opacity: 1;
                  transform: translateY(0);
                }
              }
            
              /* Header */
              .header {
                padding: 20px 24px;
                border-bottom: 1px solid var(--paper-line);
                display: flex;
                align-items: center;
                justify-content: space-between;
                background: var(--paper-bg);
              }
            
              .title-section {
                display: flex;
                align-items: center;
                gap: 12px;
                flex: 1;
                min-width: 0;
              }
            
              .icon {
                font-size: 32px;
                flex-shrink: 0;
              }
            
              .title-info {
                flex: 1;
                min-width: 0;
              }
            
              .filename {
                font-family: var(--font-code);
                font-size: 16px;
                font-weight: 700;
                color: var(--paper-ink);
                margin: 0 0 4px 0;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
              }
            
              .meta {
                font-family: var(--font-code);
                font-size: 11px;
                color: var(--paper-line);
              }
            
              .btn-close {
                background: transparent;
                border: none;
                font-size: 24px;
                color: var(--paper-ink);
                cursor: pointer;
                padding: 8px;
                line-height: 1;
                transition: opacity 0.2s;
              }
            
              .btn-close:hover {
                opacity: 0.7;
              }
            
              /* Content Area */
              .content-area {
                flex: 1;
                overflow: auto;
                background: var(--paper-bg);
                display: flex;
                align-items: center;
                justify-content: center;
                min-height: 300px;
              }
            
              .preview-image {
                width: 100%;
                height: 100%;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 24px;
              }
            
              .preview-image img {
                max-width: 100%;
                max-height: 100%;
                object-fit: contain;
                border: 1px solid var(--paper-line);
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
              }
            
              .preview-text {
                width: 100%;
                height: 100%;
                border: none;
                background: white;
              }
            
              .preview-generic {
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                gap: 12px;
                padding: 48px;
                text-align: center;
              }
            
              .file-icon-large {
                font-size: 64px;
                opacity: 0.5;
              }
            
              .preview-message {
                font-family: var(--font-code);
                font-size: 14px;
                color: var(--paper-ink);
                margin: 0;
              }
            
              .preview-hint {
                font-family: var(--font-code);
                font-size: 11px;
                color: var(--paper-line);
                margin: 0;
              }
            
              /* Metadata Panel */
              .metadata-panel {
                padding: 16px 24px;
                background: var(--paper-surface);
                border-top: 1px solid var(--paper-line);
              }
            
              .metadata-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 12px;
              }
            
              .metadata-item {
                display: flex;
                flex-direction: column;
                gap: 4px;
              }
            
              .metadata-label {
                font-family: var(--font-code);
                font-size: 9px;
                font-weight: 700;
                color: var(--paper-line);
                text-transform: uppercase;
                letter-spacing: 0.5px;
              }
            
              .metadata-value {
                font-family: var(--font-code);
                font-size: 11px;
                color: var(--paper-ink);
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
              }
            
              /* Actions */
              .actions {
                padding: 20px 24px;
                background: var(--paper-bg);
                border-top: 1px solid var(--paper-line);
                display: flex;
                gap: 12px;
                justify-content: flex-end;
              }
            
              .btn-action {
                padding: 10px 20px;
                font-family: var(--font-code);
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.5px;
                border: none;
                cursor: pointer;
                transition: all 0.2s;
                text-decoration: none;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                gap: 6px;
              }
            
              .btn-action.primary {
                background: var(--paper-ink);
                color: var(--paper-bg);
              }
            
              .btn-action.primary:hover {
                opacity: 0.9;
              }
            
              .btn-action.secondary {
                background: transparent;
                color: var(--paper-ink);
                border: 1px solid var(--paper-line);
              }
            
              .btn-action.secondary:hover {
                background: var(--paper-line);
              }
            
              .btn-action:disabled {
                opacity: 0.5;
                cursor: not-allowed;
              }
            </style>
        - ControlDeck.svelte
            <!-- // [[RARO]]/apps/web-console/src/components/ControlDeck.svelte
            // Purpose: Main interaction panel. Orchestrates the API call to start the run.
            // Architecture: View Controller
            // Dependencies: Stores, API -->
            
            <script lang="ts">
              import { selectedNode, agentNodes, pipelineEdges, addLog, updateNodeStatus,
                deselectNode, telemetry, connectRuntimeWebSocket, runtimeStore,
                planningMode,           // Import new store
                loadWorkflowManifest,    // Import new action
                attachedFiles,
                // Import graph mutation actions
                addConnection,
                removeConnection,
                createNode,
                deleteNode,
                renameNode              // Import rename action
              } from '$lib/stores'
              import { 
                startRun, 
                generateWorkflowPlan, // Import API call
                type WorkflowConfig, 
                type AgentConfig 
              } from '$lib/api'
              import { get } from 'svelte/store'
              // import { fade } from 'svelte/transition'
            
              let { expanded }: { expanded: boolean } = $props();
            
              let cmdInput = $state('')
              let activePane = $state('input') // 'input' | 'overview' | 'sim' | 'stats' | 'node-config' | 'pipeline'
              let currentModel = $state('fast')
              let currentPrompt = $state('')
              let currentAcceptsDirective = $state(false)  // Directive toggle state
              let currentAllowDelegation = $state(false)   // Delegation toggle state
              let thinkingBudget = $state(5)
              let isSubmitting = $state(false)
              let isInputFocused = $state(false)
              let tempId = $state('')  // Temporary ID for renaming
            
              // Pipeline control state
              let connectionFrom = $state('')
              let connectionTo = $state('')
              let nodeToDelete = $state('')
            
              // Reactive derivation for HITL state
              let isAwaitingApproval = $derived($runtimeStore.status === 'AWAITING_APPROVAL' || $runtimeStore.status === 'PAUSED')
            
              // Sync tempId when the selection changes
              $effect(() => {
                if ($selectedNode) {
                  tempId = $selectedNode; // Initialize input with current ID
                }
              });
            
              // === STATE SYNCHRONIZATION ===
              $effect(() => {
                if ($selectedNode && expanded) {
                  // 1. Node selected -> FORCE view to Config
                  // Load node specific data
                  const node = $agentNodes.find((n) => n.id === $selectedNode)
                  if (node) {
                    currentModel = node.model
                    currentPrompt = node.prompt
                    currentAcceptsDirective = node.acceptsDirective  // Load directive flag
                    currentAllowDelegation = node.allowDelegation    // Load delegation flag
                  }
            
                  // Force switch to node-config if not already there
                  if (activePane !== 'node-config') {
                    activePane = 'node-config'
                  }
                } else if (!$selectedNode && activePane === 'node-config') {
                  // 2. Node deselected while in config -> Fallback to Overview
                  activePane = 'overview'
                } else if (!expanded && activePane !== 'input' && !isAwaitingApproval) {
                  // 3. If collapsed, ensure we return to input mode (unless awaiting approval)
                  activePane = 'input'
                }
              });
            
              // Force expand if approval needed
              $effect(() => {
                  if (isAwaitingApproval && !expanded) {
                      // In a real app we might emit an event to parent, here we just assume user sees the indicator
                  }
              })
            
              // === 1. THE ARCHITECT HANDLER (Flow A: Planning) ===
              // Pure State Mutation: Generates graph, does NOT execute.
              async function submitPlan() {
                if (!cmdInput) return;
                isSubmitting = true;
                
                addLog('ARCHITECT', `Analyzing directive: "${cmdInput}"`, 'THINKING');
            
                try {
                    const manifest = await generateWorkflowPlan(cmdInput);
                    
                    // Pure state mutation via Store Action
                    loadWorkflowManifest(manifest);
                    
                    addLog('ARCHITECT', 'Graph construction complete.', 'DONE');
            
                } catch (e: any) {
                    addLog('ARCHITECT', `Planning failed: ${e.message}`, 'ERR');
                } finally {
                    isSubmitting = false;
                }
              }
            
              // === 2. THE KERNEL HANDLER (Flow B: Execution) ===
              // Pure Execution: Runs whatever is in the store.
              async function submitRun() {
                // Allow running if we have input OR if we have a graph to run
                if (!cmdInput && $agentNodes.length === 0) return
                if (isSubmitting) return
            
                isSubmitting = true;
                if (cmdInput) addLog('OPERATOR', `<strong>${cmdInput}</strong>`, 'EXECUTE');
            
                try {
                    // 1. Construct Workflow Config from Store State
                    const nodes = get(agentNodes)
                    const edges = get(pipelineEdges)
                    
                    // Map UI Nodes to Kernel AgentConfig
                    const agents: AgentConfig[] = nodes.map(n => {
                        // Find dependencies
                        const dependsOn = edges
                            .filter(e => e.to === n.id)
                            .map(e => e.from);
            
                        return {
                            id: n.id,
                            role: n.role,
                            model: n.model, // Use semantic alias directly (fast, reasoning, thinking)
                            tools: [],
                            input_schema: {},
                            output_schema: {},
                            cache_policy: 'ephemeral',
                            depends_on: dependsOn,
                            prompt: n.prompt,  // Keep persona clean
                            user_directive: (n.acceptsDirective && cmdInput) ? cmdInput : "",  // Inject directive based on flag
                            position: { x: n.x, y: n.y },
                            accepts_directive: n.acceptsDirective,  // Pass flag to backend
                            allow_delegation: n.allowDelegation     // Pass delegation flag to backend
                        };
                    });
            
                    const config: WorkflowConfig = {
                        id: `flow-${Date.now()}`,
                        name: 'RARO_Session',
                        agents: agents,
                        max_token_budget: 100000,
                        timeout_ms: 60000,
                        attached_files: get(attachedFiles) // <--- Send linked files
                    };
            
            
                    addLog('KERNEL', 'Compiling DAG manifest...', 'SYS');
            
                    // 2. Send to Kernel
                    const response = await startRun(config);
                    
                    addLog('KERNEL', `Workflow started. Run ID: ${response.run_id}`, 'OK');
                    
                    // 3. Connect WebSocket for live updates
                    connectRuntimeWebSocket(response.run_id);
            
                    cmdInput = '' // Clear input on successful run
            
                } catch (e: any) {
                    addLog('KERNEL', `Execution failed: ${e.message}`, 'ERR');
                } finally {
                    isSubmitting = false;
                }
              }
            
              // === 3. THE ROUTER ===
              function handleCommand() {
                if (isSubmitting) return;
                
                if ($planningMode) {
                    submitPlan();
                } else {
                    submitRun();
                }
              }
            
              function toggleMode() {
                planningMode.update(v => !v);
              }
            
              // === HELPERS ===
              function handlePaneSelect(pane: string) {
                activePane = pane
                if ($selectedNode) deselectNode()
              }
            
              function handleCloseNode() {
                deselectNode()
              }
            
              function saveNodeConfig() {
                  if (!$selectedNode) return;
                  agentNodes.update(nodes => nodes.map(n => {
                      if (n.id === $selectedNode) {
                          return { ...n, model: currentModel, prompt: currentPrompt, acceptsDirective: currentAcceptsDirective, allowDelegation: currentAllowDelegation }
                      }
                      return n;
                  }));
              }
            
              function handleKey(e: KeyboardEvent) {
                  if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      handleCommand(); // Route through the mode selector
                  }
              }
            
              function handleRename() {
                if (!$selectedNode || !tempId) return;
            
                // Clean the ID (remove spaces, etc if desired)
                const cleanId = tempId.trim().replace(/\s+/g, '_').toLowerCase();
            
                const success = renameNode($selectedNode, cleanId);
            
                if (success) {
                  addLog('SYSTEM', `Node renamed: ${cleanId}`, 'OK');
                } else {
                  // Revert if failed (duplicate or invalid)
                  tempId = $selectedNode;
                  addLog('SYSTEM', `Rename failed: ID exists or invalid`, 'WARN');
                }
              }
            
              function handleIdKey(e: KeyboardEvent) {
                  if (e.key === 'Enter') {
                      (e.target as HTMLInputElement).blur(); // Trigger onblur
                  }
              }
            
              // === PIPELINE CONTROL ACTIONS ===
              function handleAddConnection() {
                if (!connectionFrom || !connectionTo) {
                  addLog('GRAPH', 'Please select both source and target nodes', 'WARN')
                  return
                }
                addConnection(connectionFrom, connectionTo)
                addLog('GRAPH', `Connection added: ${connectionFrom} → ${connectionTo}`, 'OK')
                connectionFrom = ''
                connectionTo = ''
              }
            
              function handleRemoveConnection() {
                if (!connectionFrom || !connectionTo) {
                  addLog('GRAPH', 'Please select both source and target nodes', 'WARN')
                  return
                }
                removeConnection(connectionFrom, connectionTo)
                addLog('GRAPH', `Connection removed: ${connectionFrom} ⨯ ${connectionTo}`, 'OK')
                connectionFrom = ''
                connectionTo = ''
              }
            
              function handleCreateNode() {
                // Create node at center of viewport (normalized coords)
                const centerX = 50
                const centerY = 50
                createNode(centerX, centerY)
                addLog('GRAPH', 'New node created at center', 'OK')
              }
            
              function handleDeleteNode() {
                if (!nodeToDelete) {
                  addLog('GRAPH', 'Please select a node to delete', 'WARN')
                  return
                }
                const nodeLabel = $agentNodes.find(n => n.id === nodeToDelete)?.label || nodeToDelete
                deleteNode(nodeToDelete)
                addLog('GRAPH', `Node deleted: ${nodeLabel}`, 'OK')
                nodeToDelete = ''
              }
            </script>
            
            <div id="control-deck" class:architect-mode={expanded}>
              {#if expanded}
                <div id="deck-nav">
                  {#if activePane === 'node-config'}
                    <div class="nav-item node-tab active">
                      COMPONENT SETTINGS // {$selectedNode}
                    </div>
                    <div class="nav-item action-close" onclick={handleCloseNode}>×</div>
                  {:else}
                    <div class="nav-item {activePane === 'overview' ? 'active' : ''}" onclick={() => handlePaneSelect('overview')}>Overview</div>
                    <div class="nav-item {activePane === 'pipeline' ? 'active' : ''}" onclick={() => handlePaneSelect('pipeline')}>Pipeline</div>
                    <div class="nav-item {activePane === 'sim' ? 'active' : ''}" onclick={() => handlePaneSelect('sim')}>Simulation</div>
                    <div class="nav-item {activePane === 'stats' ? 'active' : ''}" onclick={() => handlePaneSelect('stats')}>Telemetry</div>
                  {/if}
                </div>
              {/if}
            
              <div class="pane-container">
            
                <!-- Normal Panes -->
                {#if !expanded || activePane === 'input'}
                  <!-- 1. INPUT CONSOLE -->
                  <div id="pane-input" class="deck-pane">
                    
                    <!-- === NEW: CONTEXT RACK (EXEC MODE ONLY) === -->
                    {#if !$planningMode && $attachedFiles.length > 0}
                        <div class="context-rack">
                            <div class="rack-label">LIB_LINK</div>
                            <div class="rack-files">
                                {#each $attachedFiles as file}
                                    <div class="ctx-chip">
                                        <div class="ctx-dot"></div>
                                        {file}
                                    </div>
                                {/each}
                            </div>
                        </div>
                    {/if}
            
                    <!-- Input Wrapper: Changes visual state based on Planning Mode -->
                    <div class="cmd-wrapper {isInputFocused ? 'focused' : ''} {$planningMode ? 'mode-plan' : ''}">
                        <textarea
                            id="cmd-input"
                            placeholder={$planningMode ? "ENTER ARCHITECTURAL DIRECTIVE..." : "ENTER RUNTIME DIRECTIVE..."}
                            bind:value={cmdInput}
                            disabled={isSubmitting || isAwaitingApproval}
                            onkeydown={handleKey}
                            onfocus={() => isInputFocused = true}
                            onblur={() => isInputFocused = false}
                        ></textarea>
                        
                        <!-- Main Action Button: Routes to handleCommand -->
                        <button 
                            id="btn-run" 
                            onclick={handleCommand} 
                            disabled={isSubmitting || isAwaitingApproval}
                        >
                            {#if isSubmitting}
                                <span class="loader"></span>
                            {:else if $planningMode}
                                <!-- Plan Icon -->
                                <span>◈</span> 
                            {:else}
                                <!-- Execute Icon -->
                                <span>↵</span>
                            {/if}
                        </button>
                    </div>
            
                    <!-- Footer: Mode Toggle & Hints -->
                    <div class="deck-footer">
                        
                        <!-- Mode Toggle Switch -->
                        <div 
                            class="mode-toggle" 
                            onclick={toggleMode} 
                            onkeydown={(e) => e.key === 'Enter' && toggleMode()}
                            role="button" 
                            tabindex="0"
                        >
                            <div class="toggle-label {!$planningMode ? 'active' : 'dim'}">EXEC</div>
                            
                            <div class="toggle-track">
                                <div class="toggle-thumb" style="left: {$planningMode ? '14px' : '2px'}"></div>
                            </div>
                            
                            <div class="toggle-label {$planningMode ? 'active' : 'dim'}">PLAN</div>
                        </div>
            
                        <!-- Dynamic Hint -->
                        <div class="input-hint">
                            {#if $planningMode}
                                GENERATIVE MODE // OVERWRITES GRAPH
                            {:else}
                                RUNTIME MODE // EXECUTES GRAPH
                            {/if}
                        </div>
                    </div>
                  </div>
            
                {:else if activePane === 'node-config'}
                  <!-- 2. NODE CONFIG -->
                  <div id="pane-node-config" class="deck-pane">
                    <div class="form-grid">
                      <div class="form-group">
                        <label>Agent ID</label>
                        <input
                          class="input-std"
                          bind:value={tempId}
                          onblur={handleRename}
                          onkeydown={handleIdKey}
                          disabled={$runtimeStore.status === 'RUNNING'}
                          placeholder="Enter unique ID..."
                        />
                      </div>
                      <div class="form-group">
                        <label>Model Runtime</label>
                        <select class="input-std" bind:value={currentModel} onchange={saveNodeConfig}>
                          <option value="fast">FAST</option>
                          <option value="reasoning">REASONING</option>
                          <option value="thinking">THINKING</option>
                        </select>
                      </div>
                    </div>
                    <div class="form-group">
                      <label>System Instruction (Prompt)</label>
                      <textarea 
                        class="input-std" 
                        bind:value={currentPrompt} 
                        oninput={saveNodeConfig}
                        style="height:80px; resize:none;"
                      ></textarea>
                    </div>
            
                    {#if currentModel === 'thinking'}
                      <div class="form-group deep-think-config">
                        <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
                            <label>Thinking Budget (Depth)</label>
                            <span class="slider-value-badge">LEVEL {thinkingBudget}</span>
                        </div>
            
                        <div class="slider-container">
                          <input type="range" min="1" max="10" bind:value={thinkingBudget} class="thinking-slider"/>
                        </div>
            
                        <div class="slider-description">
                          {#if thinkingBudget <= 3}
                            <span>Fast reasoning with focused hypothesis generation.</span>
                          {:else if thinkingBudget <= 6}
                            <span>Balanced reasoning depth for synthesis tasks.</span>
                          {:else}
                            <span>Extended thinking for complex cross-paper analysis.</span>
                          {/if}
                        </div>
                      </div>
                    {/if}
            
                    <!-- DIRECTIVE INPUT PORT -->
                    <div class="form-group directive-port-config">
                      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                        <label>Operator Directive Port</label>
                        <button
                          class="port-toggle {currentAcceptsDirective ? 'port-open' : 'port-closed'}"
                          onclick={() => {
                            currentAcceptsDirective = !currentAcceptsDirective;
                            saveNodeConfig();
                          }}
                        >
                          {currentAcceptsDirective ? 'LISTENING' : 'LOCKED'}
                        </button>
                      </div>
            
                      <div class="directive-hint">
                        {#if currentAcceptsDirective}
                          <span class="hint-active">This node will receive operator directives at runtime</span>
                        {:else}
                          <span class="hint-inactive">Enable to inject runtime commands directly to this node</span>
                        {/if}
                      </div>
                    </div>
            
                    <!-- DELEGATION CAPABILITY -->
                    <div class="form-group directive-port-config">
                      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                        <label>Delegation Capability</label>
                        <button
                          class="port-toggle {currentAllowDelegation ? 'port-open' : 'port-closed'}"
                          onclick={() => {
                            currentAllowDelegation = !currentAllowDelegation;
                            saveNodeConfig();
                          }}
                        >
                          {currentAllowDelegation ? 'ENABLED' : 'DISABLED'}
                        </button>
                      </div>
            
                      <div class="directive-hint">
                        {#if currentAllowDelegation}
                          <span class="hint-active">This node can spawn sub-agents and modify the workflow graph</span>
                        {:else}
                          <span class="hint-inactive">Enable to allow this node to dynamically create sub-agents</span>
                        {/if}
                      </div>
                    </div>
                  </div>
            
                {:else if activePane === 'overview'}
                   <div id="pane-overview" class="deck-pane">
                    <div class="form-grid">
                      <div class="form-group"><label>Pipeline Identifier</label><input class="input-std" value="RARO_Live_Session" readonly /></div>
                      <div class="form-group"><label>Max Token Budget</label><input class="input-std" value="100,000" /></div>
                      <div class="form-group"><label>Service Status</label><div class="status-indicator">ONLINE</div></div>
                      <div class="form-group">
                        <label>Persistence Layer</label>
                        <select class="input-std">
                          <option>Redis (Hot)</option>
                          <option>PostgreSQL (Cold)</option>
                        </select>
                      </div>
                    </div>
                  </div>
                {:else if activePane === 'pipeline'}
                  <div id="pane-pipeline" class="deck-pane compact-pane">
                    <div class="pipeline-compact">
            
                      <!-- LEFT COLUMN: Connection & Node Controls -->
                      <div class="control-col">
                        <div class="compact-section">
                          <div class="section-mini-header">CONNECTIONS</div>
                          <div class="compact-row">
                            <select class="input-mini" bind:value={connectionFrom}>
                              <option value="">From...</option>
                              {#each $agentNodes as node}
                                <option value={node.id}>{node.label}</option>
                              {/each}
                            </select>
                            <span class="arrow-sep">→</span>
                            <select class="input-mini" bind:value={connectionTo}>
                              <option value="">To...</option>
                              {#each $agentNodes as node}
                                <option value={node.id}>{node.label}</option>
                              {/each}
                            </select>
                          </div>
                          <div class="btn-row">
                            <button class="btn-mini add-btn" onclick={handleAddConnection} title="Add Connection">+</button>
                            <button class="btn-mini remove-btn" onclick={handleRemoveConnection} title="Remove Connection">−</button>
                          </div>
                        </div>
            
                        <div class="compact-section">
                          <div class="section-mini-header">NODES</div>
                          <div class="btn-row">
                            <button class="btn-mini create-btn" onclick={handleCreateNode} title="Create Node">+ New</button>
                          </div>
                          <div class="compact-row" style="margin-top: 6px;">
                            <select class="input-mini" bind:value={nodeToDelete} style="flex: 1;">
                              <option value="">Select to delete...</option>
                              {#each $agentNodes as node}
                                <option value={node.id}>{node.label}</option>
                              {/each}
                            </select>
                            <button class="btn-mini delete-btn" onclick={handleDeleteNode} disabled={!nodeToDelete} title="Delete Node">⊗</button>
                          </div>
                        </div>
                      </div>
            
                      <!-- RIGHT COLUMN: Topology Display -->
                      <div class="topology-col">
                        <div class="compact-section">
                          <div class="section-mini-header">TOPOLOGY</div>
                          <div class="topo-stats">
                            <div class="topo-stat">
                              <span class="topo-num">{$agentNodes.length}</span>
                              <span class="topo-label">Nodes</span>
                            </div>
                            <div class="topo-stat">
                              <span class="topo-num">{$pipelineEdges.length}</span>
                              <span class="topo-label">Edges</span>
                            </div>
                          </div>
                          <div class="edge-list">
                            {#each $pipelineEdges.slice(0, 6) as edge}
                              <div class="edge-micro">
                                {($agentNodes.find(n => n.id === edge.from)?.label || edge.from).substring(0, 8)} → {($agentNodes.find(n => n.id === edge.to)?.label || edge.to).substring(0, 8)}
                              </div>
                            {/each}
                            {#if $pipelineEdges.length > 6}
                              <div class="edge-micro more">+{$pipelineEdges.length - 6} more</div>
                            {/if}
                          </div>
                        </div>
                      </div>
            
                    </div>
                  </div>
                {:else if activePane === 'sim'}
                  <div id="pane-sim" class="deck-pane">
                    <div style="display:flex; gap:10px; margin-bottom:15px;">
                      <button class="input-std action-btn" onclick={() => addLog('SYSTEM', 'Simulating step 1...')}>▶ STEP EXECUTION</button>
                      <button class="input-std action-btn" onclick={() => addLog('SYSTEM', 'Resetting context...')}>↺ RESET CONTEXT</button>
                    </div>
                    <div class="sim-terminal">
                      &gt; Ready for test vector injection...<br />
                      &gt; Agents loaded: {$agentNodes.length}
                    </div>
                  </div>
                {:else if activePane === 'stats'}
                  <div id="pane-stats" class="deck-pane">
                    <div class="stat-grid">
                      <div class="stat-card"><span class="stat-val">{($telemetry.tokensUsed / 1000).toFixed(1)}k</span><span class="stat-lbl">Tokens</span></div>
                      <div class="stat-card"><span class="stat-val">${$telemetry.totalCost.toFixed(4)}</span><span class="stat-lbl">Est. Cost</span></div>
                      <div class="stat-card"><span class="stat-val">{$telemetry.errorCount}</span><span class="stat-lbl">Errors</span></div>
                      <div class="stat-card"><span class="stat-val">LIVE</span><span class="stat-lbl">Mode</span></div>
                    </div>
                  </div>
                {/if}
              </div>
            </div>
            
            <style>
              /* === LAYOUT & BASICS === */
              #control-deck {
                height: 160px;
                background: var(--paper-bg);
                border-top: 1px solid var(--paper-line);
                display: flex;
                flex-direction: column;
                transition: height 0.5s var(--ease-snap), background 0.3s, border-color 0.3s;
                position: relative;
                z-index: 150;
              }
              #control-deck.architect-mode { height: 260px; }
            
              /* NAVIGATION */
              #deck-nav { 
                height: 36px; 
                background: var(--paper-surface); 
                border-bottom: 1px solid var(--paper-line); 
                display: flex; 
                flex-shrink: 0; 
                overflow: hidden; 
              }
              
              .nav-item { 
                flex: 1; 
                display: flex; 
                align-items: center; 
                justify-content: center; 
                font-size: 10px; 
                font-weight: 600; 
                text-transform: uppercase; 
                letter-spacing: 0.5px; 
                color: var(--paper-line);
                cursor: pointer; 
                border-right: 1px solid var(--paper-line); 
                transition: all 0.2s; 
              }
              
              .nav-item:hover { 
                color: var(--paper-ink); 
                background: var(--paper-bg);
              }
              
              .nav-item.active { 
                background: var(--paper-bg); 
                color: var(--paper-ink); 
                border-bottom: 2px solid var(--paper-ink); 
              }
              
              .nav-item.node-tab { 
                flex: 4; 
                justify-content: flex-start; 
                padding-left: 20px; 
                background: var(--paper-bg); 
                color: var(--paper-ink); 
                border-bottom: 2px solid var(--paper-ink); 
                cursor: default; 
              }
              
              .action-close { 
                flex: 0; 
                min-width: 50px; 
                font-size: 16px; 
                color: #d32f2f;
                border-right: none; 
                border-left: 1px solid var(--paper-line); 
              }
              
              .action-close:hover { 
                background: var(--paper-surface-dim); 
                color: #b71c1c; 
              }
            
              .pane-container { flex: 1; overflow: hidden; position: relative; display: flex; flex-direction: column; }
              .deck-pane { flex: 1; height: 100%; padding: 20px; overflow-y: auto; }
            
              /* === INPUT CONSOLE STYLING === */
              #pane-input {
                  display: flex;
                  flex-direction: column;
                  justify-content: center;
                  padding-bottom: 8px; /* Give space for the new footer */
              }
            
              /* === CONTEXT RACK (FILES) === */
              .context-rack {
                  margin-bottom: 12px;
                  display: flex;
                  align-items: center;
                  gap: 12px;
                  animation: slideDown 0.2s ease-out;
              }
              .rack-label {
                  font-family: var(--font-code);
                  font-size: 9px;
                  color: var(--paper-line);
                  font-weight: 700;
                  letter-spacing: 1px;
                  flex-shrink: 0;
              }
              .rack-files {
                  display: flex;
                  gap: 8px;
                  flex-wrap: wrap;
                  overflow: hidden;
              }
              .ctx-chip {
                  font-family: var(--font-code);
                  font-size: 9px;
                  color: var(--paper-ink);
                  background: var(--paper-surface);
                  border: 1px solid var(--paper-line);
                  padding: 2px 6px;
                  border-radius: 2px;
                  display: flex;
                  align-items: center;
                  gap: 6px;
                  white-space: nowrap;
                  cursor: default;
              }
              .ctx-dot {
                  width: 4px; height: 4px;
                  background: var(--alert-amber);
                  border-radius: 50%;
                  box-shadow: 0 0 4px var(--alert-amber);
              }
              
              @keyframes slideDown {
                  from { opacity: 0; transform: translateY(5px); }
                  to { opacity: 1; transform: translateY(0); }
              }
            
            
              /* The floating "Device" wrapper for input */
              .cmd-wrapper {
                  display: flex;
                  background: var(--paper-bg);
                  border: 1px solid var(--paper-line);
                  height: 80px; 
                  transition: border-color 0.2s, box-shadow 0.2s;
              }
            
              /* Highlight for Planning Mode */
              .cmd-wrapper.mode-plan {
                  border-color: var(--arctic-cyan);
                  box-shadow: 0 0 10px rgba(0, 240, 255, 0.15); /* Soft cyan glow */
              }
            
              .cmd-wrapper.focused {
                  border-color: var(--paper-ink);
                  box-shadow: 0 4px 12px rgba(0,0,0,0.05);
              }
            
              #cmd-input {
                  flex: 1;
                  border: none;
                  background: transparent;
                  padding: 16px;
                  font-family: var(--font-code);
                  font-size: 13px;
                  color: var(--paper-ink);
                  resize: none;
                  outline: none;
              }
            
              #cmd-input::placeholder { opacity: 0.4; text-transform: uppercase; color: var(--paper-ink); }
            
              #btn-run {
                  width: 60px;
                  border: none;
                  border-left: 1px solid var(--paper-line);
                  background: var(--paper-surface);
                  color: var(--paper-ink); /* Default for Execute */
                  font-weight: 900;
                  font-size: 20px;
                  cursor: pointer;
                  transition: all 0.1s;
                  display: flex; align-items: center; justify-content: center;
              }
            
              /* Color change for button icon when in Planning Mode */
              .cmd-wrapper.mode-plan #btn-run {
                  color: var(--arctic-cyan); /* Architect icon color */
              }
            
              #btn-run:hover:not(:disabled) { 
                background: var(--paper-ink); 
                color: var(--paper-bg); 
              }
              
              #btn-run:active:not(:disabled) { 
                opacity: 0.8; 
              }
              
              #btn-run:disabled { 
                background: var(--paper-surface-dim); 
                color: var(--paper-line); 
                cursor: not-allowed; 
              }
            
              /* === DECK FOOTER & MODE TOGGLE === */
              .deck-footer {
                  display: flex;
                  justify-content: space-between;
                  align-items: center;
                  margin-top: 10px; /* Space from the cmd-wrapper */
                  padding: 0 4px; /* Slight horizontal padding */
                  width: 100%;
              }
            
              .input-hint {
                  font-family: var(--font-code);
                  font-size: 9px;
                  color: var(--paper-line);
                  text-align: right; /* Aligned to the right of the footer */
                  letter-spacing: 0.5px;
              }
            
              /* === MODE TOGGLE === */
              .mode-toggle {
                  display: flex;
                  align-items: center;
                  gap: 8px;
                  cursor: pointer;
                  user-select: none;
                  opacity: 0.8;
                  transition: opacity 0.2s;
                  outline: none; /* Remove default focus outline */
              }
              .mode-toggle:hover { opacity: 1; }
              /* Custom focus style */
              .mode-toggle:focus-visible { outline: 1px dotted var(--arctic-cyan); outline-offset: 2px; }
            
              .toggle-label {
                  font-family: var(--font-code);
                  font-size: 9px;
                  font-weight: 700;
                  letter-spacing: 1px;
                  transition: color 0.3s;
              }
              .toggle-label.active { color: var(--paper-ink); }
              .toggle-label.dim { color: var(--paper-line); }
            
              .toggle-track {
                  width: 28px; /* Slightly wider track */
                  height: 12px;
                  background: var(--paper-surface);
                  border: 1px solid var(--paper-line);
                  border-radius: 6px;
                  position: relative;
                  transition: background 0.2s;
              }
            
              .toggle-thumb {
                  width: 8px;
                  height: 8px;
                  background: var(--paper-ink);
                  border-radius: 50%;
                  position: absolute;
                  top: 1px; /* Center vertically in track */
                  transition: left 0.2s var(--ease-snap), background 0.2s;
              }
              /* Thumb color for Planning Mode */
              .cmd-wrapper.mode-plan + .deck-footer .mode-toggle .toggle-thumb {
                  background: var(--arctic-cyan);
              }
            
              /* === FORMS & UTILS === */
              .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
              .form-group { margin-bottom: 16px; }
              
              label { 
                display: block; 
                font-size: 9px; 
                color: var(--paper-line);
                text-transform: uppercase; 
                margin-bottom: 6px; 
                font-weight: 600; 
              }
              
              .input-std { 
                width: 100%; 
                padding: 10px; 
                border: 1px solid var(--paper-line); 
                background: var(--paper-bg);
                font-family: var(--font-code); 
                font-size: 12px; 
                color: var(--paper-ink); 
                outline: none; 
              }
              
              .input-std:focus { border-color: var(--paper-ink); }
              
              .input-readonly { 
                background: var(--paper-surface); 
                color: var(--paper-line);
                cursor: default; 
              }
              
              .status-indicator { color: #00C853; font-weight: 700; font-size: 11px; margin-top: 10px; }
              
              .action-btn { 
                width: auto; 
                cursor: pointer; 
                background: var(--paper-ink);
                color: var(--paper-bg);
                border: 1px solid var(--paper-ink);
              }
              .action-btn:hover {
                  background: var(--paper-bg);
                  color: var(--paper-ink);
              }
              
              .sim-terminal { 
                font-family: var(--font-code); 
                font-size: 11px; 
                color: var(--paper-ink);
                background: var(--paper-bg);
                border: 1px solid var(--paper-line); 
                padding: 10px; 
                height: 100px; 
                overflow-y: auto; 
              }
            
              /* === STATS === */
              .stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
              
              .stat-card { 
                border: 1px solid var(--paper-line); 
                background: var(--paper-bg);
                padding: 12px; 
                text-align: center; 
              }
              
              .stat-val { 
                font-size: 16px; 
                font-weight: 700; 
                color: var(--paper-ink); 
                display: block; 
              }
              
              .stat-lbl { 
                font-size: 9px; 
                color: var(--paper-line);
                text-transform: uppercase; 
                margin-top: 4px; 
                display: block; 
              }
            
              /* === SLIDER === */
              .deep-think-config { 
                padding: 16px; 
                background: var(--paper-surface-dim);
                border: 1px solid var(--paper-line); 
                border-radius: 0; 
              }
              
              .slider-value-badge { 
                font-size: 10px; 
                background: var(--paper-ink); 
                color: var(--paper-bg);
                padding: 2px 6px; 
                border-radius: 2px; 
              }
              
              .slider-container { display: flex; align-items: center; margin: 12px 0; }
              
              .thinking-slider {
                flex: 1; 
                -webkit-appearance: none; 
                height: 4px; 
                background: var(--paper-line);
                outline: none;
              }
              
              .thinking-slider::-webkit-slider-thumb {
                -webkit-appearance: none; 
                width: 16px; 
                height: 16px; 
                border-radius: 0; 
                background: var(--paper-ink); 
                cursor: ew-resize; 
                border: 2px solid var(--paper-bg);
                box-shadow: 0 1px 3px rgba(0,0,0,0.3); 
                transition: transform 0.1s;
              }
              
              .thinking-slider::-webkit-slider-thumb:hover { transform: scale(1.2); }
              
              .thinking-slider::-moz-range-thumb {
                width: 16px; 
                height: 16px; 
                border-radius: 0; 
                background: var(--paper-ink); 
                cursor: ew-resize; 
                border: 2px solid var(--paper-bg);
                box-shadow: 0 1px 3px rgba(0,0,0,0.3);
              }
            
              .slider-description { 
                font-size: 11px; 
                color: var(--paper-line);
                font-style: italic; 
                min-height: 1.2em; 
              }
              
              /* Loader */
              .loader {
                width: 16px;
                height: 16px;
                border: 2px solid var(--paper-line);
                border-bottom-color: transparent;
                border-radius: 50%;
                display: inline-block;
                box-sizing: border-box;
                animation: rotation 1s linear infinite;
              }
            
              @keyframes rotation { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
            
              /* === PIPELINE CONTROL - COMPACT === */
              .compact-pane {
                padding: 12px !important;
              }
            
              .pipeline-compact {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 12px;
                height: 100%;
              }
            
              .control-col, .topology-col {
                display: flex;
                flex-direction: column;
                gap: 10px;
              }
            
              .compact-section {
                background: var(--paper-surface-dim);
                border: 1px solid var(--paper-line);
                padding: 10px;
              }
            
              .section-mini-header {
                font-size: 8px;
                font-weight: 700;
                color: var(--paper-line);
                text-transform: uppercase;
                letter-spacing: 0.8px;
                margin-bottom: 8px;
                padding-bottom: 4px;
                border-bottom: 1px solid var(--paper-line);
              }
            
              .compact-row {
                display: flex;
                align-items: center;
                gap: 6px;
              }
            
              .arrow-sep {
                color: var(--paper-line);
                font-size: 12px;
                font-weight: 600;
                flex-shrink: 0;
              }
            
              .input-mini {
                flex: 1;
                padding: 6px 8px;
                border: 1px solid var(--paper-line);
                background: var(--paper-bg);
                font-family: var(--font-code);
                font-size: 10px;
                color: var(--paper-ink);
                outline: none;
                height: 28px;
              }
            
              .input-mini:focus {
                border-color: var(--paper-ink);
              }
            
              .btn-row {
                display: flex;
                gap: 6px;
                margin-top: 6px;
              }
            
              .btn-mini {
                flex: 1;
                padding: 6px 10px;
                border: 1px solid;
                font-family: var(--font-code);
                font-size: 10px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.15s;
                height: 28px;
                display: flex;
                align-items: center;
                justify-content: center;
              }
            
              .add-btn {
                background: var(--paper-bg);
                color: #00C853;
                border-color: #00C853;
              }
            
              .add-btn:hover:not(:disabled) {
                background: #00C853;
                color: var(--paper-bg);
              }
            
              .remove-btn {
                background: var(--paper-bg);
                color: #FF6F00;
                border-color: #FF6F00;
              }
            
              .remove-btn:hover:not(:disabled) {
                background: #FF6F00;
                color: var(--paper-bg);
              }
            
              .create-btn {
                background: var(--paper-bg);
                color: var(--arctic-cyan);
                border-color: var(--arctic-cyan);
              }
            
              .create-btn:hover:not(:disabled) {
                background: var(--arctic-cyan);
                color: var(--paper-bg);
              }
            
              .delete-btn {
                background: var(--paper-bg);
                color: #d32f2f;
                border-color: #d32f2f;
                flex: 0 0 32px;
              }
            
              .delete-btn:hover:not(:disabled) {
                background: #d32f2f;
                color: var(--paper-bg);
              }
            
              .delete-btn:disabled {
                opacity: 0.35;
                cursor: not-allowed;
              }
            
              /* Topology Display */
              .topo-stats {
                display: flex;
                gap: 12px;
                margin-bottom: 8px;
                padding: 8px;
                background: var(--paper-bg);
                border: 1px solid var(--paper-line);
              }
            
              .topo-stat {
                display: flex;
                flex-direction: column;
                align-items: center;
                flex: 1;
              }
            
              .topo-num {
                font-size: 16px;
                font-weight: 700;
                color: var(--paper-ink);
                font-family: var(--font-code);
              }
            
              .topo-label {
                font-size: 8px;
                color: var(--paper-line);
                text-transform: uppercase;
                margin-top: 2px;
              }
            
              .edge-list {
                background: var(--paper-bg);
                border: 1px solid var(--paper-line);
                padding: 6px;
                max-height: 110px;
                overflow-y: auto;
              }
            
              .edge-micro {
                font-family: var(--font-code);
                font-size: 9px;
                color: var(--paper-ink);
                padding: 3px 6px;
                margin-bottom: 3px;
                background: var(--paper-surface);
                border-left: 2px solid var(--paper-line);
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
              }
            
              .edge-micro.more {
                color: var(--paper-line);
                font-style: italic;
                border-left-color: transparent;
                text-align: center;
              }
            
              /* === DIRECTIVE PORT CONFIG === */
              .directive-port-config {
                margin-top: 16px;
                padding-top: 16px;
                border-top: 1px solid var(--paper-line);
              }
            
              .port-toggle {
                padding: 6px 14px;
                border: 1px solid;
                font-family: var(--font-code);
                font-size: 10px;
                font-weight: 700;
                cursor: pointer;
                transition: all 0.2s;
                letter-spacing: 0.5px;
              }
            
              .port-toggle.port-open {
                background: var(--paper-bg);
                color: var(--arctic-lilac, #00E5FF);
                border-color: var(--arctic-cyan, #00E5FF);
                box-shadow: 0 0 8px rgba(0, 229, 255, 0.3);
              }
            
              .port-toggle.port-open:hover {
                background: var(--arctic-lilac);
                color: var(--paper-bg);
              }
            
              .port-toggle.port-closed {
                background: var(--paper-bg);
                color: var(--paper-line);
                border-color: var(--paper-line);
              }
            
              .port-toggle.port-closed:hover {
                border-color: var(--paper-ink);
                color: var(--paper-ink);
              }
            
              .directive-hint {
                margin-top: 8px;
                padding: 8px;
                background: var(--paper-surface-dim);
                border-left: 2px solid;
                font-family: var(--font-code);
                font-size: 10px;
              }
            
              .hint-active {
                color: var(--arctic-cyan, #00E5FF);
                border-color: var(--arctic-cyan, #00E5FF);
              }
            
              .hint-inactive {
                color: var(--paper-line);
                border-color: var(--paper-line);
              }
            </style>
        - EnvironmentRail.svelte
            <!-- [[RARO]]/apps/web-console/src/components/EnvironmentRail.svelte -->
            <!-- Purpose: Left-hand navigation rail for File System and Artifact management. -->
            <!-- Architecture: UI Component -->
            <!-- Dependencies: stores, api -->
            
            <script lang="ts">
              import { onMount } from 'svelte';
              import { libraryFiles, attachedFiles, toggleAttachment, addLog, runtimeStore } from '$lib/stores';
              import { getLibraryFiles, uploadFile, getAllArtifacts, deleteArtifactRun, getArtifactFileUrl, type ArtifactMetadata, type ArtifactFile } from '$lib/api';
              import Spinner from './sub/Spinner.svelte';
              import ArtifactViewer from './ArtifactViewer.svelte';
            
              // Props using Svelte 5 runes syntax
              let { currentRunId = null }: { currentRunId?: string | null } = $props();
            
              let hovered = $state(false);
              let isRefreshing = $state(false);
            
              // === UPLOAD STATE ===
              let isUploading = $state(false);
              let fileInput = $state<HTMLInputElement>();
            
              // === ARTIFACT STATE ===
              let artifacts = $state<ArtifactMetadata[]>([]);
              let artifactFilter = $state<'all' | 'recent'>('all');
              let lastRefreshTime = $state<number>(0);
              let lastRuntimeStatus = $state<string>('');
              let hasNewArtifacts = $state<boolean>(false);
              let newArtifactTimer: number | null = null;
            
              // === VIEWER STATE ===
              let selectedArtifact = $state<ArtifactFile | null>(null);
              let selectedRunMetadata = $state<ArtifactMetadata | null>(null);
            
              // === AUTO-REFRESH STATE ===
              let refreshDebounceTimer: number | null = null;
            
              // Initial Load
              onMount(async () => {
                refreshAll();
            
                // Subscribe to runtime status changes
                const unsubscribe = runtimeStore.subscribe((state) => {
                  // Auto-refresh artifacts when workflow completes
                  if (state.status === 'COMPLETED' && lastRuntimeStatus !== 'COMPLETED') {
                    console.log('[EnvironmentRail] Workflow completed, refreshing artifacts...');
                    silentRefreshArtifacts();
                  }
                  lastRuntimeStatus = state.status;
                });
            
                return () => {
                  unsubscribe();
                  if (refreshDebounceTimer) {
                    clearTimeout(refreshDebounceTimer);
                  }
                  if (newArtifactTimer) {
                    clearTimeout(newArtifactTimer);
                  }
                };
              });
            
              // Watch for expansion and trigger auto-refresh
              $effect(() => {
                if (hovered) {
                  handleExpansion();
                }
              });
            
              function handleExpansion() {
                // Debounce: Only refresh if rail hasn't been refreshed in the last 5 seconds
                const now = Date.now();
                if (now - lastRefreshTime > 5000) {
                  silentRefreshArtifacts();
                }
              }
            
              async function refreshAll() {
                await Promise.all([refreshLibrary(), refreshArtifacts()]);
              }
            
              async function refreshLibrary() {
                isRefreshing = true;
                try {
                  const files = await getLibraryFiles();
                  libraryFiles.set(files);
                } catch (err) {
                  console.error(err);
                } finally {
                  isRefreshing = false;
                }
              }
            
              async function refreshArtifacts() {
                try {
                  artifacts = await getAllArtifacts();
                  lastRefreshTime = Date.now();
                } catch (err) {
                  console.error('Failed to fetch artifacts:', err);
                }
              }
            
              async function silentRefreshArtifacts() {
                try {
                  const newArtifacts = await getAllArtifacts();
            
                  // Delta detection: Only update if there are actual changes
                  const hasChanges = detectArtifactChanges(artifacts, newArtifacts);
            
                  if (hasChanges) {
                    console.log('[EnvironmentRail] New artifacts detected, updating UI...');
                    artifacts = newArtifacts;
                    lastRefreshTime = Date.now();
            
                    // Show visual indicator
                    hasNewArtifacts = true;
            
                    // Clear indicator after 3 seconds
                    if (newArtifactTimer) clearTimeout(newArtifactTimer);
                    newArtifactTimer = setTimeout(() => {
                      hasNewArtifacts = false;
                    }, 3000) as unknown as number;
                  } else {
                    console.log('[EnvironmentRail] No artifact changes detected');
                    lastRefreshTime = Date.now();
                  }
                } catch (err) {
                  console.error('Failed to silently refresh artifacts:', err);
                }
              }
            
              function detectArtifactChanges(
                oldArtifacts: ArtifactMetadata[],
                newArtifacts: ArtifactMetadata[]
              ): boolean {
                // Quick check: different lengths means changes
                if (oldArtifacts.length !== newArtifacts.length) {
                  return true;
                }
            
                // Deep check: compare run IDs and file counts
                const oldSignature = oldArtifacts
                  .map(a => `${a.run_id}:${a.artifacts.length}`)
                  .sort()
                  .join('|');
            
                const newSignature = newArtifacts
                  .map(a => `${a.run_id}:${a.artifacts.length}`)
                  .sort()
                  .join('|');
            
                return oldSignature !== newSignature;
              }
            
              function handleRefresh() {
                refreshAll();
                addLog('SYSTEM', 'Environment refreshed.', 'IO_OK');
              }
            
              // === HANDLE UPLOAD ===
              async function handleFileUpload(e: Event) {
                const target = e.target as HTMLInputElement;
                if (!target.files || target.files.length === 0) return;
            
                const file = target.files[0];
                isUploading = true;
                addLog('SYSTEM', `Uploading ${file.name} to library...`, 'IO_UP');
            
                try {
                  await uploadFile(file);
                  addLog('SYSTEM', 'Upload complete.', 'IO_OK');
                  await refreshLibrary();
                } catch (err) {
                  addLog('SYSTEM', `Upload failed: ${err}`, 'IO_ERR');
                } finally {
                  isUploading = false;
                  target.value = '';
                }
              }
            
              function triggerUpload() {
                fileInput?.click();
              }
            
              // === ARTIFACT HANDLING ===
              let filteredArtifacts = $derived(filterArtifacts(artifacts, artifactFilter));
            
              function filterArtifacts(all: ArtifactMetadata[], filter: string): ArtifactMetadata[] {
                if (filter === 'recent') {
                  const oneDayAgo = Date.now() - 24 * 60 * 60 * 1000;
                  return all.filter(a => new Date(a.created_at).getTime() > oneDayAgo);
                }
                return all;
              }
            
              function getTimeAgo(isoDate: string): string {
                const ms = Date.now() - new Date(isoDate).getTime();
                const mins = Math.floor(ms / 60000);
                if (mins < 60) return `${mins}m ago`;
                const hours = Math.floor(mins / 60);
                if (hours < 24) return `${hours}h ago`;
                return `${Math.floor(hours / 24)}d ago`;
              }
            
              function getFileIcon(contentType: string): string {
                if (contentType.includes('image')) return '📊';
                if (contentType.includes('json')) return '📋';
                if (contentType.includes('csv')) return '📈';
                if (contentType.includes('markdown')) return '📝';
                if (contentType.includes('pdf')) return '📄';
                return '📄';
              }
            
              async function handleDeleteRun(runId: string, e: Event) {
                e.stopPropagation();
                if (!confirm('Delete all artifacts from this run?')) return;
            
                try {
                  await deleteArtifactRun(runId);
                  addLog('SYSTEM', 'Artifact run deleted.', 'IO_OK');
            
                  // Immediately update UI by removing the run
                  artifacts = artifacts.filter(a => a.run_id !== runId);
                  lastRefreshTime = Date.now();
                } catch (err) {
                  addLog('SYSTEM', 'Failed to delete artifacts.', 'IO_ERR');
                }
              }
            
              function handleArtifactClick(artifact: ArtifactFile, runMetadata: ArtifactMetadata, e: Event) {
                e.preventDefault();
                selectedArtifact = artifact;
                selectedRunMetadata = runMetadata;
              }
            
              function closeViewer() {
                selectedArtifact = null;
                selectedRunMetadata = null;
              }
            
              // Calculate total file count
              let totalFileCount = $derived($libraryFiles.length + artifacts.reduce((sum, a) => sum + a.artifacts.length, 0));
            </script>
            
            <div
              class="env-rail {hovered ? 'expanded' : ''}"
              onmouseenter={() => hovered = true}
              onmouseleave={() => hovered = false}
              role="complementary"
            >
              <div class="milled-bg"></div>
            
              <div class="rail-container">
            
                <!-- TOP: LABEL -->
                <div class="sector top">
                  <div class="label-vertical">ENV</div>
                  <div class="micro-bolt"></div>
                </div>
            
                <!-- MIDDLE: CONTENT -->
                <div class="sector middle">
            
                  <!-- COLLAPSED STATE -->
                  <div class="compact-view" style="opacity: {hovered ? 0 : 1}">
                    <div class="disk-indicator {$attachedFiles.length > 0 ? 'active' : ''}">
                      {#if isRefreshing || isUploading}<Spinner />{/if}
                    </div>
                    {#if totalFileCount > 0}
                      <div class="count-badge">{totalFileCount}</div>
                    {/if}
                  </div>
            
                  <!-- EXPANDED STATE -->
                  <div class="expanded-view" style="opacity: {hovered ? 1 : 0}; pointer-events: {hovered ? 'auto' : 'none'}">
            
                    <!-- LIBRARY FILES SECTION -->
                    <section class="file-section">
                      <div class="panel-header">
                        📚 LIBRARY FILES
                        <button class="btn-icon" onclick={handleRefresh} disabled={isRefreshing || isUploading} title="Refresh">
                          {#if isRefreshing}⟳{:else}↻{/if}
                        </button>
                      </div>
            
                      <div class="file-list">
                        {#each $libraryFiles as file}
                          <button
                            class="file-item {$attachedFiles.includes(file) ? 'linked' : ''}"
                            onclick={() => toggleAttachment(file)}
                          >
                            <div class="status-led"></div>
                            <span class="filename" title={file}>{file}</span>
                            <span class="link-status">
                              {$attachedFiles.includes(file) ? 'LINKED' : 'IDLE'}
                            </span>
                          </button>
                        {/each}
            
                        {#if $libraryFiles.length === 0}
                          <div class="empty-state">
                            {#if isRefreshing}SCANNING...{:else}NO FILES{/if}
                          </div>
                        {/if}
                      </div>
            
                      <div class="actions">
                        <input
                          type="file"
                          bind:this={fileInput}
                          onchange={handleFileUpload}
                          style="display:none"
                        />
            
                        <button class="btn-action upload" onclick={triggerUpload} disabled={isRefreshing || isUploading}>
                          {#if isUploading}UPLOADING...{:else}↑ UPLOAD{/if}
                        </button>
                      </div>
                    </section>
            
                    <div class="divider"></div>
            
                    <!-- GENERATED ARTIFACTS SECTION -->
                    <section class="artifact-section">
                      <div class="panel-header">
                        🎨 ARTIFACTS
                        {#if hasNewArtifacts}
                          <span class="new-badge">NEW</span>
                        {/if}
                      </div>
            
                      <div class="controls">
                        <select bind:value={artifactFilter} class="filter-select">
                          <option value="all">All Runs</option>
                          <option value="recent">Last 24h</option>
                        </select>
                      </div>
            
                      <div class="artifact-list">
                        {#each filteredArtifacts as run}
                          <div class="run-group">
                            <div class="run-header">
                              <span class="run-id" title={run.run_id}>{run.run_id.slice(0, 8)}...</span>
                              <span class="run-time">{getTimeAgo(run.created_at)}</span>
                              <button class="btn-delete" onclick={(e) => handleDeleteRun(run.run_id, e)} title="Delete">
                                🗑️
                              </button>
                            </div>
            
                            {#each run.artifacts as artifact}
                              <button
                                class="artifact-item"
                                onclick={(e) => handleArtifactClick(artifact, run, e)}
                              >
                                <span class="icon">{getFileIcon(artifact.content_type)}</span>
                                <div class="artifact-info">
                                  <div class="artifact-name">{artifact.filename}</div>
                                  <div class="artifact-meta">by {artifact.agent_id}</div>
                                </div>
                                <span class="preview-icon">👁</span>
                              </button>
                            {/each}
                          </div>
                        {/each}
            
                        {#if filteredArtifacts.length === 0}
                          <div class="empty-state">No artifacts yet</div>
                        {/if}
                      </div>
                    </section>
            
                  </div>
            
                </div>
            
                <!-- BOTTOM: DECOR -->
                <div class="sector bottom">
                  <div class="micro-bolt"></div>
                  <div class="label-vertical">IO</div>
                </div>
              </div>
            </div>
            
            <!-- Artifact Viewer Overlay -->
            <ArtifactViewer
              artifact={selectedArtifact}
              runMetadata={selectedRunMetadata}
              onClose={closeViewer}
            />
            
            <style>
              .env-rail {
                position: absolute; left: 0; top: 0;
                height: 100vh; width: 48px;
                border-right: 1px solid var(--paper-line);
                background: var(--paper-bg);
                display: flex; flex-direction: column;
                transition: width 0.3s var(--ease-snap), background-color 0.3s;
                overflow: hidden; z-index: 50;
              }
            
              .env-rail.expanded {
                width: 260px;
                background: var(--paper-surface);
                box-shadow: 15px 0 50px rgba(0,0,0,0.1);
              }
            
              .milled-bg {
                position: absolute; top: 0; left: 0; width: 100%; height: 100%;
                opacity: 0.03;
                background-image: repeating-linear-gradient(-45deg, transparent, transparent 1px, var(--paper-ink) 1px, var(--paper-ink) 2px);
                pointer-events: none;
              }
            
              .rail-container {
                position: relative; z-index: 2; height: 100%;
                display: flex; flex-direction: column; justify-content: space-between;
              }
            
              .sector { display: flex; flex-direction: column; align-items: center; padding: 24px 0; gap: 12px; }
              .sector.middle { flex: 1; justify-content: flex-start; padding-top: 60px; width: 100%; }
            
              .label-vertical {
                writing-mode: vertical-lr; text-orientation: mixed; transform: rotate(180deg);
                font-family: var(--font-code); font-size: 8px;
                color: var(--paper-line); letter-spacing: 1px; font-weight: 700;
              }
            
              .micro-bolt { width: 4px; height: 4px; background: var(--paper-line); border-radius: 50%; opacity: 0.5; }
            
              /* COMPACT VIEW */
              .compact-view { position: absolute; top: 50%; transform: translateY(-50%); display: flex; flex-direction: column; gap: 8px; align-items: center; }
            
              .disk-indicator {
                width: 8px; height: 8px; background: var(--paper-line); border-radius: 1px;
                transition: all 0.3s;
              }
              .disk-indicator.active { background: var(--alert-amber); box-shadow: 0 0 6px var(--alert-amber); }
            
              .count-badge {
                font-family: var(--font-code); font-size: 9px; color: var(--paper-bg);
                background: var(--paper-ink); padding: 1px 4px; border-radius: 2px;
              }
            
              /* EXPANDED VIEW */
              .expanded-view {
                width: 100%; height: 100%; padding: 0 16px;
                display: flex; flex-direction: column; gap: 12px;
                overflow-y: auto;
              }
            
              .panel-header {
                font-family: var(--font-code); font-size: 10px; font-weight: 700;
                color: var(--paper-ink); border-bottom: 1px solid var(--paper-line);
                padding-bottom: 8px; letter-spacing: 1px;
                display: flex; align-items: center; justify-content: space-between;
                gap: 8px;
              }
            
              .new-badge {
                font-size: 8px;
                font-weight: 700;
                color: var(--paper-bg);
                background: var(--alert-amber);
                padding: 2px 6px;
                border-radius: 2px;
                animation: pulse-badge 0.5s ease-in-out;
              }
            
              @keyframes pulse-badge {
                0% {
                  opacity: 0;
                  transform: scale(0.8);
                }
                50% {
                  opacity: 1;
                  transform: scale(1.1);
                }
                100% {
                  opacity: 1;
                  transform: scale(1);
                }
              }
            
              .btn-icon {
                background: transparent; border: none;
                color: var(--paper-ink); font-size: 14px;
                cursor: pointer; padding: 0 4px;
              }
              .btn-icon:hover { opacity: 0.7; }
              .btn-icon:disabled { opacity: 0.3; cursor: wait; }
            
              /* LIBRARY FILES SECTION */
              .file-section {
                flex-shrink: 0;
              }
            
              .file-list {
                max-height: 180px; overflow-y: auto; display: flex; flex-direction: column; gap: 4px;
                margin-bottom: 8px;
              }
            
              .file-item {
                background: transparent; border: 1px solid transparent;
                padding: 8px; display: flex; align-items: center; gap: 8px;
                cursor: pointer; transition: all 0.2s; text-align: left;
                border-radius: 2px;
              }
            
              .file-item:hover { background: var(--paper-bg); border-color: var(--paper-line); }
            
              .file-item.linked {
                background: color-mix(in srgb, var(--paper-ink), transparent 95%);
                border-color: var(--paper-ink);
              }
            
              .status-led {
                width: 4px; height: 4px; background: var(--paper-line); border-radius: 50%;
              }
              .file-item.linked .status-led { background: var(--alert-amber); box-shadow: 0 0 4px var(--alert-amber); }
            
              .filename {
                flex: 1; font-family: var(--font-code); font-size: 10px; color: var(--paper-ink);
                white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
              }
            
              .link-status {
                font-size: 7px; font-weight: 700; color: var(--paper-line);
              }
              .file-item.linked .link-status { color: var(--paper-ink); }
            
              .actions {
                display: flex; flex-direction: column; gap: 8px;
              }
            
              .btn-action {
                background: var(--paper-ink); color: var(--paper-bg);
                border: none; padding: 8px; font-family: var(--font-code);
                font-size: 10px; font-weight: 700; cursor: pointer; letter-spacing: 1px;
                height: 32px; display: flex; align-items: center; justify-content: center;
              }
            
              .btn-action:hover { opacity: 0.9; }
              .btn-action:disabled { opacity: 0.5; cursor: wait; }
            
              .divider {
                height: 1px; background: var(--paper-line); margin: 8px 0;
              }
            
              /* ARTIFACTS SECTION */
              .artifact-section {
                flex: 1;
                min-height: 0;
                display: flex;
                flex-direction: column;
              }
            
              .controls {
                margin-bottom: 8px;
              }
            
              .filter-select {
                width: 100%;
                padding: 6px 8px;
                background: var(--paper-bg);
                color: var(--paper-ink);
                border: 1px solid var(--paper-line);
                font-family: var(--font-code);
                font-size: 9px;
                border-radius: 2px;
                cursor: pointer;
              }
            
              .artifact-list {
                flex: 1;
                overflow-y: auto;
                display: flex;
                flex-direction: column;
                gap: 12px;
              }
            
              .run-group {
                border-left: 2px solid var(--paper-line);
                padding-left: 8px;
              }
            
              .run-header {
                display: flex;
                align-items: center;
                gap: 6px;
                font-size: 9px;
                color: var(--paper-line);
                margin-bottom: 6px;
              }
            
              .run-id {
                font-family: var(--font-code);
                flex: 1;
              }
            
              .run-time {
                font-size: 8px;
              }
            
              .btn-delete {
                background: transparent;
                border: none;
                cursor: pointer;
                font-size: 10px;
                padding: 0 2px;
                opacity: 0.5;
              }
              .btn-delete:hover { opacity: 1; }
            
              .artifact-item {
                display: flex;
                align-items: center;
                gap: 8px;
                padding: 6px 8px;
                background: var(--paper-bg);
                border: 1px solid var(--paper-line);
                border-radius: 2px;
                margin-bottom: 4px;
                text-decoration: none;
                color: inherit;
                transition: all 0.2s;
                width: 100%;
                cursor: pointer;
                text-align: left;
              }
            
              .artifact-item:hover {
                background: var(--paper-surface);
                border-color: var(--paper-ink);
              }
            
              .icon {
                font-size: 14px;
                flex-shrink: 0;
              }
            
              .artifact-info {
                flex: 1;
                min-width: 0;
              }
            
              .artifact-name {
                font-size: 10px;
                font-family: var(--font-code);
                color: var(--paper-ink);
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
              }
            
              .artifact-meta {
                font-size: 8px;
                color: var(--paper-line);
              }
            
              .preview-icon {
                font-size: 12px;
                opacity: 0.5;
              }
            
              .artifact-item:hover .preview-icon {
                opacity: 1;
              }
            
              .empty-state {
                font-family: var(--font-code); font-size: 10px; color: var(--paper-line);
                text-align: center; margin-top: 20px; opacity: 0.5;
              }
            </style>
        - Hero.svelte
            <!-- // [[RARO]]/apps/web-console/src/components/Hero.svelte -->
            <!-- Purpose: The "Monolith" Boot Interface. High-fidelity entry point. -->
            <!-- Architecture: UX/UI Component -->
            <!-- Dependencies: Svelte Transition, Local Assets -->
            
            <script lang="ts">
              import { fade, fly } from 'svelte/transition';
              import { onMount } from 'svelte';
              import { USE_MOCK } from '$lib/api'; 
            
              let { onenter }: { onenter: () => void } = $props();
            
              // === STATE MACHINE ===
              type SystemState = 'IDLE' | 'CHARGING' | 'LOCKED' | 'BOOTING';
              let sysState = $state<SystemState>('IDLE');
              
              // === CAPACITOR LOGIC ===
              let chargeLevel = $state(0);
              let chargeVelocity = 0;
              let rafId: number;
            
              // === TERMINAL LOGIC ===
              let logs = $state<string[]>([]);
              let cursorVisible = $state(true);
            
              // Initial "Idle" Animation
              onMount(() => {
                const cursorInterval = setInterval(() => cursorVisible = !cursorVisible, 500);
                
                // Add some initial "noise" to the system
                setTimeout(() => logs.push("KERNEL_DAEMON_OK"), 200);
                
                // === MOCK DETECTION LOG ===
                if (USE_MOCK) {
                  setTimeout(() => logs.push(">> VIRTUAL_ENVIRONMENT_DETECTED"), 400);
                  setTimeout(() => logs.push(">> BYPASSING_HARDWARE_LINKS..."), 500);
                }
            
                setTimeout(() => logs.push("MEMORY_INTEGRITY_CHECK..."), 600);
                
                return () => clearInterval(cursorInterval);
              });
            
              // === INTERACTION HANDLERS ===
            
              function startCharge() {
                if (sysState === 'BOOTING' || sysState === 'LOCKED') return;
                sysState = 'CHARGING';
                
                let lastTime = performance.now();
            
                const loop = (now: number) => {
                  if (sysState !== 'CHARGING') return;
                  
                  const dt = now - lastTime;
                  lastTime = now;
            
                  // Physics: Charge accelerates but encounters "Resistance" near 100%
                  // This creates tactile "weight"
                  const baseSpeed = 0.15; 
                  const resistance = Math.max(0, (chargeLevel - 80) * 0.005);
                  
                  chargeLevel = Math.min(chargeLevel + (baseSpeed - resistance) * dt, 100);
            
                  if (chargeLevel >= 100) {
                    commitBoot();
                  } else {
                    rafId = requestAnimationFrame(loop);
                  }
                };
                rafId = requestAnimationFrame(loop);
              }
            
              function releaseCharge() {
                if (sysState === 'BOOTING' || sysState === 'LOCKED') return;
                sysState = 'IDLE';
                
                // Rapid discharge visual
                const discharge = () => {
                  if (sysState === 'CHARGING') return; // User grabbed it again
                  
                  chargeLevel = Math.max(0, chargeLevel - 5);
                  if (chargeLevel > 0) {
                    requestAnimationFrame(discharge);
                  }
                };
                requestAnimationFrame(discharge);
              }
            
              // === BOOT SEQUENCE ===
            
              function commitBoot() {
                sysState = 'LOCKED';
                chargeLevel = 100;
                
                // The "Sequence"
                const seq = [
                  { t: 0, msg: ">> INTERRUPT_SIGNAL_RECEIVED" },
                  { t: 200, msg: ">> ELEVATING_PRIVILEGES..." },
                  { t: 600, msg: ">> MOUNTING_AGENT_SWARM [RW]" },
                  { t: 1000, msg: ">> CONNECTING_TO_ORCHESTRATOR..." },
                ];
            
                // Add specific mock confirmation in boot sequence
                if (USE_MOCK) {
                    seq.push({ t: 1200, msg: ">> !! MOCK_ADAPTER_ENGAGED !!" });
                } else {
                    seq.push({ t: 1200, msg: ">> LIVE_SOCKET_ESTABLISHED" });
                }
            
                seq.push({ t: 1400, msg: ">> RARO_RUNTIME_ENGAGED" });
            
                seq.forEach(step => {
                  setTimeout(() => {
                    logs = [...logs, step.msg];
                    // Keep terminal scrolled to bottom
                    const el = document.getElementById('term-feed');
                    if(el) el.scrollTop = el.scrollHeight;
                  }, step.t);
                });
            
                setTimeout(() => {
                  sysState = 'BOOTING';
                  onenter();
                }, 1800);
              }
            </script>
            
            <div class="viewport" out:fade={{ duration: 600 }}>
              
              <!-- OPTIONAL: NOISE TEXTURE OVERLAY -->
              <div class="noise-layer"></div>
            
              <!-- THE MONOLITH -->
              <div class="monolith-stack">
                
                <!-- 1. THE SHADOW SLAB (Depth Anchor) -->
                <div class="slab-shadow"></div>
            
                <!-- 2. THE MAIN UNIT -->
                <div class="slab-main">
                  
                  <!-- A. HEADER BAR -->
                  <div class="machine-header">
                    <div class="brand-zone">
                      <div class="logo-type">RARO <span class="dim">//</span> KERNEL</div>
                      <div class="build-tag">
                          BUILD_2026.01.02
                          {#if USE_MOCK}<span class="tag-mock">::SIM</span>{/if}
                      </div>
                    </div>
                    
                    <!-- Status Array -->
                    <div class="status-zone">
                       <div class="status-dot {sysState === 'CHARGING' ? 'amber' : ''} {sysState === 'LOCKED' ? 'cyan' : ''}"></div>
                       <div class="status-label">
                         {#if sysState === 'IDLE'}STANDBY{:else if sysState === 'CHARGING'}ARMING{:else}ACTIVE{/if}
                       </div>
                    </div>
                  </div>
            
                  <!-- B. CONTENT GRID -->
                  <div class="machine-body">
                    
                    <!-- LEFT: Typography Engine -->
                    <div class="col-left">
                      <div class="hero-block">
                         <h1>RECURSIVE</h1>
                         <h1>AGENT</h1>
                         <h1>REASONING<span class="dot">.</span></h1>
                      </div>
                      
                      <div class="meta-block">
                        <p>
                          High-latency orchestration runtime for <span class="highlight">Gemini 3 Protocol</span>.
                          Designed for deep-context synthesis and multi-hop reasoning chains.
                        </p>
                      </div>
                    </div>
            
                    <!-- RIGHT: Telemetry Viewport -->
                    <div class="col-right">
                      <div class="terminal-frame">
                        <div class="scanlines"></div>
                        <div class="terminal-header">
                          <span>SYS_OUT</span>
                          
                          <!-- MOCK INDICATOR -->
                          {#if USE_MOCK}
                            <span class="mock-warning">MOCK_ENV</span>
                          {/if}
            
                          <span>TTY_1</span>
                        </div>
                        
                        <div id="term-feed" class="terminal-content">
                          {#each logs as log}
                            <div class="line" in:fly={{ y: 5, duration: 100 }}>{log}</div>
                          {/each}
                          <div class="line cursor-line">
                            <span class="prompt">root@raro:~#</span> 
                            <span class="cursor" style:opacity={cursorVisible ? 1 : 0}>█</span>
                          </div>
                        </div>
                      </div>
                    </div>
            
                  </div>
            
                  <!-- C. INTERACTION DECK (The Trigger) -->
                  <div class="machine-footer">
                    <button 
                      class="trigger-plate"
                      onmousedown={startCharge}
                      onmouseup={releaseCharge}
                      onmouseleave={releaseCharge}
                      ontouchstart={startCharge}
                      ontouchend={releaseCharge}
                      disabled={sysState === 'LOCKED' || sysState === 'BOOTING'}
                    >
                      <!-- The Capacitor Fill -->
                      <div class="capacitor-bar" style="width: {chargeLevel}%"></div>
                      
                      <!-- The Data Overlay -->
                      <div class="trigger-data">
                        <div class="label-primary">
                          {#if sysState === 'LOCKED' || sysState === 'BOOTING'}
                            SYSTEM_ENGAGED
                          {:else}
                            INITIALIZE_RUNTIME
                          {/if}
                        </div>
                        
                        <div class="label-secondary">
                          <span class="bracket">[</span>
                          <span class="val">{Math.floor(chargeLevel).toString().padStart(3, '0')}%</span>
                          <span class="bracket">]</span>
                        </div>
                      </div>
            
                    </button>
                  </div>
            
                </div>
              </div>
            
            </div>
            
            <style>
              /* === 1. GLOBAL VIEWPORT === */
              .viewport {
                width: 100%; height: 100vh;
                display: flex; align-items: center; justify-content: center;
                background: var(--paper-bg);
                position: absolute; top: 0; left: 0; z-index: 1000;
                overflow: hidden;
              }
            
              .noise-layer {
                position: absolute; top: 0; left: 0; width: 100%; height: 100%;
                background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)' opacity='0.04'/%3E%3C/svg%3E");
                pointer-events: none;
                z-index: 0;
              }
            
              /* === 2. THE MONOLITH STACK === */
              .monolith-stack {
                position: relative;
                width: 700px;
                z-index: 1;
              }
            
              /* The physical depth shadow layer */
              .slab-shadow {
                position: absolute;
                top: 12px; left: 12px;
                width: 100%; height: 100%;
                background: #1a1918;
                z-index: 0;
                opacity: 0.1;
              }
            
              /* The Main Interface Unit */
              .slab-main {
                position: relative;
                background: var(--paper-surface);
                border: 1px solid var(--paper-line);
                z-index: 1;
                display: flex; flex-direction: column;
                box-shadow: 0 40px 80px -20px rgba(0,0,0,0.15); /* Soft ambient float */
              }
            
              /* === 3. HEADER === */
              .machine-header {
                height: 48px;
                border-bottom: 1px solid var(--paper-line);
                display: flex; justify-content: space-between; align-items: center;
                padding: 0 24px;
                background: #fff;
              }
            
              .logo-type { font-family: var(--font-code); font-weight: 700; font-size: 12px; letter-spacing: 1px; color: var(--paper-ink); }
              .dim { color: #ccc; }
              .build-tag { font-family: var(--font-code); font-size: 9px; color: #888; margin-top: 2px; }
              .tag-mock { color: var(--alert-amber); font-weight: 700; margin-left: 4px; }
            
              .status-zone { display: flex; align-items: center; gap: 8px; }
              .status-label { font-family: var(--font-code); font-size: 9px; font-weight: 700; letter-spacing: 1px; color: var(--paper-ink); }
              
              .status-dot { width: 6px; height: 6px; background: #ccc; border-radius: 50%; }
              .status-dot.amber { background: #FFB300; box-shadow: 0 0 8px #FFB300; animation: blink 0.1s infinite; }
              .status-dot.cyan { background: #00F0FF; box-shadow: 0 0 8px #00F0FF; }
            
              @keyframes blink { 50% { opacity: 0.5; } }
            
              /* === 4. BODY LAYOUT === */
              .machine-body {
                display: grid;
                grid-template-columns: 1.4fr 1fr;
                min-height: 320px;
              }
            
              /* Left Column: Typography */
              .col-left {
                padding: 40px 32px;
                display: flex; flex-direction: column; justify-content: space-between;
                border-right: 1px solid var(--paper-line);
              }
            
              .hero-block h1 {
                font-family: var(--font-ui);
                font-size: 56px;
                font-weight: 900;
                line-height: 0.82;
                letter-spacing: -3px;
                color: var(--paper-ink);
                margin: 0;
              }
              .dot { color: #A53F2B; }
            
              .meta-block {
                font-family: var(--font-code);
                font-size: 11px;
                line-height: 1.6;
                color: #666;
                max-width: 90%;
                margin-top: 40px;
              }
              .highlight { color: var(--paper-ink); font-weight: 700; border-bottom: 1px solid #ccc; }
            
              /* Right Column: Terminal */
              .col-right {
                background: #FAFAFA;
                padding: 24px;
                display: flex; flex-direction: column;
              }
            
              .terminal-frame {
                flex: 1;
                background: #111;
                border: 1px solid #333;
                position: relative;
                overflow: hidden;
                display: flex; flex-direction: column;
                box-shadow: inset 0 0 20px rgba(0,0,0,0.5);
              }
            
              .scanlines {
                position: absolute; top: 0; left: 0; width: 100%; height: 100%;
                background: linear-gradient(rgba(18, 16, 16, 0) 50%, rgba(0, 0, 0, 0.25) 50%), linear-gradient(90deg, rgba(255, 0, 0, 0.06), rgba(0, 255, 0, 0.02), rgba(0, 0, 255, 0.06));
                background-size: 100% 2px, 3px 100%;
                pointer-events: none; z-index: 10;
              }
            
              .terminal-header {
                height: 24px; background: #222; border-bottom: 1px solid #333;
                display: flex; justify-content: space-between; align-items: center;
                padding: 0 8px;
                font-family: var(--font-code); font-size: 8px; color: #666;
              }
              
              .mock-warning {
                color: var(--alert-amber);
                font-weight: 700;
                animation: blink 1s infinite;
              }
            
              .terminal-content {
                flex: 1;
                padding: 12px;
                font-family: var(--font-code); font-size: 10px; color: #8b949e;
                overflow-y: hidden; /* Programmatic scroll */
                display: flex; flex-direction: column; justify-content: flex-end;
              }
            
              .line { margin-bottom: 4px; word-break: break-all; }
              .prompt { color: var(--arctic-lilac); margin-right: 6px; }
              .cursor { color: var(--arctic-lilac); }
            
              /* === 5. TRIGGER DECK === */
              .machine-footer {
                height: 80px;
                border-top: 1px solid var(--paper-line);
                padding: 0; /* Full bleed button */
              }
            
              .trigger-plate {
                width: 100%; height: 100%;
                background: #fff;
                border: none;
                position: relative;
                cursor: pointer;
                overflow: hidden;
                transition: background 0.2s;
              }
            
              .trigger-plate:hover:not(:disabled) { background: #fdfdfd; }
              .trigger-plate:disabled { cursor: default; }
            
              /* The Capacitor Bar */
              .capacitor-bar {
                position: absolute; top: 0; left: 0; height: 100%;
                background: var(--paper-ink);
                z-index: 1;
                /* No transition for instant physical feel */
              }
              
              /* Success State */
              .trigger-plate:disabled .capacitor-bar { background: var(--arctic-lilac-lite); transition: background 0.4s; }
            
              /* Data Overlay */
              .trigger-data {
                position: relative; z-index: 2;
                width: 100%; height: 100%;
                display: flex; justify-content: space-between; align-items: center;
                padding: 0 32px;
                mix-blend-mode: difference;
                color: white; /* Inverts to black on white bg, white on black fill */
              }
              
              /* Isolate stacking context for mix-blend-mode */
              .trigger-plate { isolation: isolate; }
            
              .label-primary { font-family: var(--font-code); font-weight: 700; font-size: 14px; letter-spacing: 2px; }
              
              .label-secondary { font-family: var(--font-code); font-size: 12px; letter-spacing: 1px; opacity: 0.8; margin-right: 30px; }
              .val { display: inline-block; width: 40px; text-align: center; }
            
            </style>
        - OutputPane.svelte
            <!-- [[RARO]]/apps/web-console/src/components/OutputPane.svelte -->
            <script lang="ts">
              import { logs, updateLog, runtimeStore, type LogEntry } from '$lib/stores'
              import Typewriter from './sub/Typewriter.svelte'
              import SmartText from './sub/SmartText.svelte'
              import ApprovalCard from './sub/ApprovalCard.svelte'
              import ArtifactCard from './sub/ArtifactCard.svelte'
              import ToolExecutionCard from './sub/ToolExecutionCard.svelte'
              import { tick } from 'svelte';
            
              // Refs & Scroll Logic
              let scrollContainer = $state<HTMLDivElement | null>(null);
              let contentWrapper = $state<HTMLDivElement | null>(null);
              let isPinnedToBottom = $state(true);
              let isAutoScrolling = false;
            
              // === GROUPING LOGIC ===
              let groupedLogs = $derived.by(() => {
                const rawLogs = $logs;
                const groups: { id: string, role: string, items: LogEntry[] }[] = [];
                
                if (rawLogs.length === 0) return [];
            
                let currentGroup = {
                  id: rawLogs[0].id,
                  role: rawLogs[0].role,
                  items: [rawLogs[0]]
                };
            
                for (let i = 1; i < rawLogs.length; i++) {
                  const log = rawLogs[i];
                  if (log.role === currentGroup.role) {
                    currentGroup.items.push(log);
                  } else {
                    groups.push(currentGroup);
                    currentGroup = {
                      id: log.id,
                      role: log.role,
                      items: [log]
                    };
                  }
                }
                groups.push(currentGroup);
                return groups;
              });
            
              function handleScroll() {
                if (!scrollContainer) return;
                if (isAutoScrolling) return;
                const { scrollTop, scrollHeight, clientHeight } = scrollContainer;
                const distanceFromBottom = scrollHeight - (scrollTop + clientHeight);
                isPinnedToBottom = distanceFromBottom < 50;
              }
            
              function scrollToBottom(behavior: ScrollBehavior = 'auto') {
                if (!scrollContainer) return;
                isAutoScrolling = true;
                try {
                  scrollContainer.scrollTo({ top: scrollContainer.scrollHeight, behavior });
                } finally {
                  requestAnimationFrame(() => { isAutoScrolling = false; });
                }
              }
            
              $effect(() => {
                if (!contentWrapper) return;
                const observer = new ResizeObserver(() => {
                  if (isPinnedToBottom) scrollToBottom('auto'); 
                });
                observer.observe(contentWrapper);
                return () => observer.disconnect();
              });
            
              $effect(() => {
                const _l = $logs;
                tick().then(() => {
                  if (isPinnedToBottom) {
                    // Use 'auto' (instant) instead of 'smooth' for live logs to prevent
                    // the viewport from lagging behind the Typewriter speed.
                    scrollToBottom('auto');
                  }
                });
              });
            
              function handleTypewriterComplete(id: string) {
                updateLog(id, { isAnimated: false });
              }
            
              // === UPDATED: Extraction Logic for Multiple Files ===
              function extractAllFilenames(msg: string): string[] {
                  const files: string[] = [];
            
                  // 1. Match RFS System Tags: [SYSTEM: Generated Image saved to 'filename.png']
                  // Allow optional spaces \s* around colons and brackets
                  const systemRegex = /\[\s*SYSTEM\s*:\s*Generated\s*(?:Image|File)\s*saved\s*to\s*'([^']+)'\s*\]/gi;
                  let match;
                  while ((match = systemRegex.exec(msg)) !== null) {
                      files.push(match[1]);
                  }
            
                  // 2. Match Markdown Images: ![alt](filename.png)
                  // (Used as fallback or for agents explicitly outputting MD)
                  // Updated regex to catch non-image extensions too in case agent formatted them as links
                  const mdRegex = /!\[.*?\]\(([^)]+\.(?:png|jpg|jpeg|svg|json|csv|txt))\)/gi;
                  while ((match = mdRegex.exec(msg)) !== null) {
                      // Avoid duplicates if both formats exist for the same file
                      if (!files.includes(match[1])) {
                          files.push(match[1]);
                      }
                  }
            
                  return files;
              }
            
              // [[FIXED]]: Removes BOTH Markdown images AND the System Tag text
              function stripSystemTags(msg: string): string {
                  let cleaned = msg;
            
                  // 1. Remove standard markdown images: ![alt](url)
                  // Updated to match the broader regex in extraction
                  cleaned = cleaned.replace(/!\[.*?\]\(([^)]+\.(?:png|jpg|jpeg|svg|json|csv|txt))\)/gi, '');
            
                  // 2. Remove RFS System Tags (Relaxed Regex)
                  cleaned = cleaned.replace(/\[\s*SYSTEM\s*:\s*Generated\s*(?:Image|File)\s*saved\s*to\s*'[^']+'\s*\]/gi, '');
            
                  return cleaned.trim();
              }
            </script>
            
            <div id="output-pane" bind:this={scrollContainer} onscroll={handleScroll}>
              <div class="log-wrapper" bind:this={contentWrapper}>
                
                {#each groupedLogs as group (group.id)}
                  <div class="log-group">
                    
                    <!-- COLUMN 1: Agent Identity -->
                    <div class="group-meta">
                        <span class="group-role">{group.role}</span>
                        <div class="timeline-line"></div>
                    </div>
            
                    <!-- COLUMN 2: Stack of Events -->
                    <div class="group-body">
                      {#each group.items as log (log.id)}
                        <div class="log-item">
                          
                          <!-- Inline Metadata Header -->
                          {#if log.metadata && log.metadata !== 'INFO'}
                            <div class="item-meta-header">
                              <span class="meta-tag">{log.metadata}</span>
                              <span class="meta-time">{new Date(log.timestamp).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'})}</span>
                            </div>
                          {/if}
            
                          <!-- Content Renderer -->
                          <div class="log-content">
                            {#if log.category === 'TOOL_CALL' || log.category === 'TOOL_RESULT'}
                              <ToolExecutionCard
                                category={log.category}
                                message={log.message}
                                metadata={log.metadata || 'INFO'}
                                agentId={log.role}
                                isComplete={log.isComplete}
                                toolResult={log.toolResult}
                                toolStatus={log.toolStatus}
                              />
                            
                            {:else if log.metadata === 'INTERVENTION'}
                              <ApprovalCard
                                reason={log.message === 'SAFETY_PATTERN_TRIGGERED' ? "System Policy Violation" : log.message}
                                runId={$runtimeStore.runId || ''}
                              />
                            
                            {:else if log.isAnimated}
                              <Typewriter
                                text={log.message}
                                onComplete={() => handleTypewriterComplete(log.id)}
                              />
                            
                            {:else}
                              <!-- Static Text + Artifacts -->
                              {@const fileList = extractAllFilenames(log.message)}
                              
                              <!-- [[FIXED]]: Use the robust cleaning function -->
                              <SmartText text={stripSystemTags(log.message)} />
                              
                              {#if fileList.length > 0}
                                 <!-- Single Card, Array of Files -->
                                 <ArtifactCard 
                                    filenames={fileList} 
                                    runId={$runtimeStore.runId || ''} 
                                 />
                              {/if}
                            {/if}
                          </div>
                        </div>
                      {/each}
                    </div>
            
                  </div>
                {/each}
            
              </div>
            </div>
            
            <style>
              :global(.error-block) { background: rgba(211, 47, 47, 0.05); border-left: 3px solid #d32f2f; color: var(--paper-ink); padding: 10px; margin-top: 8px; font-family: var(--font-code); font-size: 11px; white-space: pre-wrap; word-break: break-all; }
              :global(.log-content strong) { color: var(--paper-ink); font-weight: 700; }
              
              #output-pane { flex: 1; padding: 24px; overflow-y: auto; display: flex; flex-direction: column; scrollbar-gutter: stable; will-change: scroll-position; }
              
              .log-wrapper { display: flex; flex-direction: column; gap: 0; min-height: min-content; }
            
              /* GROUP CONTAINER */
              .log-group {
                display: grid; 
                grid-template-columns: 60px 1fr;
                gap: 16px;
                padding: 16px 0;
                border-top: 1px dashed var(--paper-line);
                animation: slideUp 0.2s cubic-bezier(0.16, 1, 0.3, 1) forwards;
              }
            
              @keyframes slideUp { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
            
              /* LEFT COLUMN */
              .group-meta {
                display: flex;
                flex-direction: column;
                align-items: flex-start;
                position: relative;
              }
            
              .group-role {
                font-weight: 700;
                font-size: 10px;
                letter-spacing: 0.5px;
                color: var(--paper-ink);
                background: var(--paper-surface);
                padding: 2px 4px;
                border-radius: 2px;
                border: 1px solid var(--paper-line);
                text-transform: uppercase;
                z-index: 2;
              }
            
              .timeline-line {
                position: absolute;
                left: 50%; top: 20px; bottom: -20px;
                width: 1px;
                background: var(--paper-line);
                opacity: 0.2;
                z-index: 1;
                transform: translateX(-50%);
              }
            
              /* RIGHT COLUMN */
              .group-body {
                display: flex;
                flex-direction: column;
                gap: 12px;
              }
            
              .log-item {
                display: flex;
                flex-direction: column;
                gap: 4px;
              }
            
              .item-meta-header {
                display: flex;
                align-items: center;
                gap: 8px;
                margin-bottom: 2px;
              }
            
              .meta-tag { 
                font-family: var(--font-code); 
                font-size: 8px; 
                font-weight: 700;
                color: var(--paper-line); 
                background: var(--paper-surface); 
                padding: 1px 4px; 
                border-radius: 2px; 
                display: inline-block; 
                border: 1px solid transparent; 
              }
              
              :global(.mode-phosphor) .meta-tag { border-color: var(--paper-line); }
            
              .meta-time {
                font-family: var(--font-code);
                font-size: 8px;
                color: var(--paper-line);
                opacity: 0.5;
              }
            
              .log-content { 
                font-size: 13px; 
                line-height: 1.6; 
                color: var(--paper-ink); 
                opacity: 0.9; 
              }
            </style>
        - PipelineStage.svelte
            <!-- // [[RARO]]/apps/web-console/src/components/PipelineStage.svelte
            // Purpose: Interactive DAG visualization with "Tactical Arctic" aesthetic.
            // Architecture: Visual Component (D3-lite) with DOM Diffing
            // Dependencies: Stores -->
            
            <script lang="ts">
              import { 
                agentNodes, 
                pipelineEdges, 
                selectedNode, 
                selectNode, 
                deselectNode, 
                runtimeStore, 
                planningMode,
                type PipelineEdge,
                type AgentNode
              } from '$lib/stores'
            
              let { expanded, ontoggle }: { expanded: boolean, ontoggle?: () => void } = $props();
            
              // DOM Bindings
              let svgElement = $state<SVGSVGElement | undefined>();
              let nodesLayer = $state<HTMLDivElement | undefined>();
              let pipelineStageElement = $state<HTMLDivElement | undefined>();
            
              let isRunComplete = $derived($runtimeStore.status === 'COMPLETED' || $runtimeStore.status === 'FAILED');
            
              // CLEANUP: Clear selection on minimize
              $effect(() => {
                if (!expanded && $selectedNode) deselectNode();
              });
            
              // === REACTIVITY ENGINE ===
              // We explicitly track store dependencies here to trigger the render loop.
              $effect(() => {
                if (!pipelineStageElement) return;
            
                // Register Dependencies
                const _nodes = $agentNodes;
                const _edges = $pipelineEdges;
                const _selected = $selectedNode;
                const _status = $runtimeStore.status;
                const _expanded = expanded;
            
                // Use RAF for smooth UI updates without blocking
                requestAnimationFrame(() => {
                  renderGraph();
                });
              });
            
              // RESIZE OBSERVER (Handles window/container shifts)
              $effect(() => {
                if (!pipelineStageElement) return;
                const observer = new ResizeObserver(() => renderGraph());
                observer.observe(pipelineStageElement);
                return () => observer.disconnect();
              })
            
              function renderGraph() {
                  if (!svgElement || !nodesLayer || !pipelineStageElement) return
            
                  const svg = svgElement
                  const w = pipelineStageElement.clientWidth
                  const h = pipelineStageElement.clientHeight
            
                  // === 1. CLUSTERING FOR MINIMIZED VIEW ===
                  const clusters = new Map<number, AgentNode[]>();
                  const sortedNodes = [...$agentNodes].sort((a, b) => {
                      if (Math.abs(a.x - b.x) > 2) return a.x - b.x;
                      return a.id.localeCompare(b.id);
                  });
            
                  sortedNodes.forEach(node => {
                      const rankKey = Math.round(node.x / 5) * 5; 
                      if (!clusters.has(rankKey)) clusters.set(rankKey, []);
                      clusters.get(rankKey)!.push(node);
                  });
            
                  const nodeOffsets = new Map<string, number>();
                  clusters.forEach((clusterNodes) => {
                      const count = clusterNodes.length;
                      if (count === 1) { nodeOffsets.set(clusterNodes[0].id, 0); return; }
                      const SPACING = 24; 
                      const startOffset = -((count - 1) * SPACING) / 2;
                      clusterNodes.forEach((node, index) => {
                          nodeOffsets.set(node.id, startOffset + (index * SPACING));
                      });
                  });
            
                  // === 2. COORDINATE MAPPING ===
                  const getY = (n: AgentNode) => expanded ? (n.y / 100) * h : h / 2;
                  const getX = (n: AgentNode) => {
                      const baseX = (n.x / 100) * w;
                      return expanded ? baseX : baseX + (nodeOffsets.get(n.id) || 0);
                  }
            
                  // === 3. RENDER EDGES (Smart Diffing) ===
                  const nodeHalfWidth = expanded ? 80 : 6;
                  
                  // Mark all current edges as "seen" to handle removal
                  const activeEdgeIds = new Set<string>();
            
                  $pipelineEdges.forEach((link: PipelineEdge) => {
                    const edgeId = `link-${link.from}-${link.to}`;
                    activeEdgeIds.add(edgeId);
            
                    const fromNode = $agentNodes.find((n) => n.id === link.from)
                    const toNode = $agentNodes.find((n) => n.id === link.to)
                    if (!fromNode || !toNode) return
            
                    const centerX1 = getX(fromNode)
                    const centerY1 = getY(fromNode)
                    const centerX2 = getX(toNode)
                    const centerY2 = getY(toNode)
            
                    // Ports: Right of source, Left of target
                    const x1 = centerX1 + nodeHalfWidth; 
                    const y1 = centerY1;
                    const x2 = centerX2 - nodeHalfWidth;
                    const y2 = centerY2;
            
                    // Curve Logic
                    const dist = Math.abs(x2 - x1);
                    const curvature = Math.max(dist * 0.5, 20);
                    const d = `M ${x1} ${y1} C ${x1 + curvature} ${y1}, ${x2 - curvature} ${y2}, ${x2} ${y2}`;
                    
                    let classes = `cable`;
                    if (link.active) classes += ` active`;
                    if (link.pulseAnimation) classes += ` pulse`;
                    if (link.finalized) classes += ` finalized`;
            
                    // Update or Create
                    // FIX: Double Cast to satisfy TS (HTMLElement -> unknown -> SVGPathElement)
                    let path = document.getElementById(edgeId) as unknown as SVGPathElement | null;
                    
                    if (!path) {
                        path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                        path.id = edgeId;
                        svg.appendChild(path);
                    }
                    
                    // Only touch DOM if changed
                    if (path.getAttribute('d') !== d) path.setAttribute('d', d);
                    if (path.getAttribute('class') !== classes) path.setAttribute('class', classes);
                  });
            
                  // Cleanup Removed Edges
                  Array.from(svg.children).forEach(child => {
                      if (!activeEdgeIds.has(child.id)) child.remove();
                  });
            
            
                  // === 4. RENDER NODES (Smart Diffing) ===
                  const activeNodeIds = new Set<string>();
                  
                  $agentNodes.forEach((node) => {
                    const domId = `node-${node.id}`;
                    activeNodeIds.add(domId);
            
                    let el = document.getElementById(domId) as HTMLDivElement;
                    const isSel = $selectedNode === node.id;
                    const className = `tactical-unit ${isSel ? 'selected' : ''} ${node.status}`;
            
                    // CREATE if missing
                    if (!el) {
                        el = document.createElement('div');
                        el.id = domId;
                        // Inner HTML: Military/Arctic Aesthetic
                        el.innerHTML = `
                          <div class="reticle tl"></div>
                          <div class="reticle tr"></div>
                          <div class="reticle bl"></div>
                          <div class="reticle br"></div>
                          <div class="io-port input"></div>
                          <div class="io-port output"></div>
                          <div class="unit-body">
                              <div class="unit-header">
                                  <span class="unit-id">:: ${node.id.toUpperCase().slice(0,6)}</span>
                                  <div class="unit-status"></div>
                              </div>
                              <div class="unit-main">
                                  <div class="unit-role">${node.role.toUpperCase()}</div>
                                  <div class="unit-label">${node.label}</div>
                              </div>
                              <div class="unit-footer">
                                  <span class="coord-text"></span>
                              </div>
                          </div>
                          <div class="isotope-core"></div>
                        `;
                        
                        el.onclick = (e) => {
                          if (!expanded) return;
                          e.stopPropagation();
                          selectNode(node.id);
                        }
                        nodesLayer!.appendChild(el);
                    }
            
                    // UPDATE Attributes
                    if (el.className !== className) el.className = className;
                    
                    const targetLeft = `${getX(node)}px`;
                    const targetTop = `${getY(node)}px`;
                    
                    if (el.style.left !== targetLeft) el.style.left = targetLeft;
                    if (el.style.top !== targetTop) el.style.top = targetTop;
                    el.style.zIndex = node.status === 'running' ? '100' : '10';
            
                    // Update Coordinates Text (Only if needed)
                    const coordEl = el.querySelector('.coord-text');
                    if (coordEl) {
                        coordEl.textContent = `X:${Math.round(node.x)} Y:${Math.round(node.y)}`;
                    }
                  });
            
                  // Cleanup Removed Nodes
                  if (nodesLayer) {
                      Array.from(nodesLayer.children).forEach(child => {
                          if (!activeNodeIds.has(child.id)) child.remove();
                      });
                  }
                }
            
              function handleClick() {
                if (!expanded) ontoggle?.()
              }
            </script>
            
            <div
              id="pipeline-stage"
              class="{expanded ? 'expanded' : ''} {isRunComplete ? 'run-complete' : ''}"
              onclick={handleClick}
              onkeydown={(e) => e.key === 'Enter' && handleClick()}
              role="button"
              tabindex="0"
              bind:this={pipelineStageElement}
            >
              <!-- 1. TACTICAL GRID -->
              <div class="tactical-grid"></div>
              <div class="polar-overlay"></div> <!-- Frost effect -->
              
              <!-- 2. HUD INTERFACE -->
              <div id="hud-banner">
                <div class="hud-left">
                    <div class="status-indicator">
                        <div class="led"></div>
                        {#if isRunComplete}
                            <span>SYS_HALT</span>
                        {:else if $runtimeStore.status === 'RUNNING'}
                            <span>OPERATIONAL</span>
                        {:else if $planningMode}
                            <span>ARCHITECT</span>
                        {:else}
                            <span>STANDBY</span>
                        {/if}
                    </div>
                    <div class="separator">/</div>
                    <span class="hud-sub">SECURE_CHANNEL_01</span>
                </div>
                
                <button class="btn-minimize" onclick={(e) => { e.stopPropagation(); ontoggle?.(); }}>
                    MINIMIZE_VIEW [-]
                </button>
              </div>
            
              <!-- 3. VISUALIZATION LAYERS -->
              <svg id="graph-svg" bind:this={svgElement}></svg>
              <div id="nodes-layer" bind:this={nodesLayer}></div>
              
            </div>
            
            <style>
              /* === PALETTE: TACTICAL ARCTIC === */
              :root {
                  --tac-bg: #050505;
                  --tac-panel: #0a0a0a;
                  --tac-border: #333;
                  --tac-cyan: #00F0FF;
                  --tac-white: #E0E0E0;
                  --tac-dim: #555;
                  --tac-amber: #FFB300;
              }
            
              /* === CHASSIS === */
              #pipeline-stage {
                position: relative;
                height: 80px;
                background: var(--tac-bg);
                border-top: 1px solid var(--paper-line);
                border-bottom: 1px solid var(--paper-line);
                z-index: 100;
                transition: height 0.5s cubic-bezier(0.16, 1, 0.3, 1), border-color 0.3s;
                overflow: hidden;
                cursor: pointer;
              }
            
              #pipeline-stage.expanded {
                height: 65vh;
                cursor: default;
                border-top: 1px solid var(--tac-cyan);
                border-bottom: 1px solid var(--tac-cyan);
                box-shadow: 0 0 50px rgba(0, 0, 0, 0.8);
              }
            
              /* === BACKGROUNDS === */
              .tactical-grid {
                  position: absolute; top: 0; left: 0; width: 100%; height: 100%;
                  background-image: 
                      linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px),
                      linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px);
                  background-size: 50px 50px;
                  pointer-events: none;
                  z-index: 0;
              }
              
              /* Adding "Crosshairs" at intersections */
              .tactical-grid::after {
                  content: ''; position: absolute; top: 0; left: 0; width: 100%; height: 100%;
                  background-image: radial-gradient(circle, rgba(255,255,255,0.1) 1px, transparent 1px);
                  background-size: 50px 50px;
                  background-position: -25px -25px; /* Offset to intersect */
              }
            
              .polar-overlay {
                  position: absolute; top: 0; left: 0; width: 100%; height: 100%;
                  background: radial-gradient(circle at 50% 0%, rgba(0, 240, 255, 0.03), transparent 70%);
                  pointer-events: none;
                  z-index: 1;
              }
            
              /* === HUD === */
              #hud-banner {
                position: absolute; top: 0; left: 0; right: 0;
                height: 32px;
                display: flex; justify-content: space-between; align-items: center;
                padding: 0 16px;
                z-index: 50;
                transform: translateY(-100%);
                transition: transform 0.3s;
                background: rgba(5, 5, 5, 0.9);
                border-bottom: 1px solid var(--tac-border);
                font-family: var(--font-code);
              }
              #pipeline-stage.expanded #hud-banner { transform: translateY(0); }
            
              .hud-left { display: flex; align-items: center; gap: 12px; }
              
              .status-indicator {
                  display: flex; align-items: center; gap: 8px;
                  font-size: 10px; font-weight: 700; color: var(--tac-white);
                  letter-spacing: 1px;
              }
              
              .led {
                  width: 4px; height: 4px; background: var(--tac-dim);
                  box-shadow: 0 0 4px var(--tac-dim);
              }
              /* Active states via parent context would be cleaner, but simple logic here: */
              #pipeline-stage:not(.run-complete) .led { background: var(--tac-cyan); box-shadow: 0 0 6px var(--tac-cyan); }
              .run-complete .led { background: var(--tac-white); }
              
              .separator { color: var(--tac-dim); font-size: 10px; }
              .hud-sub { font-size: 9px; color: var(--tac-dim); letter-spacing: 0.5px; }
            
              .btn-minimize {
                  background: transparent; border: none;
                  font-family: var(--font-code); font-size: 9px; font-weight: 700; 
                  color: var(--tac-dim); cursor: pointer; transition: color 0.2s;
              }
              .btn-minimize:hover { color: var(--tac-white); }
            
              /* === LAYERS === */
              #graph-svg { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 5; pointer-events: none; }
              #nodes-layer { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 10; }
            
              /* === TACTICAL UNIT (NODE) === */
              :global(.tactical-unit) {
                  position: absolute;
                  transform: translate(-50%, -50%);
                  background: var(--tac-bg);
                  border: 1px solid var(--tac-border);
                  color: var(--tac-white);
                  display: flex; flex-direction: column;
                  transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
                  
                  /* Minimized State */
                  width: 12px; height: 32px;
                  border-radius: 0; /* Hard corners */
              }
            
              /* --- EXPANDED STATE --- */
              #pipeline-stage.expanded :global(.tactical-unit) {
                  width: 160px; height: auto;
                  min-height: 50px;
                  background: rgba(10, 10, 10, 0.95);
                  border: 1px solid var(--tac-border);
                  box-shadow: 0 10px 30px rgba(0,0,0,0.5);
                  cursor: pointer;
              }
            
              /* Hide Minimized Element in Expanded */
              :global(.isotope-core) {
                  width: 2px; height: 100%; background: var(--tac-dim); margin: 0 auto;
                  transition: background 0.2s;
              }
              #pipeline-stage.expanded :global(.isotope-core) { display: none; }
            
              /* Hide Expanded Elements in Minimized */
              :global(.reticle), :global(.io-port), :global(.unit-body) { display: none; }
            
              /* --- EXPANDED DETAILS --- */
              
              /* Target Reticles (Corner Brackets) */
              #pipeline-stage.expanded :global(.reticle) {
                  display: block; position: absolute; width: 6px; height: 6px;
                  border-color: var(--tac-dim); opacity: 0.5; transition: all 0.2s;
              }
              #pipeline-stage.expanded :global(.reticle.tl) { top: -1px; left: -1px; border-top: 1px solid; border-left: 1px solid; }
              #pipeline-stage.expanded :global(.reticle.tr) { top: -1px; right: -1px; border-top: 1px solid; border-right: 1px solid; }
              #pipeline-stage.expanded :global(.reticle.bl) { bottom: -1px; left: -1px; border-bottom: 1px solid; border-left: 1px solid; }
              #pipeline-stage.expanded :global(.reticle.br) { bottom: -1px; right: -1px; border-bottom: 1px solid; border-right: 1px solid; }
              
              /* Active Hover State on Reticles */
              #pipeline-stage.expanded :global(.tactical-unit:hover .reticle) {
                  width: 8px; height: 8px; border-color: var(--tac-cyan); opacity: 1;
              }
            
              /* IO Ports - ALIGNED WITH CABLE OFFSETS */
              #pipeline-stage.expanded :global(.io-port) {
                  display: block; position: absolute; top: 50%; width: 4px; height: 8px;
                  background: var(--tac-bg); border: 1px solid var(--tac-dim);
                  transform: translateY(-50%);
                  z-index: 20;
              }
              #pipeline-stage.expanded :global(.io-port.input) { left: -3px; border-right: none; }
              #pipeline-stage.expanded :global(.io-port.output) { right: -3px; border-left: none; }
            
              /* Unit Content */
              #pipeline-stage.expanded :global(.unit-body) {
                  display: flex; flex-direction: column; width: 100%;
              }
            
              :global(.unit-header) {
                  display: flex; justify-content: space-between; align-items: center;
                  padding: 4px 8px; border-bottom: 1px solid var(--tac-border);
                  background: rgba(255,255,255,0.02);
              }
              :global(.unit-id) { font-family: var(--font-code); font-size: 8px; color: var(--tac-dim); letter-spacing: 1px; }
              :global(.unit-status) { width: 4px; height: 4px; background: #222; }
            
              :global(.unit-main) { padding: 8px; display: flex; flex-direction: column; gap: 2px; }
              :global(.unit-role) { font-family: var(--font-code); font-size: 7px; color: var(--tac-dim); text-transform: uppercase; }
              :global(.unit-label) { 
                  font-family: var(--font-code); font-size: 10px; font-weight: 700; 
                  color: var(--tac-white); text-transform: uppercase; letter-spacing: 0.5px;
              }
              
              :global(.unit-footer) {
                  padding: 2px 8px; display: flex; gap: 8px; border-top: 1px solid var(--tac-border);
                  background: #020202;
              }
              :global(.coord-text) { font-family: var(--font-code); font-size: 7px; color: #333; }
            
              /* --- STATES --- */
            
              /* Running */
              :global(.tactical-unit.running) { border-color: var(--tac-amber); }
              :global(.tactical-unit.running .isotope-core) { background: var(--tac-amber); box-shadow: 0 0 6px var(--tac-amber); }
              :global(.tactical-unit.running .unit-status) { background: var(--tac-amber); box-shadow: 0 0 4px var(--tac-amber); animation: blink 0.2s infinite; }
              :global(.tactical-unit.running .unit-label) { color: var(--tac-amber); }
              
              /* Complete */
              :global(.tactical-unit.complete) { border-color: var(--tac-cyan); }
              :global(.tactical-unit.complete .isotope-core) { background: var(--tac-cyan); }
              :global(.tactical-unit.complete .unit-status) { background: var(--tac-cyan); box-shadow: 0 0 4px var(--tac-cyan); }
              
              /* Selected */
              :global(.tactical-unit.selected) { 
                  background: #000; border-color: var(--tac-white); z-index: 200 !important; 
                  box-shadow: 0 0 0 1px var(--tac-white);
              }
            
              /* Hover (Minimized) */
              #pipeline-stage:not(.expanded) :global(.tactical-unit:hover) {
                  transform: translate(-50%, -60%) scale(1.1);
                  border-color: var(--tac-cyan);
              }
            
              /* === CABLES === */
              :global(.cable) {
                  fill: none;
                  stroke: var(--tac-border);
                  stroke-width: 1px;
                  transition: stroke 0.3s;
              }
              :global(.cable.active) {
                  stroke: var(--tac-amber);
                  stroke-width: 1.5px;
                  stroke-dasharray: 2 4;
                  animation: dataflow 0.2s linear infinite;
              }
              :global(.cable.finalized) {
                  stroke: var(--tac-cyan);
                  opacity: 0.6;
              }
            
              @keyframes dataflow { to { stroke-dashoffset: -6; } }
              @keyframes blink { 50% { opacity: 0; } }
            </style>
        - SettingsRail.svelte
            <!-- // [[RARO]]/apps/web-console/src/components/SettingsRail.svelte
            // Purpose: "Micro-Latch" Service Rail. Compact, high-precision system control.
            // Architecture: Ancillary UI Component
            // Dependencies: Stores -->
            
            <script lang="ts">
              import { themeStore, toggleTheme } from '$lib/stores';
              
              let hovered = $state(false);
              let focused = $state(false); 
            
              let isOpen = $derived(hovered || focused);
            
              function handleFocus() { focused = true; }
              function handleBlur() { focused = false; }
            
              function handleInteraction(e: MouseEvent | KeyboardEvent) {
                toggleTheme();
                if (e.detail > 0 && e.currentTarget instanceof HTMLElement) {
                  e.currentTarget.blur();
                  focused = false; 
                }
              }
            </script>
            
            <div 
              class="service-rail {isOpen ? 'expanded' : ''}"
              onmouseenter={() => hovered = true}
              onmouseleave={() => hovered = false}
              onfocusin={handleFocus}
              onfocusout={handleBlur}
              role="complementary"
              aria-label="System Configuration"
            >
              <!-- Fine Grain Texture -->
              <div class="milled-bg"></div>
            
              <div class="rail-container">
                
                <!-- TOP: ID -->
                <div class="sector top">
                  <div class="label-vertical">SYS</div>
                  <div class="micro-bolt"></div>
                </div>
            
                <!-- MIDDLE: The Precision Switch -->
                <div class="sector middle">
                  
                  <!-- Collapsed: Nano LED -->
                  <div class="compact-view" style="opacity: {isOpen ? 0 : 1}">
                    <div class="pilot-dot {$themeStore === 'PHOSPHOR' ? 'active' : ''}"></div>
                  </div>
            
                  <!-- Expanded: Micro Latch -->
                  <div class="mechanism-view" style="opacity: {isOpen ? 1 : 0}; pointer-events: {isOpen ? 'auto' : 'none'}">
                    <div class="mech-label">REALITY</div>
                    
                    <button 
                      class="micro-track" 
                      onclick={handleInteraction} 
                      aria-label="Toggle Reality"
                      aria-pressed={$themeStore === 'PHOSPHOR'}
                    >
                      <!-- Internal Hairline Glow -->
                      <div class="hairline-luma {$themeStore === 'PHOSPHOR' ? 'glow' : ''}"></div>
            
                      <!-- The Compact Block -->
                      <div class="micro-block {$themeStore === 'PHOSPHOR' ? 'engaged' : 'disengaged'}">
                        <!-- Fine Grip Lines -->
                        <div class="fine-grip">
                          <span></span><span></span><span></span><span></span>
                        </div>
                      </div>
                    </button>
            
                    <div class="readout-group">
                      <span class="value">{$themeStore === 'ARCHIVAL' ? 'ARC' : 'PHO'}</span>
                    </div>
                  </div>
            
                </div>
            
                <!-- BOTTOM: Decor -->
                <div class="sector bottom">
                  <div class="micro-bolt"></div>
                  <div class="label-vertical">V1</div>
                </div>
            
              </div>
            </div>
            
            <style>
              /* === RAIL CHASSIS === */
              .service-rail {
                position: absolute; right: 0; top: 0;
                height: 100vh; width: 48px;
                border-left: 1px solid var(--paper-line);
                background: var(--paper-bg); 
                display: flex; flex-direction: column;
                transition: width 0.3s var(--ease-snap), background-color 0.3s;
                overflow: hidden; z-index: 50;
              }
            
              .service-rail.expanded {
                width: 80px; /* Tighter expansion */
                background: var(--paper-surface);
                box-shadow: -15px 0 50px rgba(0,0,0,0.1);
              }
            
              .milled-bg {
                position: absolute; top: 0; left: 0; width: 100%; height: 100%;
                opacity: 0.03;
                background-image: repeating-linear-gradient(45deg, transparent, transparent 1px, var(--paper-ink) 1px, var(--paper-ink) 2px);
                pointer-events: none;
              }
            
              .rail-container {
                position: relative; z-index: 2; height: 100%; 
                display: flex; flex-direction: column; justify-content: space-between;
              }
            
              /* === SECTORS === */
              .sector { display: flex; flex-direction: column; align-items: center; padding: 24px 0; gap: 12px; }
            
              .label-vertical {
                writing-mode: vertical-rl; text-orientation: mixed;
                font-family: var(--font-code); font-size: 8px;
                color: var(--paper-line); letter-spacing: 1px; font-weight: 700;
                user-select: none;
              }
            
              .micro-bolt {
                width: 4px; height: 4px; background: var(--paper-line); border-radius: 50%; opacity: 0.5;
              }
            
              /* === CONTROLS === */
              .sector.middle { flex: 1; justify-content: center; }
            
              /* Compact View */
              .compact-view { position: absolute; transition: opacity 0.2s; pointer-events: none; }
              
              .pilot-dot {
                width: 4px; height: 4px; background: var(--paper-line); border-radius: 50%;
                transition: all 0.3s;
              }
              .pilot-dot.active {
                background: var(--arctic-cyan);
                box-shadow: 0 0 6px var(--arctic-cyan);
              }
            
              /* Expanded View */
              .mechanism-view {
                display: flex; flex-direction: column; align-items: center; gap: 12px;
                transition: opacity 0.2s 0.1s; width: 100%;
              }
            
              .mech-label {
                font-family: var(--font-code); font-size: 7px; color: var(--paper-ink); opacity: 0.5; letter-spacing: 1px;
              }
            
              /* === MICRO TRACK === */
              .micro-track {
                width: 26px; height: 64px; /* Much smaller footprint */
                background: var(--digi-void);
                border: 1px solid var(--paper-line);
                border-radius: 2px;
                position: relative; cursor: pointer; padding: 0;
                box-shadow: inset 0 2px 8px rgba(0,0,0,0.3);
                overflow: hidden;
              }
              .micro-track:focus-visible { outline: 1px solid var(--arctic-cyan); }
            
              /* Hairline Luma */
              .hairline-luma {
                position: absolute; left: 50%; top: 4px; bottom: 4px; width: 1px;
                background: var(--paper-line); opacity: 0.2; transform: translateX(-50%);
                transition: all 0.3s;
              }
              .hairline-luma.glow {
                background: var(--arctic-cyan); opacity: 0.8;
                box-shadow: 0 0 4px var(--arctic-cyan);
              }
            
              /* === MICRO BLOCK === */
              .micro-block {
                width: 20px; height: 28px;
                background: var(--paper-surface);
                border: 1px solid var(--paper-ink);
                border-radius: 1px;
                position: absolute; left: 2px;
                z-index: 10;
                /* Precise, snappy movement */
                transition: top 0.3s cubic-bezier(0.25, 1, 0.5, 1);
                box-shadow: 0 2px 6px rgba(0,0,0,0.2);
                display: flex; align-items: center; justify-content: center;
              }
            
              /* States */
              .micro-block.disengaged { top: 2px; }
              .micro-block.engaged { 
                top: 32px; /* 64 - 28 - 2 - 2(borders) */
                background: #111;
                border-color: var(--arctic-cyan);
                box-shadow: 0 0 10px rgba(0, 240, 255, 0.2);
              }
            
              /* Fine Grip Texture */
              .fine-grip { display: flex; flex-direction: column; gap: 2px; }
              .fine-grip span {
                width: 10px; height: 1px; background: var(--paper-ink); opacity: 0.5;
              }
              .micro-block.engaged .fine-grip span { background: var(--arctic-cyan); }
            
              /* === READOUT === */
              .readout-group .value { 
                font-family: var(--font-code); font-size: 9px; font-weight: 700; color: var(--paper-ink); 
              }
            </style>
      - lib/
        - api.ts
            Imports: mock-api.ts
            Imported by: mock-api.ts, stores.ts
            // [[RARO]]/apps/web-console/src/lib/api.ts
            import { mockStartRun, mockGetArtifact, mockResumeRun, mockStopRun, mockGetLibraryFiles, getMockGeneratedFile } from './mock-api';
            
            
            const KERNEL_API = import.meta.env.VITE_KERNEL_URL || '/api';
            const AGENT_API = import.meta.env.VITE_AGENT_URL || '/agent-api';
            
            // ** NEW DEBUG FLAG **
            export const USE_MOCK = import.meta.env.VITE_USE_MOCK_API === 'true';
            
            export interface WorkflowConfig {
              id: string;
              name: string;
              agents: AgentConfig[];
              max_token_budget: number;
              timeout_ms: number;
              // === RFS Integration ===
              attached_files?: string[];
            }
            
            
            export interface AgentConfig {
              id: string;
              role: 'orchestrator' | 'worker' | 'observer';
              model: string;
              tools: string[];
              input_schema: any;
              output_schema: any;
              cache_policy: string;
              depends_on: string[];
              prompt: string;
              position?: { x: number; y: number };
              user_directive?: string;        // Runtime task from operator
              accepts_directive?: boolean;    // Can this node receive operator directives?
              allow_delegation?: boolean;
            }
            
            export async function startRun(config: WorkflowConfig): Promise<{ success: boolean; run_id: string }> {
              // ** MOCK INTERCEPTION **
              if (USE_MOCK) {
                return mockStartRun(config);
              }
            
              try {
                const res = await fetch(`${KERNEL_API}/runtime/start`, {
                  method: 'POST',
                  headers: {
                    'Content-Type': 'application/json',
                  },
                  body: JSON.stringify(config),
                });
            
                if (!res.ok) {
                  throw new Error(`API Error: ${res.statusText}`);
                }
            
                return await res.json();
              } catch (e) {
                console.error('Failed to start run:', e);
                throw e;
              }
            }
            
            export function getWebSocketURL(runId: string): string {
              if (USE_MOCK) return `mock://runtime/${runId}`;
            
              // In development with Vite proxy, use relative WebSocket path
              // Vite will proxy ws://localhost:5173/ws/runtime/{id} → ws://kernel:3000/ws/runtime/{id}
              const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
              const host = window.location.host; // localhost:5173 in dev, actual host in prod
            
              return `${protocol}//${host}/ws/runtime/${runId}`;
            }
            
            export async function getArtifact(runId: string, agentId: string): Promise<any> {
              // ** MOCK INTERCEPTION **
              if (USE_MOCK) {
                return mockGetArtifact(runId, agentId);
              }
            
              try {
                const res = await fetch(`${KERNEL_API}/runtime/${runId}/artifact/${agentId}`);
            
                if (res.status === 404) {
                  console.warn(`Artifact not found for agent ${agentId}`);
                  return null;
                }
            
                if (!res.ok) {
                  throw new Error(`Failed to fetch artifact: ${res.status} ${res.statusText}`);
                }
            
                return await res.json();
              } catch (e) {
                console.error('Artifact fetch error:', e);
                throw e;
              }
            }
            
            export async function generateWorkflowPlan(userQuery: string): Promise<WorkflowConfig> {
                if (USE_MOCK) {
                    // Mock Architect behavior
                    return {
                        id: `plan-${Date.now()}`,
                        name: 'Mock_Architecture_Plan',
                        agents: [
                            {
                                id: 'mock_researcher',
                                role: 'worker',
                                model: 'fast',
                                tools: ['web_search'],
                                input_schema: {},
                                output_schema: {},
                                cache_policy: 'ephemeral',
                                depends_on: [],
                                prompt: `Research request: ${userQuery}`,
                                position: { x: 30, y: 50 },
                                accepts_directive: false
                            },
                            {
                                id: 'mock_synthesizer',
                                role: 'worker',
                                model: 'reasoning',
                                tools: [],
                                input_schema: {},
                                output_schema: {},
                                cache_policy: 'ephemeral',
                                depends_on: ['mock_researcher'],
                                prompt: 'Synthesize findings',
                                position: { x: 70, y: 50 },
                                accepts_directive: false
                            }
                        ],
                        max_token_budget: 50000,
                        timeout_ms: 60000
                    };
                }
            
                try {
                    const res = await fetch(`${AGENT_API}/plan`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ text: userQuery })
                    });
            
                    if (!res.ok) throw new Error(`Architect Error: ${res.statusText}`);
                    
                    const manifest = await res.json();
                    
                    // Enrich logic: The Python manifest might lack UI positions.
                    // We add basic layouting here if missing.
                    const enrichedAgents = manifest.agents.map((agent: any, index: number) => ({
                        ...agent,
                        // Simple diagonal layout if missing (PipelineStage handles display logic mostly)
                        position: agent.position || { x: 20 + (index * 15), y: 30 + (index * 10) },
                        // Ensure Rust-required fields exist
                        input_schema: agent.input_schema || {},
                        output_schema: agent.output_schema || {},
                        cache_policy: 'ephemeral' 
                    }));
            
                    return {
                        ...manifest,
                        // Ensure ID exists
                        id: manifest.id || `flow-${Date.now()}`,
                        max_token_budget: 100000,
                        timeout_ms: 60000,
                        agents: enrichedAgents
                    };
            
                } catch (e) {
                    console.error('Plan generation failed:', e);
                    throw e;
                }
            }
            
            // === RFS API ===
            
            export async function getLibraryFiles(): Promise<string[]> {
                if (USE_MOCK) {
                    return mockGetLibraryFiles();
                }
            
                try {
                    const res = await fetch(`${KERNEL_API}/runtime/library`);
                    if (!res.ok) throw new Error('Failed to fetch library');
                    const data = await res.json();
                    return data.files || [];
                } catch (e) {
                    console.error('Library fetch failed:', e);
                    return [];
                }
            }
            
            export async function uploadFile(file: File): Promise<string> {
                if (USE_MOCK) {
                    console.warn("[MOCK] Upload simulated.");
                    return new Promise(resolve => setTimeout(() => resolve("success"), 1000));
                }
            
                const formData = new FormData();
                formData.append('file', file);
            
                try {
                    const res = await fetch(`${KERNEL_API}/runtime/library/upload`, {
                        method: 'POST',
                        body: formData, // Fetch automatically sets Content-Type to multipart/form-data
                    });
            
                    if (!res.ok) {
                        throw new Error(`Upload failed: ${res.statusText}`);
                    }
            
                    return "success";
                } catch (e) {
                    console.error('Upload API Error:', e);
                    throw e;
                }
            }
            
            // === ARTIFACT STORAGE API ===
            
            export interface ArtifactFile {
                filename: string;
                agent_id: string;
                generated_at: string;
                size_bytes: number;
                content_type: string;
            }
            
            export interface ArtifactMetadata {
                run_id: string;
                workflow_id: string;
                user_directive: string;
                created_at: string;
                expires_at: string;
                artifacts: ArtifactFile[];
                status: string;
            }
            
            export async function getAllArtifacts(): Promise<ArtifactMetadata[]> {
                if (USE_MOCK) {
                    // Mock will be implemented in mock-api.ts
                    const { mockGetAllArtifacts } = await import('./mock-api');
                    return mockGetAllArtifacts();
                }
            
                try {
                    const res = await fetch(`${KERNEL_API}/runtime/artifacts`);
                    if (!res.ok) throw new Error('Failed to fetch artifacts');
                    const data = await res.json();
                    return data.artifacts.map((a: any) => a.metadata);
                } catch (e) {
                    console.error('Artifacts fetch failed:', e);
                    return [];
                }
            }
            
            export async function getRunArtifacts(runId: string): Promise<ArtifactMetadata | null> {
                if (USE_MOCK) {
                    const { mockGetRunArtifacts } = await import('./mock-api');
                    return mockGetRunArtifacts(runId);
                }
            
                try {
                    const res = await fetch(`${KERNEL_API}/runtime/artifacts/${runId}`);
                    if (res.status === 404) return null;
                    if (!res.ok) throw new Error('Failed to fetch run artifacts');
                    return await res.json();
                } catch (e) {
                    console.error('Run artifacts fetch failed:', e);
                    return null;
                }
            }
            
            export async function deleteArtifactRun(runId: string): Promise<void> {
                if (USE_MOCK) {
                    console.warn("[MOCK] Delete artifact run simulated.");
                    return;
                }
            
                try {
                    const res = await fetch(`${KERNEL_API}/runtime/artifacts/${runId}`, {
                        method: 'DELETE'
                    });
                    if (!res.ok) throw new Error('Failed to delete artifact run');
                } catch (e) {
                    console.error('Artifact deletion failed:', e);
                    throw e;
                }
            }
            
            export async function promoteArtifactToLibrary(runId: string, filename: string): Promise<void> {
                if (USE_MOCK) {
                    console.warn("[MOCK] Promote artifact simulated.");
                    return;
                }
            
                try {
                    const res = await fetch(`${KERNEL_API}/runtime/artifacts/${runId}/files/${filename}/promote`, {
                        method: 'POST'
                    });
                    if (!res.ok) throw new Error('Failed to promote artifact');
                } catch (e) {
                    console.error('Artifact promotion failed:', e);
                    throw e;
                }
            }
            
            export function getArtifactFileUrl(runId: string, filename: string): string {
                if (USE_MOCK) {
                    // In mock mode, return data URL directly
                    const mockUrl = getMockGeneratedFile(filename);
                    if (mockUrl) return mockUrl;
            
                    // Fallback to placeholder for unknown files
                    return 'data:text/plain;charset=utf-8,' + encodeURIComponent(`Mock file: ${filename}\n\nNo preview available in demo mode.`);
                }
            
                return `${KERNEL_API}/runtime/artifacts/${runId}/files/${filename}`;
            }
        - layout-engine.ts
            Imports: stores.ts
            Imported by: stores.ts
            // [[RARO]]/apps/web-console/src/lib/layout-engine.ts
            import type { AgentNode, PipelineEdge } from './stores';
            
            export class DagLayoutEngine {
                static computeLayout(nodes: AgentNode[], edges: PipelineEdge[]): AgentNode[] {
                    if (nodes.length === 0) return [];
            
                    // 1. Build Graph Structure
                    const adj = new Map<string, string[]>();
                    const inDegree = new Map<string, number>();
                    
                    nodes.forEach(n => {
                        adj.set(n.id, []);
                        inDegree.set(n.id, 0);
                    });
            
                    edges.forEach(e => {
                        if (adj.has(e.from) && inDegree.has(e.to)) {
                            adj.get(e.from)?.push(e.to);
                            inDegree.set(e.to, (inDegree.get(e.to) || 0) + 1);
                        }
                    });
            
                    // 2. Assign Ranks (X-Axis Layering via Longest Path)
                    const ranks = new Map<string, number>();
                    const queue: string[] = [];
            
                    // Find roots
                    nodes.forEach(n => {
                        if ((inDegree.get(n.id) || 0) === 0) {
                            ranks.set(n.id, 0);
                            queue.push(n.id);
                        }
                    });
            
                    // Fallback for cycles/no-roots: force first node as root
                    if (queue.length === 0 && nodes.length > 0) {
                        ranks.set(nodes[0].id, 0);
                        queue.push(nodes[0].id);
                    }
            
                    // BFS for Rank Assignment
                    while (queue.length > 0) {
                        const u = queue.shift()!;
                        const currentRank = ranks.get(u)!;
                        
                        const neighbors = adj.get(u) || [];
                        neighbors.forEach(v => {
                            const existingRank = ranks.get(v) || 0;
                            // Push child to at least parent + 1
                            const newRank = Math.max(existingRank, currentRank + 1);
                            ranks.set(v, newRank);
                            
                            // Add to queue if not processed in this specific path context
                            // (Simple DAG traversal)
                            if (!queue.includes(v)) queue.push(v);
                        });
                    }
            
                    // 3. Assign Y-Axis (Distribute within Rank)
                    const layers = new Map<number, string[]>();
                    let maxRank = 0;
            
                    ranks.forEach((rank, nodeId) => {
                        if (!layers.has(rank)) layers.set(rank, []);
                        layers.get(rank)?.push(nodeId);
                        if (rank > maxRank) maxRank = rank;
                    });
            
                    // 4. Normalize to 0-100% Viewport
                    const MARGIN_X = 10; 
                    const MARGIN_Y = 15;
                    const AVAILABLE_W = 100 - (MARGIN_X * 2);
                    const AVAILABLE_H = 100 - (MARGIN_Y * 2);
            
                    return nodes.map(node => {
                        const rank = ranks.get(node.id) || 0;
                        const layerNodes = layers.get(rank)!;
                        
                        // X Position
                        const xPercent = maxRank === 0 
                            ? 50 
                            : MARGIN_X + (rank / maxRank) * AVAILABLE_W;
            
                        // Y Position (Sort by ID for stability, or index)
                        layerNodes.sort(); 
                        const indexInLayer = layerNodes.indexOf(node.id);
                        const countInLayer = layerNodes.length;
                        
                        // Distribute evenly vertically
                        const yPercent = MARGIN_Y + ((indexInLayer + 1) / (countInLayer + 1)) * AVAILABLE_H;
            
                        return { ...node, x: xPercent, y: yPercent };
                    });
                }
            }
        - markdown.ts
            // [[RARO]]/apps/web-console/src/lib/markdown.ts
            import { marked } from 'marked';
            
            // Configure renderer to match our CSS variables
            const renderer = new marked.Renderer();
            
            // 1. Links: Add accent color and underline
            renderer.link = ({ href, title, text }) => {
              return `<a href="${href}" title="${title || ''}" target="_blank" rel="noopener noreferrer" class="md-link">${text}</a>`;
            };
            
            // 2. Blockquotes: Style like our error/info blocks
            renderer.blockquote = ({ text }) => {
              return `<blockquote class="md-quote">${text}</blockquote>`;
            };
            
            marked.setOptions({
              renderer,
              gfm: true, // GitHub Flavored Markdown (tables, etc)
              breaks: true // Enter key = new line
            });
            
            export function parseMarkdown(text: string): string {
              if (!text) return '';
              // FIX: Handle literal escaped newlines ("\n") often sent by JSON loggers
              // converting them to actual newlines so 'breaks: true' can render <br>
              const processed = text.replace(/\\n/g, '\n');
              return marked.parse(processed) as string;
            }
        - mock-api.ts
            Imports: api.ts
            Imported by: api.ts, stores.ts
            // [[RARO]]/apps/web-console/src/lib/mock-api.ts
            import { type WorkflowConfig, type ArtifactMetadata } from './api';
            
            // --- Types ---
            type TopologySnapshot = {
                nodes: string[];
                edges: Array<{ from: string; to: string }>;
            };
            
            type SimulationStep = {
                delay: number;
                state: {
                    status: string;
                    active_agents: string[];
                    completed_agents: string[];
                    failed_agents: string[];
                    total_tokens_used: number;
                    invocations: Array<{
                        id: string;
                        agent_id: string;
                        status: 'success' | 'failed';
                        tokens_used: number;
                        latency_ms: number;
                        artifact_id?: string;
                        error_message?: string;
                    }>;
                };
                signatures?: Record<string, string>;
                topology?: TopologySnapshot;
                // New: Action to execute when this step triggers (for emitting logs)
                action?: () => void;
            };
            
            // --- Mock Data Generators ---
            
            const generateDelegationArtifact = (reason: string, parentId: string, newAgentId: string) => {
                const payload = {
                    reason: reason,
                    strategy: "child",
                    new_nodes: [
                        {
                            id: newAgentId,
                            role: "worker",
                            model: "gemini-2.5-flash",
                            prompt: `Dynamically delegated task from ${parentId}`,
                            tools: ["web_search"],
                            depends_on: [parentId]
                        }
                    ]
                };
            
                return `I need to delegate a sub-task to handle this request properly.
            
            \`\`\`json:delegation
            ${JSON.stringify(payload, null, 2)}
            \`\`\`
            
            Delegating execution to ${newAgentId}...`;
            };
            
            const STATIC_ARTIFACTS: Record<string, any> = {
                'n1': {
                    result: `## Orchestration Plan
            Analysis indicates a need for deep retrieval and verification.
            1. **Retrieval**: Gather architecture docs.
            2. **Analysis**: Profile latency metrics.
            3. **Synthesis**: Generate final report.`
                },
                'n3': {
                    success: true,
                    result: `Analysis complete. Generated visualization of latency metrics across all service endpoints.
            
            Key Findings:
            - P99 latency variance: 0.042ms
            - 3 outlier endpoints identified
            - Peak load correlation: 0.87
            
            The variance analysis chart and raw data have been saved to disk.
            
            [SYSTEM: Generated Image saved to 'latency_variance_analysis.png']
            [SYSTEM: Generated File saved to 'fictional_data.json']`,
                    // UPDATED: Include both image and JSON to test ArtifactCard logic
                    files_generated: ['latency_variance_analysis.png', 'fictional_data.json'],
                    artifact_stored: true
                },
                'n4': {
                    result: `# Final Report
            The analysis confirms that the latency regression is caused by "Cold Expert" switching in the MoE layer.
            **Recommendation**: Enable pre-warming on the Orchestrator.`
                }
            };
            
            // --- Singleton for Controlling the Active Simulation ---
            let activeSocket: MockWebSocket | null = null;
            
            // --- API Methods ---
            
            export async function mockStartRun(config: WorkflowConfig): Promise<{ success: boolean; run_id: string }> {
                console.log('[MOCK] Starting run with config:', config);
                return new Promise((resolve) => {
                    setTimeout(() => {
                        resolve({
                            success: true,
                            run_id: `mock-run-${Date.now()}`
                        });
                    }, 500);
                });
            }
            
            // Global artifact store to hold dynamic outputs during a session
            let SESSION_ARTIFACTS: Record<string, any> = {};
            
            export async function mockGetArtifact(runId: string, agentId: string): Promise<any> {
                console.log(`[MOCK] Fetching artifact for ${agentId}`);
                return new Promise((resolve) => {
                    setTimeout(() => {
                        const artifact = SESSION_ARTIFACTS[agentId] || STATIC_ARTIFACTS[agentId];
                        resolve(artifact || { text: `[MOCK] Output generated by agent ${agentId}.` });
                    }, 600); 
                });
            }
            
            // ** NEW: Mock Pause/Resume Handlers **
            export async function mockResumeRun(runId: string): Promise<void> {
                console.log(`[MOCK] Resuming run ${runId}`);
                if (activeSocket) {
                    activeSocket.resume();
                }
            }
            
            export async function mockStopRun(runId: string): Promise<void> {
                console.log(`[MOCK] Stopping run ${runId}`);
                if (activeSocket) {
                    activeSocket.close();
                }
            }
            
            // === NEW MOCK RFS ===
            export async function mockGetLibraryFiles(): Promise<string[]> {
                return [
                    'financials_Q3_2024.pdf',
                    'system_architecture_v2.md',
                    'raw_telemetry_dump.csv',
                    'cortex_safety_policy.json'
                ];
            }
            
            // === MOCK GENERATED FILE SERVER ===
            // Simple bar chart SVG for mock demonstration
            const MOCK_CHART_SVG = `
            <svg width="600" height="400" xmlns="http://www.w3.org/2000/svg">
              <style>
                .title { font: bold 16px monospace; fill: #333; }
                .label { font: 11px monospace; fill: #666; }
                .bar { fill: #4285f4; }
                .grid { stroke: #e0e0e0; stroke-width: 1; }
              </style>
            
              <!-- Background -->
              <rect width="600" height="400" fill="#fafafa"/>
            
              <!-- Title -->
              <text x="300" y="30" text-anchor="middle" class="title">P99 Latency Variance Analysis</text>
            
              <!-- Grid Lines -->
              <line x1="80" y1="100" x2="80" y2="350" class="grid"/>
              <line x1="80" y1="350" x2="550" y2="350" class="grid"/>
            
              <!-- Bars -->
              <rect x="120" y="180" width="60" height="170" class="bar"/>
              <rect x="220" y="120" width="60" height="230" class="bar"/>
              <rect x="320" y="200" width="60" height="150" class="bar"/>
              <rect x="420" y="90" width="60" height="260" class="bar"/>
            
              <!-- Labels -->
              <text x="150" y="370" text-anchor="middle" class="label">Auth</text>
              <text x="250" y="370" text-anchor="middle" class="label">Search</text>
              <text x="350" y="370" text-anchor="middle" class="label">Data</text>
              <text x="450" y="370" text-anchor="middle" class="label">Upload</text>
            
              <!-- Y-axis labels -->
              <text x="70" y="355" text-anchor="end" class="label">0ms</text>
              <text x="70" y="225" text-anchor="end" class="label">50ms</text>
              <text x="70" y="105" text-anchor="end" class="label">100ms</text>
            
              <!-- Variance annotation -->
              <text x="300" y="385" text-anchor="middle" class="label">Variance: 0.042ms</text>
            </svg>`;
            
            const MOCK_RESEARCH_CHART_SVG = `
            <svg width="700" height="500" xmlns="http://www.w3.org/2000/svg">
              <style>
                .title { font: bold 18px monospace; fill: #2c3e50; }
                .subtitle { font: 12px monospace; fill: #7f8c8d; }
                .line { stroke: #e74c3c; stroke-width: 3; fill: none; }
                .point { fill: #e74c3c; }
                .grid { stroke: #ecf0f1; stroke-width: 1; }
                .axis-label { font: 11px monospace; fill: #95a5a6; }
              </style>
            
              <rect width="700" height="500" fill="#ffffff"/>
            
              <text x="350" y="30" text-anchor="middle" class="title">Research Findings Timeline</text>
              <text x="350" y="50" text-anchor="middle" class="subtitle">Key Insights Distribution (Q1-Q4 2024)</text>
            
              <!-- Grid -->
              <line x1="80" y1="100" x2="80" y2="420" class="grid" stroke-width="2"/>
              <line x1="80" y1="420" x2="650" y2="420" class="grid" stroke-width="2"/>
            
              <!-- Data Line -->
              <polyline points="100,380 200,320 300,280 400,200 500,240 600,160" class="line"/>
            
              <!-- Data Points -->
              <circle cx="100" cy="380" r="6" class="point"/>
              <circle cx="200" cy="320" r="6" class="point"/>
              <circle cx="300" cy="280" r="6" class="point"/>
              <circle cx="400" cy="200" r="6" class="point"/>
              <circle cx="500" cy="240" r="6" class="point"/>
              <circle cx="600" cy="160" r="6" class="point"/>
            
              <!-- X-axis labels -->
              <text x="100" y="445" text-anchor="middle" class="axis-label">Jan</text>
              <text x="200" y="445" text-anchor="middle" class="axis-label">Mar</text>
              <text x="300" y="445" text-anchor="middle" class="axis-label">May</text>
              <text x="400" y="445" text-anchor="middle" class="axis-label">Jul</text>
              <text x="500" y="445" text-anchor="middle" class="axis-label">Sep</text>
              <text x="600" y="445" text-anchor="middle" class="axis-label">Nov</text>
            
              <!-- Y-axis labels -->
              <text x="70" y="425" text-anchor="end" class="axis-label">0</text>
              <text x="70" y="325" text-anchor="end" class="axis-label">25</text>
              <text x="70" y="225" text-anchor="end" class="axis-label">50</text>
              <text x="70" y="125" text-anchor="end" class="axis-label">75</text>
            </svg>`;
            
            const MOCK_EXECUTIVE_SUMMARY_MD = `# Executive Summary
            
            **Report Generated**: ${new Date().toLocaleDateString()}
            **Workflow**: Research & Analysis Pipeline
            **Status**: ✓ Complete
            
            ---
            
            ## Key Findings
            
            ### 1. Market Analysis
            The comprehensive market research indicates a **42% growth opportunity** in the target segment over the next fiscal year. Primary drivers include:
            
            - Increased demand for automation solutions (+35%)
            - Emerging markets expansion (+18%)
            - Technology adoption acceleration (+28%)
            
            ### 2. Competitive Landscape
            
            | Competitor | Market Share | Growth Rate | Key Advantage |
            |------------|--------------|-------------|---------------|
            | Alpha Corp | 32%          | +12%        | Brand Recognition |
            | Beta Inc   | 28%          | +8%         | Price Leadership |
            | Gamma Ltd  | 15%          | +22%        | Innovation |
            | **Our Position** | **25%** | **+18%** | **Service Quality** |
            
            ### 3. Strategic Recommendations
            
            #### Immediate Actions (Q1 2025)
            1. **Product Diversification**: Expand portfolio with 3 new SKUs
            2. **Market Penetration**: Target 5 new geographic regions
            3. **Technology Investment**: Allocate $2.5M for R&D initiatives
            
            #### Medium-Term Goals (2025-2026)
            - Achieve 30% market share
            - Reduce operational costs by 15%
            - Increase customer retention to 92%
            
            ### 4. Risk Assessment
            
            **High Priority Risks**:
            - Supply chain volatility (Probability: 65%, Impact: High)
            - Regulatory changes (Probability: 40%, Impact: Medium)
            - Competitive price wars (Probability: 55%, Impact: High)
            
            **Mitigation Strategies**:
            - Diversify supplier base (3+ vendors per critical component)
            - Establish regulatory compliance task force
            - Focus on value-add services vs. price competition
            
            ---
            
            ## Financial Projections
            
            \`\`\`
            Year    Revenue    Growth    EBITDA    Margin
            2024    $45.2M     +18%      $12.4M    27.4%
            2025    $58.7M     +30%      $17.8M    30.3%
            2026    $78.3M     +33%      $25.1M    32.1%
            \`\`\`
            
            ---
            
            ## Conclusion
            
            The research findings support a **STRONG BUY** recommendation for strategic expansion initiatives. Market conditions are favorable, competitive positioning is solid, and execution capabilities are proven.
            
            **Next Steps**:
            1. Board approval for Q1 initiatives
            2. Resource allocation finalization
            3. KPI tracking dashboard deployment
            
            ---
            
            *This report was generated by the RARO autonomous research pipeline.*
            *For questions, contact the Strategy Team.*
            `;
            
            const MOCK_ANOMALY_LOG_CSV = `timestamp,severity,component,message,correlation_id
            2024-01-09T08:15:23Z,WARNING,auth-service,Failed login attempt from IP 192.168.1.45,corr-001
            2024-01-09T08:16:42Z,ERROR,database,Connection timeout to replica-2,corr-002
            2024-01-09T08:17:15Z,WARNING,api-gateway,Rate limit exceeded for client_abc123,corr-003
            2024-01-09T08:18:33Z,CRITICAL,payment-processor,Transaction validation failed - insufficient funds,corr-004
            2024-01-09T08:19:01Z,WARNING,auth-service,Failed login attempt from IP 192.168.1.45,corr-005
            2024-01-09T08:20:12Z,ERROR,cache-layer,Redis connection lost - failover initiated,corr-006
            2024-01-09T08:21:45Z,WARNING,api-gateway,Rate limit exceeded for client_abc123,corr-007
            2024-01-09T08:22:33Z,ERROR,message-queue,RabbitMQ consumer lag exceeds threshold,corr-008
            2024-01-09T08:23:18Z,CRITICAL,auth-service,Brute force attack detected from IP 192.168.1.45,corr-009
            2024-01-09T08:24:55Z,WARNING,database,Slow query detected (5.2s) on users table,corr-010
            2024-01-09T08:25:22Z,ERROR,storage-service,S3 bucket access denied - check IAM roles,corr-011
            2024-01-09T08:26:40Z,WARNING,notification-service,Email delivery delayed - SMTP timeout,corr-012
            2024-01-09T08:27:13Z,CRITICAL,payment-processor,Payment gateway unreachable - network issue,corr-013
            2024-01-09T08:28:02Z,ERROR,api-gateway,Invalid JWT signature from client_xyz789,corr-014
            2024-01-09T08:29:31Z,WARNING,monitoring,Disk usage at 87% on server-prod-3,corr-015`;
            
            const MOCK_GENERATED_FILES: Record<string, string> = {
                'latency_variance_analysis.png': 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(MOCK_CHART_SVG),
                'metrics_summary.json': 'data:application/json;charset=utf-8,' + encodeURIComponent(JSON.stringify({
                    p99_variance: 0.042,
                    outliers: ['endpoint_auth', 'endpoint_search', 'endpoint_upload'],
                    peak_correlation: 0.87,
                    generated_at: new Date().toISOString()
                }, null, 2)),
                'executive_summary.md': 'data:text/markdown;charset=utf-8,' + encodeURIComponent(MOCK_EXECUTIVE_SUMMARY_MD),
                'research_chart.png': 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(MOCK_RESEARCH_CHART_SVG),
                'security_report.pdf': 'data:text/plain;charset=utf-8,' + encodeURIComponent('PDF Preview: Security Audit Report\n\n[This is a mock PDF file - actual PDF rendering not available in demo mode]\n\nReport Summary:\n- 15 vulnerabilities identified\n- 8 critical issues resolved\n- System compliance: 94%\n- Next audit: Q2 2025'),
                'anomaly_log.csv': 'data:text/csv;charset=utf-8,' + encodeURIComponent(MOCK_ANOMALY_LOG_CSV),
                // UPDATED: Added the JSON file from your logs
                'fictional_data.json': 'data:application/json;charset=utf-8,' + encodeURIComponent(JSON.stringify([
                    { "id": 1, "name": "John Doe", "email": "john@example.com", "age": 28 },
                    { "id": 2, "name": "Jane Smith", "email": "jane@test.org", "age": 34 }
                ], null, 2)),
            };
            
            export function getMockGeneratedFile(filename: string): string | null {
                return MOCK_GENERATED_FILES[filename] || null;
            }
            
            
            // --- Mock WebSocket Class ---
            
            export class MockWebSocket {
                url: string;
                onopen: (() => void) | null = null;
                onmessage: ((event: { data: string }) => void) | null = null;
                onclose: ((e: { code: number; reason: string; wasClean: boolean }) => void) | null = null;
                onerror: ((err: any) => void) | null = null;
                
                private steps: SimulationStep[] = [];
                private currentStep = 0;
                private timer: any;
                private isPaused = false;
            
                // Current State Trackers
                private topology: TopologySnapshot;
                private activeAgents: string[] = [];
                private completedAgents: string[] = [];
                private invocations: any[] = [];
                private signatures: Record<string, string> = {};
                private totalTokens = 0;
            
                constructor(url: string) {
                    this.url = url;
                    activeSocket = this; // Register singleton
                    
                    SESSION_ARTIFACTS = {};
                    
                    this.topology = {
                        nodes: ['n1', 'n2', 'n3', 'n4'],
                        edges: [
                            { from: 'n1', to: 'n2' },
                            { from: 'n1', to: 'n3' },
                            { from: 'n2', to: 'n4' },
                            { from: 'n3', to: 'n4' }
                        ]
                    };
            
                    this.planSimulation();
                    
                    setTimeout(() => {
                        if (this.onopen) this.onopen();
                        this.runLoop();
                    }, 500);
                }
            
                // Called by the external mockResumeRun API
                resume() {
                    if (this.isPaused) {
                        console.log('[MOCK WS] Resuming simulation...');
                        this.isPaused = false;
                        // Emit a resumed log
                        this.emitLog('KERNEL', 'INFO', 'Resuming execution loop', 'SYS');
                        this.runLoop();
                    }
                }
            
                send(data: any) {
                    console.log('[MOCK WS] Received:', data);
                }
            
                close() {
                    console.log('[MOCK WS] Closing connection');
                    clearTimeout(this.timer);
                    activeSocket = null;
                    
                    if (this.onclose) {
                        this.onclose({ 
                            code: 1000, 
                            reason: 'Mock Simulation Ended', 
                            wasClean: true 
                        });
                    }
                }
            
                /**
                 * Helper to emit live log events (for ToolExecutionCard testing)
                 */
                private emitLog(agentId: string, category: string, message: string, metadata: string) {
                    const payload = {
                        type: 'log_event',
                        agent_id: agentId,
                        payload: {
                            category: category,
                            message: message,
                            metadata: metadata
                        },
                        timestamp: new Date().toISOString()
                    };
            
                    if (this.onmessage) {
                        this.onmessage({ data: JSON.stringify(payload) });
                    }
                }
            
                private planSimulation() {
                    // 1. Start State
                    this.addStep(500, 'RUNNING');
            
                    // 2. Orchestrator (n1) - Thinking logic
                    this.addStep(200, undefined, () => {
                         this.activeAgents.push('n1');
                         this.emitLog('n1', 'THOUGHT', 'Analyzing workflow requirements...', 'PLANNING');
                    });
                    this.simulateAgentExecution('n1', 1500, 450);
            
                    // 3. Start Parallel Execution
                    this.addStep(200, undefined, () => {
                         this.activeAgents.push('n2', 'n3');
                    });
            
                    // --- AGENT n3: PYTHON TOOL USER ---
                    // Simulating the Tool Loop from llm.py
                    this.addStep(800, undefined, () => {
                        this.emitLog('n3', 'THOUGHT', 'I need to calculate variance using pandas.', 'REASONING');
                    });
            
                    // 3a. Call Tool
                    this.addStep(400, undefined, () => {
                        // "IO_REQ" + "TOOL_CALL" triggers blue spinner in ToolExecutionCard
                        this.emitLog('n3', 'TOOL_CALL', 'execute_python({"code": "import pandas as pd\\ndf = pd.read_csv(\'data.csv\')..."})', 'IO_REQ');
                    });
            
                    // 3b. Tool Result (Success)
                    this.addStep(1500, undefined, () => {
                         // "IO_OK" + "TOOL_RESULT" triggers green check in ToolExecutionCard
                         // UPDATED: Include both files in the log message to trigger the UI ArtifactCards
                         this.emitLog('n3', 'TOOL_RESULT', 'Files generated: [\'latency_variance_analysis.png\', \'fictional_data.json\']\nStandard Output: Done.', 'IO_OK');
                    });
                    
                    // Complete n3
                    this.simulateAgentCompletion('n3', 500, 800, false);
            
                    // --- AGENT n2: DELEGATOR + ERROR SIMULATION ---
                    this.addStep(500, undefined, () => {
                         // Simulate a failed tool call first to test the Error Card
                         this.emitLog('n2', 'TOOL_CALL', 'web_search({"query": "internal_docs_v2"})', 'IO_REQ');
                    });
            
                    this.addStep(1200, undefined, () => {
                        // "IO_ERR" + "TOOL_RESULT" triggers red Error Card with traceback support
                        this.emitLog('n2', 'TOOL_RESULT', 'Error: Connection Timeout\nTraceback (most recent call last):\n  File "lib/search.py", line 40\n    raise TimeoutError("Gateway 504")', 'IO_ERR');
                    });
            
                    this.addStep(800, undefined, () => {
                        this.emitLog('n2', 'THOUGHT', 'Search failed. Attempting dynamic delegation strategy.', 'RECOVERY');
                    });
            
                    // n2 does its dynamic delegation thing
                    this.processDynamicChain('n2', 'n4'); 
            
                    // === INSERT SYSTEM INTERVENTION HERE ===
                    // We simulate a pause just before the final synthesis node (n4) starts.
                    this.addStep(0, 'AWAITING_APPROVAL', () => {
                        // Emit the specific log that triggers the ApprovalCard in OutputPane
                        // message="SAFETY_PATTERN_TRIGGERED", metadata="INTERVENTION"
                        this.emitLog('CORTEX', 'INFO', 'SAFETY_PATTERN_TRIGGERED', 'INTERVENTION');
                    }); 
                    
                    // 4. Synthesis (n4) runs (After approval)
                    this.simulateAgentExecution('n4', 3000, 2500);
            
                    // 5. Completion
                    this.addStep(1000, 'COMPLETED');
                }
            
                private processDynamicChain(currentId: string, finalDependentId: string) {
                    const isRoot = currentId === 'n2';
                    // Force delegation for n2 to show feature
                    const shouldDelegate = isRoot; 
                    
                    if (shouldDelegate) {
                        const newAgentId = `${currentId}_sub_A`;
                        const reason = "Search failed; spawning specialist.";
            
                        // This delay simulates the LLM generation time
                        this.addStep(1000, undefined, () => {
                            const output = generateDelegationArtifact(reason, currentId, newAgentId);
                            SESSION_ARTIFACTS[currentId] = { result: output };
            
                            this.activeAgents = this.activeAgents.filter(id => id !== currentId);
                            this.completedAgents.push(currentId);
                            this.totalTokens += 500;
                            
                            this.invocations.push({
                                id: `inv-${currentId}`,
                                agent_id: currentId,
                                status: 'success',
                                tokens_used: 500,
                                latency_ms: 1200,
                                artifact_id: `mock-art-${currentId}`
                            });
            
                            // Update Topology
                            this.topology.nodes.push(newAgentId);
                            this.topology.edges.push({ from: currentId, to: newAgentId });
                            // Rewire: n2 -> n4 becomes n2 -> sub -> n4
                            this.topology.edges = this.topology.edges.filter(e => !(e.from === currentId && e.to === finalDependentId));
                            this.topology.edges.push({ from: newAgentId, to: finalDependentId });
            
                            this.signatures[currentId] = `hash_${currentId}`;
                        });
            
                        // Start New Agent
                        this.addStep(500, undefined, () => {
                            this.activeAgents.push(newAgentId);
                            this.emitLog(newAgentId, 'THOUGHT', 'I have been spawned to handle the missing documentation.', 'INIT');
                        });
            
                        this.simulateAgentCompletion(newAgentId, 2000, 600, true);
            
                    } else {
                        SESSION_ARTIFACTS[currentId] = { 
                            result: `Analysis complete for node ${currentId}. Validated 100% data points.` 
                        };
                        this.simulateAgentCompletion(currentId, 1500 + Math.random() * 1000, 600, true);
                    }
                }
            
                private simulateAgentExecution(id: string, duration: number, tokens: number) {
                    this.addStep(200, undefined, () => {
                        if (!this.activeAgents.includes(id)) {
                            this.activeAgents.push(id);
                        }
                    });
                    this.simulateAgentCompletion(id, duration, tokens, false);
                }
            
                private simulateAgentCompletion(id: string, duration: number, tokens: number, isDynamic: boolean) {
                    this.addStep(duration, undefined, () => {
                        this.activeAgents = this.activeAgents.filter(a => a !== id);
                        this.completedAgents.push(id);
                        this.totalTokens += tokens;
                        this.signatures[id] = `hash_${Math.floor(Math.random()*10000).toString(16)}`;
            
                        const invocation: any = {
                            id: `inv-${id}`,
                            agent_id: id,
                            status: 'success',
                            tokens_used: tokens,
                            latency_ms: duration,
                            artifact_id: `mock-art-${id}`
                        };
            
                        // n3 uses execute_python to generate artifacts
                        if (id === 'n3') {
                            invocation.tools_used = ['execute_python'];
                        }
            
                        this.invocations.push(invocation);
                    });
                }
            
                private addStep(delay: number, statusOverride?: string, action?: () => void) {
                    // We defer the state snapshot to execution time (in runLoop)
                    // by wrapping the current state logic into the stored action or checking it dynamically
                    // BUT, since we are defining the plan sequentially, we need the simulation step object
                    // to hold the *intent* of the change, and we apply it when the timer hits.
                    
                    // However, the `steps` array in the original mock assumes pre-calculated state snapshots.
                    // To support `action` callback modifying state mid-flight, we need to adapt `runLoop`.
                    
                    this.steps.push({
                        delay,
                        state: {
                            status: statusOverride || 'RUNNING',
                            active_agents: [], // These will be filled dynamically in runLoop based on `this.activeAgents`
                            completed_agents: [],
                            failed_agents: [],
                            total_tokens_used: 0,
                            invocations: []
                        },
                        signatures: {},
                        topology: { nodes: [], edges: [] },
                        action // Store the action to be run before sending update
                    });
                }
            
                private runLoop() {
                    if (this.currentStep >= this.steps.length) {
                        this.close();
                        return;
                    }
            
                    const step = this.steps[this.currentStep];
                    
                    // 1. EXECUTE ACTION (Mutates this.activeAgents, this.topology, etc.)
                    if (step.action) {
                        step.action();
                    }
            
                    // 2. CONSTRUCT DYNAMIC STATE
                    // We ignore the empty placeholders in `step.state` and build from class properties
                    const dynamicState = {
                        status: step.state.status === 'RUNNING' ? 'RUNNING' : step.state.status,
                        active_agents: [...this.activeAgents],
                        completed_agents: [...this.completedAgents],
                        failed_agents: [],
                        total_tokens_used: this.totalTokens,
                        invocations: JSON.parse(JSON.stringify(this.invocations))
                    };
            
                    // 3. Send the Update
                    const message = {
                        type: 'state_update',
                        state: dynamicState,
                        signatures: { ...this.signatures },
                        topology: JSON.parse(JSON.stringify(this.topology))
                    };
            
                    if (this.onmessage) {
                        this.onmessage({ data: JSON.stringify(message) });
                    }
            
                    // 4. CHECK FOR INTERVENTION (PAUSE)
                    if (dynamicState.status === 'AWAITING_APPROVAL') {
                        console.log('[MOCK WS] Simulation paused for approval.');
                        this.isPaused = true;
                        this.currentStep++; 
                        return; // EXIT LOOP
                    }
            
                    // 5. Schedule next step
                    this.timer = setTimeout(() => {
                        this.currentStep++;
                        this.runLoop();
                    }, step.delay);
                }
            }
            
            // === MOCK ARTIFACT STORAGE ===
            
            const MOCK_ARTIFACTS: ArtifactMetadata[] = [
                {
                    run_id: 'mock-run-1234567890',
                    workflow_id: 'data_analysis_v2',
                    user_directive: 'Analyze Q3 financials and generate variance report',
                    created_at: new Date(Date.now() - 2 * 60 * 1000).toISOString(), // 2 minutes ago
                    expires_at: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString(), // 7 days from now
                    status: 'completed',
                    artifacts: [
                        {
                            filename: 'latency_variance_analysis.png',
                            agent_id: 'visualization_agent',
                            generated_at: new Date(Date.now() - 1.5 * 60 * 1000).toISOString(),
                            size_bytes: 45120,
                            content_type: 'image/png'
                        },
                        {
                            filename: 'metrics_summary.json',
                            agent_id: 'data_processor',
                            generated_at: new Date(Date.now() - 1.8 * 60 * 1000).toISOString(),
                            size_bytes: 2048,
                            content_type: 'application/json'
                        },
                        // UPDATED: Add JSON file here for sidebar testing
                        {
                            filename: 'fictional_data.json',
                            agent_id: 'data_generator',
                            generated_at: new Date(Date.now() - 1.9 * 60 * 1000).toISOString(),
                            size_bytes: 1024,
                            content_type: 'application/json'
                        }
                    ]
                },
                {
                    run_id: 'mock-run-0987654321',
                    workflow_id: 'report_generation',
                    user_directive: 'Generate executive summary from research findings',
                    created_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(), // 1 hour ago
                    expires_at: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString(),
                    status: 'completed',
                    artifacts: [
                        {
                            filename: 'executive_summary.md',
                            agent_id: 'document_writer',
                            generated_at: new Date(Date.now() - 58 * 60 * 1000).toISOString(),
                            size_bytes: 8192,
                            content_type: 'text/markdown'
                        },
                        {
                            filename: 'research_chart.png',
                            agent_id: 'visualization_agent',
                            generated_at: new Date(Date.now() - 59 * 60 * 1000).toISOString(),
                            size_bytes: 52000,
                            content_type: 'image/png'
                        }
                    ]
                },
                {
                    run_id: 'mock-run-5555555555',
                    workflow_id: 'system_audit',
                    user_directive: 'Audit system logs and generate security report',
                    created_at: new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString(), // 25 hours ago
                    expires_at: new Date(Date.now() + 6 * 24 * 60 * 60 * 1000).toISOString(),
                    status: 'completed',
                    artifacts: [
                        {
                            filename: 'security_report.pdf',
                            agent_id: 'security_analyzer',
                            generated_at: new Date(Date.now() - 24.5 * 60 * 60 * 1000).toISOString(),
                            size_bytes: 156000,
                            content_type: 'application/pdf'
                        },
                        {
                            filename: 'anomaly_log.csv',
                            agent_id: 'log_processor',
                            generated_at: new Date(Date.now() - 24.8 * 60 * 60 * 1000).toISOString(),
                            size_bytes: 12400,
                            content_type: 'text/csv'
                        }
                    ]
                }
            ];
            
            export async function mockGetAllArtifacts(): Promise<ArtifactMetadata[]> {
                console.log('[MOCK] Fetching all artifacts');
                return new Promise((resolve) => {
                    setTimeout(() => {
                        resolve(MOCK_ARTIFACTS);
                    }, 300);
                });
            }
            
            export async function mockGetRunArtifacts(runId: string): Promise<ArtifactMetadata | null> {
                console.log(`[MOCK] Fetching artifacts for run ${runId}`);
                return new Promise((resolve) => {
                    setTimeout(() => {
                        const metadata = MOCK_ARTIFACTS.find(a => a.run_id === runId);
                        resolve(metadata || null);
                    }, 300);
                });
            }
        - stores.ts
            Imports: api.ts, layout-engine.ts, mock-api.ts
            Imported by: layout-engine.ts
            // [[RARO]]/apps/web-console/src/lib/stores.ts
            
            import { writable, get } from 'svelte/store';
            import { getWebSocketURL, USE_MOCK, type WorkflowConfig } from './api'; // Import USE_MOCK
            import { MockWebSocket, mockResumeRun, mockStopRun } from './mock-api'; 
            import { DagLayoutEngine } from './layout-engine'; // Import Layout Engine
            
            // Import KERNEL_API for resume/stop endpoints
            const KERNEL_API = import.meta.env.VITE_KERNEL_URL || '/api';
            
            // === TYPES ===
            export interface LogEntry {
              id: string
              timestamp: string;
              role: string;
              message: string;
              metadata?: string;
              isAnimated?: boolean;
              category?: string;  // NEW: For tool/thought categorization (TOOL_CALL, TOOL_RESULT, THOUGHT)
              // [[NEW]] Fields for merging Tool Results into the Call log
              toolResult?: string;      // The output text from the tool
              toolStatus?: 'success' | 'error'; 
              toolDuration?: number;    // Execution time in ms
              isComplete?: boolean;     // Has the tool finished?
            }
            
            export interface AgentNode {
              id: string;
              label: string;
              x: number;
              y: number;
              model: string;
              prompt: string;
              status: 'idle' | 'running' | 'complete' | 'failed';
              role: 'orchestrator' | 'worker' | 'observer';
              acceptsDirective: boolean;  // Can this node receive operator directives?
              allowDelegation: boolean;   // Can this node spawn sub-agents?
            }
            
            export interface PipelineEdge {
              from: string;
              to: string;
              active: boolean;    // True = Animated Flow (Processing)
              finalized: boolean; // True = Solid Line (Completed)
              pulseAnimation: boolean;
              signatureHash?: string;
            }
            
            interface TopologySnapshot {
                nodes: string[];
                edges: { from: string; to: string }[];
            }
            
            export interface TelemetryState {
              latency: number;
              cacheHitRate: number;
              totalCost: number;
              errorCount: number;
              tokensUsed: number;
            }
            
            // === STORES ===
            export const logs = writable<LogEntry[]>([]);
            export const runtimeStore = writable<{ status: string; runId: string | null }>({
              status: 'IDLE',
              runId: null
            });
            
            // === THEME STORE ===
            export type ThemeMode = 'ARCHIVAL' | 'PHOSPHOR';
            export const themeStore = writable<ThemeMode>('ARCHIVAL');
            
            export function toggleTheme() {
                themeStore.update(current => current === 'ARCHIVAL' ? 'PHOSPHOR' : 'ARCHIVAL');
            }
            
            // === RFS STORES ===
            // The list of all files available in /storage/library
            export const libraryFiles = writable<string[]>([]);
            
            // The subset of files currently linked to the active directive
            export const attachedFiles = writable<string[]>([]);
            
            // Helper to toggle attachment status
            export function toggleAttachment(fileName: string) {
                attachedFiles.update(files => {
                    if (files.includes(fileName)) {
                        return files.filter(f => f !== fileName);
                    } else {
                        return [...files, fileName];
                    }
                });
            }
            
            // Initial Nodes State
            const initialNodes: AgentNode[] = [
              { id: 'orchestrator', label: 'ORCHESTRATOR', x: 20, y: 50, model: 'reasoning', prompt: 'Analyze the user request and determine optimal sub-tasks.', status: 'idle', role: 'orchestrator', acceptsDirective: true, allowDelegation: true },
              { id: 'retrieval', label: 'RETRIEVAL', x: 50, y: 30, model: 'fast', prompt: 'Search knowledge base for relevant context.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false },
              { id: 'code_interpreter', label: 'CODE_INTERP', x: 50, y: 70, model: 'fast', prompt: 'Execute Python analysis on provided data.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false },
              { id: 'synthesis', label: 'SYNTHESIS', x: 80, y: 50, model: 'thinking', prompt: 'Synthesize all findings into a final report.', status: 'idle', role: 'worker', acceptsDirective: false, allowDelegation: false }
            ];
            
            export const agentNodes = writable<AgentNode[]>(initialNodes);
            
            // Initial Edges State
            const initialEdges: PipelineEdge[] = [
              { from: 'orchestrator', to: 'retrieval', active: false, finalized: false, pulseAnimation: false },
              { from: 'orchestrator', to: 'code_interpreter', active: false, finalized: false, pulseAnimation: false },
              { from: 'retrieval', to: 'synthesis', active: false, finalized: false, pulseAnimation: false },
              { from: 'code_interpreter', to: 'synthesis', active: false, finalized: false, pulseAnimation: false }
            ];
            
            export const pipelineEdges = writable<PipelineEdge[]>(initialEdges);
            export const selectedNode = writable<string | null>(null);
            
            // Telemetry Store
            export const telemetry = writable<TelemetryState>({
              latency: 0,
              cacheHitRate: 0,
              totalCost: 0,
              errorCount: 0,
              tokensUsed: 0
            });
            
            // === NEW STORE ===
            // False = Execution Mode (Direct to Kernel)
            // True = Architect Mode (Query -> Agent Service -> Update Graph)
            export const planningMode = writable<boolean>(false);
            
            
            // === ACTIONS ===
            // === GRAPH MUTATION ACTIONS ===
            
            export function updateNodePosition(id: string, x: number, y: number) {
                agentNodes.update(nodes => 
                    nodes.map(n => n.id === id ? { ...n, x, y } : n)
                );
            }
            
            export function addConnection(from: string, to: string) {
                pipelineEdges.update(edges => {
                    // Prevent duplicates
                    if (edges.find(e => e.from === from && e.to === to)) return edges;
                    // Prevent self-loops
                    if (from === to) return edges;
                    
                    return [...edges, {
                        from,
                        to,
                        active: false,
                        finalized: false,
                        pulseAnimation: false
                    }];
                });
            }
            
            export function removeConnection(from: string, to: string) {
                pipelineEdges.update(edges => 
                    edges.filter(e => !(e.from === from && e.to === to))
                );
            }
            
            
            
            export function createNode(x: number, y: number) {
                agentNodes.update(nodes => {
                    const id = `node_${Date.now().toString().slice(-4)}`;
                    return [...nodes, {
                        id,
                        label: 'NEW_AGENT',
                        x,
                        y,
                        model: 'fast',
                        prompt: 'Describe task...',
                        status: 'idle',
                        role: 'worker',
                        acceptsDirective: false,  // Default to false for new nodes
                        allowDelegation: false    // Default to false for new nodes
                    }];
                });
            }
            
            
            
            export function deleteNode(id: string) {
                // 1. Remove Node
                agentNodes.update(nodes => nodes.filter(n => n.id !== id));
                
                // 2. Remove associated edges
                pipelineEdges.update(edges => edges.filter(e => e.from !== id && e.to !== id));
                
                // 3. Clear selection if needed
                if (get(selectedNode) === id) {
                    selectedNode.set(null);
                }
            }
            
            export function renameNode(oldId: string, newId: string): boolean {
              // 1. Validation: Ensure new ID is unique and valid
              if (!newId || newId === oldId) return false;
              
              const currentNodes = get(agentNodes);
              if (currentNodes.find(n => n.id === newId)) {
                console.warn(`ID "${newId}" already exists.`);
                return false;
              }
            
              // 2. Update the Node definition
              agentNodes.update(nodes => 
                nodes.map(n => n.id === oldId ? { ...n, id: newId } : n)
              );
            
              // 3. Update all Edges (Rewiring)
              pipelineEdges.update(edges => 
                edges.map(e => ({
                  ...e,
                  from: e.from === oldId ? newId : e.from,
                  to: e.to === oldId ? newId : e.to
                }))
              );
            
              // 4. Update Selection State (Keep the panel open)
              if (get(selectedNode) === oldId) {
                selectedNode.set(newId);
              }
            
              return true;
            }
            
            
            /**
             * PURE STATE MUTATION
             * Takes a backend manifest and paints it to the UI stores.
             * Does NOT trigger execution.
             */
            export function loadWorkflowManifest(manifest: WorkflowConfig) {
              // 1. Transform Manifest Agents -> UI Nodes
              const newNodes: AgentNode[] = manifest.agents.map((agent, index) => {
                // Use semantic alias directly (fast, reasoning, thinking)
                // No normalization needed - backend already sends the correct alias
                return {
                  id: agent.id,
                  label: agent.id.replace(/^(agent_|node_)/i, '').toUpperCase().substring(0, 12),
                  // Use provided position or fallback calculation
                  x: agent.position?.x || (20 + (index * 15)),
                  y: agent.position?.y || (30 + (index * 10)),
                  model: agent.model,
                  prompt: agent.prompt,
                  status: 'idle',
                  role: agent.role,
                  acceptsDirective: agent.accepts_directive || agent.role === 'orchestrator',  // Use backend flag or default to true for orchestrators
                  allowDelegation: agent.allow_delegation || false  // Use backend flag or default to false
                };
              });
            
              // 2. Transform Dependencies -> UI Edges
              const newEdges: PipelineEdge[] = [];
              manifest.agents.forEach(agent => {
                if (agent.depends_on) {
                  agent.depends_on.forEach(parentId => {
                    newEdges.push({
                      from: parentId,
                      to: agent.id,
                      active: false,
                      finalized: false,
                      pulseAnimation: false
                    });
                  });
                }
              });
            
              // 3. Commit
              agentNodes.set(newNodes);
              pipelineEdges.set(newEdges);
              selectedNode.set(null); // Clear selection
            }
            
            /**
             * LOGIC GAP FIX: Flow A
             * Translates Backend Manifest -> Frontend State
             */
            export function overwriteGraphFromManifest(manifest: WorkflowConfig) {
              // 1. Transform Manifest Agents -> UI Nodes
              const newNodes: AgentNode[] = manifest.agents.map((agent, index) => {
                // Use semantic alias directly (fast, reasoning, thinking)
                // No normalization needed - backend already sends the correct alias
                return {
                  id: agent.id,
                  label: agent.id.replace(/^(agent_|node_)/i, '').toUpperCase(), // Clean ID for display
                  x: agent.position?.x || (20 + index * 15), // Fallback layout logic
                  y: agent.position?.y || (30 + index * 10),
                  model: agent.model,
                  prompt: agent.prompt,
                  status: 'idle',
                  role: agent.role,
                  acceptsDirective: agent.accepts_directive || agent.role === 'orchestrator',  // Use backend flag or default to true for orchestrators
                  allowDelegation: agent.allow_delegation || false  // Use backend flag or default to false
                };
              });
            
              // 2. Transform Manifest Dependencies -> UI Edges
              const newEdges: PipelineEdge[] = [];
              manifest.agents.forEach(agent => {
                agent.depends_on.forEach(parentId => {
                  newEdges.push({
                    from: parentId,
                    to: agent.id,
                    active: false,
                    finalized: false,
                    pulseAnimation: false
                  });
                });
              });
            
              // 3. Commit to Store
              agentNodes.set(newNodes);
              pipelineEdges.set(newEdges);
            }
            
            
            // HITL (Human-in-the-Loop) Actions
            export async function resumeRun(runId: string) {
                if (USE_MOCK) {
                    runtimeStore.update(s => ({ ...s, status: 'RUNNING' }));
                    addLog('KERNEL', 'Mock: Resuming execution...', 'SYS');
                    // CHANGE: Actually trigger the mock engine
                    await mockResumeRun(runId);
                    return;
                }
            
                try {
                    const res = await fetch(`${KERNEL_API}/runtime/${runId}/resume`, { method: 'POST' });
            
                    if (!res.ok) {
                        throw new Error(`Resume failed: ${res.status} ${res.statusText}`);
                    }
            
                    addLog('KERNEL', 'Execution resumed by operator', 'SYS');
                } catch (e) {
                    console.error('Resume API error:', e);
                    addLog('KERNEL', `Resume failed: ${e}`, 'ERR');
                }
            }
            
            export async function stopRun(runId: string) {
                if (USE_MOCK) {
                    runtimeStore.update(s => ({ ...s, status: 'FAILED' }));
                    addLog('KERNEL', 'Mock: Run terminated by operator', 'SYS');
                    // CHANGE: Actually trigger the mock engine
                    await mockStopRun(runId);
                    return;
                }
            
                try {
                    const res = await fetch(`${KERNEL_API}/runtime/${runId}/stop`, { method: 'POST' });
            
                    if (!res.ok) {
                        throw new Error(`Stop failed: ${res.status} ${res.statusText}`);
                    }
            
                    addLog('KERNEL', 'Run terminated by operator', 'SYS');
                } catch (e) {
                    console.error('Stop API error:', e);
                    addLog('KERNEL', `Stop failed: ${e}`, 'ERR');
                }
            }
            // === AUTHORITATIVE TOPOLOGY SYNC ===
            // This function trusts the Kernel's topology as the source of truth
            function syncTopology(topology: TopologySnapshot) {
                const currentNodes = get(agentNodes);
                const currentEdges = get(pipelineEdges);
            
                // 1. Reconcile Edges (Source of Truth)
                // Rebuild the edge list based on Kernel topology to ensure we capture rewiring
                const newEdges: PipelineEdge[] = topology.edges.map(kEdge => {
                    // Try to preserve animation state if edge already existed
                    const existing = currentEdges.find(e => e.from === kEdge.from && e.to === kEdge.to);
                    return {
                        from: kEdge.from,
                        to: kEdge.to,
                        active: existing ? existing.active : false,
                        finalized: existing ? existing.finalized : false,
                        pulseAnimation: existing ? existing.pulseAnimation : false,
                        signatureHash: existing ? existing.signatureHash : undefined
                    };
                });
            
                // 2. Reconcile Nodes
                const nodeMap = new Map(currentNodes.map(n => [n.id, n]));
                const newNodes: AgentNode[] = [];
                let structureChanged = false;
            
                // Check for edge count mismatch or node count mismatch
                if (newEdges.length !== currentEdges.length || topology.nodes.length !== currentNodes.length) {
                    structureChanged = true;
                }
            
                topology.nodes.forEach(nodeId => {
                    if (nodeMap.has(nodeId)) {
                        // Existing node: Keep it, preserve state
                        newNodes.push(nodeMap.get(nodeId)!);
                    } else {
                        // NEW NODE DETECTED (Delegation)
                        // Initialize at 0,0. The Layout Engine will move it immediately.
                        structureChanged = true;
                        newNodes.push({
                            id: nodeId,
                            // Heuristic Labeling since Kernel currently sends IDs only in topology
                            label: nodeId.toUpperCase().substring(0, 12),
                            x: 0,
                            y: 0,
                            model: 'fast', // Default to fast for dynamically spawned agents
                            prompt: 'Dynamic Agent',
                            status: 'running', // Usually spawned active
                            role: 'worker',
                            acceptsDirective: false,  // Dynamically spawned agents don't accept directives by default
                            allowDelegation: false    // Dynamically spawned agents don't delegate by default
                        });
                    }
                });
            
                // 3. APPLY LAYOUT (Only if structure changed)
                if (structureChanged) {
                    console.log('[UI] Topology mutation detected. Recalculating layout...');
                    const layoutNodes = DagLayoutEngine.computeLayout(newNodes, newEdges);
                    agentNodes.set(layoutNodes);
                    pipelineEdges.set(newEdges);
                } else {
                    // If structure is same, update edges to respect any strict rewiring
                    pipelineEdges.set(newEdges);
                }
            }
            
            /**
             * addLog: Enhanced to merge TOOL_RESULT into previous TOOL_CALL
             */
            export function addLog(
                role: string,
                message: string,
                metadata: string = '',
                isAnimated: boolean = false,
                customId?: string,
                category?: string,
                extra?: any // Optional bag for duration, etc.
            ) {
              logs.update(currentLogs => {
                // 1. Check for Duplicate IDs
                if (customId && currentLogs.find(entry => entry.id === customId)) {
                  return currentLogs;
                }
            
                // 2. MERGE STRATEGY: If this is a TOOL_RESULT, find the pending TOOL_CALL
                if (category === 'TOOL_RESULT') {
                    // Search backwards for the most recent TOOL_CALL by this agent that isn't complete
                    for (let i = currentLogs.length - 1; i >= 0; i--) {
                        const entry = currentLogs[i];
                        
                        // Match Agent + Category + Pending Status
                        if (entry.role === role && entry.category === 'TOOL_CALL' && !entry.isComplete) {
                            // Return new array with specific entry updated
                            const updatedLogs = [...currentLogs];
                            updatedLogs[i] = {
                                ...entry,
                                isComplete: true,
                                toolResult: message, // The result message replaces/appends to the call
                                toolStatus: metadata === 'IO_ERR' ? 'error' : 'success',
                                metadata: metadata // Update metadata (IO_OK / IO_ERR)
                            };
                            return updatedLogs;
                        }
                    }
                    // Fallback: If no matching call found (rare), insert as new entry below
                }
            
                // 3. Standard Insertion
                return [...currentLogs, {
                  id: customId || crypto.randomUUID(),
                  timestamp: new Date().toISOString(),
                  role,
                  message,
                  metadata,
                  isAnimated,
                  category,
                  // Initialize Tool Call state
                  isComplete: category === 'TOOL_CALL' ? false : undefined
                }];
              });
            }
            
            export function updateLog(id: string, updates: Partial<LogEntry>) {
              logs.update(l => 
                l.map(entry => entry.id === id ? { ...entry, ...updates } : entry)
              );
            }
            
            export function updateNodeStatus(id: string, status: 'idle' | 'running' | 'complete' | 'failed') {
              agentNodes.update(nodes =>
                nodes.map(n => n.id === id ? { ...n, status } : n)
              );
            }
            
            export function selectNode(id: string) {
              selectedNode.set(id);
            }
            
            export function deselectNode() {
              selectedNode.set(null);
            }
            
            // === WEBSOCKET HANDLING ===
            
            // Change type to union to allow MockSocket
            let ws: WebSocket | MockWebSocket | null = null;
            
            export function connectRuntimeWebSocket(runId: string) {
              if (ws) {
                ws.close();
              }
            
              const url = getWebSocketURL(runId);
              console.log('[WS] Connecting to:', url);
            
              // ** MOCK SWITCHING **
              if (USE_MOCK) {
                addLog('SYSTEM', 'Initializing MOCK runtime environment...', 'DEBUG');
                ws = new MockWebSocket(url);
              } else {
                ws = new WebSocket(url);
              }
            
              // TypeScript note: MockWebSocket and WebSocket need matching signatures
              // for the methods we use below. Since we defined them similarly in mock-api, this works.
            
              ws.onopen = () => {
                console.log('[WS] Connected successfully to:', url);
                addLog('KERNEL', `Connected to runtime stream: ${runId}`, 'NET_OK');
                runtimeStore.set({ status: 'RUNNING', runId });
              };
            
              ws.onmessage = (event: any) => { // Use 'any' or generic event type
                console.log('[WS] Message received:', event.data.substring(0, 200));
                try {
                  const data = JSON.parse(event.data);
                  if (data.type === 'state_update' && data.state) {
                    
                    // === APPROVAL DETECTION ===
                    const currentState = get(runtimeStore);
                    // FIXED: Normalize to uppercase and check for underscore format
                    const newStateStr = (data.state.status || '').toUpperCase();
            
                    if (newStateStr === 'AWAITING_APPROVAL' && currentState.status !== 'AWAITING_APPROVAL') {
                      // Check if we already logged this approval request to avoid duplicates
                      const logsList = get(logs);
                      const hasPending = logsList.some(l => l.metadata === 'INTERVENTION');
            
                      if (!hasPending) {
                        addLog(
                          'CORTEX',
                          'SAFETY_PATTERN_TRIGGERED',
                          'INTERVENTION', // Metadata tag
                          false,
                          'approval-req-' + Date.now() // Custom ID
                        );
                      }
                    }
            
                    // CRITICAL FIX: Pass topology to syncState
                    syncState(data.state, data.signatures, data.topology);
            
                    if (data.state.status) {
                         runtimeStore.update(s => ({ ...s, status: data.state.status.toUpperCase() }));
                    }
                  }
            
                  // [[NEW]] Intermediate log events
                      else if (data.type === 'log_event') {
                        const agentId = data.agent_id ? data.agent_id.toUpperCase() : 'SYSTEM';
                        const p = data.payload;
            
                        addLog(
                          agentId,
                          p.message,
                          p.metadata || 'INFO',
                          false,
                          undefined,
                          p.category,
                          p.extra // Pass extra data if mock sends it (e.g. duration)
                        );
                      }
            
                  else if (data.error) {
                    addLog('KERNEL', `Runtime error: ${data.error}`, 'ERR');
                  }
                } catch (e) {
                  console.error('[WS] Failed to parse message:', e, event.data);
                }
              };
            
              ws.onclose = (e: CloseEvent) => {
                console.log('[WS] Connection closed:', e.code, e.reason);
                addLog('KERNEL', 'Connection closed.', 'NET_END');
                
                // 1. Force Global Status to COMPLETED (if not failed)
                runtimeStore.update(s => {
                    if (s.status !== 'FAILED') return { ...s, status: 'COMPLETED' };
                    return s;
                });
            
                // 2. Force Finalize Edges
                pipelineEdges.update(edges => {
                  return edges.map(e => ({
                    ...e,
                    active: false,
                    pulseAnimation: false,
                    finalized: e.active || e.finalized 
                  }));
                });
              };
            
              if (!USE_MOCK) {
                  (ws as WebSocket).onerror = (e) => {
                    console.error('[WS] Error event:', e);
                    addLog('KERNEL', 'WebSocket connection error.', 'ERR');
                  };
              }
            }
            
            // === STATE SYNCHRONIZATION LOGIC ===
            
            const processedInvocations = new Set<string>();
            
            function syncState(state: any, signatures: Record<string, string> = {}, topology?: TopologySnapshot) {
                // 1. Sync Topology FIRST (Create/update nodes/edges from Kernel's authoritative view)
                if (topology) {
                    syncTopology(topology);
                }
            
                // Normalize status to handle lowercase from Rust serialization
                const rawStatus = state.status ? state.status.toLowerCase() : 'running';
                const isRunComplete = rawStatus === 'completed' || rawStatus === 'failed';
            
                // 2. Sync Node Status
                agentNodes.update(nodes => {
                    return nodes.map(n => {
                        let status: 'idle' | 'running' | 'complete' | 'failed' = 'idle';
                        if (state.active_agents.includes(n.id)) status = 'running';
                        else if (state.completed_agents.includes(n.id)) status = 'complete';
                        else if (state.failed_agents.includes(n.id)) status = 'failed';
                        return { ...n, status };
                    });
                });
            
                // 3. Sync Edges
                pipelineEdges.update(edges => {
                    return edges.map(e => {
                        const fromComplete = state.completed_agents.includes(e.from);
                        const toStarted = state.active_agents.includes(e.to) || state.completed_agents.includes(e.to);
            
                        const hasDataFlowed = fromComplete && toStarted;
            
                        // Active: Flowing but not done
                        const active = hasDataFlowed && !isRunComplete;
            
                        // Finalized: Flowed and now done
                        const finalized = hasDataFlowed && isRunComplete;
            
                        const sig = signatures[e.from];
            
                        return {
                            ...e,
                            active,
                            finalized,
                            pulseAnimation: state.active_agents.includes(e.to),
                            signatureHash: sig
                        };
                    });
                });
            
                // 4. Sync Telemetry
                const cost = (state.total_tokens_used / 1_000_000) * 2.0;
                telemetry.set({
                    latency: 0,
                    cacheHitRate: 0,
                    totalCost: cost,
                    errorCount: state.failed_agents.length,
                    tokensUsed: state.total_tokens_used
                });
            
                // 5. Sync Logs
                if (state.invocations && Array.isArray(state.invocations)) {
                    state.invocations.forEach(async (inv: any) => {
                        if (!inv || !inv.id || processedInvocations.has(inv.id)) return;
            
                        processedInvocations.add(inv.id);
                        const agentLabel = (inv.agent_id || 'UNKNOWN').toUpperCase();
            
                        try {
                            if (inv.status === 'success') {
                                if (inv.artifact_id) {
                                    addLog(agentLabel, 'Initiating output retrieval...', 'LOADING', false, inv.id);
                                    try {
                                        const { getArtifact } = await import('./api');
                                        const fetchPromise = getArtifact(state.run_id, inv.agent_id);
                                        const timeoutPromise = new Promise((_, reject) => setTimeout(() => reject(new Error('Timeout')), 5000));
                                        const artifact: any = await Promise.race([fetchPromise, timeoutPromise]);
            
                                        if (artifact) {
                                            let outputText = '';
            
                                            if (typeof artifact === 'string') {
                                                outputText = artifact;
                                            } else if (typeof artifact === 'object') {
                                                // 1. Try to find actual human-readable content
                                                if (artifact.result) outputText = artifact.result;
                                                else if (artifact.output) outputText = artifact.output;
                                                else if (artifact.text) outputText = artifact.text;
                                                
                                                // 2. Intercept Metadata-only objects (The issue you saw)
                                                // If we see 'artifact_stored' or 'model' but no text fields, 
                                                // suppress the raw JSON dump.
                                                else if ('artifact_stored' in artifact || 'model' in artifact) {
                                                    // Leave empty; we will populate with file tag or generic success below
                                                    outputText = ''; 
                                                }
                                                else {
                                                    // Genuine unknown object, fallback to dump
                                                    outputText = JSON.stringify(artifact, null, 2);
                                                }
                                            }
            
                                            // 3. Ensure File Generation Tags are present
                                            // This is crucial for <ArtifactCard /> rendering
                                            if (artifact.files_generated && Array.isArray(artifact.files_generated) && artifact.files_generated.length > 0) {
                                                const filename = artifact.files_generated[0];
            
                                                const isImage = /\.(png|jpg|jpeg|svg|webp)$/i.test(filename);
                                                const label = isImage ? "Generated Image" : "Generated File";
                                                
                                                const systemTag = `[SYSTEM: ${label} saved to '${filename}']`;
            
                                                // Only append if the tag isn't already present in the text
                                                if (!outputText.includes(systemTag)) {
                                                    outputText = outputText ? `${outputText}\n\n${systemTag}` : systemTag;
                                                }
                                            }
            
                                            // 4. Final Safety Fallback
                                            // If the Agent Service saved metadata to Redis but didn't save the text "result",
                                            // and no files were generated, we show a polite status instead of raw JSON.
                                            if (!outputText || outputText.trim() === '') {
                                                outputText = "Task execution completed successfully.";
                                            }
            
                                            updateLog(inv.id, {
                                                message: outputText,
                                                metadata: `TOKENS: ${inv.tokens_used || 0} | LATENCY: ${Math.round(inv.latency_ms || 0)}ms`, // Rounded latency
                                                isAnimated: true
                                            });
                                        } else {
                                            updateLog(inv.id, { message: 'Artifact empty or expired', metadata: 'WARN' });
                                        }
                                    } catch (err) {
                                        console.error('Artifact fetch failed:', err);
                                        updateLog(inv.id, { message: 'Output retrieval failed. Check connection.', metadata: 'NET_ERR' });
                                    }
                                } else {
                                    addLog(agentLabel, 'Completed (No Output)', `TOKENS: ${inv.tokens_used}`, false, inv.id);
                                }
                            } else if (inv.status === 'failed') {
                                let errorDisplay = 'Execution Failed';
                                if (inv.error_message) {
                                    errorDisplay = `<div style="color:#d32f2f; font-weight:bold; margin-bottom:4px">EXECUTION HALTED</div><div style="background: rgba(211, 47, 47, 0.05); border-left: 3px solid #d32f2f; padding: 8px; font-family: monospace; font-size: 11px; white-space: pre-wrap; color: #b71c1c;">${escapeHtml(inv.error_message)}</div>`;
                                }
                                addLog(agentLabel, errorDisplay, 'ERR', false, inv.id);
                            }
                        } catch (e) {
                            console.error('Error processing invocation log:', e);
                        }
                    });
                }
            }
            
            function escapeHtml(unsafe: string) {
                return unsafe.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
            }
        - syntax-lite.ts
            // [[RARO]]/apps/web-console/src/lib/syntax-lite.ts
            
            export function highlight(code: string, lang: string): string {
                // 1. Sanitize HTML entities first to prevent injection/layout breaking
                let html = code
                  .replace(/&/g, "&amp;")
                  .replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;");
              
                // Storage for protected tokens (Strings & Comments)
                const tokens: string[] = [];
                const pushToken = (str: string) => {
                    tokens.push(str);
                    return `%%%TOKEN_${tokens.length - 1}%%%`;
                };
              
                // 2. Extract Strings (Single/Double/Backtick) -> Placeholders
                // We do this first so keywords/comments inside strings are ignored
                html = html.replace(/(['"`])(.*?)\1/g, (match) => {
                    return pushToken(`<span class="token-str">${match}</span>`);
                });
              
                // 3. Extract Comments -> Placeholders
                // Note: JS/TS style // and Python/Bash style #
                html = html.replace(/(\/\/.*$)|(#.*$)/gm, (match) => {
                    return pushToken(`<span class="token-comment">${match}</span>`);
                });
              
                // 4. Highlight Keywords (Safe to do now, strings/comments are hidden)
                const keywords = /\b(import|export|from|const|let|var|function|return|if|else|for|while|class|interface|type|async|await|def|print|impl|struct|fn|pub)\b/g;
                html = html.replace(keywords, '<span class="token-kw">$1</span>');
              
                // 5. Highlight Numbers
                html = html.replace(/\b(\d+)\b/g, '<span class="token-num">$1</span>');
              
                // 6. Highlight Booleans
                html = html.replace(/\b(true|false|null|None)\b/g, '<span class="token-bool">$1</span>');
              
                // 7. Restore Placeholders
                // We cycle until no placeholders remain (just in case)
                tokens.forEach((token, i) => {
                    html = html.replace(`%%%TOKEN_${i}%%%`, token);
                });
              
                return html;
            }
      - App.svelte
          <!-- // [[RARO]]/apps/web-console/src/App.svelte
          // Purpose: Root Application Layout. Handles Global State (Theme/Hero) and Top-Level Layout.
          // Architecture: Layout Orchestrator
          // Dependencies: Stores, Components -->
          
          <script lang="ts">
            import { fade } from 'svelte/transition'
            import OutputPane from '$components/OutputPane.svelte'
            import PipelineStage from '$components/PipelineStage.svelte'
            import ControlDeck from '$components/ControlDeck.svelte'
            import Hero from '$components/Hero.svelte'
            import SettingsRail from '$components/SettingsRail.svelte'
            import EnvironmentRail from '$components/EnvironmentRail.svelte'
            import { addLog, themeStore } from '$lib/stores'
          
            let expanded = $state(false)
            let appState = $state<'HERO' | 'CONSOLE'>('HERO')
            
            // DEBUG STATE: Toggle with Alt + S
            let slowMotion = $state(false); 
          
            function togglePipeline() {
              expanded = !expanded
            }
          
            function enterConsole() {
              appState = 'CONSOLE'
              setTimeout(() => {
                  addLog('KERNEL', 'RARO Runtime Environment v0.1.0.', 'SYSTEM_BOOT')
                  setTimeout(() => addLog('SYSTEM', 'Connection established. Status: IDLE.', 'NET_OK'), 300)
              }, 500)
            }
          
            // GLOBAL SHORTCUTS
            function handleGlobalKey(e: KeyboardEvent) {
              // Alt + S: Toggle Slow Motion for animation debugging
              if (e.altKey && e.code === 'KeyS') {
                  slowMotion = !slowMotion;
                  if (slowMotion) addLog('DEBUG', 'Time dilation enabled (Slow Motion).', 'SYS');
                  else addLog('DEBUG', 'Time dilation disabled.', 'SYS');
              }
            }
          </script>
          
          <svelte:window onkeydown={handleGlobalKey} />
          
          <!-- Apply .slow-motion class based on state -->
          <main class="mode-{$themeStore.toLowerCase()} {slowMotion ? 'slow-motion' : ''}">
              
              <!-- Global Texture Overlay -->
              <div class="noise-overlay"></div>
          
              {#if appState === 'HERO'}
                <Hero onenter={enterConsole} />
              {:else}
                <!-- 
                  WORKSPACE LAYOUT
                  SettingsRail is absolute positioned (right), chassis is centered.
                -->
                <div class="workspace" in:fade={{ duration: 800, delay: 200 }}>
                  
          
                  <!-- LEFT: Environment -->
                  <EnvironmentRail />
          
                  <div 
                    id="chassis" 
                    class={expanded ? 'expanded' : ''}
                  >
                    <OutputPane />
                    <PipelineStage {expanded} ontoggle={togglePipeline} />
                    <ControlDeck {expanded} />
                  </div>
          
                  <SettingsRail />
                  
                </div>
              {/if}
          
          </main>
          
          <style>
            /* 
              GLOBAL RESET & VARS
            */
            :global(:root) {
              /* === CONSTANTS === */
              --font-ui: 'Inter', -apple-system, system-ui, sans-serif;
              --font-code: 'JetBrains Mono', 'Fira Code', monospace;
              --ease-snap: cubic-bezier(0.16, 1, 0.3, 1);
              
              /* Digital Constants */
              --arctic-cyan: #00F0FF;
              --arctic-dim: rgba(0, 240, 255, 0.08);
              --arctic-glow: rgba(0, 240, 255, 0.4);
              --arctic-lilac: rgba(113, 113, 242, 0.7);
              --arctic-lilac-lite: rgba(55, 49, 242, 0.2);
              
              /* Semantic Signals */
              --alert-amber: #FFB300;
              --signal-success: #2ea043; /* Added: Standard Terminal Green */
            }
          
            /* === DEBUG: SLOW MOTION OVERRIDE === */
            /* This forces all transitions and animations to take 3 seconds */
            :global(.slow-motion) :global(*),
            :global(.slow-motion) :global(*::before),
            :global(.slow-motion) :global(*::after) {
                transition-duration: 3s !important;
                animation-duration: 3s !important;
            }
            
            /* Exclude things that look broken when slow (like typing cursors) */
            :global(.slow-motion) :global(.cursor),
            :global(.slow-motion) :global(.blink),
            :global(.slow-motion) :global(.led) {
                animation-duration: 0.5s !important;
            }
          
            /* === REALITY 1: ARCHIVAL (Day / Physical) === */
            :global(.mode-archival) {
              --paper-bg: #EAE6DF;
              --paper-surface: #F2EFEA;
              --paper-surface-dim: #E6E2DD;
              --paper-ink: #1A1918;
              --paper-line: #A8A095;
              --paper-accent: #D4CDC5;
              
              /* The Screen stays dark even in day mode */
              --digi-void: #090C10;
              --digi-panel: #161B22;
              --digi-line: #30363D;
              --digi-text: #e6edf3;
              --digi-text-dim: #8b949e;
            }
          
            /* === REALITY 2: PHOSPHOR (Night / Digital) === */
            :global(.mode-phosphor) {
              --paper-bg: #050505;
              --paper-surface: #090C10;
              --paper-surface-dim: #020202;
              --paper-ink: #E0E0E0;
              --paper-line: #7087a7;
              --paper-accent: #30363d;
              
              --digi-void: #050505;
              --digi-panel: #0d1117;
              --digi-line: #21262d;
              --digi-text: #e6edf3;
              --digi-text-dim: #8b949e;
            }
          
          
            :global(*) { box-sizing: border-box; }
          
            /* SCROLLBARS */
            :global(*) { scrollbar-width: thin; scrollbar-color: var(--paper-accent) transparent; }
            :global(::-webkit-scrollbar) { width: 6px; height: 6px; }
            :global(::-webkit-scrollbar-track) { background: transparent; }
            :global(::-webkit-scrollbar-thumb) { background-color: var(--paper-accent); border-radius: 3px; border: 1px solid transparent; background-clip: content-box; }
            :global(::-webkit-scrollbar-thumb:hover) { background-color: var(--paper-line); }
            
            :global(.mode-phosphor ::-webkit-scrollbar-thumb) { background-color: var(--paper-line); }
            :global(.mode-phosphor ::-webkit-scrollbar-thumb:hover) { background-color: var(--paper-ink); }
          
            :global(html), :global(body) {
              margin: 0; padding: 0; width: 100%; height: 100%;
              background: #000; overflow: hidden;
            }
          
            :global(body) {
              font-family: var(--font-ui);
              color: var(--paper-ink);
            }
          
            main {
              width: 100vw; height: 100vh; display: flex; justify-content: center;
              background: var(--paper-bg);
              transition: background 0.6s var(--ease-snap), color 0.6s var(--ease-snap);
              position: relative; overflow: hidden;
            }
          
            .noise-overlay {
              position: absolute; top: 0; left: 0; width: 100%; height: 100%;
              background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)' opacity='0.04'/%3E%3C/svg%3E");
              pointer-events: none; z-index: 9999; opacity: 0.35; mix-blend-mode: overlay;
            }
          
            .workspace {
              width: 100%; height: 100vh;
              display: flex; justify-content: center; align-items: flex-start;
              position: relative; /* Context for SettingsRail absolute positioning */
            }
          
            #chassis {
              width: 800px; min-width: 800px; flex-shrink: 0; height: 100vh;
              border-left: 1px solid var(--paper-line); border-right: 1px solid var(--paper-line);
              background: var(--paper-bg);
              display: flex; flex-direction: column;
              position: relative;
              box-shadow: 0 0 100px rgba(0,0,0,0.1);
              transition: border-color 0.6s, background 0.6s, box-shadow 0.6s;
              z-index: 10;
            }
          </style>
      - main.ts
          import { mount } from 'svelte'
          import App from './App.svelte'
          
          const app = mount(App, {
            target: document.getElementById('app')!
          })
          
          export default app
    - .dockerignore
        target
        **/*.rs.bk
        .env
        .git
        .DS_Store
    - .env.mock.example
        VITE_API_URL=http://localhost:3000
        # Set to 'true' to use the new mock-api and avoid real backend connection
        VITE_USE_MOCK_API=true
    - Dockerfile
        FROM node:20-alpine AS builder
        
        WORKDIR /app
        
        # 1. Accept the Argument (Default to false for production container safety)
        ARG VITE_USE_MOCK_API=false
        
        # 2. Set it as an ENV for the build process
        ENV VITE_USE_MOCK_API=$VITE_USE_MOCK_API
        
        # Copy package management files
        COPY package.json package-lock.json* ./
        
        # Install dependencies
        # Old: RUN npm ci
        # New:
        RUN npm install
        
        # Copy source code
        COPY . .
        
        # Build the application
        RUN npm run build
        
        # --- Runtime Stage ---
        FROM node:20-alpine
        
        WORKDIR /app
        
        # Copy built assets and package files
        COPY --from=builder /app/dist ./dist
        COPY --from=builder /app/package.json ./
        COPY --from=builder /app/node_modules ./node_modules
        
        # Expose Vite's default port
        EXPOSE 5173
        
        # Bind to 0.0.0.0 to ensure accessibility outside container
        CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0"]
    - Dockerfile.dev
        # [[RARO]]/apps/web-console/Dockerfile.dev
        # Purpose: Development Dockerfile with hot reload and Vite proxy support
        # Usage: docker-compose up (with updated docker-compose.yml)
        
        FROM node:20-alpine
        
        WORKDIR /app
        
        # Install dependencies
        COPY package.json package-lock.json* ./
        RUN npm install
        
        # Copy source code (or use volume mount for hot reload)
        COPY . .
        
        # Expose Vite dev server port
        EXPOSE 5173
        
        # Run Vite in dev mode with proxy support
        # --host 0.0.0.0 makes it accessible from outside container
        CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
    - index.html
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="UTF-8" />
            <link rel="icon" type="image/svg+xml" href="/vite.svg" />
            <meta name="viewport" content="width=device-width, initial-scale=1.0" />
            <title>RARO | Operator Console</title>
          </head>
          <body>
            <div id="app"></div>
            <script type="module" src="/src/main.ts"></script>
          </body>
        </html>
    - package.json
        {
          "name": "raro-web-console",
          "version": "0.1.0",
          "private": true,
          "type": "module",
          "scripts": {
            "dev": "vite",
            "build": "vite build",
            "preview": "vite preview --host --port 5173",
            "check": "svelte-check --tsconfig ./tsconfig.json"
          },
          "dependencies": {
            "@types/marked": "^6.0.0",
            "marked": "^17.0.1",
            "svelte": "^5.0.0",
            "svelte-routing": "^2.13.0"
          },
          "devDependencies": {
            "@sveltejs/vite-plugin-svelte": "^4.0.0",
            "@tsconfig/svelte": "^5.0.0",
            "@types/node": "^22.0.0",
            "svelte-check": "^4.0.0",
            "typescript": "^5.5.0",
            "vite": "^5.4.11",
            "d3": "^7.9.0"
          }
        }
    - svelte.config.js
        import { vitePreprocess } from '@sveltejs/vite-plugin-svelte'
        
        export default {
          preprocess: vitePreprocess(),
          compilerOptions: {
            runes: true,
          },
        }
    - tsconfig.json
        {
          "extends": "@tsconfig/svelte/tsconfig.json",
          "compilerOptions": {
            "target": "ES2020",
            "useDefineForClassFields": true,
            "lib": ["ES2020", "DOM", "DOM.Iterable"],
            "module": "ESNext",
            "types": ["vite/client", "node"], 
            "skipLibCheck": true,
            "esModuleInterop": true,
            "allowSyntheticDefaultImports": true,
            "strict": true,
            "forceConsistentCasingInFileNames": true,
            "resolveJsonModule": true,
            "moduleResolution": "bundler",
            "paths": {
              "$lib/*": ["./src/lib/*"],
              "$components/*": ["./src/components/*"]
            }
          },
          "include": ["src/**/*.ts", "src/**/*.svelte"],
          "exclude": ["node_modules", "dist"]
        }
    - vite.config.ts
        // [[RARO]]/apps/web-console/vite.config.ts
        import { defineConfig } from 'vite'
        import { svelte } from '@sveltejs/vite-plugin-svelte'
        import path from 'path'
        
        export default defineConfig({
          plugins: [svelte()],
          resolve: {
            alias: {
              $lib: path.resolve(__dirname, './src/lib'),
              $components: path.resolve(__dirname, './src/components'),
            },
          },
          server: {
            port: 5173,
            host: '0.0.0.0', // Allow access from outside container
            proxy: {
              // Proxy /api to Rust Kernel (HTTP)
              // Use 'kernel' (Docker service name) when running in Docker
              // Use 'localhost' when running locally
              '/api': {
                target: process.env.DOCKER_ENV === 'true' ? 'http://kernel:3000' : 'http://localhost:3000',
                changeOrigin: true,
                rewrite: (path) => path.replace(/^\/api/, ''),
              },
        
              // Proxy /ws to Rust Kernel (WebSocket) - CRITICAL FOR REAL-TIME UPDATES
              '/ws': {
                target: process.env.DOCKER_ENV === 'true' ? 'ws://kernel:3000' : 'ws://localhost:3000',
                ws: true,  // Enable WebSocket proxying
                changeOrigin: true,
              },
        
              // Proxy /agent-api to Python Agent Service
              '/agent-api': {
                target: process.env.DOCKER_ENV === 'true' ? 'http://agents:8000' : 'http://localhost:8000',
                changeOrigin: true,
                rewrite: (path) => path.replace(/^\/agent-api/, ''),
              },
            },
          },
        })
- hackathon-ii/
  - docker-compose.yml
      # docker-compose.yml
      # Purpose: Define services and shared volumes for the RARO system.
      # Architecture: Infrastructure Layer.
      # Dependencies: Docker.
      
      services:
        # 1. THE BRAIN: Rust Kernel Server
        # Orchestrates the DAG and manages state.
        kernel:
          build:
            context: ./apps/kernel-server
            dockerfile: Dockerfile
          container_name: raro-kernel
          ports:
            - "3000:3000"
          environment:
            - RUST_LOG=raro_kernel=debug,tower_http=trace
            - KERNEL_PORT=3000
            
            # === ROUTING CONFIGURATION ===
            # Point back to real agents (Probe intercepts at the Agent level now)
            - AGENT_HOST=agents
            - AGENT_PORT=8000
            
            - REDIS_URL=redis://redis:6379
          volumes:
            - ./storage:/app/storage # <--- SHARED MOUNT FOR RFS
          networks:
            - raro-net
          healthcheck:
            test: ["CMD", "curl", "-f", "http://localhost:3000/health"]
            interval: 5s
            timeout: 3s
            retries: 3
          depends_on:
            agents:
              condition: service_started
            redis:
              condition: service_healthy
      
        # 2. THE MUSCLE: Python Agent Service
        # Executes Gemini 3 inference and handles heavy lifting.
        agents:
          build:
            context: ./apps/agent-service
            dockerfile: Dockerfile
          container_name: raro-agents
          ports:
            - "8000:8000" # Exposed for direct debugging if needed
          environment:
            - PYTHONUNBUFFERED=1
            # API Key must be set in your local .env file
            - GEMINI_API_KEY=${GEMINI_API_KEY}
            - E2B_API_KEY=${E2B_API_KEY}
            - TAVILY_API_KEY=${TAVILY_API_KEY}
            - REDIS_URL=redis://redis:6379
            - LOG_LEVEL=${LOG_LEVEL:-DEBUG}  # Default to DEBUG, override with env var
            # ACTIVATE DEBUG INTERCEPTION
            - DEBUG_PROBE_URL=http://debug-probe:8080
          volumes:
            - ./storage:/app/storage # <--- SHARED MOUNT FOR RFS
          networks:
            - raro-net
          healthcheck:
            test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
            interval: 10s
            timeout: 5s
            retries: 3
          depends_on:
            debug-probe:
              condition: service_started
            redis:
              condition: service_healthy
      
        # 3. THE INSPECTOR: Debug Probe
        # Passive receiver that visualizes the exact prompts sent from Agent Service.
        debug-probe:
          build:
            context: ./apps/debug-probe
            dockerfile: Dockerfile
          container_name: raro-debug-probe
          ports:
            - "8081:8080" # UI Dashboard accessible at localhost:8081
          networks:
            - raro-net
      
        # 4. THE FACE: Svelte Web Console
        # Reactive UI for visualization and control.
        web:
          build:
            context: ./apps/web-console
            dockerfile: Dockerfile.dev  # Use dev Dockerfile with hot reload
          container_name: raro-web
          ports:
            - "5173:5173"
          environment:
            # - VITE_KERNEL_URL=http://kernel:3000
            # - VITE_AGENT_URL=http://agents:8000
            # FORCE DISABLE MOCK IN DOCKER
            - VITE_USE_MOCK_API=false 
            - DOCKER_ENV=true
          volumes:
            # Hot reload: mount source code
            - ./apps/web-console/src:/app/src
            - ./apps/web-console/vite.config.ts:/app/vite.config.ts
          depends_on:
            kernel:
              condition: service_healthy
          networks:
            - raro-net
      
        # Redis for caching (Optional)
        redis:
          image: redis:7-alpine
          container_name: raro-redis
          ports:
            - "6379:6379"
          networks:
            - raro-net
          healthcheck:
            test: ["CMD", "redis-cli", "ping"]
            interval: 10s
            timeout: 5s
            retries: 5
      
        # PostgreSQL for persistence (Optional)
        postgres:
          image: postgres:16-alpine
          container_name: raro-postgres
          ports:
            - "5432:5432"
          environment:
            - POSTGRES_DB=raro
            - POSTGRES_USER=raro
            - POSTGRES_PASSWORD=raro
          networks:
            - raro-net
          healthcheck:
            test: ["CMD-SHELL", "pg_isready -U raro"]
            interval: 10s
            timeout: 5s
            retries: 5
          volumes:
            - postgres_data:/var/lib/postgresql/data
      
      networks:
        raro-net:
          driver: bridge
      
      volumes:
        postgres_data:
