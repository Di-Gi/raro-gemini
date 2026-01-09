Here are the modifications required to switch from native API function calling to manual `json:function` parsing.

This change involves:
1.  **`src/intelligence/tools.py`**: Adding a helper to export tool definitions as a text-based JSON schema.
2.  **`src/intelligence/prompts.py`**: Updating the system instruction to teach the model the new format.
3.  **`src/core/llm.py`**: Disabling native tools in the request configuration and implementing the Regex parsing loop.

### 1. `apps/agent-service/src/intelligence/tools.py`

We need to add a method to get the raw JSON schemas for the prompt, rather than the API objects.

```python
# [[RARO]]/apps/agent-service/src/intelligence/tools.py
# Purpose: Tool definitions and Secure Workspace Execution Logic (Real Implementation)
# Architecture: Intelligence Layer bridge to E2B and Tavily
# Dependencies: google-genai, e2b-code-interpreter, tavily-python

import os
import base64
import logging
import json
from typing import List, Dict, Any, Optional, Union
from google.genai import types
from core.config import settings, logger

# ... [Keep existing Imports and WorkspaceManager class unchanged] ...

# --- TOOL DEFINITIONS & DISPATCHER ---

def get_tool_definitions_for_prompt(tool_names: List[str]) -> str:
    """
    Returns a formatted JSON string of tool definitions for injection into the System Prompt.
    Used for manual parsing mode (Flow B).
    """
    definitions = []
    
    # 1. Define raw schemas (Independent of Google GenAI types)
    registry = {
        'web_search': {
            "name": "web_search",
            "description": "Search the web for real-time information, news, or facts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"}
                },
                "required": ["query"]
            }
        },
        'execute_python': {
            "name": "execute_python",
            "description": "EXECUTE Python code. REQUIRED for data analysis, math, and creating files (plots/images).",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to run."}
                },
                "required": ["code"]
            }
        },
        'read_file': {
            "name": "read_file",
            "description": "Read text content from the local session workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Name of the file (e.g. 'data.csv')"}
                },
                "required": ["filename"]
            }
        },
        'write_file': {
            "name": "write_file",
            "description": "Save text content to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Destination filename"},
                    "content": {"type": "string", "description": "Text content"}
                },
                "required": ["filename", "content"]
            }
        },
        'list_files': {
            "name": "list_files",
            "description": "List files in current workspace.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    }

    for name in tool_names:
        if name in registry:
            definitions.append(registry[name])

    return json.dumps(definitions, indent=2)

# Keep legacy getter just in case, or remove if fully deprecated
def get_tool_declarations(tool_names: List[str]) -> List[types.FunctionDeclaration]:
    # ... [Keep existing implementation or stub it out] ...
    return []

def execute_tool_call(tool_name: str, args: Dict[str, Any], run_id: str = "default_run") -> Dict[str, Any]:
    # ... [Keep existing execute_tool_call implementation unchanged] ...
    # Initialize Workspace Manager with the specific run ID
    ws = WorkspaceManager(run_id)

    try:
        # === RFS TOOLS ===
        if tool_name == 'read_file':
            filename = args.get('filename', '')
            if not filename:
                return {'success': False, 'error': 'Parameter "filename" is required'}
            result = ws.read(filename)
            if result.startswith("Error:"):
                return {'success': False, 'error': result}
            return {'success': True, 'result': result}

        elif tool_name == 'write_file':
            filename = args.get('filename', '')
            content = args.get('content', '')
            if not filename:
                return {'success': False, 'error': 'Parameter "filename" is required'}
            result = ws.write(filename, content)
            if result.startswith("Error"):
                return {'success': False, 'error': result}
            return {'success': True, 'result': result}

        elif tool_name == 'list_files':
            result = ws.list_contents()
            if result.startswith("Error"):
                return {'success': False, 'error': result}
            return {'success': True, 'result': result}

        # === INTELLIGENCE TOOLS ===
        elif tool_name == 'web_search':
            query = args.get('query', '')
            if not query:
                return {'success': False, 'error': 'Parameter "query" is required'}
            logger.info(f"Executing Web Search: {query}")
            return _run_tavily_search(query)

        elif tool_name == 'execute_python':
            code = args.get('code', '')
            if not code:
                logger.error("Execute Python called with empty code")
                return {'success': False, 'error': 'Parameter "code" is required and cannot be empty'}

            if not isinstance(code, str):
                return {'success': False, 'error': 'Parameter "code" must be a string'}

            return _run_e2b_sandbox(code, ws)

        # Unknown tool
        logger.warning(f"Unknown tool requested: {tool_name}")
        return {
            'success': False,
            'error': f"Unknown tool: {tool_name}. Available: read_file, write_file, list_files, web_search, execute_python"
        }

    except Exception as e:
        logger.error(f"Tool Dispatcher Failure for {tool_name}: {e}", exc_info=True)
        return {
            'success': False,
            'error': f"Tool Execution Failure ({tool_name}): {str(e)}"
        }
```

