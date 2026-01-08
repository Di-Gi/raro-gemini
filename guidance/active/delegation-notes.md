This is a solid architectural refinement. It moves delegation from a "global capability" to a **privileged capability** and grounds the agents in the reality of the workflow state.

Here is the implementation plan across the **Kernel (Rust)** and the **Agent Service (Python)**.

### Phase 1: Kernel Updates (The Source of Truth)

We need to update the data models to store the boolean flag and update the runtime to generate the graph views.

#### 1. `apps/kernel-server/src/models.rs`
Add `allow_delegation` to the agent config.

```rust
// ... existing imports

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentNodeConfig {
    pub id: String,
    // ... existing fields ...
    pub prompt: String,
    
    // [[NEW]] Privilege flag
    #[serde(default)] 
    pub allow_delegation: bool, 
    
    // ... existing fields ...
}

// ... existing code ...
```

#### 2. `apps/kernel-server/src/runtime.rs`
Update the payload sent to Python and implement the Graph View generator.

**Update `InvocationPayload` struct:**
```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InvocationPayload {
    // ... existing fields ...
    pub tools: Vec<String>,
    
    // [[NEW]] Context fields
    pub allow_delegation: bool,
    pub graph_view: String, 
}
```

**Implement Graph View Logic (inside `RARORuntime` impl):**
Add this helper method to generate the text representation.

```rust
    fn generate_graph_context(&self, run_id: &str, current_agent_id: &str, detailed: bool) -> String {
        let state = match self.runtime_states.get(run_id) {
            Some(s) => s,
            None => return "Graph state unavailable.".to_string(),
        };

        let dag = match self.dag_store.get(run_id) {
            Some(d) => d,
            None => return "Graph topology unavailable.".to_string(),
        };

        if detailed {
            // OPTION 2: Bird's Eye (JSON Topology + Status)
            // Useful for Orchestrators to see the whole board
            let nodes: Vec<serde_json::Value> = dag.export_nodes().iter().map(|node_id| {
                let status = if state.completed_agents.contains(node_id) { "completed" }
                else if state.failed_agents.contains(node_id) { "failed" }
                else if state.active_agents.contains(node_id) { "running" }
                else { "pending" };
                
                serde_json::json!({
                    "id": node_id,
                    "status": status,
                    "is_you": node_id == current_agent_id,
                    "dependencies": dag.get_dependencies(node_id)
                })
            }).collect();

            return serde_json::to_string_pretty(&nodes).unwrap_or_default();
        } else {
            // OPTION 3: High-Level Linear View
            // [n1:complete] -> [n2:running(you)] -> [n3:pending]
            // We use topological sort to give a linearized sense of time
            match dag.topological_sort() {
                Ok(order) => {
                    let parts: Vec<String> = order.iter().map(|node_id| {
                        let status = if state.completed_agents.contains(node_id) { "COMPLETE" }
                        else if state.failed_agents.contains(node_id) { "FAILED" }
                        else if state.active_agents.contains(node_id) { "RUNNING" }
                        else { "PENDING" };

                        if node_id == current_agent_id {
                            format!("[{}:{}(YOU)]", node_id, status)
                        } else {
                            format!("[{}:{}]", node_id, status)
                        }
                    }).collect();
                    return parts.join(" -> ");
                },
                Err(_) => return "Cycle detected in graph view.".to_string()
            }
        }
    }
```

**Update `prepare_invocation_payload`:**

```rust
    pub async fn prepare_invocation_payload(
        &self,
        run_id: &str,
        agent_id: &str,
    ) -> Result<InvocationPayload, String> {
        // ... (existing context retrieval code) ...

        // [[NEW]] Generate the graph view based on the flag
        // If allow_delegation is true, give them the full detailed map.
        // If false, give them the linear summary.
        let graph_view = self.generate_graph_context(
            run_id, 
            agent_id, 
            agent_config.allow_delegation
        );

        // ... (existing tool logic) ...

        Ok(InvocationPayload {
            // ... existing fields ...
            allow_delegation: agent_config.allow_delegation, // Pass flag
            graph_view, // Pass generated text
            // ... existing fields ...
        })
    }
```

---

### Phase 2: Agent Service Updates (The Executioner)

We need to update the protocol to accept these fields and inject them into the prompt logic.

#### 1. `apps/agent-service/src/domain/protocol.py`

```python
class AgentRequest(BaseModel):
    # ... existing fields ...
    file_paths: List[str] = []
    
    # [[NEW]]
    allow_delegation: bool = False
    graph_view: str = "Context unavailable"
```

#### 2. `apps/agent-service/src/core/llm.py`

