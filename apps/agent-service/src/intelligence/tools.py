# [[RARO]]/apps/agent-service/src/intelligence/tools.py
# Purpose: Tool definitions and Secure Workspace Execution Logic
# Architecture: Intelligence Layer bridge to E2B and Tavily
# Dependencies: google-genai, e2b-code-interpreter, tavily-python

import os
import base64
import logging
from typing import List, Dict, Any, Optional, Union
from google.genai import types
from core.config import settings, logger
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
            with open(path, mode, encoding=encoding) as f: f.write(content)
            return f"Successfully saved to {clean_name}"
        except Exception as e: return f"Error writing file: {str(e)}"

    def list_contents(self) -> str:
        try:
            inputs = os.listdir(self.input_dir)
            outputs = os.listdir(self.output_dir)
            return f"FILES:\nInputs: {inputs}\nOutputs: {outputs}"
        except Exception as e: return f"Error: {str(e)}"

# --- EXECUTION LOGIC ---

def _run_e2b_sandbox(code: str, ws: WorkspaceManager) -> Dict[str, Any]:
    if Sandbox is None: return {"error": "E2B library missing."}
    if not settings.E2B_API_KEY: return {"error": "E2B_API_KEY missing."}

    logger.info(f"Initializing E2B Sandbox for run {ws.run_id}...")

    try:
        with Sandbox.create(api_key=settings.E2B_API_KEY) as sandbox:
            # Sync Inputs
            if os.path.exists(ws.input_dir):
                for filename in os.listdir(ws.input_dir):
                    file_path = os.path.join(ws.input_dir, filename)
                    if os.path.isfile(file_path):
                        with open(file_path, "rb") as f:
                            sandbox.files.write(filename, f.read())

            # Execute
            logger.info(f"E2B: Executing code ({len(code)} chars)")
            execution = sandbox.run_code(code)

            # Capture Outputs
            output_log = []
            if execution.logs.stdout: output_log.append(f"STDOUT:\n{''.join(execution.logs.stdout)}")
            if execution.logs.stderr: output_log.append(f"STDERR:\n{''.join(execution.logs.stderr)}")
            
            # Capture Artifacts
            artifacts_created = []
            for result in execution.results:
                if hasattr(result, 'png') and result.png:
                    img_filename = f"plot_{ws.run_id}_{len(artifacts_created)}.png"
                    ws.write(img_filename, base64.b64decode(result.png))
                    artifacts_created.append(img_filename)
                    output_log.append(f"\n[SYSTEM: Generated Image saved to '{img_filename}']")

            if execution.error:
                error_msg = f"RUNTIME ERROR: {execution.error.name}: {execution.error.value}"
                if execution.error.traceback: error_msg += f"\n{execution.error.traceback}"
                return {"success": False, "error": error_msg, "logs": "\n".join(output_log)}

            logs_text = "\n".join(output_log)
            result_text = logs_text if logs_text else ("Execution successful (No stdout)." if not artifacts_created else f"Execution successful. Files created: {artifacts_created}")

            return {
                "success": True, 
                "result": result_text,
                "files_generated": artifacts_created,
                "artifact_stored": len(artifacts_created) > 0
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
            return {'success': True, 'result': ws.write(args.get('filename', ''), args.get('content', ''))}
        elif tool_name == 'list_files':
            return {'success': True, 'result': ws.list_contents()}
        elif tool_name == 'web_search':
            return _run_tavily_search(args.get('query', ''))
        elif tool_name == 'execute_python':
            return _run_e2b_sandbox(args.get('code', ''), ws)
        
        return {'success': False, 'error': f"Unknown tool: {tool_name}"}
    except Exception as e:
        return {'success': False, 'error': f"Tool execution error: {str(e)}"}