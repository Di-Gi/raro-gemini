# [[RARO]]/apps/agent-service/src/intelligence/tools.py
# Purpose: Tool definitions and Secure Workspace Execution Logic (Real Implementation)
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
# We handle imports safely so the service doesn't crash if packages are missing during build.
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
        # Define the sandbox roots
        self.session_root = os.path.join(RFS_BASE, "sessions", run_id)
        self.input_dir = os.path.join(self.session_root, "input")
        self.output_dir = os.path.join(self.session_root, "output")
        
        # Ensure directories exist (Agent side failsafe)
        os.makedirs(self.input_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)

    def _get_secure_path(self, filename: str) -> Optional[str]:
        """
        Security Enforcement:
        1. Strips directory traversal (../)
        2. Checks if file exists in Output (priority) or Input.
        3. Returns absolute path.
        """
        # Discard 'folder/../' junk and force simple filename
        clean_name = os.path.basename(filename) 
        
        # Priority 1: Has the agent created this file?
        out_path = os.path.join(self.output_dir, clean_name)
        if os.path.exists(out_path):
            return out_path
            
        # Priority 2: Was it provided by the user (Input)?
        in_path = os.path.join(self.input_dir, clean_name)
        if os.path.exists(in_path):
            return in_path
            
        return None

    def read(self, filename: str) -> str:
        """Securely read a file from the workspace."""
        path = self._get_secure_path(filename)
        if not path:
            return f"Error: File '{filename}' not found in input or output directories."
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
                # Basic output truncation to save tokens if massive
                if len(content) > 50000: 
                    return content[:50000] + "\n...[TRUNCATED BY SYSTEM]..."
                return content
        except UnicodeDecodeError:
            return "Error: File appears to be binary or non-UTF-8 text."
        except Exception as e:
            return f"Error reading file: {str(e)}"

    def write(self, filename: str, content: Union[str, bytes]) -> str:
        """Securely write a file to the workspace output directory."""
        clean_name = os.path.basename(filename)
        path = os.path.join(self.output_dir, clean_name)
        
        try:
            # Handle binary vs text
            mode = 'wb' if isinstance(content, bytes) else 'w'
            encoding = None if isinstance(content, bytes) else 'utf-8'
            
            with open(path, mode, encoding=encoding) as f:
                f.write(content)
            return f"Successfully saved to {clean_name}"
        except Exception as e:
            return f"Error writing file: {str(e)}"

    def list_contents(self) -> str:
        """List files available in the workspace."""
        try:
            inputs = os.listdir(self.input_dir)
            outputs = os.listdir(self.output_dir)
            
            return (
                f"=== WORKSPACE MANIFEST ===\n"
                f"READ-ONLY INPUTS: {inputs if inputs else 'None'}\n"
                f"SESSION OUTPUTS: {outputs if outputs else 'None'}"
            )
        except Exception as e:
            return f"Error accessing workspace directories: {str(e)}"

