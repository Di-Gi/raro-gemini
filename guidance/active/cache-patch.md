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