
#### 1. Inefficient File Sync in E2B (Optimization)
**File:** `apps/agent-service/src/intelligence/tools.py`
**Lines:** ~102-113 (`_run_e2b_sandbox`)

**Issue:**
While the *Sandbox* is persistent (reused), the **File Synchronization** is naive.
The code iterates `ws.input_dir` and uploads **all files** to the sandbox on **every single tool execution**, even if the sandbox was just reused.
```python
if os.path.exists(ws.input_dir):
    for filename in os.listdir(ws.input_dir):
        # ... writes file to sandbox ...
```
**Impact:** If a user attaches a 10MB CSV, and the agent runs 5 steps of python code, that 10MB file is uploaded 5 times to the cloud, adding significant latency.
**Recommendation:** Implement a checksum or a Redis key (e.g., `raro:e2b_files:{run_id}`) to track which files are already present in the sandbox and skip re-uploading them.

#### 2. WebSocket Event Filtering (Visibility)
**File:** `apps/kernel-server/src/server/handlers.rs`
**Lines:** ~380

**Issue:**
The WebSocket handler explicitly filters for **only** `IntermediateLog` events.
```rust
if let crate::events::EventType::IntermediateLog = event.event_type {
    // sends log_event
}
```
**Impact:** The UI `stores.ts` has logic to handle state updates, but if you wanted to visualize specific system events (like `SystemIntervention` or `NodeCreated`) instantly via the event stream (rather than waiting for the 250ms state poll), they are currently being dropped by the Kernel.
**Recommendation:** Broaden the match arm to forward `SystemIntervention` events so the UI "Intervention Overlay" triggers instantly rather than after a polling delay.

#### 3. Redundant Logic in Stores (Cleanup)
**File:** `apps/web-console/src/lib/stores.ts`

**Issue:**
The file contains `loadWorkflowManifest` (Line ~240) and `overwriteGraphFromManifest` (Line ~285).
*   They appear to do almost exactly the same thing (map `manifest.agents` to `agentNodes` and `depends_on` to `pipelineEdges`).
*   `loadWorkflowManifest` calculates X/Y positions.
*   `overwriteGraphFromManifest` also calculates X/Y positions but logs `[ARCHITECT]`.
**Recommendation:** Consolidate these into a single function to prevent divergent behavior when loading plans vs. loading templates.

#### 4. Hardcoded Tool definitions in Prompt (Maintenance)
**File:** `apps/agent-service/src/intelligence/prompts.py` vs `tools.py`

**Issue:**
*   `prompts.py` calls `get_tool_definitions_for_prompt` to inject schemas.
*   `tools.py` defines the schemas manually in a dictionary `registry`.
*   However, `llm.py` logic sometimes manually constructs tool prompts or relies on `render_runtime_system_instruction`.
**Verification:** Ensure `tools.py` registry matches the actual logic in `execute_tool_call`. For example, `execute_tool_call` handles `web_search`, `execute_python`, `read_file`, `write_file`, `list_files`. Ensure the `registry` in `tools.py` exposes all of these so the LLM knows they exist. (The code looks correct here, but it's a common drift point).

---