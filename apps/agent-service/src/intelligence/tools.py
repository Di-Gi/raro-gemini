# [[RARO]]/apps/agent-service/src/intelligence/tools.py
# Purpose: Tool definitions and Secure Workspace Execution Logic
# Architecture: Intelligence Layer providing bridge between LLM and system actions.
# Dependencies: google-genai, os

import os
from typing import List, Dict, Any, Optional
from google.genai import types
from core.config import logger

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
        # In a perfect world, Kernel creates these. But concurrency is tricky.
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

    def write(self, filename: str, content: str) -> str:
        """Securely write a file to the workspace output directory."""
        clean_name = os.path.basename(filename)
        path = os.path.join(self.output_dir, clean_name)
        
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"Successfully saved to {clean_name}"
        except Exception as e:
            return f"Error writing file: {str(e)}"

    def list_contents(self) -> str:
        """List files available in the workspace."""
        try:
            inputs = os.listdir(self.input_dir)
            outputs = os.listdir(self.output_dir)
            
            # Format nicely for the LLM
            return (
                f"=== WORKSPACE MANIFEST ===\n"
                f"READ-ONLY INPUTS: {inputs if inputs else 'None'}\n"
                f"SESSION OUTPUTS: {outputs if outputs else 'None'}"
            )
        except Exception as e:
            return f"Error accessing workspace directories: {str(e)}"

# --- TOOL DEFINITIONS ---

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
    """
    
    # Initialize Workspace Manager with the specific run ID
    ws = WorkspaceManager(run_id)

    try:
        # === RFS TOOLS ===
        if tool_name == 'read_file':
            return {'result': ws.read(args.get('filename', ''))}
            
        elif tool_name == 'write_file':
            return {'result': ws.write(args.get('filename', ''), args.get('content', ''))}
            
        elif tool_name == 'list_files':
            return {'result': ws.list_contents()}

        # === STANDARD TOOLS ===
        elif tool_name == 'web_search':
            query = args.get('query', 'unspecified')
            # Mock Implementation for Prototype
            return {
                'success': True,
                'result': f"Found relevant information for '{query}': [Mocked content from search engine]."
            }

        elif tool_name == 'execute_python':
            # Mock Implementation for Prototype
            return {
                'success': True,
                'result': "Code executed successfully. Output: [Calculated value or plot confirmation]."
            }

        return {
            'success': False,
            'error': f"Unknown tool: {tool_name}"
        }

    except Exception as e:
        return {
            'success': False,
            'error': f"Tool Execution Failure: {str(e)}"
        }

# Integration: Used by src/core/llm.py to handle the function call loop.