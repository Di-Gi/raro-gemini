# Project Structure with Import Analysis

- apps/
  - agent-service/
    - archive/
      - active/
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
                REDIS_URL: str = "redis://localhost:6379"
                LOG_LEVEL: str = "INFO"
                
                # === MODEL AUTHORITY ===
                # Change specific versions here to propagate across the entire system.
                MODEL_FAST: str = "gemini-flash-latest"
                MODEL_REASONING: str = "gemini-flash-latest"
                MODEL_THINKING: str = "gemini-flash-latest"
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
            from pathlib import Path
            from datetime import datetime
            from google.genai import types
            from core.config import gemini_client, logger, resolve_model
            
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
                concrete_model = resolve_model(model)
                logger.debug(f"Resolved model alias '{model}' to '{concrete_model}'")
            
                try:
                    # Prepare initial request
                    params = await _prepare_gemini_request(
                        concrete_model, prompt, input_data, file_paths, # <--- Pass concrete_model here
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
                concrete_model = resolve_model(model)
                # Reuse helper to setup context
                params = await _prepare_gemini_request(
                    concrete_model, prompt, input_data, file_paths, # <--- Pass concrete_model here
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
                run_id: str
                agent_id: str
                model: str
                prompt: str
                input_data: Dict[str, Any]
                tools: List[str] = []
                thought_signature: Optional[str] = None
                parent_signature: Optional[str] = None
                cached_content_id: Optional[str] = None
                thinking_level: Optional[int] = None
                file_paths: List[str] = []
            
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
            # Notes: Role and Strategy are now Enums, providing better error messages during parsing.
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
            from domain.protocol import WorkflowManifest, DelegationRequest, PatternDefinition
            
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
        - tools.py
            # [[RARO]]/apps/agent-service/src/intelligence/tools.py
            # Purpose: Tool definitions for Gemini Function Calling
            # Architecture: Intelligence Layer providing bridge between LLM and system actions.
            # Dependencies: google-genai
            
            from google.genai import types
            from typing import List, Dict, Any, Optional
            
            def get_tool_declarations(tool_names: List[str]) -> List[types.FunctionDeclaration]:
                """
                Maps logical tool names to Google GenAI FunctionDeclaration objects.
                """
                tool_registry = {
                    'web_search': types.FunctionDeclaration(
                        name='web_search',
                        description='Search the web for up-to-date information, news, or technical documentation.',
                        parameters=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                'query': types.Schema(
                                    type=types.Type.STRING,
                                    description='The search query string'
                                ),
                                'num_results': types.Schema(
                                    type=types.Type.INTEGER,
                                    description='Number of results to return (default 5)'
                                )
                            },
                            required=['query']
                        )
                    ),
            
                    'execute_python': types.FunctionDeclaration(
                        name='execute_python',
                        description='Execute Python code for data analysis, math, or visualization.',
                        parameters=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                'code': types.Schema(
                                    type=types.Type.STRING,
                                    description='Full Python code block to execute'
                                ),
                            },
                            required=['code']
                        )
                    ),
            
                    'read_file': types.FunctionDeclaration(
                        name='read_file',
                        description='Read text content from the local filesystem workspace.',
                        parameters=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                'path': types.Schema(
                                    type=types.Type.STRING,
                                    description='Path relative to workspace root'
                                ),
                            },
                            required=['path']
                        )
                    ),
            
                    'write_file': types.FunctionDeclaration(
                        name='write_file',
                        description='Write or overwrite content to a file in the workspace.',
                        parameters=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                'path': types.Schema(
                                    type=types.Type.STRING,
                                    description='Destination path'
                                ),
                                'content': types.Schema(
                                    type=types.Type.STRING,
                                    description='Text content to write'
                                ),
                            },
                            required=['path', 'content']
                        )
                    ),
                }
            
                return [tool_registry[name] for name in tool_names if name in tool_registry]
            
            def execute_tool_call(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
                """
                Dispatcher for executing the logic associated with a function call.
                Currently implements mock responses for Phase 2.
                """
                try:
                    if tool_name == 'web_search':
                        query = args.get('query', 'unspecified')
                        return {
                            'success': True,
                            'result': f"Found relevant information for '{query}': [Mocked content from search engine]."
                        }
            
                    elif tool_name == 'execute_python':
                        return {
                            'success': True,
                            'result': "Code executed successfully. Output: [Calculated value or plot confirmation]."
                        }
            
                    elif tool_name == 'read_file':
                        return {
                            'success': True,
                            'result': f"Content of {args.get('path')}: [Mocked file contents]."
                        }
            
                    elif tool_name == 'write_file':
                        return {
                            'success': True,
                            'result': f"File successfully written to {args.get('path')}."
                        }
            
                    return {
                        'success': False,
                        'error': f"Unknown tool: {tool_name}"
                    }
            
                except Exception as e:
                    return {
                        'success': False,
                        'error': str(e)
                    }
            
            # Integration: Used by src/core/llm.py to handle the function call loop.
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
          
          import json
          import time
          import re
          import asyncio
          from typing import Dict, Any, List
          from datetime import datetime
          from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
          from fastapi.responses import JSONResponse
          from fastapi.middleware.cors import CORSMiddleware
          from pydantic import ValidationError
          
          from core.config import settings, gemini_client, redis_client, logger
          from core.llm import call_gemini_with_context
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
                          "id": "researcher",
                          "role": "worker",
                          "model": settings.MODEL_FAST,
                          "description": "Deep research and fact-finding",
                          "tools": ["search_papers", "extract_citations", "build_knowledge_graph"]
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
                      input_data=request.input_data,
                      file_paths=request.file_paths,
                      parent_signature=request.parent_signature,
                      thinking_level=request.thinking_level,
                      tools=request.tools,
                      agent_id=request.agent_id
                  )
          
                  response_text = result["text"]
          
                  # 3. Parse Delegation Request (Flow B)
                  # ROBUST PARSING STRATEGY:
                  # We look specifically for the ```json:delegation block defined in the prompt.
                  # This prevents accidental parsing of standard ```json code blocks.
                  
                  delegation_request = None
                  
                  # Regex to capture content inside ```json:delegation ... ``` 
                  # flags=re.DOTALL allows matching across newlines
                  delegation_pattern = r"```json:delegation\s*(\{[\s\S]*?\})\s*```"
                  
                  match = re.search(delegation_pattern, response_text, re.IGNORECASE)
          
                  if match:
                      try:
                          json_str = match.group(1)
                          data = json.loads(json_str)
          
                          # Validate against schema
                          # Pydantic validation handles extra fields gracefully if configured, 
                          # but we explicitly check structure here via the model.
                          delegation_request = DelegationRequest(**data)
                          
                          logger.info(
                              f"Delegation signal received via explicit tag: {len(delegation_request.new_nodes)} nodes. "
                              f"Reason: {delegation_request.reason[:50]}..."
                          )
                      except json.JSONDecodeError as e:
                          logger.warning(f"Delegation block found but Invalid JSON: {e}")
                      except Exception as e:
                          logger.warning(f"Failed to parse delegation request model: {e}")
                  else:
                      logger.debug("No explicit delegation tag found in response.")
          
                  # 4. Store Artifact to Redis (if available)
                  artifact_stored = False
                  if redis_client and response_text:
                      try:
                          key = f"run:{request.run_id}:agent:{request.agent_id}:output"
                          artifact_data = {
                              "result": response_text,
                              "status": "completed",
                              "thinking_depth": request.thinking_level or 0,
                              "model": request.model
                          }
                          redis_client.setex(key, 3600, json.dumps(artifact_data))
                          artifact_stored = True
                          logger.debug(f"Artifact stored to Redis: {key}")
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
                  "features": ["multimodal", "dynamic-dag", "safety-compiler"],
                  "parsing_strategy": "explicit-tag (json:delegation)"
              }
          
          if __name__ == "__main__":
              import uvicorn
              uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
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
  - kernel-server/
    - src/
      - archive/
      - server/
        - handlers.rs
            // [[RARO]]/apps/kernel-server/src/server/handlers.rs
            // Purpose: API Handlers. updated to allow async spawning of workflows.
            // Architecture: API Layer
            // Dependencies: Axum, Runtime
            
            use axum::{
                extract::{Path, State, Json, Query, ws::{WebSocket, WebSocketUpgrade}},
                http::StatusCode,
                response::IntoResponse,
            };
            use serde_json::json;
            use std::sync::Arc;
            use futures::{sink::SinkExt, stream::StreamExt};
            use axum::extract::ws::Message;
            use redis::AsyncCommands;
            
            use crate::models::*;
            use crate::runtime::{RARORuntime, InvocationPayload};
            
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
                    }
                }
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
          
          use axum::{
              Router,
              routing::{get, post},
              http::Method,
          };
          use std::sync::Arc;
          use tower_http::cors::{CorsLayer, Any};
          use tracing_subscriber;
          
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
          
              // Configure CORS
              let cors = CorsLayer::new()
                  .allow_origin(Any)
                  .allow_methods([Method::GET, Method::POST])
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
          // Purpose: Core data models. Updated to support Dynamic Delegation and Graph Mutation.
          // Architecture: Shared Data Layer
          // Dependencies: Serde
          
          use serde::{Deserialize, Serialize};
          use std::collections::HashMap;
          
          // #[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
          // pub enum ModelVariant {
          //     #[serde(rename = "gemini-2.5-flash")]
          //     GeminiFlash,
          //     #[serde(rename = "gemini-2.5-flash-lite")]
          //     GeminiPro,
          //     #[serde(rename = "gemini-3.0-flash")]
          //     GeminiDeepThink,
          // }
          
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
          use crate::models::AgentNodeConfig;
          
          #[derive(Debug, Clone, Serialize, Deserialize)]
          pub struct Pattern {
              pub id: String,
              pub name: String,
              /// The event type that wakes this pattern up (matched against EventType debug string)
              pub trigger_event: String, 
              /// JSONPath-like filter string (e.g., "$.payload.tool == 'fs_delete'")
              pub condition: String,
              pub action: PatternAction,
          }
          
          #[derive(Debug, Clone, Serialize, Deserialize)]
          pub enum PatternAction {
              /// Stop the agent immediately
              Interrupt { reason: String },
              /// Pause and ask for human approval
              RequestApproval { reason: String },
              /// Spawn a "fixer" agent to handle the error (Flow C / Self-Healing)
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
                  
                  // Initialize Default Safety Patterns (Proof of Concept)
                  registry.register_default_patterns();
                  
                  registry
              }
          
              pub fn register(&self, pattern: Pattern) {
                  self.patterns.insert(pattern.id.clone(), pattern);
              }
          
              /// Retrieve all patterns listening for a specific event type
              pub fn get_patterns_for_trigger(&self, event_type: &str) -> Vec<Pattern> {
                  self.patterns
                      .iter()
                      .filter(|p| p.trigger_event == event_type)
                      .map(|p| p.value().clone())
                      .collect()
              }
          
              fn register_default_patterns(&self) {
                  // 1. Safety Guard: Prevent file deletion
                  // This corresponds to Atlas 'prevent_destructive_shell'
                  self.register(Pattern {
                      id: "guard_fs_delete".to_string(),
                      name: "Prevent File Deletion".to_string(),
                      trigger_event: "ToolCall".to_string(),
                      condition: "fs_delete".to_string(), // Simplified matching for Phase 3 MVP
                      action: PatternAction::Interrupt { 
                          reason: "Safety Violation: File deletion is prohibited by system policy.".to_string() 
                      },
                  });
          
                  // 2. Infinite Loop Detector (Heuristic)
                  // If an agent fails 3 times, stop the run.
                  // Note: Real implementation requires stateful counting, this is a stateless example.
                  self.register(Pattern {
                      id: "guard_max_failures".to_string(),
                      name: "Max Failure Guard".to_string(),
                      trigger_event: "AgentFailed".to_string(),
                      condition: "*".to_string(), 
                      action: PatternAction::RequestApproval { 
                          reason: "Agent failed. Requesting human intervention before retry.".to_string() 
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
          
          /// Payload for invoking an agent with signature routing and caching
          #[derive(Debug, Clone, Serialize, Deserialize)]
          pub struct InvocationPayload {
              pub run_id: String,
              pub agent_id: String,
              pub model: String,
              pub prompt: String,
              pub input_data: serde_json::Value,
              pub parent_signature: Option<String>,
              pub cached_content_id: Option<String>,
              pub thinking_level: Option<i32>,
              pub file_paths: Vec<String>,
              pub tools: Vec<String>,
          }
          
          // #[derive(Debug, Clone, Serialize, Deserialize)]
          // pub struct RemoteAgentResponse {
          //     pub agent_id: String,
          //     pub success: bool,
          //     pub output: Option<serde_json::Value>,
          //     pub error: Option<String>,
          //     pub tokens_used: usize,
          //     pub thought_signature: Option<String>,
          //     pub input_tokens: usize,
          //     pub output_tokens: usize,
          //     pub cache_hit: bool,
          //     pub latency_ms: f64,
          // }
          // moved to models
          
          
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
                      http_client: reqwest::Client::new(),
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
          
                                      // Splice the graph
                                      match self.handle_delegation(&run_id, &agent_id, delegation).await {
                                          Ok(_) => {
                                              // Delegation successful.
                                              // Mark current agent as complete (it successfully delegated).
                                              // The loop will pick up the new nodes next.
                                              tracing::info!("Delegation processed. Graph updated.");
                                          }
                                          Err(e) => {
                                              tracing::error!("Delegation failed: {}", e);
                                              self.fail_run(&run_id, &agent_id, &format!("Delegation error: {}", e)).await;
                                              continue;
                                          }
                                      }
                                  }
          
                                  // B. Standard Completion Logic
                                  if let Some(sig) = res.thought_signature {
                                      let _ = self.set_thought_signature(&run_id, &agent_id, sig);
                                  }
          
                                  // Store Artifact
                                  let artifact_id = if let Some(output_data) = &res.output {
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
                          }
                      }
                  }
              }
          
              /// Handles the "Graph Surgery" when an agent requests delegation
              async fn handle_delegation(&self, run_id: &str, parent_id: &str, req: DelegationRequest) -> Result<(), String> {
                  // 1. Get lock on Workflow Config (to register new agents)
                  // 2. Get lock on DAG (to rewire edges)
          
                  let state = self.runtime_states.get(run_id).ok_or("Run not found")?;
                  let workflow_id = state.workflow_id.clone();
                  drop(state); // Drop read lock
          
                  // Mutate Workflow Config
                  // We need to add the new agent configs so `prepare_invocation_payload` can find them later
                  if let Some(mut workflow) = self.workflows.get_mut(&workflow_id) {
                      for node in &req.new_nodes {
                          // Ensure unique IDs if not provided? Assuming agent provides unique IDs or we should prefix them.
                          // For safety, let's prefix if they don't look unique, but trust agent for now.
                          workflow.agents.push(node.clone());
                      }
                  } else {
                      return Err("Workflow config not found".to_string());
                  }
          
                  // Mutate DAG
                  if let Some(mut dag) = self.dag_store.get_mut(run_id) {
                      // A. Get existing children of the Parent (Dependents)
                      // e.g. Parent -> Child1. We want Parent -> [NewNodes] -> Child1
                      let existing_dependents = dag.get_children(parent_id);
          
                      // B. Add New Nodes & Edges from Parent
                      for node in &req.new_nodes {
                          dag.add_node(node.id.clone()).map_err(|e| e.to_string())?;
          
                          // Parent -> New Node (so New Node can see Parent's context)
                          dag.add_edge(parent_id.to_string(), node.id.clone()).map_err(|e| e.to_string())?;
          
                          // C. Rewire Dependents
                          // If strategy is Child (default), new nodes block the original dependents.
                          if req.strategy == DelegationStrategy::Child {
                              for dep in &existing_dependents {
                                  // Add edge New Node -> Dependent
                                  dag.add_edge(node.id.clone(), dep.clone()).map_err(|e| e.to_string())?;
                              }
                          }
                      }
          
                      // D. Remove Old Edges (Parent -> Dependents)
                      // Only if we successfully inserted the intermediaries.
                      if req.strategy == DelegationStrategy::Child {
                          for dep in &existing_dependents {
                              // It's okay if this fails (edge might not exist), but logic says it should.
                              let _ = dag.remove_edge(parent_id, dep);
                          }
                      }
          
                      // Validate Cycle (Rollback is hard, so we just check and error if bad)
                      if dag.topological_sort().is_err() {
                          // If we broke the graph, we are in trouble.
                          // In production, we'd clone DAG, test mutation, then apply.
                          // For prototype, we fail the run.
                          return Err("Delegation created a cycle".to_string());
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
          
              /// Prepare invocation payload with signature routing AND artifact context
              pub async fn prepare_invocation_payload(
                  &self,
                  run_id: &str,
                  agent_id: &str,
              ) -> Result<InvocationPayload, String> {
                  // Get the workflow and agent config
                  let state = self
                      .runtime_states
                      .get(run_id)
                      .ok_or_else(|| "Run not found".to_string())?;
          
                  let workflow = self
                      .workflows
                      .get(&state.workflow_id)
                      .ok_or_else(|| "Workflow not found".to_string())?;
          
                  // Find the agent config
                  let agent_config = workflow
                      .agents
                      .iter()
                      .find(|a| a.id == agent_id)
                      .ok_or_else(|| format!("Agent {} not found", agent_id))?;
          
                  // 1. Fetch Parent Signatures (Reasoning Continuity)
                  let parent_signature = if !agent_config.depends_on.is_empty() {
                      agent_config
                          .depends_on
                          .iter()
                          .find_map(|parent_id| self.get_thought_signature(run_id, parent_id))
                  } else {
                      None
                  };
          
                  // 2. Fetch Parent Artifacts (Data Context)
                  let mut context_prompt_appendix = String::new();
                  let mut input_data_map = serde_json::Map::new();
          
                  if !agent_config.depends_on.is_empty() {
                      if let Some(client) = &self.redis_client {
                          // We use a separate connection for this fetch to avoid borrowing issues
                          match client.get_async_connection().await {
                              Ok(mut con) => {
                                  for parent_id in &agent_config.depends_on {
                                      let key = format!("run:{}:agent:{}:output", run_id, parent_id);
                                      
                                      // Try to get artifact from Redis
                                      let data: Option<String> = con.get(&key).await.unwrap_or(None);
          
                                      if let Some(json_str) = data {
                                          if let Ok(val) = serde_json::from_str::<serde_json::Value>(&json_str) {
                                              
                                              // Add to structured input data
                                              input_data_map.insert(parent_id.clone(), val.clone());
          
                                              // Extract text result for the prompt
                                              let content = val.get("result")
                                                  .and_then(|v| v.as_str())
                                                  .or_else(|| val.get("output").and_then(|v| v.as_str()))
                                                  .unwrap_or("No text output");
          
                                              context_prompt_appendix.push_str(&format!("\n\n=== CONTEXT FROM AGENT {} ===\n{}\n", parent_id, content));
                                          }
                                      }
                                  }
                              },
                              Err(e) => tracing::warn!("Could not connect to Redis for context fetching: {}", e),
                          }
                      }
                  }
          
                  // 3. Construct Final Prompt
                  let mut final_prompt = agent_config.prompt.clone();
                  if !context_prompt_appendix.is_empty() {
                      final_prompt.push_str(&context_prompt_appendix);
                  }
          
                  // Get cached content if available
                  let cached_content_id = self.cache_resources.get(run_id).map(|c| (*c).clone());
          
                  // Determine model variant
                  // let model = match agent_config.model {
                  //     ModelVariant::GeminiFlash => "gemini-2.5-flash",
                  //     ModelVariant::GeminiPro => "gemini-2.5-flash-lite",
                  //     ModelVariant::GeminiDeepThink => "gemini-2.5-flash",
                  // }
                  // .to_string();
          
                  // let thinking_level = if matches!(agent_config.model, ModelVariant::GeminiDeepThink) {
                  //     Some(5)
                  // } else {
                  //     None
                  // };
                  let model_string = match &agent_config.model {
                      ModelVariant::Fast => "fast".to_string(),
                      ModelVariant::Reasoning => "reasoning".to_string(),
                      ModelVariant::Thinking => "thinking".to_string(),
                      ModelVariant::Custom(s) => s.clone(),
                  };
          
                  // 2. Logic is now based on Semantic Type, not string matching!
                  let thinking_level = if matches!(agent_config.model, ModelVariant::Thinking) {
                      Some(5) // Default budget for Thinking tier
                  } else {
                      None
                  };
          
          
          
                  Ok(InvocationPayload {
                      run_id: run_id.to_string(),
                      agent_id: agent_id.to_string(),
                      model: model_string,
                      prompt: final_prompt, // Updated with context
                      input_data: serde_json::Value::Object(input_data_map), // Updated with structured data
                      parent_signature,
                      cached_content_id,
                      thinking_level,
                      file_paths: Vec::new(),
                      tools: agent_config.tools.clone(),
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
    - Cargo.toml
        [package]
        name = "raro-kernel"
        version = "0.1.0"
        edition = "2021"
        authors = ["RARO Team"]
        license = "MIT"
        
        [dependencies]
        tokio = { version = "1.35", features = ["full"] }
        axum = { version = "0.7", features = ["ws"] }
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
    - patches/
      - active/
      - archive/
    - src/
      - components/
        - sub/
          - ApprovalCard.svelte
              <!-- [[RARO]]/apps/web-console/src/components/sub/ApprovalCard.svelte -->
              <script lang="ts">
                import { resumeRun, stopRun, runtimeStore } from '$lib/stores';
                import { fade } from 'svelte/transition';
              
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
              
              <div class="approval-card {decision ? decision.toLowerCase() : 'pending'}" transition:fade>
                <div class="header">
                    <span class="icon">⚠</span>
                    <span>INTERVENTION REQUIRED</span>
                </div>
              
                <div class="content">
                    <div class="reason-label">REASONING:</div>
                    <div class="reason-text">"{reason}"</div>
                </div>
              
                <div class="actions">
                    {#if decision === 'APPROVED'}
                        <div class="stamp success">AUTHORIZED</div>
                    {:else if decision === 'DENIED'}
                        <div class="stamp fail">TERMINATED</div>
                    {:else}
                        <button class="btn deny" onclick={handleDeny} disabled={processing}>
                            STOP RUN
                        </button>
                        <button class="btn approve" onclick={handleApprove} disabled={processing}>
                            {#if processing}PROCESSING...{:else}AUTHORIZE RESUME{/if}
                        </button>
                    {/if}
                </div>
              </div>
              
              <style>
                .approval-card {
                    margin: 16px 0;
                    border: 1px solid var(--alert-amber);
                    background: color-mix(in srgb, var(--paper-bg), var(--alert-amber) 5%);
                    font-family: var(--font-code);
                    border-radius: 2px;
                    overflow: hidden;
                }
              
                .approval-card.approved { border-color: var(--signal-success); opacity: 0.7; }
                .approval-card.denied { border-color: #d32f2f; opacity: 0.7; }
              
                .header {
                    background: var(--alert-amber);
                    color: #000;
                    padding: 6px 12px;
                    font-weight: 700;
                    font-size: 10px;
                    letter-spacing: 1px;
                    display: flex; gap: 8px; align-items: center;
                }
                
                .approved .header { background: var(--signal-success); color: white; }
                .denied .header { background: #d32f2f; color: white; }
              
                .content { padding: 16px; border-bottom: 1px solid var(--paper-line); }
                .reason-label { font-size: 8px; color: var(--paper-line); font-weight: 700; margin-bottom: 4px; }
                .reason-text { font-size: 13px; color: var(--paper-ink); font-style: italic; }
              
                .actions { padding: 12px; display: flex; justify-content: flex-end; gap: 12px; height: 50px; align-items: center; }
              
                .btn {
                    border: none; padding: 8px 16px; font-family: var(--font-code);
                    font-size: 10px; font-weight: 700; cursor: pointer; border-radius: 2px;
                }
              
                .btn.deny { background: transparent; border: 1px solid var(--paper-line); color: var(--paper-ink); }
                .btn.deny:hover { background: #d32f2f; color: white; border-color: #d32f2f; }
              
                .btn.approve { background: var(--paper-ink); color: var(--paper-bg); }
                .btn.approve:hover { opacity: 0.9; }
              
                .stamp { font-weight: 900; letter-spacing: 2px; font-size: 12px; }
                .stamp.success { color: var(--signal-success); }
                .stamp.fail { color: #d32f2f; }
              </style>
          - CodeBlock.svelte
              <!-- // [[RARO]]/apps/web-console/src/components/sub/CodeBlock.svelte -->
              
              <script lang="ts">
                import { fade } from 'svelte/transition';
                import { highlight } from '$lib/syntax-lite';
                
                let { 
                  code, 
                  language, 
                  activeCursor = false // NEW: specific prop to show cursor inside block
                }: { 
                  code: string, 
                  language: string, 
                  activeCursor?: boolean 
                } = $props();
              
                let copied = $state(false);
                let timeout: any;
              
                // Highlight logic
                let highlightedCode = $derived(highlight(code, language));
              
                function copyToClipboard() {
                  navigator.clipboard.writeText(code);
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
                /* ... Keep existing styles ... */
                
                .code-chassis {
                  margin: 16px 0;
                  border: 1px solid var(--paper-line);
                  background: color-mix(in srgb, var(--paper-bg), var(--paper-ink) 3%);
                  border-radius: 2px;
                  overflow: hidden;
                  font-family: var(--font-code);
                  transition: border-color 0.3s;
                  /* Ensure it breaks out of inline contexts */
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
              
                /* Token styles from previous step... */
                :global(.token-kw) { color: var(--arctic-cyan); font-weight: 700; }
                :global(.mode-archival .token-kw) { color: #005cc5; }
                :global(.token-str) { color: #a5d6ff; }
                :global(.mode-archival .token-str) { color: #032f62; }
                :global(.token-comment) { color: var(--paper-line); font-style: italic; }
                :global(.token-num), :global(.token-bool) { color: var(--alert-amber); }
                :global(.mode-archival .token-num), :global(.mode-archival .token-bool) { color: #d73a49; }
              
                /* Cursor specific to code block */
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
                import DelegationCard from './DelegationCard.svelte'; // Import new component
                import { parseMarkdown } from '$lib/markdown';
              
                let { text }: { text: string } = $props();
              
                function parseContent(input: string) {
                  // FIX: Regex now accepts colons, hyphens, and underscores in the lang tag
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
                  gap: 8px; /* Breathing room between code blocks and text */
                }
              
                /* === MARKDOWN TYPOGRAPHY SYSTEM === */
                /* This maps standard HTML tags to your Aesthetic Variables */
              
                :global(.markdown-body) {
                  font-size: 13px;
                  line-height: 1.6;
                  color: var(--paper-ink);
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
                  color: var(--paper-line); /* Subtle bullets */
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
                  color: var(--arctic-cyan); /* Matches your code theme */
                }
                
                /* Paper Mode Override for inline code */
                :global(.mode-archival .markdown-body code) {
                  color: #e36209; /* Visible orange/red for paper mode */
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
                  color: var(--paper-line); /* Dimmed text */
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
          - Typewriter.svelte
              <!-- // [[RARO]]/apps/web-console/src/components/sub/Typewriter.svelte -->
              
              <script lang="ts">
                import Spinner from './Spinner.svelte';
                import CodeBlock from './CodeBlock.svelte';
                import DelegationCard from './DelegationCard.svelte'; // Render immediately
              
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
              </script>
              
              <div class="typewriter-container">
                
                <div class="stream-content">
                  {#each segments as segment, i}
                    {#if segment.type === 'code'}
                      <!-- ROUTER -->
                      {#if segment.lang === 'json:delegation'}
                          <!-- IMMEDIATE RENDER WITH LOADING STATE -->
                          <DelegationCard 
                              rawJson={segment.content} 
                              loading={segment.isOpen} 
                          />
                      {:else}
                          <!-- STANDARD CODE BLOCK -->
                          <CodeBlock 
                              code={segment.content} 
                              language={segment.lang || 'text'} 
                              activeCursor={isTyping && i === segments.length - 1} 
                          />
                      {/if}
                    {:else}
                      <span class="text-body">{@html segment.content}</span>
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
                  word-break: break-word;
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
        - ControlDeck.svelte
            <!-- // [[RARO]]/apps/web-console/src/components/ControlDeck.svelte
            // Purpose: Main interaction panel. Orchestrates the API call to start the run.
            // Architecture: View Controller
            // Dependencies: Stores, API -->
            
            <script lang="ts">
              import { selectedNode, agentNodes, pipelineEdges, addLog, updateNodeStatus,
                deselectNode, telemetry, connectRuntimeWebSocket, runtimeStore, resumeRun, stopRun,
                planningMode,           // Import new store
                loadWorkflowManifest    // Import new action
              } from '$lib/stores'
              import { 
                startRun, 
                generateWorkflowPlan, // Import API call
                type WorkflowConfig, 
                type AgentConfig 
              } from '$lib/api'
              import { get } from 'svelte/store'
              import { fade } from 'svelte/transition'
            
              let { expanded }: { expanded: boolean } = $props();
            
              let cmdInput = $state('')
              let activePane = $state('input') // 'input' | 'overview' | 'sim' | 'stats' | 'node-config'
              let currentModel = $state('fast')
              let currentPrompt = $state('')
              let thinkingBudget = $state(5)
              let isSubmitting = $state(false)
              let isInputFocused = $state(false)
            
              // Reactive derivation for HITL state
              let isAwaitingApproval = $derived($runtimeStore.status === 'AWAITING_APPROVAL' || $runtimeStore.status === 'PAUSED')
            
              // === STATE SYNCHRONIZATION ===
              $effect(() => {
                if ($selectedNode && expanded) {
                  // 1. Node selected -> FORCE view to Config
                  // Load node specific data
                  const node = $agentNodes.find((n) => n.id === $selectedNode)
                  if (node) {
                    currentModel = node.model
                    currentPrompt = node.prompt
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
                            prompt: n.prompt,
                            position: { x: n.x, y: n.y }
                        };
                    });
            
                    // Inject Runtime Command into Orchestrator if present
                    const orchestrator = agents.find(a => a.role === 'orchestrator');
                    if (orchestrator && cmdInput) {
                        orchestrator.prompt = `${orchestrator.prompt}\n\nRUNTIME COMMAND: ${cmdInput}`;
                    }
            
                    const config: WorkflowConfig = {
                        id: `flow-${Date.now()}`,
                        name: 'RARO_Session',
                        agents: agents,
                        max_token_budget: 100000,
                        timeout_ms: 60000
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
                          return { ...n, model: currentModel, prompt: currentPrompt }
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
            
              // HITL Handlers
              async function handleApprove() {
                  if (!$runtimeStore.runId) return;
                  addLog('OPERATOR', 'APPROVAL GRANTED. Resuming execution.', 'HITL');
                  await resumeRun($runtimeStore.runId);
              }
            
              async function handleDeny() {
                  if (!$runtimeStore.runId) return;
                  addLog('OPERATOR', 'APPROVAL DENIED. Terminating run.', 'HITL');
                  await stopRun($runtimeStore.runId);
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
                    <div class="nav-item {activePane === 'sim' ? 'active' : ''}" onclick={() => handlePaneSelect('sim')}>Simulation</div>
                    <div class="nav-item {activePane === 'stats' ? 'active' : ''}" onclick={() => handlePaneSelect('stats')}>Telemetry</div>
                  {/if}
                </div>
              {/if}
            
              <div class="pane-container">
            
                <!-- === INTERVENTION OVERLAY === -->
                {#if isAwaitingApproval}
                    <div class="intervention-overlay" transition:fade={{ duration: 200 }}>
                        <div class="intervention-card">
                            <div class="int-header">
                                <span class="blink-dot"></span>
                                SYSTEM INTERVENTION REQUIRED
                            </div>
                            <div class="int-body">
                                A Safety Pattern or Delegation Request has paused execution.
                                Please review the logs and authorize the next step.
                            </div>
                            <div class="int-actions">
                                <button class="btn-deny" onclick={handleDeny}>ABORT RUN</button>
                                <button class="btn-approve" onclick={handleApprove}>AUTHORIZE & RESUME</button>
                            </div>
                        </div>
                    </div>
                {/if}
            
                <!-- Normal Panes -->
                {#if !expanded || activePane === 'input'}
                  <!-- 1. INPUT CONSOLE -->
                  <div id="pane-input" class="deck-pane">
                    
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
                        <input class="input-std input-readonly" value={$selectedNode} readonly />
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
            
              /* === INTERVENTION STYLES === */
              .intervention-overlay {
                  position: absolute;
                  top: 0;
                  left: 0;
                  width: 100%;
                  height: 100%;
                  background: rgba(0,0,0,0.6);
                  backdrop-filter: blur(4px);
                  z-index: 200;
                  display: flex;
                  align-items: center;
                  justify-content: center;
              }
            
              .intervention-card {
                  background: var(--paper-bg);
                  border: 1px solid var(--alert-amber);
                  box-shadow: 0 10px 40px rgba(0,0,0,0.5);
                  width: 400px;
                  padding: 2px; /* Border padding */
              }
            
              .int-header {
                  background: var(--alert-amber);
                  color: #000;
                  padding: 8px 12px;
                  font-weight: 700;
                  font-size: 11px;
                  letter-spacing: 1px;
                  display: flex;
                  align-items: center;
                  gap: 8px;
              }
            
              .blink-dot {
                  width: 8px;
                  height: 8px;
                  background: #000;
                  border-radius: 50%;
                  animation: blink 0.5s infinite alternate;
              }
            
              .int-body {
                  padding: 20px;
                  font-size: 13px;
                  line-height: 1.5;
                  color: var(--paper-ink);
                  border-bottom: 1px solid var(--paper-line);
              }
            
              .int-actions {
                  display: grid;
                  grid-template-columns: 1fr 1fr;
              }
            
              .int-actions button {
                  border: none;
                  padding: 12px;
                  font-family: var(--font-code);
                  font-weight: 700;
                  font-size: 11px;
                  cursor: pointer;
                  transition: background 0.2s;
              }
            
              .btn-deny {
                  background: var(--paper-surface);
                  color: var(--paper-ink);
              }
            
              .btn-deny:hover {
                  background: #d32f2f;
                  color: white;
              }
            
              .btn-approve {
                  background: var(--paper-ink);
                  color: var(--paper-bg);
              }
            
              .btn-approve:hover {
                  opacity: 0.9;
              }
            
              @keyframes blink {
                  from { opacity: 1; }
                  to { opacity: 0.3; }
              }
            </style>
        - Hero.svelte
            <!-- // [[RARO]]/apps/web-console/src/components/Hero.svelte
            // Purpose: The "Monolith" Boot Interface. High-fidelity entry point.
            // Architecture: UX/UI Component
            // Dependencies: Svelte Transition, Local Assets -->
            
            <script lang="ts">
              import { fade, fly } from 'svelte/transition';
              import { onMount } from 'svelte';
            
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
                  { t: 1400, msg: ">> RARO_RUNTIME_ENGAGED" }
                ];
            
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
                      <div class="build-tag">BUILD_2026.01.02</div>
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
            <!-- // [[RARO]]/apps/web-console/src/components/OutputPane.svelte
            // Purpose: Log display with "Perforated Paper" styling and robust auto-scroll.
            // Architecture: UI View
            // Dependencies: Typewriter, Stores -->
            
            <script lang="ts">
              import { logs, updateLog, runtimeStore } from '$lib/stores'
              import Typewriter from './sub/Typewriter.svelte'
              import SmartText from './sub/SmartText.svelte'
              import ApprovalCard from './sub/ApprovalCard.svelte'
              import { tick } from 'svelte';
            
              // Refs
              let scrollContainer = $state<HTMLDivElement | null>(null);
              let contentWrapper = $state<HTMLDivElement | null>(null);
              
              // State
              let isPinnedToBottom = $state(true);
              let isAutoScrolling = false;
            
              // ... (Keep handleScroll, scrollToBottom, and Observers as they were) ...
            
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
                const _logs = $logs;
                tick().then(() => {
                  if (isPinnedToBottom) scrollToBottom('smooth');
                });
              });
            
              // Callback for Typewriter completion
              function handleTypewriterComplete(id: string) {
                // We update the store so this log entry is permanently marked as "done animating"
                // This triggers the switch to SmartText
                updateLog(id, { isAnimated: false });
              }
            </script>
            
            <div 
              id="output-pane" 
              bind:this={scrollContainer} 
              onscroll={handleScroll}
            >
              <div class="log-wrapper" bind:this={contentWrapper}>
                {#each $logs as log (log.id)}
                  <div class="log-entry">
                    <!-- Column 1: Metadata -->
                    <div class="log-meta">
                        <span class="meta-tag">{log.metadata || 'SYSTEM'}</span>
                    </div>
            
                    <!-- Column 2: Content -->
                    <div class="log-body">
                      <span class="log-role">{log.role}</span>
                      
                      <div class="log-content">
                        {#if log.metadata === 'INTERVENTION'}
                          <!-- RENDER APPROVAL CARD -->
                          <ApprovalCard
                            reason={log.message === 'SAFETY_PATTERN_TRIGGERED' ? "System Policy Violation or Manual Pause Triggered" : log.message}
                            runId={$runtimeStore.runId || ''}
                          />
                        {:else if log.isAnimated}
                          <!--
                             Typewriter View:
                             Pass the ID to handle completion.
                          -->
                          <Typewriter
                            text={log.message}
                            onComplete={() => handleTypewriterComplete(log.id)}
                          />
                        {:else}
                          <!--
                             Static / Complete View:
                             Uses SmartText to render code blocks beautifully.
                          -->
                          <SmartText text={log.message} />
                        {/if}
                      </div>
                    </div>
                  </div>
                {/each}
              </div>
            </div>
            
            <style>
              /* Error Block Styling - Global for HTML injection */
              :global(.error-block) {
                background: rgba(211, 47, 47, 0.05); /* Subtle red tint that works on light and dark */
                border-left: 3px solid #d32f2f;      /* Semantic Red - kept constant */
                color: var(--paper-ink);             /* Adaptive text color */
                padding: 10px;
                margin-top: 8px;
                font-family: var(--font-code);
                font-size: 11px;
                white-space: pre-wrap;
                word-break: break-all;
              }
            
              :global(.log-content strong) {
                color: var(--paper-ink);
                font-weight: 700;
              }
            
              #output-pane {
                flex: 1;
                padding: 24px;
                overflow-y: auto;
                display: flex;
                flex-direction: column;
                /* Important: Remove CSS scroll-behavior to allow JS to control 'auto' vs 'smooth' explicitly */
                scrollbar-gutter: stable;
                will-change: scroll-position;
              }
            
              .log-wrapper {
                display: flex;
                flex-direction: column;
                min-height: min-content;
              }
              
              .log-entry {
                /* "Perforated" divider style */
                border-top: 1px dashed var(--paper-line);
                padding: 16px 0;
                display: grid;
                grid-template-columns: 80px 1fr;
                gap: 16px;
                animation: slideUp 0.2s cubic-bezier(0.16, 1, 0.3, 1) forwards;
              }
            
              @keyframes slideUp {
                from { opacity: 0; transform: translateY(5px); }
                to { opacity: 1; transform: translateY(0); }
              }
            
              .log-meta {
                padding-top: 3px;
              }
            
              .meta-tag {
                font-family: var(--font-code);
                font-size: 9px;
                color: var(--paper-line); /* Replaced #888 */
                background: var(--paper-surface); /* Replaced #f5f5f5 */
                padding: 2px 6px;
                border-radius: 2px;
                display: inline-block;
                border: 1px solid transparent;
              }
              
              /* In dark mode, we might want a slight border to define the tag */
              :global(.mode-phosphor) .meta-tag {
                  border-color: var(--paper-line);
              }
            
              .log-body {
                display: flex;
                flex-direction: column;
              }
            
              .log-role {
                font-weight: 700;
                font-size: 11px;
                letter-spacing: 0.5px;
                color: var(--paper-ink);
                display: block;
                margin-bottom: 6px;
                text-transform: uppercase;
              }
            
              .log-content {
                font-size: 13px;
                line-height: 1.6;
                color: var(--paper-ink); /* Replaced #333 */
                opacity: 0.9;
              }
            </style>
        - PipelineStage.svelte
            <!-- // [[RARO]]/apps/web-console/src/components/PipelineStage.svelte
            // Purpose: Interactive DAG visualization with "Chip" aesthetic.
            // Architecture: Visual Component (D3-lite)
            // Dependencies: Stores -->
            
            <script lang="ts">
              import { 
                agentNodes, 
                pipelineEdges, 
                selectedNode, 
                selectNode, 
                deselectNode, // Added import
                runtimeStore, 
                planningMode,
                type PipelineEdge,
                type AgentNode
              } from '$lib/stores'
            
              let { expanded, ontoggle }: { expanded: boolean, ontoggle?: () => void } = $props();
            
              // Reactive state for DOM element bindings
              let svgElement = $state<SVGSVGElement | undefined>();
              let nodesLayer = $state<HTMLDivElement | undefined>();
              let pipelineStageElement = $state<HTMLDivElement | undefined>();
            
              let isRunComplete = $derived($runtimeStore.status === 'COMPLETED' || $runtimeStore.status === 'FAILED');
            
              // CLEANUP HOOK: Clear selection when minimizing
              $effect(() => {
                if (!expanded && $selectedNode) {
                    deselectNode();
                }
              });
            
              $effect(() => {
                if (!pipelineStageElement) return;
                const resizeObserver = new ResizeObserver(() => {
                  renderGraph();
                });
                resizeObserver.observe(pipelineStageElement);
                return () => {
                  resizeObserver.disconnect();
                };
              })
            
              function renderGraph() {
                if (!svgElement || !nodesLayer || !pipelineStageElement) return
            
                const svg = svgElement
                const w = pipelineStageElement.clientWidth
                const h = pipelineStageElement.clientHeight
            
                // === 1. CALCULATE MINIMIZED POSITIONS (CLUSTERING) ===
                // We calculate this regardless of state to ensure smooth transitions
                
                // Group nodes by their approximate X coordinate (Rank)
                const clusters = new Map<number, AgentNode[]>();
                const sortedNodes = [...$agentNodes].sort((a, b) => {
                    if (Math.abs(a.x - b.x) > 2) return a.x - b.x;
                    return a.id.localeCompare(b.id);
                });
            
                sortedNodes.forEach(node => {
                    const rankKey = Math.round(node.x / 5) * 5; // Quantize X to nearest 5%
                    if (!clusters.has(rankKey)) clusters.set(rankKey, []);
                    clusters.get(rankKey)!.push(node);
                });
            
                const nodeOffsets = new Map<string, number>();
                
                clusters.forEach((clusterNodes) => {
                    const count = clusterNodes.length;
                    if (count === 1) {
                        nodeOffsets.set(clusterNodes[0].id, 0);
                        return;
                    }
                    
                    // Spread logic: Tighter spacing for the "Fuse" look
                    const SPACING = 24; 
                    const totalSpread = (count - 1) * SPACING;
                    const startOffset = -totalSpread / 2;
            
                    clusterNodes.forEach((node, index) => {
                        nodeOffsets.set(node.id, startOffset + (index * SPACING));
                    });
                });
            
                // === 2. COORDINATE FUNCTIONS ===
                const getY = (n: AgentNode) => {
                    return expanded ? (n.y / 100) * h : h / 2;
                }
            
                const getX = (n: AgentNode) => {
                    const baseX = (n.x / 100) * w;
                    if (expanded) return baseX;
                    return baseX + (nodeOffsets.get(n.id) || 0);
                }
            
                // === 3. RENDER EDGES ===
                svg.innerHTML = ''
            
                $pipelineEdges.forEach((link: PipelineEdge) => {
                  const fromNode = $agentNodes.find((n) => n.id === link.from)
                  const toNode = $agentNodes.find((n) => n.id === link.to)
            
                  if (!fromNode || !toNode) return
            
                  const x1 = getX(fromNode)
                  const y1 = getY(fromNode)
                  const x2 = getX(toNode)
                  const y2 = getY(toNode)
            
                  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path')
                  
                  const curvature = expanded ? 60 : 20;
                  const d = `M ${x1} ${y1} C ${x1 + curvature} ${y1}, ${x2 - curvature} ${y2}, ${x2} ${y2}`
            
                  path.setAttribute('d', d)
                  
                  let classes = `cable`;
                  if (link.active) classes += ` active`;
                  if (link.pulseAnimation) classes += ` pulse`;
                  if (link.finalized) classes += ` finalized`;
                  
                  path.setAttribute('class', classes);
                  path.setAttribute('id', `link-${link.from}-${link.to}`)
            
                  if (link.signatureHash) {
                    path.setAttribute('data-signature', link.signatureHash)
                  }
            
                  svg.appendChild(path)
                })
            
                // === 4. RENDER NODES ===
                if (nodesLayer) {
                  nodesLayer.innerHTML = ''
                  $agentNodes.forEach((node) => {
                    const el = document.createElement('div')
                    
                    el.className = `node ${$selectedNode === node.id ? 'selected' : ''} ${
                      node.status === 'running' ? 'running' : ''
                    } ${node.status === 'complete' ? 'complete' : ''}`
            
                    const role = node.role ? node.role.toUpperCase() : 'WORKER';
            
                    el.innerHTML = `
                        <!-- EXPANDED CONTENT -->
                        <div class="node-indicator"></div>
                        <div class="node-content">
                            <div class="node-role">${role}</div>
                            <div class="node-label">${node.label}</div>
                        </div>
                        <div class="node-decor"></div>
                        
                        <!-- MINIMIZED CONTENT (The Fuse) -->
                        <div class="fuse-cap top"></div>
                        <div class="fuse-filament"></div>
                        <div class="fuse-cap bottom"></div>
                    `
                    
                    el.id = `node-${node.id}`
            
                    const x = getX(node)
                    const y = getY(node)
            
                    el.style.left = `${x}px`
                    el.style.top = `${y}px`
                    
                    const zIndexBase = node.status === 'running' ? 100 : 10;
                    el.style.zIndex = `${zIndexBase}`;
            
                    el.onclick = (e) => {
                      if (!expanded) {
                        // If minimized, bubble up to container to trigger toggle
                        return; 
                      }
                      e.stopPropagation()
                      selectNode(node.id)
                    }
            
                    nodesLayer!.appendChild(el)
                  })
                }
              }
            
              // React to changes
              $effect(() => {
                // Dependencies to trigger re-render
                const _expanded = expanded;
                const _nodeCount = $agentNodes.length; 
                const _edgeCount = $pipelineEdges.length;
                const _nodes = $agentNodes;
                const _selected = $selectedNode;
                const _status = $runtimeStore.status;
            
                requestAnimationFrame(() => {
                  renderGraph();
                });
              })
            
              function handleClick() {
                if (!expanded) {
                  ontoggle?.()
                }
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
              <div id="hud-banner">
                <div class="hud-title">
                  {#if isRunComplete}
                     <div class="hud-status-dot complete"></div>
                     SESSION COMPLETE // DATA HARDENED
                  {:else if $runtimeStore.status === 'RUNNING'}
                     <div class="hud-status-dot active"></div>
                     PIPELINE ACTIVE // PROCESSING
                  {:else if $planningMode}
                     <div class="hud-status-dot blueprint"></div>
                     BLUEPRINT MODE // ARCHITECT ACTIVE
                  {:else}
                     <div class="hud-status-dot"></div>
                     READY // EXECUTION MODE
                  {/if}
                </div>
                <button
                  class="btn-minimize"
                  onclick={(e) => {
                    e.stopPropagation()
                    ontoggle?.()
                  }}
                >
                  ▼ MINIMIZE
                </button>
              </div>
            
              <svg id="graph-svg" bind:this={svgElement}></svg>
              <div id="nodes-layer" bind:this={nodesLayer}></div>
            </div>
            
            <style>
              #pipeline-stage {
                height: 80px;
                background: var(--digi-void);
                border-top: 1px solid var(--paper-line);
                border-bottom: 1px solid var(--paper-line);
                position: relative;
                z-index: 100;
                transition: height 0.5s var(--ease-snap), border-color 0.3s;
                overflow: hidden;
                cursor: pointer;
                background-image: 
                    linear-gradient(color-mix(in srgb, var(--digi-line), transparent 50%) 1px, transparent 1px),
                    linear-gradient(90deg, color-mix(in srgb, var(--digi-line), transparent 50%) 1px, transparent 1px);
                background-size: 40px 40px;
              }
            
              #pipeline-stage.expanded {
                height: 65vh;
                cursor: default;
                box-shadow: 0 20px 80px rgba(0, 0, 0, 0.4);
                border-top: 1px solid var(--digi-line);
              }
            
              #pipeline-stage.expanded.run-complete {
                border-top: 1px solid var(--arctic-cyan);
                border-bottom: 1px solid var(--arctic-cyan);
              }
            
              #hud-banner {
                position: absolute;
                top: 0; left: 0; right: 0;
                height: 32px;
                background: var(--digi-void);
                border-bottom: 1px solid var(--digi-line);
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 0 12px;
                transform: translateY(-100%);
                transition: transform 0.3s ease;
                z-index: 200;
              }
            
              #pipeline-stage.expanded #hud-banner {
                transform: translateY(0);
              }
            
              .hud-title {
                color: var(--digi-text-dim);
                font-family: var(--font-code);
                font-size: 10px;
                letter-spacing: 1px;
                font-weight: 700;
                display: flex;
                align-items: center;
                gap: 8px;
              }
              
              .run-complete .hud-title { color: var(--arctic-cyan); }
            
              .hud-status-dot {
                width: 6px; height: 6px;
                background: var(--digi-text-dim);
                border-radius: 50%;
              }
            
              .hud-status-dot.active {
                background: var(--alert-amber);
                box-shadow: 0 0 8px var(--alert-amber);
                animation: blink 1s infinite alternate;
              }
            
              .hud-status-dot.complete {
                background: var(--arctic-cyan);
                box-shadow: 0 0 8px var(--arctic-cyan);
              }
            
              .hud-status-dot.blueprint {
                background: var(--arctic-cyan);
                box-shadow: 0 0 8px var(--arctic-cyan);
                animation: pulse 2s infinite;
              }
            
              .btn-minimize {
                background: transparent;
                border: none;
                color: var(--digi-text-dim);
                font-size: 10px;
                font-family: var(--font-code);
                cursor: pointer;
                transition: color 0.2s;
              }
              .btn-minimize:hover { color: var(--arctic-cyan); }
            
              #graph-svg {
                width: 100%; height: 100%;
                position: absolute; top: 0; left: 0;
              }
            
              #nodes-layer {
                width: 100%; height: 100%;
                position: absolute; top: 0; left: 0;
              }
            
              /* === NODE STYLING === */
              
              :global(.node) {
                position: absolute;
                transform: translate(-50%, -50%);
                transition: all 0.5s var(--ease-snap);
                user-select: none;
                display: flex;
                
                /* DEFAULT: MINIMIZED FUSE AESTHETIC */
                width: 14px;
                height: 36px;
                background: #000;
                border: 1px solid var(--digi-line);
                border-radius: 2px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.5);
                flex-direction: column;
                align-items: center;
                justify-content: space-between;
                padding: 2px 0;
                overflow: visible; /* Allow glow to spill */
                pointer-events: none;
              }
            
              /* --- FUSE ELEMENTS (MINIMIZED ONLY) --- */
              :global(.fuse-cap) {
                width: 8px;
                height: 2px;
                background: var(--digi-text-dim);
                opacity: 0.5;
              }
              
              :global(.fuse-filament) {
                width: 2px;
                flex: 1;
                background: var(--digi-line);
                margin: 2px 0;
                transition: background 0.3s, box-shadow 0.3s;
              }
            
              /* Hover effect for Minimized Nodes */
              #pipeline-stage:not(.expanded) :global(.node):hover {
                transform: translate(-50%, -55%) scale(1.1);
                border-color: var(--arctic-cyan);
                z-index: 200 !important;
              }
            
              /* --- EXPANDED CARD OVERRIDES --- */
              
              #pipeline-stage.expanded :global(.node) {
                width: auto;
                min-width: 140px;
                height: auto;
                background: var(--digi-panel);
                border-radius: 0;
                padding: 0;
                align-items: stretch;
                justify-content: flex-start;
                flex-direction: row;
                box-shadow: 0 4px 10px rgba(0,0,0,0.3);
                overflow: hidden;
                pointer-events: auto;
                cursor: pointer;
              }
            
              /* Hide Fuse elements in expanded */
              #pipeline-stage.expanded :global(.node) :global(.fuse-cap),
              #pipeline-stage.expanded :global(.node) :global(.fuse-filament) {
                display: none;
              }
            
              /* --- CARD ELEMENTS (EXPANDED ONLY) --- */
              
              :global(.node-indicator), 
              :global(.node-content), 
              :global(.node-decor) {
                display: none; /* Hidden by default (minimized) */
              }
            
              #pipeline-stage.expanded :global(.node) :global(.node-indicator) { 
                display: block; 
                width: 4px;
                background: var(--digi-line);
                transition: background 0.3s;
              }
            
              #pipeline-stage.expanded :global(.node) :global(.node-content) { 
                display: flex;
                flex: 1;
                padding: 8px 12px;
                flex-direction: column;
                gap: 2px;
              }
            
              #pipeline-stage.expanded :global(.node) :global(.node-decor) { 
                display: block;
                width: 12px;
                background: repeating-linear-gradient(0deg, transparent, transparent 2px, var(--digi-line) 2px, var(--digi-line) 3px);
                opacity: 0.3;
                border-left: 1px solid var(--digi-line);
              }
            
              :global(.node-role) {
                font-size: 8px;
                text-transform: uppercase;
                color: var(--digi-text-dim);
                opacity: 0.7;
                letter-spacing: 0.5px;
              }
            
              :global(.node-label) {
                font-size: 11px;
                font-weight: 600;
                color: var(--digi-text);
              }
            
              /* --- STATE STYLING (Shared Mapping) --- */
            
              /* RUNNING */
              :global(.node.running) {
                border-color: var(--alert-amber);
              }
              /* Card */
              :global(.node.running .node-indicator) { 
                background: var(--alert-amber);
                box-shadow: 0 0 8px var(--alert-amber);
              }
              :global(.node.running .node-label) { color: var(--alert-amber); }
              /* Fuse */
              :global(.node.running .fuse-filament) {
                background: var(--alert-amber);
                box-shadow: 0 0 8px var(--alert-amber), 0 0 4px #fff;
                width: 4px; /* Thicker when running */
                animation: flickerglow 0.1s infinite alternate;
              }
            
              /* COMPLETE */
              :global(.node.complete) {
                border-color: var(--signal-success);
              }
              /* Card */
              :global(.node.complete .node-indicator) { background: var(--signal-success); }
              :global(.node.complete .node-label) { color: var(--signal-success); }
              /* Fuse */
              :global(.node.complete .fuse-filament) {
                background: var(--signal-success);
                box-shadow: 0 0 4px var(--signal-success);
              }
            
              /* SELECTED (Expanded Only) */
              :global(.node.selected) {
                border-color: var(--arctic-cyan);
                background: var(--arctic-dim);
              }
              :global(.node.selected .node-indicator) { background: var(--arctic-cyan); }
              :global(.node.selected .node-label) { color: var(--arctic-cyan); }
            
              /* --- HOVER (Expanded Only) --- */
              #pipeline-stage.expanded :global(.node:hover) {
                border-color: var(--arctic-cyan);
                transform: translate(-50%, -52%);
                box-shadow: 0 8px 20px rgba(0,0,0,0.5);
              }
              #pipeline-stage.expanded :global(.node:hover .node-label) { color: white; }
            
              /* --- CABLES --- */
              :global(.cable) {
                fill: none;
                stroke: var(--digi-line);
                stroke-width: 1.5px;
                transition: stroke 0.3s;
              }
            
              :global(.cable.active) {
                stroke: var(--arctic-cyan);
                stroke-dasharray: 8 4;
                animation: flow 0.6s linear infinite;
                opacity: 0.9;
              }
            
              :global(.cable.active.pulse) {
                stroke-width: 2.5px;
                filter: drop-shadow(0 0 6px var(--arctic-cyan));
              }
            
              :global(.cable.finalized) {
                stroke: var(--arctic-cyan);
                stroke-width: 1.5px;
                opacity: 0.6;
              }
            
              @keyframes flow {
                to { stroke-dashoffset: -12; }
              }
              @keyframes blink {
                from { opacity: 0.4; }
                to { opacity: 1; }
              }
              @keyframes pulse {
                0% { opacity: 1; }
                50% { opacity: 0.5; }
                100% { opacity: 1; }
              }
              @keyframes flickerglow {
                  0% { opacity: 0.8; }
                  100% { opacity: 1; box-shadow: 0 0 12px var(--alert-amber); }
              }
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
            import { mockStartRun, mockGetArtifact } from './mock-api';
            
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
                                position: { x: 30, y: 50 }
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
                                position: { x: 70, y: 50 }
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
              // We explicitly treat this as synchronous
              return marked.parse(text) as string;
            }
        - mock-api.ts
            Imports: api.ts
            Imported by: api.ts, stores.ts
            // [[RARO]]/apps/web-console/src/lib/mock-api.ts
            import { type WorkflowConfig } from './api';
            
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
                    result: "testing\n```python\n# Analyzing latency variance\nvar = data['p99'].var()\nprint(f'Variance: {var}')\n```\n**Output:** `Variance: 0.042`" 
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
                        
                        // Immediately transition status back to RUNNING
                        this.addStep(0, 'RUNNING'); 
                        
                        // Restart the loop
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
            
                private planSimulation() {
                    // 1. Start State
                    this.addStep(500, 'RUNNING');
            
                    // 2. Orchestrator (n1) runs
                    this.simulateAgentExecution('n1', 1500, 450);
            
                    // 3. Parallel Execution: n2 (Dynamic) & n3 (Static)
                    this.activeAgents.push('n2', 'n3');
                    this.addStep(200);
            
                    // Complete n3
                    this.simulateAgentCompletion('n3', 2500, 800, false);
            
                    // n2 does its dynamic delegation thing
                    this.processDynamicChain('n2', 'n4'); 
            
                    // === INSERT SYSTEM INTERVENTION HERE ===
                    // We simulate a pause just before the final synthesis node (n4) starts.
                    this.addStep(0, 'AWAITING_APPROVAL'); 
                    // Note: The loop will PAUSE here until resume() is called.
            
                    // 4. Synthesis (n4) runs (After approval)
                    this.simulateAgentExecution('n4', 3000, 2500);
            
                    // 5. Completion
                    this.addStep(1000, 'COMPLETED');
                }
            
                private processDynamicChain(currentId: string, finalDependentId: string) {
                    // Logic kept same as previous version...
                    const isRoot = currentId === 'n2';
                    const chance = isRoot ? 0.6 : 0.3;
                    const shouldDelegate = Math.random() < chance;
                    const depth = currentId.split('_').length;
            
                    if (shouldDelegate && depth <= 3) {
                        const newAgentId = `${currentId}_sub${Math.floor(Math.random() * 100)}`;
                        const reason = isRoot ? "Topic too broad; spawning specialist." : "Additional verification.";
            
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
            
                        this.topology.nodes.push(newAgentId);
                        this.topology.edges.push({ from: currentId, to: newAgentId });
                        this.topology.edges = this.topology.edges.filter(e => !(e.from === currentId && e.to === finalDependentId));
                        this.topology.edges.push({ from: newAgentId, to: finalDependentId });
            
                        this.signatures[currentId] = `hash_${currentId}`;
                        this.addStep(1000); 
            
                        this.activeAgents.push(newAgentId);
                        this.addStep(500);
            
                        this.processDynamicChain(newAgentId, finalDependentId);
            
                    } else {
                        SESSION_ARTIFACTS[currentId] = { 
                            result: `Analysis complete for node ${currentId}. Validated 100% data points.` 
                        };
                        this.simulateAgentCompletion(currentId, 1500 + Math.random() * 1000, 600, true);
                    }
                }
            
                private simulateAgentExecution(id: string, duration: number, tokens: number) {
                    if (!this.activeAgents.includes(id)) {
                        this.activeAgents.push(id);
                    }
                    this.addStep(200);
                    this.simulateAgentCompletion(id, duration, tokens, false);
                }
            
                private simulateAgentCompletion(id: string, duration: number, tokens: number, isDynamic: boolean) {
                    this.addStep(duration, undefined, () => {
                        this.activeAgents = this.activeAgents.filter(a => a !== id);
                        this.completedAgents.push(id);
                        this.totalTokens += tokens;
                        this.signatures[id] = `hash_${Math.floor(Math.random()*10000).toString(16)}`;
                        
                        this.invocations.push({
                            id: `inv-${id}`,
                            agent_id: id,
                            status: 'success',
                            tokens_used: tokens,
                            latency_ms: duration,
                            artifact_id: `mock-art-${id}`
                        });
                    });
                }
            
                private addStep(delay: number, statusOverride?: string, action?: () => void) {
                    // Closure to capture state at execution time
                    const stepAction = () => {
                        if (action) action();
                        // Snapshot logic...
                    };
            
                    // We push a 'thunk' that generates the snapshot when called,
                    // or we push a config object and generate snapshot in runLoop.
                    // For this procedural generation, we are building a timeline array upfront,
                    // but the state needs to accumulate.
                    
                    // REFACTOR NOTE: To allow procedural state accumulation, we will execute the action
                    // immediately during generation, but capture the *resultant* state into the step array.
                    // This is strictly deterministic for the mock.
                    if (action) action();
            
                    const snapshot = {
                        delay,
                        state: {
                            status: statusOverride || 'RUNNING',
                            active_agents: [...this.activeAgents],
                            completed_agents: [...this.completedAgents],
                            failed_agents: [],
                            total_tokens_used: this.totalTokens,
                            invocations: JSON.parse(JSON.stringify(this.invocations)) // Deep copy
                        },
                        signatures: { ...this.signatures },
                        topology: JSON.parse(JSON.stringify(this.topology))
                    };
            
                    this.steps.push(snapshot);
                }
            
                private runLoop() {
                    if (this.currentStep >= this.steps.length) {
                        this.close();
                        return;
                    }
            
                    const step = this.steps[this.currentStep];
                    
                    // 1. Send the Update
                    const message = {
                        type: 'state_update',
                        state: step.state,
                        signatures: step.signatures || {},
                        topology: step.topology || null
                    };
            
                    if (this.onmessage) {
                        this.onmessage({ data: JSON.stringify(message) });
                    }
            
                    // 2. CHECK FOR INTERVENTION (PAUSE)
                    if (step.state.status === 'AWAITING_APPROVAL') {
                        console.log('[MOCK WS] Simulation paused for approval.');
                        this.isPaused = true;
                        this.currentStep++; // Ready for next step when resumed
                        return; // EXIT LOOP - Do not set timer
                    }
            
                    // 3. Schedule next step
                    this.timer = setTimeout(() => {
                        this.currentStep++;
                        this.runLoop();
                    }, step.delay);
                }
            }
        - stores.ts
            Imports: api.ts, layout-engine.ts, mock-api.ts
            Imported by: layout-engine.ts
            // [[RARO]]/apps/web-console/src/lib/stores.ts
            
            import { writable, get } from 'svelte/store';
            import { getWebSocketURL, USE_MOCK, type WorkflowConfig } from './api'; // Import USE_MOCK
            import { MockWebSocket } from './mock-api';        // Import Mock Class
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
            
            // Initial Nodes State
            const initialNodes: AgentNode[] = [
              { id: 'n1', label: 'ORCHESTRATOR', x: 20, y: 50, model: 'reasoning', prompt: 'Analyze the user request and determine optimal sub-tasks.', status: 'idle', role: 'orchestrator' },
              { id: 'n2', label: 'RETRIEVAL', x: 50, y: 30, model: 'fast', prompt: 'Search knowledge base for relevant context.', status: 'idle', role: 'worker' },
              { id: 'n3', label: 'CODE_INTERP', x: 50, y: 70, model: 'fast', prompt: 'Execute Python analysis on provided data.', status: 'idle', role: 'worker' },
              { id: 'n4', label: 'SYNTHESIS', x: 80, y: 50, model: 'thinking', prompt: 'Synthesize all findings into a final report.', status: 'idle', role: 'worker' }
            ];
            
            export const agentNodes = writable<AgentNode[]>(initialNodes);
            
            // Initial Edges State
            const initialEdges: PipelineEdge[] = [
              { from: 'n1', to: 'n2', active: false, finalized: false, pulseAnimation: false },
              { from: 'n1', to: 'n3', active: false, finalized: false, pulseAnimation: false },
              { from: 'n2', to: 'n4', active: false, finalized: false, pulseAnimation: false },
              { from: 'n3', to: 'n4', active: false, finalized: false, pulseAnimation: false }
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
                  role: agent.role
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
                  role: agent.role
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
                            role: 'worker'
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
            
            export function addLog(role: string, message: string, metadata: string = '', isAnimated: boolean = false, customId?: string) {
              logs.update(l => {
                if (customId && l.find(entry => entry.id === customId)) {
                  return l;
                }
                return [...l, {
                  id: customId || crypto.randomUUID(),
                  timestamp: new Date().toISOString(),
                  role,
                  message,
                  metadata,
                  isAnimated
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
                    console.log('[WS] State update:', {
                      status: data.state.status,
                      active: data.state.active_agents,
                      completed: data.state.completed_agents,
                      topology: data.topology ? `${data.topology.nodes?.length || 0} nodes, ${data.topology.edges?.length || 0} edges` : 'none'
                    });
            
                    // === APPROVAL DETECTION ===
                    // Check if state transitioned to AWAITING_APPROVAL
                    const currentState = get(runtimeStore);
                    const newStateStr = data.state.status?.toLowerCase() || '';
            
                    if (newStateStr === 'awaitingapproval' && currentState.status !== 'AWAITINGAPPROVAL') {
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
                  } else if (data.error) {
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
                                            let outputText = 'Output received';
                                            if (typeof artifact === 'string') outputText = artifact;
                                            else if (typeof artifact === 'object') {
                                                if (artifact.result) outputText = artifact.result;
                                                else if (artifact.output) outputText = artifact.output;
                                                else if (artifact.text) outputText = artifact.text;
                                                else outputText = JSON.stringify(artifact, null, 2);
                                            }
                                            updateLog(inv.id, {
                                                message: outputText,
                                                metadata: `TOKENS: ${inv.tokens_used || 0} | LATENCY: ${inv.latency_ms || 0}ms`,
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
                // Basic sanitization
                let html = code
                  .replace(/&/g, "&amp;")
                  .replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;");
              
                // 1. Strings (Single and Double quotes)
                // We utilize a specific class to style them via CSS variables
                html = html.replace(/(['"`])(.*?)\1/g, '<span class="token-str">$1$2$1</span>');
              
                // 2. Comments (Simple single line)
                html = html.replace(/(\/\/.*$)/gm, '<span class="token-comment">$1</span>');
                html = html.replace(/(#.*$)/gm, '<span class="token-comment">$1</span>'); // Python style
              
                // 3. Keywords (Generic set for JS/TS/Rust/Python)
                const keywords = /\b(import|export|from|const|let|var|function|return|if|else|for|while|class|interface|type|async|await|def|print|impl|struct|fn|pub)\b/g;
                html = html.replace(keywords, '<span class="token-kw">$1</span>');
              
                // 4. Numbers
                html = html.replace(/\b(\d+)\b/g, '<span class="token-num">$1</span>');
              
                // 5. Booleans
                html = html.replace(/\b(true|false|null|None)\b/g, '<span class="token-bool">$1</span>');
              
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
            import { addLog, themeStore } from '$lib/stores'
          
            let expanded = $state(false)
            let appState = $state<'HERO' | 'CONSOLE'>('HERO')
          
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
          </script>
          
          <main class="mode-{$themeStore.toLowerCase()}">
              
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
