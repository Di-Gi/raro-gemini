Here is the conclusive implementation guide for the **RARO File System (RFS)**. This integrates a standardized workspace environment into your multi-agent system.

### The Architecture: RFS Standard

We separate **Persistent User Data** from **Ephemeral Run Data**.

1.  **`/app/storage/library`**: The "Vault". User uploads go here. Agents generally read-only (via copy).
2.  **`/app/storage/sessions/{run_id}`**: The "Sandbox". Created fresh for every workflow run.
    *   **`/input`**: Files attached to the directive are copied here on start.
    *   **`/output`**: Agents write new files here.

---

### Phase 1: Infrastructure (Docker)

Both services must share the same physical volume to see the files. Update your `docker-compose.yml`.

```yaml
services:
  kernel:
    # ... other config ...
    volumes:
      - ./storage:/app/storage  # <--- SHARED MOUNT

  agents:
    # ... other config ...
    volumes:
      - ./storage:/app/storage  # <--- SHARED MOUNT
```

*Note: Ensure you create a local folder named `storage/library` on your host machine before starting.*

---

### Phase 2: Agent Service (Python)

We need to rewrite the tool execution logic to be "Workspace Aware" and secure against path traversal attacks.

#### 1. Update `src/intelligence/tools.py`

Replace the existing mock logic with this robust implementation:

```python
# [[RARO]]/apps/agent-service/src/intelligence/tools.py
import os
from typing import List, Dict, Any, Optional
from google.genai import types
from core.config import logger

# Hard anchor to prevent agents from breaking out of the sandbox
RFS_BASE = "/app/storage"

class WorkspaceManager:
    def __init__(self, run_id: str):
        self.run_id = run_id
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
        clean_name = os.path.basename(filename) # Discards 'folder/../' junk
        
        # Priority 1: Has the agent created this file?
        out_path = os.path.join(self.output_dir, clean_name)
        if os.path.exists(out_path):
            return out_path
            
        # Priority 2: Was it provided by the user?
        in_path = os.path.join(self.input_dir, clean_name)
        if os.path.exists(in_path):
            return in_path
            
        return None

    def read(self, filename: str) -> str:
        path = self._get_secure_path(filename)
        if not path:
            return f"Error: File '{filename}' not found in input or output directories."
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            return f"Error reading file: {str(e)}"

    def write(self, filename: str, content: str) -> str:
        clean_name = os.path.basename(filename)
        path = os.path.join(self.output_dir, clean_name)
        
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"Successfully saved to {clean_name}"
        except Exception as e:
            return f"Error writing file: {str(e)}"

    def list_contents(self) -> str:
        try:
            inputs = os.listdir(self.input_dir)
            outputs = os.listdir(self.output_dir)
            return f"Input Files (Read-Only): {inputs}\nCreated Files: {outputs}"
        except Exception:
            return "Error accessing workspace directories."

# --- TOOL DEFINITIONS ---

def get_tool_declarations(tool_names: List[str]) -> List[types.FunctionDeclaration]:
    # ... (Keep existing definitions, update descriptions to mention 'workspace') ...
    # Add list_files:
    tools = {
        'read_file': types.FunctionDeclaration(
            name='read_file',
            description='Read content from a text file in the workspace.',
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={'filename': types.Schema(type=types.Type.STRING)},
                required=['filename']
            )
        ),
        'write_file': types.FunctionDeclaration(
            name='write_file',
            description='Save text content to a file in the workspace.',
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    'filename': types.Schema(type=types.Type.STRING),
                    'content': types.Schema(type=types.Type.STRING)
                },
                required=['filename', 'content']
            )
        ),
        'list_files': types.FunctionDeclaration(
            name='list_files',
            description='List all available files in the current workspace.',
            parameters=types.Schema(type=types.Type.OBJECT, properties={})
        )
    }
    # Add other tools (web_search etc) back here...
    return [tools[t] for t in tool_names if t in tools]

def execute_tool_call(tool_name: str, args: Dict[str, Any], run_id: str = "default") -> Dict[str, Any]:
    """
    Now accepts run_id to target the specific session folder.
    """
    ws = WorkspaceManager(run_id)
    
    if tool_name == 'read_file':
        return {'result': ws.read(args.get('filename', ''))}
    elif tool_name == 'write_file':
        return {'result': ws.write(args.get('filename', ''), args.get('content', ''))}
    elif tool_name == 'list_files':
        return {'result': ws.list_contents()}
    
    # ... handle other tools ...
    
    return {'error': f"Unknown tool: {tool_name}"}
```

#### 2. Update `src/core/llm.py`

We must propagate the `run_id` from the main request down to the tool executor.

