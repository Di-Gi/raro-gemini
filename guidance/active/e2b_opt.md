look into optimizing e2b, hold sandbox active for active agent over creating new on each run : dispose on execution final
---

This is a high-impact architectural change. To achieve persistence (allowing `pip install` to survive across multiple tool calls within the same run), we need to shift from **Ephemeral Sandboxes** (created/destroyed per tool call) to **Session-Based Sandboxes** (created on first call, reconnected on subsequent calls, destroyed on workflow completion).

We will use **Redis** to store the mapping between your `run_id` and the remote `e2b_sandbox_id`.

### The Plan

1.  **Agent Service (`tools.py`)**: Implement a `SandboxSession` manager that checks Redis for an existing Sandbox ID before creating a new one.
2.  **Agent Service (`main.py`)**: Add a `/cleanup/{run_id}` endpoint to explicitly destroy the sandbox when the workflow ends.
3.  **Kernel (`runtime.rs`)**: Update the execution loop to call the cleanup endpoint when the Run status reaches `Completed` or `Failed`.

---

### 1. Update `apps/agent-service/src/intelligence/tools.py`

We will modify `execute_tool_call` and `_run_e2b_sandbox` to support connection reuse.

```python
# [[RARO]]/apps/agent-service/src/intelligence/tools.py

import os
import base64
import logging
import json
from datetime import datetime
from typing import List, Dict, Any, Optional, Union
from core.config import settings, logger, redis_client # Ensure redis_client is imported

# ... (Imports for E2B and Tavily remain the same) ...
try:
    from e2b_code_interpreter import Sandbox
    from tavily import TavilyClient
except ImportError:
    Sandbox = None
    TavilyClient = None

RFS_BASE = "/app/storage"

# ... (WorkspaceManager class remains the same) ...

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
                    logger.info(f"Reconnecting to existing E2B sandbox: {stored_sandbox_id} for run {run_id}")
                    # Connect to existing session
                    sandbox = Sandbox.connect(stored_sandbox_id, api_key=settings.E2B_API_KEY)
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
                logger.info(f"Killing E2B sandbox {stored_sandbox_id} for completed run {run_id}")
                Sandbox.kill(stored_sandbox_id, api_key=settings.E2B_API_KEY)
            except Exception as e:
                logger.warning(f"Error killing sandbox: {e}")
            finally:
                redis_client.delete(key)

# ============================================================================
# EXECUTION LOGIC
# ============================================================================

def _run_e2b_sandbox(code: str, ws: WorkspaceManager) -> Dict[str, Any]:
    if Sandbox is None: return {"error": "E2B library missing."}
    
    # 1. Acquire Persistent Sandbox
    sandbox = SandboxSession.get_or_create(ws.run_id)
    if not sandbox:
        return {"error": "Failed to initialize E2B Sandbox connection."}

    try:
        # 2. SYNC FILES (Smart Sync)
        # We only upload files if they haven't been uploaded to this specific sandbox instance yet.
        # However, E2B filesystem is persistent. If we uploaded 'data.csv' in step 1, it is there in step 2.
        # We check the local RFS inputs and ensure they exist remotely.
        
        # A simple optimization: If we just created the sandbox, upload everything.
        # If it's a reconnect, we assume files persist, BUT new files might have arrived in RFS.
        # For safety/robustness: We overwrite Input files to ensure latest version.
        if os.path.exists(ws.input_dir):
            for filename in os.listdir(ws.input_dir):
                file_path = os.path.join(ws.input_dir, filename)
                if os.path.isfile(file_path):
                    try:
                        with open(file_path, "rb") as f:
                            # E2B write is fast; acceptable overhead for consistency
                            sandbox.files.write(filename, f.read())
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
        
        # A. Plot/Image Results
        for result in execution.results:
            if hasattr(result, 'png') and result.png:
                # Use timestamp to prevent overwriting plot.png from previous steps
                timestamp = datetime.now().strftime("%H%M%S")
                img_filename = f"plot_{ws.run_id}_{timestamp}_{len(artifacts_created)}.png"
                ws.write(img_filename, base64.b64decode(result.png))
                artifacts_created.append(img_filename)
                output_log.append(f"\n[SYSTEM: Generated Image saved to '{img_filename}']")

        # B. File System Artifacts
        # We perform a list to see new files.
        try:
            files_in_sandbox = sandbox.files.list(".")
            for remote_file in files_in_sandbox:
                if remote_file.name.startswith(".") or remote_file.name == "__pycache__": continue
                
                # Check if we should download (is it new or updated?)
                # Simplification: Download all non-system files to Output Dir to ensure persistence
                try:
                    file_bytes = sandbox.files.read(remote_file.name, format="bytes")
                    ws.write(remote_file.name, file_bytes)
                    if remote_file.name not in artifacts_created:
                        artifacts_created.append(remote_file.name)
                except Exception: pass
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
    
    # CRITICAL: We DO NOT close/kill the sandbox here. 
    # It remains active for the next tool call in this run.

# ... (Rest of file) ...
```