# --- REAL IMPLEMENTATIONS ---
def _run_e2b_sandbox(code: str, ws: WorkspaceManager) -> Dict[str, Any]:
    """
    Spins up an E2B sandbox, syncs RFS input files, executes code,
    and captures text output + image artifacts.
    """
    if Sandbox is None:
        return {"error": "E2B library not installed in environment."}
    
    if not settings.E2B_API_KEY:
        return {"error": "E2B_API_KEY not configured."}

    logger.info(f"Initializing E2B Sandbox for run {ws.run_id}...")

    try:
        # FIX: Use Sandbox.create() context manager
        with Sandbox.create(api_key=settings.E2B_API_KEY) as sandbox:
            
            # 1. SYNC INPUTS: Upload files from RFS Input -> Sandbox
            if os.path.exists(ws.input_dir):
                for filename in os.listdir(ws.input_dir):
                    file_path = os.path.join(ws.input_dir, filename)
                    if os.path.isfile(file_path):
                        with open(file_path, "rb") as f:
                            # E2B allows writing bytes directly
                            sandbox.files.write(filename, f.read())
                        logger.debug(f"Uploaded {filename} to sandbox.")

            # 2. EXECUTE CODE
            logger.info(f"E2B: Executing Python Code block (Length: {len(code)})")
            execution = sandbox.run_code(code)

            # 3. PROCESS OUTPUTS
            output_log = []
            
            # === IMPROVED LOGGING FOR STDOUT/STDERR ===
            if execution.logs.stdout:
                stdout_str = ''.join(execution.logs.stdout)
                output_log.append(f"STDOUT:\n{stdout_str}")
                logger.info(f"E2B STDOUT (Run {ws.run_id}):\n{stdout_str}")
            else:
                logger.info(f"E2B STDOUT (Run {ws.run_id}): [EMPTY]")

            if execution.logs.stderr:
                stderr_str = ''.join(execution.logs.stderr)
                output_log.append(f"STDERR:\n{stderr_str}")
                logger.warning(f"E2B STDERR (Run {ws.run_id}):\n{stderr_str}")
            
            # 4. HANDLE ARTIFACTS (PLOTS/IMAGES)
            # E2B captures matplotlib plots automatically in execution.results
            artifacts_created = []
            
            for result in execution.results:
                # E2B returns formats like png, jpeg, svg, etc.
                if hasattr(result, 'png') and result.png:
                    # Generate a unique filename for the run
                    img_filename = f"plot_{ws.run_id}_{len(artifacts_created)}.png"
                    
                    # Decode base64 and write to RFS Output
                    img_bytes = base64.b64decode(result.png)
                    ws.write(img_filename, img_bytes)
                    
                    artifacts_created.append(img_filename)
                    output_log.append(f"\n[SYSTEM: Generated Image saved to '{img_filename}']")
            
            if artifacts_created:
                logger.info(f"E2B Artifacts Created: {artifacts_created}")

            # 5. HANDLE ERRORS
            if execution.error:
                error_msg = f"RUNTIME ERROR: {execution.error.name}: {execution.error.value}"
                if execution.error.traceback:
                    error_msg += f"\n{execution.error.traceback}"
                
                logger.error(f"E2B Execution Failed: {error_msg}")

                # We return success=False so the Agent knows it failed
                return {
                    "success": False, 
                    "error": error_msg, 
                    "logs": "\n".join(output_log)
                }

            # 6. RETURN RESULT (Manifest Pattern)
            # If artifacts were created but no text output, return the manifest list as the result.
            # This ensures the LLM and next Agent know files exist.
            logs_text = "\n".join(output_log)
            
            if not logs_text and artifacts_created:
                result_text = f"Execution Complete. SYSTEM_ARTIFACTS_CREATED: {artifacts_created}"
            elif not logs_text:
                result_text = "Code executed successfully (No text output)."
            else:
                result_text = logs_text

            return {
                "success": True, 
                "result": result_text,
                "files_generated": artifacts_created,
                "artifact_stored": len(artifacts_created) > 0
            }

    except Exception as e:
        logger.error(f"E2B Sandbox failure: {e}", exc_info=True)
        return {"success": False, "error": f"Sandbox infrastructure failed: {str(e)}"}
    
def _run_tavily_search(query: str) -> Dict[str, Any]:
    """
    Performs a specialized AI-optimized search via Tavily.
    """
    if TavilyClient is None:
        return {"error": "Tavily library not installed."}

    if not settings.TAVILY_API_KEY:
        return {"error": "Tavily API Key missing."}

    try:
        # Initialize client locally to ensure it exists in this scope
        tavily = TavilyClient(api_key=settings.TAVILY_API_KEY)
        
        # 'search_context' returns a concatenated string of the most relevant content
        context = tavily.get_search_context(
            query=query, 
            search_depth="advanced", 
            max_tokens=2000
        )
        return {"success": True, "result": context}
    except Exception as e:
        return {"success": False, "error": f"Search failed: {str(e)}"}

# --- TOOL DEFINITIONS & DISPATCHER ---

