# Privileged Delegation: Implementation Guide

## Context & Motivation

Currently, delegation is a **global capability** injected into all non-deep-think agents. This creates two problems:

1. **Security Risk:** Worker agents that should only execute focused tasks can spawn arbitrary sub-agents
2. **Cognitive Overhead:** Workers receive delegation instructions they'll never use, wasting context

This patch moves delegation from a global capability to a **privileged capability** controlled by an `allow_delegation` boolean flag on each agent.

### Behavioral Changes

**Before:**
- All non-deep-think agents receive delegation capability
- No awareness of graph structure or workflow state

**After:**
- Only agents with `allow_delegation: true` can delegate
- Privileged agents receive detailed JSON graph view
- Workers receive simplified linear progress view
- All agents gain operational awareness of their position in the workflow

---

## Architecture Overview

The implementation spans two services:

1. **Kernel (Rust):** Authoritative source of truth
   - Stores `allow_delegation` flag in agent config
   - Generates graph views based on privilege level
   - Enforces delegation permissions

2. **Agent Service (Python):** Execution layer
   - Accepts `allow_delegation` and `graph_view` fields
   - Conditionally injects delegation capability into prompts
   - Presents contextual graph awareness to agents

---

## Phase 1: Kernel Updates (Rust)

### Step 1: Update Agent Config Model

**File:** `apps/kernel-server/src/models.rs`

**Location:** Inside `AgentNodeConfig` struct (around line 38-58)

**Change:** Add the `allow_delegation` field after the `accepts_directive` field.

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentNodeConfig {
    pub id: String,
    pub role: AgentRole,
    pub model: ModelVariant,
    pub tools: Vec<String>,
    #[serde(default)]
    pub input_schema: serde_json::Value,
    #[serde(default)]
    pub output_schema: serde_json::Value,
    #[serde(default = "default_cache_policy")]
    pub cache_policy: String,
    #[serde(default)]
    pub depends_on: Vec<String>,
    pub prompt: String,
    pub position: Option<Position>,
    #[serde(default)]
    pub accepts_directive: bool,
    #[serde(default)]
    pub user_directive: String,

    // [[NEW FIELD]]
    #[serde(default)]
    pub allow_delegation: bool,
}
```

**Why `#[serde(default)]`?**
This ensures backward compatibility. Existing workflow manifests without this field will default to `false` (no delegation).

---

### Step 2: Update Invocation Payload

**File:** `apps/kernel-server/src/runtime.rs`

**Location:** Find the `InvocationPayload` struct (around line 21-33)

**Change:** Add two new fields to pass graph context to Python.

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InvocationPayload {
    pub run_id: String,
    pub agent_id: String,
    pub model: String,
    pub prompt: String,
    pub user_directive: String,
    pub input_data: serde_json::Value,
    pub parent_signature: Option<String>,
    pub cached_content_id: Option<String>,
    pub thinking_level: Option<i32>,
    pub file_paths: Vec<String>,
    pub tools: Vec<String>,

    // [[NEW FIELDS]]
    pub allow_delegation: bool,
    pub graph_view: String,
}
```

---

### Step 3: Implement Graph View Generator

**File:** `apps/kernel-server/src/runtime.rs`

**Location:** Inside the `impl RARORuntime` block, add this new method after existing helpers (suggested location: after line 700, before `prepare_invocation_payload`)

**Purpose:** Generate different graph views based on delegation privilege:
- **Detailed View (for Orchestrators):** Full JSON with all nodes, statuses, and dependencies
- **Linear View (for Workers):** Simple text progress indicator

```rust
    /// Generate a contextual graph view based on agent's delegation privilege.
    ///
    /// - **detailed=true**: Returns JSON array with full topology (for orchestrators)
    /// - **detailed=false**: Returns linear text view (for workers)
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
            // DETAILED VIEW: JSON topology for orchestrators
            // Useful for making informed delegation decisions
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
            // LINEAR VIEW: High-level progress indicator for workers
            // Shows position in pipeline: [n1:COMPLETE] -> [n2:RUNNING(YOU)] -> [n3:PENDING]
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

---

### Step 4: Update Payload Preparation

**File:** `apps/kernel-server/src/runtime.rs`

**Location:** Inside `prepare_invocation_payload` function (around line 950-962, where the payload is constructed)

**Change:** Generate the graph view and pass the delegation flag.