---

### 2. Update `apps/agent-service/src/main.py`

Add the cleanup endpoint so the Kernel can tell us when to kill the sandbox.

```python
# [[RARO]]/apps/agent-service/src/main.py

# ... existing imports ...
# Add this import
from intelligence.tools import SandboxSession 

# ... app setup ...

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

# ... rest of file ...
```

---

### 3. Update Kernel `apps/kernel-server/src/runtime.rs`

We need to fire the cleanup hook when the workflow enters a terminal state.

First, update `invoke_remote_agent` to be a generic HTTP helper or create a new one. Let's create a specific helper for cleanup.

In `RARORuntime` impl:

```rust
// [[RARO]]/apps/kernel-server/src/runtime.rs

// Add this method to RARORuntime
impl RARORuntime {
    
    // ... existing methods ...

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

    // ...
}
```

Now, update `execute_dynamic_dag` to call this method when finishing.

```rust
// [[RARO]]/apps/kernel-server/src/runtime.rs -> execute_dynamic_dag

// ... inside the loop ...

    // 3. If no next agent, check if we are done
    let agent_id = match next_agent_opt {
        Some(id) => id,
        None => {
            // ... check running count ...
            if running_count > 0 {
                tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                continue;
            } else {
                // Nothing running, nothing ready -> We are done!
                if let Some(mut state) = self.runtime_states.get_mut(&run_id) {
                    state.status = RuntimeStatus::Completed;
                    state.end_time = Some(Utc::now().to_rfc3339());
                }
                self.persist_state(&run_id).await;
                
                // [[NEW]]: Trigger Cleanup
                self.trigger_remote_cleanup(&run_id).await;
                
                tracing::info!("Workflow run {} completed successfully", run_id);
                break;
            }
        }
    };

// ...

// Also handle the failure cases:

    if let Err(e) = payload_res {
        self.fail_run(&run_id, &agent_id, &e).await;
        self.trigger_remote_cleanup(&run_id).await; // [[NEW]]
        continue;
    }

// ... inside match response ...

    if !res.success {
        // ... logging ...
        self.fail_run(&run_id, &agent_id, &error).await;
        self.trigger_remote_cleanup(&run_id).await; // [[NEW]]
    }
```

### Summary of Flows

1.  **Agent 1** calls `execute_python("pip install pandas")`.
2.  **Tools.py** checks Redis. No key found.
3.  **Tools.py** creates E2B Sandbox `sb-123`, stores `sb-123` in Redis (TTL 1hr), runs code. `pandas` is installed.
4.  **Agent 1** finishes. Sandbox `sb-123` remains active in cloud.
5.  **Agent 2** (or Agent 1 second turn) calls `execute_python("import pandas")`.
6.  **Tools.py** checks Redis. Finds `sb-123`.
7.  **Tools.py** reconnects to `sb-123`. The environment is preserved. `import pandas` works.
8.  **Kernel** detects all nodes complete.
9.  **Kernel** sends `DELETE /runtime/{run_id}/cleanup`.
10. **Tools.py** kills `sb-123`.