def get_tool_definitions_for_prompt(tool_names: List[str]) -> str:
    """
    Returns a formatted JSON string of tool definitions for injection into the System Prompt.
    Used for manual parsing mode (json:function blocks).

    Args:
        tool_names: List of tool names to include

    Returns:
        JSON string containing tool schemas
    """
    # Define raw schemas (Independent of Google GenAI types)
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
            "description": "EXECUTE Python code in a secure sandbox. REQUIRED for data analysis, math, and creating files (plots/images).",
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

    definitions = []
    for name in tool_names:
        if name in registry:
            definitions.append(registry[name])

    return json.dumps(definitions, indent=2)


def get_tool_declarations(tool_names: List[str]) -> List[types.FunctionDeclaration]:
    """
    Maps logical tool names to Google GenAI FunctionDeclaration objects.
    """
    tool_registry = {
        'web_search': types.FunctionDeclaration(
            name='web_search',
            description='Search the web for real-time information. Use for news, facts, or technical docs.',
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    'query': types.Schema(type=types.Type.STRING, description='The search query')
                },
                required=['query']
            )
        ),

        'execute_python': types.FunctionDeclaration(
            name='execute_python',
            description='EXECUTE Python code in a secure sandbox. MANDATORY for: data analysis, math calculations, and CREATING FILES (like plots/images). Do NOT write code in markdown; you must send it to this tool to produce results.',
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    'code': types.Schema(type=types.Type.STRING, description='Python code to run. Do not wrap in markdown blocks.')
                },
                required=['code']
            )
        ),

        'read_file': types.FunctionDeclaration(
            name='read_file',
            description='Read text content from the local session workspace.',
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    'filename': types.Schema(
                        type=types.Type.STRING,
                        description='Name of the file to read (e.g., "data.csv")'
                    ),
                },
                required=['filename']
            )
        ),

        'write_file': types.FunctionDeclaration(
            name='write_file',
            description='Save text content to a file in the session workspace.',
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    'filename': types.Schema(
                        type=types.Type.STRING,
                        description='Name of the destination file'
                    ),
                    'content': types.Schema(
                        type=types.Type.STRING,
                        description='Text content to write'
                    ),
                },
                required=['filename', 'content']
            )
        ),

        'list_files': types.FunctionDeclaration(
            name='list_files',
            description='List all available files in the current session workspace.',
            parameters=types.Schema(type=types.Type.OBJECT, properties={})
        )
    }

    return [tool_registry[name] for name in tool_names if name in tool_registry]

def execute_tool_call(tool_name: str, args: Dict[str, Any], run_id: str = "default_run") -> Dict[str, Any]:
    """
    Dispatcher for executing the logic associated with a function call.
    Accepts run_id to target the specific session folder.

    Returns:
        Dict with 'success' (bool), 'result' (str), and optional 'error' (str)
    """

    # Initialize Workspace Manager with the specific run ID
    ws = WorkspaceManager(run_id)

    try:
        # === RFS TOOLS ===
        if tool_name == 'read_file':
            filename = args.get('filename', '')
            if not filename:
                return {'success': False, 'error': 'Parameter "filename" is required'}
            result = ws.read(filename)
            # Check if result is an error message
            if result.startswith("Error:"):
                return {'success': False, 'error': result}
            return {'success': True, 'result': result}

        elif tool_name == 'write_file':
            filename = args.get('filename', '')
            content = args.get('content', '')
            if not filename:
                return {'success': False, 'error': 'Parameter "filename" is required'}
            result = ws.write(filename, content)
            # Check if result is an error message
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

            # Validate code is a string
            if not isinstance(code, str):
                return {'success': False, 'error': 'Parameter "code" must be a string'}

            # Logger is handled inside _run_e2b_sandbox now for detail
            return _run_e2b_sandbox(code, ws)

        # Unknown tool
        logger.warning(f"Unknown tool requested: {tool_name}")
        return {
            'success': False,
            'error': f"Unknown tool: {tool_name}. Available tools: read_file, write_file, list_files, web_search, execute_python"
        }

    except Exception as e:
        logger.error(f"Tool Dispatcher Failure for {tool_name}: {e}", exc_info=True)
        return {
            'success': False,
            'error': f"Tool Execution Failure ({tool_name}): {str(e)}"
        }