**Find this section:**
```rust
        // 12. Return Payload
        Ok(InvocationPayload {
            run_id: run_id.to_string(),
            agent_id: agent_id.to_string(),
            model: model_string,
            prompt: final_prompt,
            user_directive: agent_config.user_directive.clone(),
            input_data: serde_json::Value::Object(input_data_map),
            parent_signature,
            cached_content_id,
            thinking_level,
            file_paths: full_file_paths,
            tools,
        })
```

**Replace with:**
```rust
        // 11. Generate Graph Context (NEW)
        // Give orchestrators detailed JSON, workers a simple linear view
        let graph_view = self.generate_graph_context(
            run_id,
            agent_id,
            agent_config.allow_delegation
        );

        // 12. Return Payload
        Ok(InvocationPayload {
            run_id: run_id.to_string(),
            agent_id: agent_id.to_string(),
            model: model_string,
            prompt: final_prompt,
            user_directive: agent_config.user_directive.clone(),
            input_data: serde_json::Value::Object(input_data_map),
            parent_signature,
            cached_content_id,
            thinking_level,
            file_paths: full_file_paths,
            tools,

            // [[NEW FIELDS]]
            allow_delegation: agent_config.allow_delegation,
            graph_view,
        })
```

---

### Step 5 (Optional): Enforce Delegation Security

**File:** `apps/kernel-server/src/runtime.rs`

**Location:** Inside `execute_dynamic_dag` function, where delegation responses are processed (search for `if let Some(delegation) = res.delegation`)

**Purpose:** Prevent agents without permission from delegating (defense in depth)

**Add this validation:**
```rust
// Inside the delegation handling block
if let Some(delegation) = res.delegation {
    // Verify agent has delegation privilege
    let config = self.workflows.get(&state.workflow_id)
        .ok_or_else(|| "Workflow not found".to_string())?
        .agents.iter()
        .find(|a| a.id == agent_id)
        .ok_or_else(|| format!("Agent {} config not found", agent_id))?;

    if !config.allow_delegation {
        tracing::warn!("Agent {} attempted delegation without permission. Ignoring.", agent_id);
        // Continue without processing delegation
    } else {
        // Process delegation normally
        match self.handle_delegation(run_id, agent_id, delegation).await {
            // ... existing delegation logic
        }
    }
}
```

---

## Phase 2: Agent Service Updates (Python)

### Step 6: Update Protocol Contract

**File:** `apps/agent-service/src/domain/protocol.py`

**Location:** Inside the `AgentRequest` class (around line 88-110)

**Change:** Add the two new fields that the Kernel will send.

```python
class AgentRequest(BaseModel):
    """Request from the Kernel to execute a specific agent node."""
    agent_id: str
    model: str
    prompt: str
    input_data: Dict[str, Any]
    run_id: str
    user_directive: str = ""
    tools: List[str] = []
    thought_signature: Optional[str] = None
    parent_signature: Optional[str] = None
    cached_content_id: Optional[str] = None
    thinking_level: Optional[int] = None
    file_paths: List[str] = []

    # [[NEW FIELDS]]
    allow_delegation: bool = False
    graph_view: str = "Context unavailable"
```

---

### Step 7: Update LLM Preparation Logic

**File:** `apps/agent-service/src/core/llm.py`

**Location:** Update the `_prepare_gemini_request` function signature and implementation

**Change 1:** Add new parameters to function signature (around line 77)

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
    # [[NEW PARAMETERS]]
    allow_delegation: bool = False,
    graph_view: str = "",
) -> Dict[str, Any]:
```

**Change 2:** Update system instruction building logic (around line 100-103)

**Find:**
```python
    # 1. Generate System Instruction (Base RARO Rules + Tool Protocols)
    base_instruction = render_runtime_system_instruction(agent_id, tools)

    # 2. Layer in the Node's Assigned Persona
    system_instruction = f"{base_instruction}\n\n[YOUR SPECIALTY]\n{prompt}"
```

**Replace with:**
```python
    # 1. Generate Base System Instruction
    base_instruction = render_runtime_system_instruction(agent_id, tools)

    # 2. Conditionally Inject Delegation Capability
    if allow_delegation:
        from intelligence.prompts import inject_delegation_capability
        base_instruction = inject_delegation_capability(base_instruction)
        logger.debug(f"Delegation capability granted to {agent_id}")

    # 3. Add Graph Awareness
    graph_context = f"\n\n[OPERATIONAL AWARENESS]\n{graph_view}\n"

    # 4. Combine: Base + Graph + Persona
    system_instruction = f"{base_instruction}{graph_context}\n\n[YOUR SPECIALTY]\n{prompt}"