### 2. `apps/agent-service/src/intelligence/prompts.py`

Update the system instruction to strictly enforce the `json:function` block format.

```python
# [[RARO]]/apps/agent-service/src/intelligence/prompts.py

import json
from typing import Optional, List
from domain.protocol import WorkflowManifest, DelegationRequest, PatternDefinition
try:
    from intelligence.tools import get_tool_definitions_for_prompt
except ImportError:
    get_tool_definitions_for_prompt = lambda x: "[]"

# ... [Keep existing imports and schema helpers] ...

def render_runtime_system_instruction(agent_id: str, tools: Optional[List[str]]) -> str:
    """
    Generates the high-priority System Instruction for the Runtime Loop (Flow B).
    """
    # 1. Base Identity
    instruction = f"""
SYSTEM IDENTITY:
You are Agent '{agent_id}', an autonomous execution node within the RARO Kernel.
You are running in a headless environment. You do NOT interact with a human user.
Your outputs are consumed programmatically.

OPERATIONAL CONSTRAINTS:
1. NO CHAT: Do not output conversational filler.
2. DIRECT ACTION: If the user request implies an action, use a tool immediately.
3. FAIL FAST: If you cannot complete the task, return a clear error.
"""

    # 2. Tool Protocols (MANUAL PARSING MODE)
    if tools:
        tool_schemas = get_tool_definitions_for_prompt(tools)
        
        instruction += f"""
[SYSTEM CAPABILITY: TOOL USE]
You have access to the following tools. 
To use a tool, you MUST output a JSON object wrapped in a specific code block tag.

AVAILABLE TOOLS (JSON Schema):
{tool_schemas}

TOOL CALLING PROTOCOL:
You do not have a native tool interface. You must parse the tool call yourself using Markdown.
To invoke a tool, output a block exactly like this:

```json:function
{{
  "name": "tool_name_here",
  "args": {{
    "arg_name": "value"
  }}
}}
```

CRITICAL RULES:
1. The tag MUST be `json:function`.
2. The content MUST be valid JSON.
3. Do NOT wrap python code in `python` blocks if you want to execute it. Use the `execute_python` tool structure.
"""

        # Specific guidance for Python
        if "execute_python" in tools:
            instruction += """
[TOOL SPECIFIC: execute_python]
- You have a secure Python sandbox.
- To run code, you MUST use the `execute_python` tool.
- Do NOT output ```python ... ``` text blocks. The system ignores them.
- Example:
```json:function
{
  "name": "execute_python",
  "args": {
    "code": "print('Hello World')"
  }
}
```
"""
    else:
        instruction += "\nNOTE: You have NO tools available. Provide analysis based solely on the provided context.\n"

    return instruction
```

### 3. `apps/agent-service/src/core/llm.py`

This is the largest change. We strip out the native `tools` parameter in the API call and implement a Regex Loop in the response handler.

