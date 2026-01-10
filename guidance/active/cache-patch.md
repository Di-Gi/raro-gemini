Here is a complete, step-by-step patch guide to implement the Context Caching plumbing.

**Objective:** Enable the `RemoteAgentResponse` to carry a Cache ID, and wire up the unused methods in `runtime.rs` to store and retrieve this ID, ensuring efficient context reuse across the DAG.

---

### Part 1: Update Data Protocols (The Bridge)

We must first update the data contracts on both the Python (Agent Service) and Rust (Kernel) sides so they can exchange the `cached_content_id`.

#### 1. Update Python Protocol
**File:** `apps/agent-service/src/domain/protocol.py`

**Action:** Add `cached_content_id` to the `AgentResponse` model.

```python
# Find class AgentResponse(BaseModel):
# Add the new field:

class AgentResponse(BaseModel):
    """Result of an agent execution returned to the Kernel."""
    agent_id: str
    success: bool
    tokens_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit: bool = False
    
    # [[ADD THIS FIELD]]
    cached_content_id: Optional[str] = None
    
    latency_ms: float = 0.0
    thought_signature: Optional[str] = None
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    delegation: Optional[DelegationRequest] = None
```

#### 2. Update Python Entry Point
**File:** `apps/agent-service/src/main.py`

**Action:** Update the return statement in `_execute_agent_logic` to map the new field (even if None for now).

```python
# Find the return AgentResponse(...) block inside _execute_agent_logic:

    # 6. Build Response
    return AgentResponse(
        agent_id=request.agent_id,
        success=True,
        output={
            "result": response_text,
            "artifact_stored": artifact_stored,
            "files_generated": files_generated
        },
        delegation=delegation_request,
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        tokens_used=result["input_tokens"] + result["output_tokens"],
        thought_signature=result["thought_signature"],
        cache_hit=result["cache_hit"],
        
        # [[ADD THIS LINE]]
        # Note: logic to populate this from result["cached_content_id"] can be added to llm.py later
        cached_content_id=result.get("cached_content_id"),
        
        latency_ms=latency_ms
    )
```

#### 3. Update Rust Model
**File:** `apps/kernel-server/src/models.rs`

**Action:** Update `RemoteAgentResponse` struct to receive the new field.

```rust
// Find struct RemoteAgentResponse

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
    
    // [[ADD THIS FIELD]]
    pub cached_content_id: Option<String>,
    
    pub latency_ms: f64,
    pub delegation: Option<DelegationRequest>,
}
```

---

### Part 2: Implement Rust Logic (The Plumbing)

Now we wire up the unused methods in `runtime.rs` and `handlers.rs`.

#### 1. Wire up `get_cache_resource`
**File:** `apps/kernel-server/src/runtime.rs`

**Action:** Refactor `prepare_invocation_payload` to use the helper method instead of raw map access.

```rust
// Find the method: pub async fn prepare_invocation_payload
// Look for Step 7: // 7. Get Cached Content ID (if applicable)

// REPLACE this line:
// let cached_content_id = self.cache_resources.get(run_id).map(|c| (*c).clone());

// WITH this line:
let cached_content_id = self.get_cache_resource(run_id);
```

#### 2. Wire up `set_cache_resource`
**File:** `apps/kernel-server/src/runtime.rs`

**Action:** Update `execute_dynamic_dag` to save the cache ID if the agent returns one.

```rust
// Find the method: pub(crate) async fn execute_dynamic_dag
// Look for: // 6. Handle Result & Potential Delegation
// Inside match response -> Ok(res) -> if res.success { ... }

// INSERT this block immediately after "if res.success {":

    if res.success {
        // [[NEW: Context Caching Persistence]]
        // If the agent returned a specific cache ID (either created new or refreshed),
        // update the runtime store so subsequent agents reuse it.
        if let Some(cache_id) = &res.cached_content_id {
            if let Err(e) = self.set_cache_resource(&run_id, cache_id.clone()) {
                tracing::warn!("Failed to update cache resource for run {}: {}", run_id, e);
            } else {
                tracing::debug!("Updated Context Cache for run {}: {}", run_id, cache_id);
            }
        }

        // ... existing delegation logic ...
        if let Some(delegation) = res.delegation {
```

#### 3. Wire up `has_dag`
**File:** `apps/kernel-server/src/server/handlers.rs`

**Action:** Add a fail-fast check in `resume_run` using the `has_dag` method.

```rust
// Find: pub async fn resume_run

pub async fn resume_run(
    State(runtime): State<Arc<RARORuntime>>,
    Path(run_id): Path<String>
) -> StatusCode {
    
    // [[INSERT THIS BLOCK AT THE START]]
    // 0. Fail fast if structural integrity is lost (DAG missing from memory)
    if !runtime.has_dag(&run_id) {
        tracing::error!("Cannot resume run {}: DAG structure missing from memory.", run_id);
        return StatusCode::NOT_FOUND;
    }

    // 1. Verify currently paused (Existing code follows...)
    let is_paused = runtime.get_state(&run_id)
    // ...
```