```

**Change 3:** Update `call_gemini_with_context` signature (search for the function definition, around line 200)

**Find:**
```python
async def call_gemini_with_context(
    model: str,
    prompt: str,
    agent_id: str = "default_agent",
    user_directive: str = "",
    input_data: Optional[Dict[str, Any]] = None,
    file_paths: Optional[List[str]] = None,
    parent_signature: Optional[str] = None,
    thinking_level: Optional[int] = None,
    tools: Optional[List[str]] = None,
) -> Dict[str, Any]:
```

**Add parameters:**
```python
async def call_gemini_with_context(
    model: str,
    prompt: str,
    agent_id: str = "default_agent",
    user_directive: str = "",
    input_data: Optional[Dict[str, Any]] = None,
    file_paths: Optional[List[str]] = None,
    parent_signature: Optional[str] = None,
    thinking_level: Optional[int] = None,
    tools: Optional[List[str]] = None,
    # [[NEW PARAMETERS]]
    allow_delegation: bool = False,
    graph_view: str = "",
) -> Dict[str, Any]:
```

**Change 4:** Pass parameters to `_prepare_gemini_request` (inside the same function, search for the call)

**Find:**
```python
    params = await _prepare_gemini_request(
        concrete_model, prompt, safe_agent_id, user_directive, input_data, file_paths,
        parent_signature, thinking_level, tools
    )
```

**Replace with:**
```python
    params = await _prepare_gemini_request(
        concrete_model, prompt, safe_agent_id, user_directive, input_data, file_paths,
        parent_signature, thinking_level, tools,
        # Pass new parameters
        allow_delegation=allow_delegation,
        graph_view=graph_view
    )
```

---

### Step 8: Update Execution Handler

**File:** `apps/agent-service/src/main.py`

**Location:** Inside the `_execute_agent_logic` function (around line 270-280)

**Change:** Remove old model-based delegation logic and use the flag from the Kernel.

**Find:**
```python
        # 1. Prompt Enhancement (Flow B Support)
        # For non-deep-think models, inject delegation capability
        final_prompt = request.prompt
        if "deep-think" not in request.model:
            final_prompt = inject_delegation_capability(request.prompt)
            logger.debug(f"Delegation capability injected for agent {request.agent_id}")

        # 2. Call Unified LLM Module
        result = await call_gemini_with_context(
            model=request.model,
            prompt=final_prompt,
            user_directive=request.user_directive,
            input_data=request.input_data,
            file_paths=request.file_paths,
            parent_signature=request.parent_signature,
            thinking_level=request.thinking_level,
            tools=request.tools,
            agent_id=request.agent_id,
        )
```

**Replace with:**
```python
        # 1. Call Unified LLM Module
        # NOTE: Delegation injection now happens in llm.py based on allow_delegation flag
        result = await call_gemini_with_context(
            model=request.model,
            prompt=request.prompt,  # Pass raw prompt, injection handled conditionally
            user_directive=request.user_directive,
            input_data=request.input_data,
            file_paths=request.file_paths,
            parent_signature=request.parent_signature,
            thinking_level=request.thinking_level,
            tools=request.tools,
            agent_id=request.agent_id,
            # [[NEW PARAMETERS FROM KERNEL]]
            allow_delegation=request.allow_delegation,
            graph_view=request.graph_view,
        )
```

**Also remove the import** at the top of the file (around line 21):
```python
# REMOVE THIS LINE:
from intelligence.prompts import inject_delegation_capability
```

---

## Phase 3: Frontend Updates (Optional)

### Step 9: Update UI Agent Config

**File:** `apps/web-console/src/lib/stores.ts`

**Location:** Inside the `AgentNode` interface (around line 21-31)

**Change:** Add the `allow_delegation` field to the UI model.

```typescript
export interface AgentNode {
  id: string;
  label: string;
  x: number;
  y: number;
  model: string;
  prompt: string;
  status: 'idle' | 'running' | 'complete' | 'failed';
  role: 'orchestrator' | 'worker' | 'observer';
  acceptsDirective: boolean;
  // [[NEW FIELD]]
  allowDelegation: boolean;
}
```

**Location:** Update the `submitRun` function in `apps/web-console/src/components/ControlDeck.svelte` to include the field (around line 116-136)

**Find:**
```typescript
            return {
                id: n.id,
                role: n.role,
                model: n.model,
                tools: [],
                input_schema: {},
                output_schema: {},
                cache_policy: 'ephemeral',
                depends_on: dependsOn,
                prompt: n.prompt,
                user_directive: (n.acceptsDirective && cmdInput) ? cmdInput : "",
                position: { x: n.x, y: n.y },
                accepts_directive: n.acceptsDirective
            };