```python
# [[RARO]]/apps/agent-service/src/core/llm.py
# Purpose: LLM Wrapper with Multimodal, Parent Signature, Tool Handling & Streaming
# Architecture: Core Layer
# Dependencies: google.genai, pathlib, base64

from typing import Dict, Any, List, Optional, AsyncIterator, Union
import base64
import mimetypes
import json
import asyncio
import re
from pathlib import Path
from datetime import datetime
from google.genai import types
from core.config import gemini_client, logger, resolve_model
from intelligence.prompts import render_runtime_system_instruction

# Import Tooling Logic
try:
    from intelligence.tools import execute_tool_call
except ImportError:
    # FIX: Robust fallback signature
    execute_tool_call = lambda tool_name, args, run_id="default": {"error": "Tool execution unavailable"}

# ... [Keep load_multimodal_file unchanged] ...

# ============================================================================
# Private Helper: Request Preparation
# ============================================================================

async def _prepare_gemini_request(
    model: str,
    prompt: str,
    agent_id: str,
    input_data: Optional[Dict[str, Any]] = None,
    file_paths: Optional[List[str]] = None,
    parent_signature: Optional[str] = None,
    thinking_level: Optional[int] = None,
    tools: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Internal helper to build contents, config.
    NOTE: In this Manual Parsing Mode, we do NOT pass 'tools' object to the API.
    We inject tool info into the system_instruction instead.
    """
    
    # 1. Generate System Instruction (Includes JSON Schema and Protocol)
    system_instruction = render_runtime_system_instruction(agent_id, tools)

    # 2. Build Generation Config
    config_params: Dict[str, Any] = {
        "temperature": 1.0,
        "system_instruction": system_instruction
    }

    # Add Deep Think configuration
    if "deep-think" in model and thinking_level:
        thinking_budget = min(max(thinking_level * 1000, 1000), 10000)
        config_params["thinking_config"] = types.ThinkingConfig(
            include_thoughts=True,
            thinking_budget=thinking_budget
        )

    # NOTE: We intentionally DO NOT set config_params["tools"] here.
    # The agent is instructed to use ```json:function``` text blocks.

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
            "parts": [{"text": "Context accepted. Maintaining reasoning chain."}]
        })

    # Build User Message
    user_parts: List[Dict[str, Any]] = []

    # Multimodal files
    if file_paths:
        for file_path in file_paths:
            try:
                # Assuming load_multimodal_file is defined above
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
    Execute Gemini interaction with MANUAL tool parsing.
    Handles the 'Tool Loop' by parsing ```json:function ... ``` blocks.
    """
    if not gemini_client:
        raise ValueError("GEMINI_API_KEY not set")
    concrete_model = resolve_model(model)
    safe_agent_id = agent_id or "unknown_agent"

    logger.info(
        f"\n{'#'*70}\n"
        f"AGENT INVOCATION: {safe_agent_id} (Manual Tooling)\n"
        f"Model: {concrete_model} | Run ID: {run_id}\n"
        f"Tools: {tools}\n"
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
        final_response_text = ""
        
        # Regex to capture content inside ```json:function ... ``` 
        # Captures { ... } content in group 1
        function_pattern = r"```json:function\s*(\{[\s\S]*?\})\s*```"

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
            tool_calls_found = []
            
            # Find all matches
            matches = list(re.finditer(function_pattern, content_text, re.IGNORECASE))
            
            if not matches:
                # No tools called, this is the final answer
                final_response_text = content_text
                break
            
            # 4. Process Tool Calls
            tool_outputs_text = ""
            
            for match in matches:
                json_str = match.group(1)
                try:
                    call_data = json.loads(json_str)
                    tool_name = call_data.get("name")
                    tool_args = call_data.get("args", {})
                    
                    logger.info(
                        f"[TOOL DETECTED] Agent: {agent_id} | Tool: {tool_name} | Args: {str(tool_args)[:100]}..."
                    )

                    # Execute
                    result_dict = execute_tool_call(tool_name, tool_args, run_id=run_id)
                    
                    # Log result
                    success = result_dict.get('success', True)
                    logger.info(
                        f"[TOOL RESULT] Agent: {agent_id} | Status: {'✓' if success else '✗'}"
                    )

                    # Format Output for the Model
                    # Since we aren't using native tools, we feed this back as a USER message
                    # clearly labeled as system output.
                    tool_outputs_text += f"\n[SYSTEM: Tool '{tool_name}' Result]\n{json.dumps(result_dict, indent=2)}\n"

                except json.JSONDecodeError:
                    logger.warning(f"Agent {agent_id} generated invalid JSON in function block.")
                    tool_outputs_text += f"\n[SYSTEM ERROR] Invalid JSON in json:function block.\n"
                except Exception as e:
                    logger.error(f"Tool processing error: {e}")
                    tool_outputs_text += f"\n[SYSTEM ERROR] Tool execution exception: {str(e)}\n"

            # 5. Append Tool Outputs to History
            # We append as 'user' role because Gemini treats 'function' role strictly for native calls
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

        logger.info(f"Agent {safe_agent_id} Completed. Tokens: {input_tokens}/{output_tokens}")

        return {
            "text": final_response_text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "thought_signature": thought_signature,
            "cache_hit": cache_hit
        }

    except Exception as e:
        logger.error(f"Agent {safe_agent_id} Failed: {str(e)}", exc_info=True)
        raise

# ... [Keep streaming functions, adjusting similarly if needed, but Sync is priority] ...
```