Update `_prepare_gemini_request` to handle the conditional injection.

```python
async def _prepare_gemini_request(
    model: str,
    prompt: str,
    agent_id: str,
    # ... other args ...
    # [[NEW ARGUMENTS]]
    allow_delegation: bool = False,
    graph_view: str = "",
) -> Dict[str, Any]:
    
    # 1. Generate System Instruction
    # We pass 'allow_delegation' to the prompt renderer (see next step)
    # or handle the logic right here.
    
    # Let's modify render_runtime_system_instruction to accept the flag? 
    # Or inject strictly here. Let's inject here to keep prompts.py clean.
    
    base_instruction = render_runtime_system_instruction(agent_id, tools)
    
    if allow_delegation:
        # Only inject the capability if explicitly allowed
        base_instruction = inject_delegation_capability(base_instruction)
    
    # 2. Add Graph Awareness to the System Instruction
    graph_context = f"\n\n[OPERATIONAL AWARENESS]\n{graph_view}\n"
    
    # Combine: Base + Graph + Persona
    system_instruction = f"{base_instruction}{graph_context}\n\n[YOUR SPECIALTY]\n{prompt}"

    # ... rest of function ...
```

**Update `call_gemini_with_context` signature:**

```python
async def call_gemini_with_context(
    # ... existing args ...
    # [[NEW]]
    allow_delegation: bool = False,
    graph_view: str = "",
) -> Dict[str, Any]:
    
    # ... inside the function ...
    params = await _prepare_gemini_request(
        concrete_model, prompt, safe_agent_id, user_directive, input_data, file_paths,
        parent_signature, thinking_level, tools,
        # Pass new args
        allow_delegation=allow_delegation,
        graph_view=graph_view
    )
    # ... rest of function ...
```

#### 3. `apps/agent-service/src/main.py`

Update `_execute_agent_logic` to map the request fields to the LLM call.

```python
# ... inside _execute_agent_logic ...

# REMOVE the old logic that injected delegation based solely on model type
# final_prompt = request.prompt
# if "deep-think" not in request.model:
#     final_prompt = inject_delegation_capability(request.prompt)

# Use the flag from the Kernel instead
result = await call_gemini_with_context(
    model=request.model,
    prompt=request.prompt, # Pass raw prompt, injection happens in llm.py
    user_directive=request.user_directive,
    input_data=request.input_data,
    file_paths=request.file_paths,
    parent_signature=request.parent_signature,
    thinking_level=request.thinking_level,
    tools=request.tools,
    agent_id=request.agent_id,
    run_id=request.run_id,
    # [[NEW]]
    allow_delegation=request.allow_delegation,
    graph_view=request.graph_view
)
```

#### 4. `apps/agent-service/src/intelligence/prompts.py`

Update `render_runtime_system_instruction` to handle the simpler high-level view vs detailed view formatting if you want specialized formatting, but putting it directly into the `graph_view` string in Rust is more efficient.

However, we need to ensure `inject_delegation_capability` is imported in `llm.py`.

In `llm.py`:
```python
from intelligence.prompts import render_runtime_system_instruction, inject_delegation_capability
```

### Summary of Behavioral Changes

1.  **Orchestrator (`allow_delegation=true`):**
    *   Sees: `[{"id": "n1", "status": "completed", ...}, ...]` (Detailed JSON)
    *   System Prompt includes: `[SYSTEM CAPABILITY: DYNAMIC DELEGATION] ...`
    *   Can emit `json:delegation`.

2.  **Worker (`allow_delegation=false`):**
    *   Sees: `[n1:COMPLETE] -> [n2:RUNNING(YOU)] -> [n3:PENDING]` (Linear text)
    *   System Prompt **does not** include delegation instructions.
    *   Even if it hallucinates a delegation block, the Kernel won't process it (we can enforce this in `server/handlers.rs` by checking the config before processing the delegation field in the response, strictly speaking, though not strictly required if the prompt is clean).

### Optional: Safety Enforcement (Kernel)

In `apps/kernel-server/src/runtime.rs`, inside `execute_dynamic_dag`:

```rust
// Inside handle result match
if let Some(delegation) = res.delegation {
    // Check if agent was actually allowed to delegate
    let config = self.workflows.get(&state.workflow_id).unwrap()
        .agents.iter().find(|a| a.id == agent_id).unwrap();

    if !config.allow_delegation {
        tracing::warn!("Agent {} attempted delegation without permission. Ignoring.", agent_id);
        // Continue without delegating
    } else {
        // Process delegation...
        match self.handle_delegation(...)
    }
}
```