```

**Add the field:**
```typescript
            return {
                id: n.id,
                role: n.role,
                model: n.model,
                tools: [],
                input_schema: {},
                output_schema: {},
                cache_policy: 'ephemeral',
                depends_on: dependsOn,
                prompt: n.prompt,
                user_directive: (n.acceptsDirective && cmdInput) ? cmdInput : "",
                position: { x: n.x, y: n.y },
                accepts_directive: n.acceptsDirective,
                // [[NEW FIELD]]
                allow_delegation: n.allowDelegation
            };
```

---

## Testing Strategy

### Unit Tests

1. **Kernel Graph View Generation**
   ```rust
   #[test]
   fn test_generate_graph_context_detailed() {
       // Verify JSON structure for orchestrators
   }

   #[test]
   fn test_generate_graph_context_linear() {
       // Verify linear text for workers
   }
   ```

2. **Python Prompt Injection**
   ```python
   def test_delegation_injection_conditional():
       # Verify delegation only injected when allow_delegation=True
   ```

### Integration Tests

1. **Orchestrator Delegation Flow**
   - Create workflow with `allow_delegation: true` on orchestrator
   - Verify orchestrator receives detailed JSON graph
   - Verify delegation capability in system prompt
   - Trigger delegation, confirm sub-agents spawn

2. **Worker Restriction**
   - Create workflow with `allow_delegation: false` on worker
   - Verify worker receives linear graph view
   - Verify NO delegation capability in system prompt
   - Attempt delegation output, confirm it's ignored

3. **Mixed Graph**
   - Workflow with both orchestrator and workers
   - Verify each receives appropriate graph view
   - Verify only orchestrator can successfully delegate

---

## Migration Path

### For Existing Workflows

All existing workflow definitions will continue to work because `allow_delegation` defaults to `false` via `#[serde(default)]`.

To grant delegation privileges:

```json
{
  "agents": [
    {
      "id": "orchestrator",
      "role": "orchestrator",
      "allow_delegation": true,  // ← Add this line
      "model": "reasoning",
      "prompt": "Analyze and decompose complex tasks..."
    },
    {
      "id": "worker_1",
      "role": "worker",
      // allow_delegation omitted → defaults to false
      "model": "fast",
      "prompt": "Execute focused retrieval task..."
    }
  ]
}
```

### Recommended Initial Settings

- **Orchestrators:** `allow_delegation: true`
- **Workers:** `allow_delegation: false` (default)
- **Observers:** `allow_delegation: false` (default)

---

## Summary of Changes

### Rust (Kernel)
- ✅ Add `allow_delegation: bool` to `AgentNodeConfig`
- ✅ Add `allow_delegation` and `graph_view` to `InvocationPayload`
- ✅ Implement `generate_graph_context()` method
- ✅ Update `prepare_invocation_payload()` to generate and pass context
- ✅ (Optional) Add delegation permission enforcement

### Python (Agent Service)
- ✅ Add `allow_delegation` and `graph_view` to `AgentRequest`
- ✅ Update `_prepare_gemini_request()` signature and logic
- ✅ Update `call_gemini_with_context()` signature
- ✅ Remove model-based delegation logic from `main.py`
- ✅ Conditionally inject delegation in `llm.py`

### TypeScript (Web Console)
- ✅ Add `allowDelegation` to `AgentNode` interface
- ✅ Update `submitRun()` to include field in payload

---

## Expected Outcomes

### Security
- Prevents unauthorized delegation by worker agents
- Clear audit trail of which agents can modify graph

### Performance
- Reduces context size for workers (no delegation instructions)
- Targeted graph information based on need

### Clarity
- All agents understand their position in workflow
- Orchestrators get strategic view
- Workers get tactical progress indicator

### Behavioral Examples

**Orchestrator (allow_delegation: true):**
```
[OPERATIONAL AWARENESS]
[
  {"id": "orchestrator", "status": "running", "is_you": true, "dependencies": []},
  {"id": "retrieval", "status": "pending", "is_you": false, "dependencies": ["orchestrator"]},
  {"id": "synthesis", "status": "pending", "is_you": false, "dependencies": ["retrieval"]}
]

[SYSTEM CAPABILITY: DYNAMIC DELEGATION]
If the task is too complex, you can spawn sub-agents...
```

**Worker (allow_delegation: false):**
```
[OPERATIONAL AWARENESS]
[orchestrator:COMPLETE] -> [retrieval:RUNNING(YOU)] -> [synthesis:PENDING]
```