---

### Verification Checklist

After applying these changes:

1.  **Compile Rust:** Run `cargo check` in `apps/kernel-server`. It should pass without warnings about unused methods for `set_cache_resource`, `get_cache_resource`, or `has_dag`.
2.  **Compile Python:** Ensure `apps/agent-service` starts correctly.
3.  **Logic Flow:**
    *   Kernel calls Agent.
    *   Agent (hypothetically) returns `cached_content_id: "caches/12345"`.
    *   Kernel receives response, calls `set_cache_resource`.
    *   Next Agent invocation calls `get_cache_resource` and sends `caches/12345` in the payload.
    *   Resuming a non-existent run triggers the `has_dag` check and returns 404 immediately.


    To fully operationalize this feature, we need to implement the logic inside the Python Agent Service (`llm.py` and `main.py`) to actually **accept**, **use**, and **return** the `cached_content_id`.

Currently, the ID travels from the Kernel to the Agent Service's API layer (`protocol.py`), but it stops there. The LLM function signature doesn't accept it, so it never gets sent to Google.

Here is the explicit patch guide to close the loop.

---

### Area 1: Core LLM Logic (`llm.py`)

We need to update the internal helper to inject the cache ID into the Gemini configuration, and update the main caller to thread it through.

#### 1. Update `_prepare_gemini_request`
**File:** `apps/agent-service/src/core/llm.py`

**Instruction:** Update the signature to accept `cached_content_id` and inject it into `config_params`.

```python
# FIND the definition of _prepare_gemini_request
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
    # [[ADD THIS ARGUMENT]]
    cached_content_id: Optional[str] = None,
    allow_delegation: bool = False,
    graph_view: str = "",
) -> Dict[str, Any]:

    # ... [keep existing logic for base_instruction, graph_context, etc.] ...

    # FIND the config_params dictionary definition
    # UPDATE it to include cached_content
    config_params: Dict[str, Any] = {
        "temperature": 1.0,
        "system_instruction": system_instruction,
    }

    # [[ADD THIS BLOCK immediately after config_params definition]]
    if cached_content_id:
        config_params["cached_content"] = cached_content_id
        logger.info(f"Using Gemini Context Cache: {cached_content_id}")

    # ... [rest of the function remains the same] ...
```

#### 2. Update `call_gemini_with_context`
**File:** `apps/agent-service/src/core/llm.py`

**Instruction:** Update the signature to accept `cached_content_id` and ensure it is returned in the result dictionary.

```python
# FIND the definition of call_gemini_with_context
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
    # [[ADD THIS ARGUMENT]]
    cached_content_id: Optional[str] = None,
    allow_delegation: bool = False,
    graph_view: str = "",
) -> Dict[str, Any]:

    # ... [logging logic] ...

    try:
        # UPDATE the call to _prepare_gemini_request
        params = await _prepare_gemini_request(
            concrete_model, prompt, safe_agent_id, user_directive, input_data, file_paths,
            parent_signature, thinking_level, tools,
            # [[PASS THE NEW ARGUMENT]]
            cached_content_id=cached_content_id,
            allow_delegation=allow_delegation,
            graph_view=graph_view
        )

        # ... [probe_sink logic] ...
        # ... [execution loop logic] ...

        # FIND the return statement at the bottom of the function
        # UPDATE it to include cached_content_id
        return {
            "text": final_response_text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "thought_signature": thought_signature,
            "cache_hit": cache_hit,
            "files_generated": all_files_generated,
            # [[ADD THIS KEY]]
            "cached_content_id": cached_content_id 
        }
```

---

### Area 2: Service Entry Point (`main.py`)