```python
# [[RARO]]/apps/agent-service/src/core/llm.py

# Update the function signature
async def call_gemini_with_context(
    # ... existing args ...
    agent_id: Optional[str] = None,
    run_id: str = "default_run" # <--- ADD THIS
) -> Dict[str, Any]:

    # ... inside the loop where tools are executed ...
    
    # Execute Tools
    function_responses = []
    for call in function_calls:
        tool_name = call.name
        tool_args = call.args
        
        logger.debug(f"Executing tool: {tool_name}")
        
        # PASS THE RUN_ID HERE
        result_dict = execute_tool_call(tool_name, tool_args, run_id=run_id)
        
        function_responses.append(types.Part.from_function_response(
            name=tool_name,
            response=result_dict
        ))
```

#### 3. Update `src/main.py`

Update the endpoint to pass the ID from the incoming request object.

```python
# [[RARO]]/apps/agent-service/src/main.py

async def _execute_agent_logic(request: AgentRequest) -> AgentResponse:
    # ...
    result = await call_gemini_with_context(
        # ... other args
        agent_id=request.agent_id,
        run_id=request.run_id # <--- PASS IT HERE
    )
    # ...
```

---

### Phase 3: Kernel Service (Rust)

The Kernel is responsible for creating the session environment when a workflow starts.

#### 1. Create `src/fs_manager.rs`

```rust
// [[RARO]]/apps/kernel-server/src/fs_manager.rs
use std::fs;
use std::path::Path;
use std::io;

const STORAGE_ROOT: &str = "/app/storage";

pub struct WorkspaceInitializer;

impl WorkspaceInitializer {
    pub fn init_run_session(run_id: &str, library_files: Vec<String>) -> io::Result<()> {
        let session_path = format!("{}/sessions/{}", STORAGE_ROOT, run_id);
        let input_path = format!("{}/input", session_path);
        let output_path = format!("{}/output", session_path);

        // 1. Create Directories
        fs::create_dir_all(&input_path)?;
        fs::create_dir_all(&output_path)?;

        // 2. Copy requested files from Library -> Session Input
        for filename in library_files {
            let src = format!("{}/library/{}", STORAGE_ROOT, filename);
            let dest = format!("{}/{}", input_path, filename);
            
            if Path::new(&src).exists() {
                // We copy to ensure the run is an isolated snapshot
                fs::copy(&src, &dest)?;
                tracing::info!("Attached file {} to run {}", filename, run_id);
            } else {
                tracing::warn!("Requested file {} not found in library", filename);
            }
        }

        Ok(())
    }
    
    // Optional: Cleanup
    pub fn cleanup_run(run_id: &str) {
        let path = format!("{}/sessions/{}", STORAGE_ROOT, run_id);
        let _ = fs::remove_dir_all(path);
    }
}
```

#### 2. Update `src/runtime.rs`

Call the initializer when starting a workflow.

```rust
// [[RARO]]/apps/kernel-server/src/runtime.rs

// Add module
mod fs_manager; 

// Inside RARORuntime::start_workflow
pub fn start_workflow(self: &Arc<Self>, config: WorkflowConfig) -> Result<String, String> {
    
    // ... existing DAG setup ...

    let run_id = Uuid::new_v4().to_string();

    // === FILESYSTEM INIT ===
    // Assuming WorkflowConfig has a field `attached_files: Vec<String>`
    // If not, pass an empty vector for now, or add the field to models.rs
    let attached_files = vec![]; 
    
    if let Err(e) = crate::fs_manager::WorkspaceInitializer::init_run_session(&run_id, attached_files) {
        tracing::error!("Failed to initialize workspace: {}", e);
        // Depending on strictness, you might want to return Err here
    }

    // ... continue with spawning execution task ...
}
```

---

### Phase 4: Usage Flow

1.  **Preparation**: You manually drop a file (e.g., `financials.txt`) into `storage/library`.
2.  **Payload**: You send a workflow request. You can conceptually add an `attached_files` array to the config (or just assume for now the user mentioned it in the prompt).
3.  **Execution**:
    *   Kernel creates `storage/sessions/{uuid}/input`.
    *   Kernel copies `financials.txt` there.
    *   Agent starts.
    *   Agent Prompt: "Read the financials file."
    *   Agent Tool Call: `read_file(filename="financials.txt")`.
    *   Agent Service: Checks `sessions/{uuid}/input/financials.txt`. **Found.**
    *   Agent Output: "I have read the file..."
    *   Agent Tool Call: `write_file(filename="summary.md", content="...")`.
    *   Agent Service: Writes to `sessions/{uuid}/output/summary.md`.

This structure is secure, scalable, and keeps your persistent data clean while giving agents full R/W access to their specific task environment.