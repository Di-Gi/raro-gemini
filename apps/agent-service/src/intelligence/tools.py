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