Now we need to pass the ID from the incoming HTTP/WebSocket request (where it's already defined in `AgentRequest`) into the updated LLM function.

#### 1. Update `_execute_agent_logic`
**File:** `apps/agent-service/src/main.py`

**Instruction:** Pass the `cached_content_id` from the request object into `call_gemini_with_context`.

```python
# FIND the _execute_agent_logic function
async def _execute_agent_logic(request: AgentRequest) -> AgentResponse:
    
    # ... [setup logic] ...

    try:
        # 1. Call Unified LLM Module
        result = await call_gemini_with_context(
            model=request.model,
            prompt=request.prompt,
            user_directive=request.user_directive,
            input_data=request.input_data,
            file_paths=request.file_paths,
            parent_signature=request.parent_signature,
            thinking_level=request.thinking_level,
            tools=request.tools,
            agent_id=request.agent_id,
            run_id=request.run_id,
            # [[ADD THIS LINE]]
            cached_content_id=request.cached_content_id,
            
            allow_delegation=request.allow_delegation,
            graph_view=request.graph_view,
        )

        # ... [rest of the function] ...
```

---

### Summary of What This Achieves

1.  **Kernel (`runtime.rs`)**: Retrieves the Cache ID using `get_cache_resource` and sends it to the Agent Service.
2.  **Agent Service (`main.py`)**: Receives the ID in `AgentRequest` and passes it to `call_gemini_with_context`.
3.  **LLM Core (`llm.py`)**: Injects `cached_content` into the Gemini `config`.
    *   *Result*: Gemini skips processing the input files again (tokens = 0 for context), saving money and time.
4.  **Return Trip**: `llm.py` returns the used ID -> `main.py` packs it into `AgentResponse` -> Kernel receives it and calls `set_cache_resource` (updating the TTL or confirming existence).

### Are there any other areas left?

**Yes, one advanced optimization (Optional Phase 2):**

Currently, this setup assumes **consumption** (using a cache if provided). It does not yet automatically **create** a cache if one is missing but files are large.

To implement *Creation*, you would need logic in `llm.py` inside `call_gemini_with_context`:
1.  Check `if not cached_content_id and input_tokens > 32000`.
2.  If true, call `gemini_client.caches.create(...)` with the file content.
3.  Use the new ID for the current request.
4.  Return the *new* ID so the Kernel can store it via `set_cache_resource`.

However, the patches provided above are sufficient to enable the **infrastructure** for caching. You can manually create a cache ID and pass it in, or add the auto-creation logic later without changing the Kernel or Protocol.


This patch implements **Flow D (Automatic Context Persistence)**.

Currently, if you attach large files (PDFs, Data Dumps) to a workflow, every agent re-uploads them, wasting tokens and bandwidth. This patch modifies the Agent Service to:
1.  **Detect** if the context files exceed the caching threshold (approx. 32k tokens).
2.  **Automatically Create** a Gemini Context Cache if one doesn't exist.
3.  **Return** the new Cache ID to the Kernel so subsequent agents use it for free.

---

### Step 1: Update Imports
**File:** `apps/agent-service/src/core/llm.py`

**Action:** Add `timedelta` to imports to define the Cache Time-To-Live (TTL).

```python
# apps/agent-service/src/core/llm.py

# ... existing imports ...
import httpx
import re
from pathlib import Path
from datetime import datetime, timedelta  # <--- ADD timedelta
from google.genai import types
# ...
```

---

### Step 2: Implement Auto-Caching Logic
**File:** `apps/agent-service/src/core/llm.py`

**Action:** Completely replace the `_prepare_gemini_request` function. This version adds the logic to measure context size and dynamically create a cache if needed.

> **Note:** This replaces the version from the previous step. It handles both *consuming* an ID and *creating* one.

```python
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
    cached_content_id: Optional[str] = None,
    allow_delegation: bool = False,
    graph_view: str = "",
) -> Dict[str, Any]:
    """
    Internal helper to build contents, config, and manage Context Caching.
    """

    # 1. Generate Base System Instruction
    base_instruction = render_runtime_system_instruction(agent_id, tools)

    if allow_delegation:
        from intelligence.prompts import inject_delegation_capability
        base_instruction = inject_delegation_capability(base_instruction)

    graph_context = f"\n\n[OPERATIONAL AWARENESS]\n{graph_view}\n" if graph_view else ""
    system_instruction = f"{base_instruction}{graph_context}\n\n[YOUR SPECIALTY]\n{prompt}"

    # 2. Build Generation Config
    config_params: Dict[str, Any] = {
        "temperature": 1.0,
        "system_instruction": system_instruction,
    }

    # Add Deep Think configuration
    if "deep-think" in model and thinking_level:
        thinking_budget = min(max(thinking_level * 1000, 1000), 32000)
        config_params["thinking_config"] = types.ThinkingConfig(
            include_thoughts=True,
            thinking_budget=thinking_budget
        )

    # 3. Handle File Context & Caching
    # Logic: If ID exists, use it. If not AND files are huge, create it.
    
    active_cache_id = cached_content_id
    user_parts: List[Dict[str, Any]] = []

    if file_paths:
        if active_cache_id:
            # A: Cache Exists - Skip file loading to save tokens/bandwidth
            logger.info(f"Using existing Context Cache: {active_cache_id} (Skipping file upload)")
        else:
            # B: No Cache - Load files and check size
            loaded_parts = []
            total_est_chars = 0
            
            for file_path in file_paths:
                try:
                    file_part = await load_multimodal_file(file_path)
                    loaded_parts.append(file_part)
                    
                    # Heuristic size estimation
                    if "text" in file_part:
                        total_est_chars += len(file_part["text"])
                    elif "inline_data" in file_part:
                        # Base64 char count is rough proxy for binary complexity
                        total_est_chars += len(file_part["inline_data"]["data"])
                except Exception as e:
                    logger.error(f"Failed to load file {file_path}: {e}")
                    user_parts.append({"text": f"[ERROR: Failed to load {file_path}]"})

            # Threshold: ~32k tokens. 1 token ~= 4 chars. 32000 * 4 = 128,000 chars.
            # We use 100,000 chars as a safe lower bound to trigger caching.
            CACHE_THRESHOLD_CHARS = 100_000

            if total_est_chars > CACHE_THRESHOLD_CHARS:
                logger.info(f"Context size ({total_est_chars} chars) exceeds threshold. Creating Cache...")
                try:
                    # Create cache on specific model (must be base model, e.g. gemini-1.5-flash-002)
                    # We strip 'deep-think' or aliases to ensure compatibility if needed, 
                    # but usually 'model' passed here is already resolved.
                    
                    cache_content = await asyncio.to_thread(
                        gemini_client.caches.create,
                        model=model,
                        config={
                            "contents": [{"role": "user", "parts": loaded_parts}],
                            "ttl": "3600s" # 1 Hour TTL
                        }
                    )
                    active_cache_id = cache_content.name
                    logger.info(f"✓ Cache Created: {active_cache_id}")
                    
                    # Do NOT add loaded_parts to user_parts (they are now in the cache)
                except Exception as e:
                    logger.error(f"Cache creation failed: {e}. Falling back to standard payload.")
                    user_parts.extend(loaded_parts)
            else:
                # Small context - just inject directly
                user_parts.extend(loaded_parts)

    # 4. Inject Cache Config if active
    if active_cache_id:
        config_params["cached_content"] = active_cache_id

    # 5. Build Remaining User Message (Directive + Context)
    
    # Parent Signature
    contents: List[Dict[str, Any]] = []
    if parent_signature:
        contents.append({
            "role": "user",
            "parts": [{"text": f"[CONTEXT CONTINUITY]\nPrevious Agent Signature: {parent_signature}"}]
        })
        contents.append({
            "role": "model",
            "parts": [{"text": "Context accepted."}]
        })

    # User Directive
    if user_directive:
        user_parts.append({"text": f"[OPERATOR DIRECTIVE]\n{user_directive}\n\n"})

    # Context Data
    if input_data:
        context_str = json.dumps(input_data, indent=2)
        user_parts.append({"text": f"[CONTEXT DATA]\n{context_str}\n\n"})

    # Safety for empty parts
    if not user_parts:
        user_parts.append({"text": "[SYSTEM] Ready. Execute based on system instructions."})

    contents.append({
        "role": "user",
        "parts": user_parts
    })

    return {
        "model": model,
        "contents": contents,
        "config": config_params,
        "active_cache_id": active_cache_id # Return this so we can persist it
    }
```

---

### Step 3: Extract New ID in Caller
**File:** `apps/agent-service/src/core/llm.py`

**Action:** Update `call_gemini_with_context` to retrieve the `active_cache_id` from the helper's result and return it.

```python
# In call_gemini_with_context...

    # ...
    params = await _prepare_gemini_request(
        concrete_model, prompt, safe_agent_id, user_directive, input_data, file_paths,
        parent_signature, thinking_level, tools,
        cached_content_id=cached_content_id, # Passed from argument
        allow_delegation=allow_delegation,
        graph_view=graph_view
    )
    
    # [[NEW]] Capture the potentially created ID
    final_cache_id = params.get("active_cache_id")

    # ... [Probe Sink Logic] ...
    # ... [Execution Loop] ...

    # At the very bottom return statement:
    return {
        "text": final_response_text,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "thought_signature": thought_signature,
        "cache_hit": cache_hit,
        "files_generated": all_files_generated,
        
        # [[UPDATE THIS LINE]]
        "cached_content_id": final_cache_id 
    }
```

### Verification

1.  **Scenario A (Small Context):** Attach a small text file (< 100kb).
    *   System log should show "Small context - just inject directly".
    *   `cached_content_id` returned is `None`.
    *   Kernel stores nothing.

2.  **Scenario B (Large Context):** Attach a large PDF or huge CSV (> 150kb).
    *   Agent 1 runs.
    *   Log: "Context size (...) exceeds threshold. Creating Cache..."
    *   Log: "✓ Cache Created: caches/..."
    *   Agent returns `cached_content_id` -> Kernel calls `set_cache_resource`.
    *   Agent 2 runs.
    *   Kernel calls `get_cache_resource` and sends ID to Agent 2.
    *   Agent 2 Log: "Using existing Context Cache: caches/... (Skipping file upload)".
    *   **Result:** Agent 2 starts almost instantly and costs very few input tokens.