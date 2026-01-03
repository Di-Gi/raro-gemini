# Logic Gaps - Complete Implementation Proposal

## Executive Summary

All infrastructure is **ready and functional**. The gaps are purely **wiring and logic integration** issues. This proposal provides complete implementation details for the three missing flows:

- **Flow A:** Architect (Query → DAG Generation)
- **Flow B:** Tool Execution (Agent → Tool Calls)
- **Flow C:** Safety Patterns (Pattern → Approval Request)

**Effort Estimate:** 3-4 hours total
**Risk Level:** LOW (all infrastructure exists, just needs wiring)

---

## Gap 1: Flow A - Architect (Auto-DAG Generation)

### Current State ✅

**Backend (agent-service):**
- `POST /plan` endpoint implemented ✓
- `ArchitectEngine` generates `WorkflowManifest` from natural language ✓
- Prompts include JSON schema for strict format ✓

**Frontend (web-console):**
- `generateWorkflowPlan(userQuery)` function exists in `api.ts` ✓
- Handles response enrichment (positions, defaults) ✓

**Missing:** UI doesn't call the `/plan` endpoint. It uses hardcoded `initialNodes`.

---

### Solution: Wire Architect Into ControlDeck

#### Step 1: Add "Generate Plan" Mode

Update `apps/web-console/src/components/ControlDeck.svelte`:

```typescript
// Add state for planning mode
let isPlanningMode = $state(false);

// Modify executeRun to check for planning mode
async function executeRun() {
  if (!cmdInput) return;
  if (isSubmitting) return;

  isSubmitting = true;
  addLog('OPERATOR', `<strong>${cmdInput}</strong>`, 'USER_INPUT');

  try {
    // NEW: Check if user wants to generate a plan first
    // For now, always generate plan if nodes haven't been customized
    // Later can add a toggle or button
    const shouldGeneratePlan = true; // or check if nodes are still default

    if (shouldGeneratePlan) {
      addLog('ARCHITECT', 'Generating workflow plan...', 'SYS');

      // Call the Architect
      const manifest = await generateWorkflowPlan(cmdInput);

      // Transform and update stores
      overwriteGraphFromManifest(manifest);

      addLog('ARCHITECT', `Plan generated: ${manifest.agents.length} agents`, 'OK');

      // Give user a moment to review (optional)
      // await new Promise(resolve => setTimeout(resolve, 1000));
    }

    // Now execute with the generated (or existing) graph
    const nodes = get(agentNodes);
    const edges = get(pipelineEdges);

    // ... rest of existing executeRun logic
  } catch (e: any) {
    addLog('KERNEL', `Execution failed: ${e.message}`, 'ERR');
  } finally {
    isSubmitting = false;
  }
}
```

#### Step 2: Add Graph Overwrite Function

Add to `apps/web-console/src/lib/stores.ts`:

```typescript
/**
 * Overwrites the current graph with a new WorkflowManifest from the Architect.
 *
 * This transforms:
 * - manifest.agents[] → agentNodes store
 * - agent.depends_on[] → pipelineEdges store
 */
export function overwriteGraphFromManifest(manifest: WorkflowConfig) {
  console.log('[ARCHITECT] Overwriting graph with manifest:', manifest);

  // 1. Transform agents to AgentNode format
  const newNodes: AgentNode[] = manifest.agents.map((agent, index) => {
    // Extract model name from variant string
    let modelName = agent.model.toUpperCase();
    if (agent.model.includes('flash-lite')) modelName = 'GEMINI-3-PRO';
    else if (agent.model.includes('flash')) modelName = 'GEMINI-3-FLASH';

    // Generate label from ID (capitalize, remove prefixes)
    const label = agent.id.replace(/^(agent_|node_)/i, '').toUpperCase();

    return {
      id: agent.id,
      label: label,
      x: agent.position?.x || (20 + index * 15),  // Default diagonal layout
      y: agent.position?.y || (30 + index * 10),
      model: modelName,
      prompt: agent.prompt,
      status: 'idle' as const,
      role: agent.role
    };
  });

  // 2. Transform dependencies to PipelineEdge format
  const newEdges: PipelineEdge[] = [];

  manifest.agents.forEach(agent => {
    // Each item in depends_on creates an edge FROM parent TO this agent
    agent.depends_on.forEach(parentId => {
      newEdges.push({
        from: parentId,
        to: agent.id,
        active: false,
        finalized: false,
        pulseAnimation: false
      });
    });
  });

  // 3. Overwrite stores
  agentNodes.set(newNodes);
  pipelineEdges.set(newEdges);

  console.log('[ARCHITECT] Graph updated:', { nodes: newNodes.length, edges: newEdges.length });
}
```

#### Step 3: Import and Use in ControlDeck

```typescript
// At top of ControlDeck.svelte
import { ..., overwriteGraphFromManifest } from '$lib/stores'
import { ..., generateWorkflowPlan } from '$lib/api'
```

---

### Testing Flow A

```bash
# 1. Run system
docker-compose up

# 2. Open browser: localhost:5173
# 3. Enter: "Research quantum computing applications"
# 4. Click RUN
# 5. Watch logs:
#    - "Generating workflow plan..."
#    - "Plan generated: 4 agents"
#    - Graph updates with new nodes
#    - "Workflow started..."
```

**Expected Result:**
- Graph nodes change from default (n1, n2, n3, n4) to AI-generated agents
- Dependencies correctly displayed as edges
- Execution runs with generated plan

---

## Gap 2: Flow B - Tool Execution

### Current State ✅

**Backend (agent-service):**
- `call_gemini_with_context()` accepts `tools` parameter ✓
- Tools are commented out with `TODO` note ✓

**Backend (kernel):**
- `AgentNodeConfig` has `tools: Vec<String>` field ✓
- Kernel passes tools to agent-service ✓

**Missing:** Agent-service doesn't enable tool calling, so agents can't execute actions.

---

### Solution: Enable Gemini Tool Calling

#### Step 1: Define Tool Schemas

Add to `apps/agent-service/src/intelligence/tools.py` (NEW FILE):

```python
# [[RARO]]/apps/agent-service/src/intelligence/tools.py
# Purpose: Tool definitions for Gemini Function Calling
# Architecture: Intelligence Layer

from google.genai import types
from typing import List, Dict, Any

def get_tool_declarations(tool_names: List[str]) -> List[types.FunctionDeclaration]:
    """
    Convert tool name strings to Gemini FunctionDeclaration objects.

    Args:
        tool_names: List of tool identifiers (e.g., ['web_search', 'execute_python'])

    Returns:
        List of FunctionDeclaration objects for Gemini API
    """
    tool_registry = {
        'web_search': types.FunctionDeclaration(
            name='web_search',
            description='Search the web for information',
            parameters={
                'type': 'object',
                'properties': {
                    'query': {'type': 'string', 'description': 'Search query'},
                    'num_results': {'type': 'integer', 'description': 'Number of results (1-10)', 'default': 5}
                },
                'required': ['query']
            }
        ),

        'execute_python': types.FunctionDeclaration(
            name='execute_python',
            description='Execute Python code in a sandboxed environment',
            parameters={
                'type': 'object',
                'properties': {
                    'code': {'type': 'string', 'description': 'Python code to execute'},
                },
                'required': ['code']
            }
        ),

        'read_file': types.FunctionDeclaration(
            name='read_file',
            description='Read contents of a file',
            parameters={
                'type': 'object',
                'properties': {
                    'path': {'type': 'string', 'description': 'File path to read'},
                },
                'required': ['path']
            }
        ),

        'write_file': types.FunctionDeclaration(
            name='write_file',
            description='Write content to a file',
            parameters={
                'type': 'object',
                'properties': {
                    'path': {'type': 'string', 'description': 'File path to write'},
                    'content': {'type': 'string', 'description': 'Content to write'},
                },
                'required': ['path', 'content']
            }
        ),
    }

    # Return only the tools requested
    return [tool_registry[name] for name in tool_names if name in tool_registry]


def execute_tool_call(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute a tool call and return the result.

    Args:
        tool_name: Name of the tool to execute
        args: Arguments for the tool

    Returns:
        Dict with 'success' and 'result' or 'error'
    """
    try:
        if tool_name == 'web_search':
            # TODO: Implement actual web search
            # For now, return mock result
            return {
                'success': True,
                'result': f"Mock search results for: {args.get('query')}"
            }

        elif tool_name == 'execute_python':
            # TODO: Implement sandboxed Python execution
            # For now, return mock result
            return {
                'success': True,
                'result': f"Mock: Would execute Python code (disabled for safety)"
            }

        elif tool_name == 'read_file':
            # TODO: Implement with safety checks
            return {
                'success': True,
                'result': f"Mock: Would read file {args.get('path')}"
            }

        elif tool_name == 'write_file':
            # TODO: Implement with safety checks
            return {
                'success': True,
                'result': f"Mock: Would write to {args.get('path')}"
            }

        else:
            return {
                'success': False,
                'error': f"Unknown tool: {tool_name}"
            }

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }
```

#### Step 2: Enable Tool Calling in LLM Module

Update `apps/agent-service/src/core/llm.py`:

```python
# At top, add import
from intelligence.tools import get_tool_declarations, execute_tool_call

# In call_gemini_with_context(), replace the commented tools line:

# OLD:
# tools=tools if tools else None  # TODO: Enable when tool support is implemented

# NEW (around line 140-150):
# 4. Prepare Tools
tool_declarations = None
if tools:
    tool_declarations = get_tool_declarations(tools)
    if tool_declarations:
        logger.info(f"Enabling {len(tool_declarations)} tools for agent {agent_id}")

# 5. Call Gemini 3 API (update API call)
response = await asyncio.to_thread(
    gemini_client.models.generate_content,
    model=model,
    contents=contents,  # type: ignore
    config=config_params,  # type: ignore
    tools=tool_declarations if tool_declarations else None  # ← Enable tools!
)

# 6. Handle Tool Calls in Response
# After extracting response_text, check for function calls
function_calls = []
if hasattr(response, "candidates") and response.candidates:
    candidate = response.candidates[0]
    if hasattr(candidate, "content") and hasattr(candidate.content, "parts"):
        for part in candidate.content.parts:
            if hasattr(part, "function_call"):
                fc = part.function_call
                logger.info(f"Function call: {fc.name}({fc.args})")

                # Execute the tool
                result = execute_tool_call(fc.name, dict(fc.args))
                function_calls.append({
                    'name': fc.name,
                    'args': dict(fc.args),
                    'result': result
                })

# 7. If there were function calls, we need to send results back to LLM
# For MVP, just append tool results to response text
if function_calls:
    tool_results_text = "\n\n[TOOL EXECUTION RESULTS]\n"
    for fc in function_calls:
        tool_results_text += f"- {fc['name']}: {fc['result']}\n"
    response_text += tool_results_text

# 8. Return updated result with tool calls logged
return {
    "text": response_text,
    "input_tokens": input_tokens,
    "output_tokens": output_tokens,
    "thought_signature": thought_signature,
    "cache_hit": cache_hit,
    "tool_calls": function_calls  # ← Add this
}
```

---

### Testing Flow B

```bash
# 1. Update a workflow agent to include tools
# In UI, edit n2 (RETRIEVAL) node:
# Tools: ['web_search']

# 2. Run workflow
# 3. Check logs for:
#    - "Enabling 1 tools for agent n2"
#    - "Function call: web_search(...)"
#    - Tool results in output
```

---

## Gap 3: Flow C - Safety Patterns (Approval Request)

### Current State ✅

**Backend (kernel):**
- `RuntimeStatus::AwaitingApproval` enum exists ✓
- Cortex loop listens for events ✓
- Pattern registry has default safety patterns ✓

**Frontend (web-console):**
- UI checks for `AWAITING_APPROVAL` status ✓
- Intervention overlay is fully implemented ✓
- Approve/Deny buttons call `resumeRun`/`stopRun` ✓

**Missing:** Cortex loop logs warning but doesn't update status.

---

### Solution: Implement Approval Request Action

#### Step 1: Add Status Update Method to Runtime

Update `apps/kernel-server/src/runtime.rs`:

```rust
// Add after fail_run method (around line 595)

/// Update the status of a run (for Cortex interventions)
pub fn update_run_status(&self, run_id: &str, new_status: RuntimeStatus) {
    if let Some(mut state) = self.runtime_states.get_mut(run_id) {
        let old_status = state.status.clone();
        state.status = new_status.clone();
        tracing::info!("Run {} status: {:?} → {:?}", run_id, old_status, new_status);
    }

    // Persist the status change
    tokio::spawn({
        let runtime = self.clone();
        let run_id = run_id.to_string();
        async move {
            runtime.persist_state(&run_id).await;
        }
    });
}
```

#### Step 2: Wire Approval Action in Cortex Loop

Update `apps/kernel-server/src/main.rs` (around line 78):

```rust
// OLD:
crate::registry::PatternAction::RequestApproval { reason } => {
    tracing::warn!("Approval requested: {} (Not yet implemented)", reason);
}

// NEW:
crate::registry::PatternAction::RequestApproval { reason } => {
    tracing::warn!("⚠️  APPROVAL REQUIRED: {}", reason);

    // Update runtime status to pause execution
    runtime_ref.update_run_status(
        &event.run_id,
        crate::models::RuntimeStatus::AwaitingApproval
    );

    // Log for human operator
    tracing::info!(
        "Run {} paused. Waiting for operator approval. Reason: {}",
        event.run_id,
        reason
    );
}
```

#### Step 3: Add Resume Endpoint to Kernel

Update `apps/kernel-server/src/server/handlers.rs`:

```rust
// Add new handler
pub async fn resume_run(
    State(runtime): State<Arc<RARORuntime>>,
    Path(run_id): Path<String>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    tracing::info!("Resume request for run: {}", run_id);

    // Verify run exists and is paused
    let is_paused = runtime
        .get_state(&run_id)
        .map(|s| s.status == crate::models::RuntimeStatus::AwaitingApproval)
        .unwrap_or(false);

    if !is_paused {
        return Err(StatusCode::BAD_REQUEST);
    }

    // Update status back to Running
    runtime.update_run_status(&run_id, crate::models::RuntimeStatus::Running);

    Ok(Json(json!({
        "success": true,
        "message": "Run resumed"
    })))
}

pub async fn stop_run(
    State(runtime): State<Arc<RARORuntime>>,
    Path(run_id): Path<String>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    tracing::info!("Stop request for run: {}", run_id);

    // Fail the run
    runtime.fail_run(&run_id, "OPERATOR", "Stopped by operator").await;

    Ok(Json(json!({
        "success": true,
        "message": "Run stopped"
    })))
}
```

#### Step 4: Register Routes

Update `apps/kernel-server/src/main.rs` (router section):

```rust
// Add after existing routes (around line 105)
.route("/runtime/:run_id/resume", post(handlers::resume_run))
.route("/runtime/:run_id/stop", post(handlers::stop_run))
```

#### Step 5: Update Frontend to Call Real Endpoints

Update `apps/web-console/src/lib/stores.ts`:

```typescript
// Replace placeholder implementations (around line 287-301)

export async function resumeRun(runId: string) {
    if (USE_MOCK) {
        runtimeStore.update(s => ({ ...s, status: 'RUNNING' }));
        addLog('KERNEL', 'Mock: Resuming execution...', 'SYS');
        return;
    }

    try {
        const res = await fetch(`${KERNEL_API}/runtime/${runId}/resume`, {
            method: 'POST'
        });

        if (!res.ok) throw new Error(`Resume failed: ${res.statusText}`);

        addLog('KERNEL', 'Execution resumed by operator', 'SYS');
    } catch (e) {
        console.error('Resume failed:', e);
        addLog('KERNEL', `Resume failed: ${e}`, 'ERR');
    }
}

export async function stopRun(runId: string) {
    if (USE_MOCK) {
        runtimeStore.update(s => ({ ...s, status: 'FAILED' }));
        addLog('KERNEL', 'Mock: Run terminated by operator', 'SYS');
        return;
    }

    try {
        const res = await fetch(`${KERNEL_API}/runtime/${runId}/stop`, {
            method: 'POST'
        });

        if (!res.ok) throw new Error(`Stop failed: ${res.statusText}`);

        addLog('KERNEL', 'Run terminated by operator', 'SYS');
    } catch (e) {
        console.error('Stop failed:', e);
        addLog('KERNEL', `Stop failed: ${e}`, 'ERR');
    }
}
```

Also need to import KERNEL_API:

```typescript
// At top of stores.ts
import { getWebSocketURL, USE_MOCK } from './api';

// Change to:
import { getWebSocketURL, USE_MOCK } from './api';

// Add after existing imports:
const KERNEL_API = import.meta.env.VITE_KERNEL_URL || '/api';
```

#### Step 6: Update Dynamic Loop to Respect Paused Status

Update `apps/kernel-server/src/runtime.rs` (execute_dynamic_dag):

```rust
// In the loop, update the status check (around line 279-285):

// OLD:
let is_failed = self.runtime_states.get(&run_id)
    .map(|s| s.status == RuntimeStatus::Failed)
    .unwrap_or(true);

if is_failed { break; }

// NEW:
let status = self.runtime_states.get(&run_id)
    .map(|s| s.status.clone())
    .unwrap_or(RuntimeStatus::Failed);

match status {
    RuntimeStatus::Failed => {
        tracing::info!("Run {} failed, stopping execution", run_id);
        break;
    }
    RuntimeStatus::AwaitingApproval => {
        // Wait for operator approval
        tracing::debug!("Run {} awaiting approval, sleeping...", run_id);
        tokio::time::sleep(std::time::Duration::from_millis(500)).await;
        continue; // Don't process agents, just wait
    }
    _ => {} // Continue normal execution
}
```

---

### Testing Flow C

```bash
# 1. Trigger a safety pattern
# Option A: Add a default pattern (already exists in registry.rs)
# Option B: Test with delegation that triggers approval

# 2. Run workflow
# 3. Expected behavior:
#    - Agent triggers pattern (e.g., file deletion)
#    - Cortex detects pattern match
#    - Status updates to AwaitingApproval
#    - UI shows intervention overlay
#    - User clicks "AUTHORIZE & RESUME"
#    - Status updates to Running
#    - Execution continues

# 4. Check logs:
docker-compose logs kernel | grep "APPROVAL\|Approval\|AwaitingApproval"
# Should see:
# - "⚠️ Pattern Triggered: guard_fs_delete on Agent n3"
# - "⚠️ APPROVAL REQUIRED: Dangerous file operation detected"
# - "Run {id} status: Running → AwaitingApproval"
# - "Resume request for run: {id}"
# - "Run {id} status: AwaitingApproval → Running"
```

---

## Implementation Checklist

### Flow A (Architect) - Estimated: 1 hour

- [ ] Add `overwriteGraphFromManifest()` to `stores.ts`
- [ ] Import function in `ControlDeck.svelte`
- [ ] Modify `executeRun()` to call `/plan` endpoint
- [ ] Test with "Research quantum computing"
- [ ] Verify graph updates with AI-generated nodes

### Flow B (Tool Execution) - Estimated: 1.5 hours

- [ ] Create `intelligence/tools.py` with tool definitions
- [ ] Update `core/llm.py` to enable tool calling
- [ ] Handle function call responses
- [ ] Test with web_search tool
- [ ] Verify tool results in output

### Flow C (Safety Patterns) - Estimated: 1.5 hours

- [ ] Add `update_run_status()` to `runtime.rs`
- [ ] Wire `RequestApproval` action in Cortex loop
- [ ] Add `resume_run()` and `stop_run()` handlers
- [ ] Register `/resume` and `/stop` routes
- [ ] Update frontend `resumeRun()`/`stopRun()`
- [ ] Update dynamic loop to respect paused status
- [ ] Test approval workflow end-to-end

---

## Testing Strategy

### Integration Test Scenarios

**Scenario 1: Full Architect Flow**
```
1. User: "Build a research pipeline for climate data"
2. System generates 5-agent DAG
3. User reviews graph
4. User clicks RUN
5. Workflow executes with generated plan
```

**Scenario 2: Tool Calling**
```
1. Agent n2 needs web data
2. LLM calls web_search tool
3. Tool executes, returns results
4. LLM continues with tool output
5. Final response includes search results
```

**Scenario 3: Safety Intervention**
```
1. Agent attempts file deletion
2. Pattern triggers: guard_fs_delete
3. Status → AwaitingApproval
4. UI shows intervention overlay
5. User clicks AUTHORIZE
6. Status → Running
7. Execution continues
```

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Architect generates invalid DAG | Medium | High | Add validation before overwriting stores |
| Tool execution security issues | High | Critical | Keep tools mocked until sandboxing implemented |
| Approval deadlock | Low | Medium | Add timeout to auto-deny after N minutes |
| WebSocket doesn't detect status change | Low | Medium | Verify polling interval (250ms should be fine) |

---

## Success Criteria

✅ Flow A working when:
- User input generates new DAG
- Graph visualizes AI-generated nodes
- Execution runs with generated plan

✅ Flow B working when:
- Agent can call tools
- Tool results appear in output
- Execution continues after tool use

✅ Flow C working when:
- Pattern triggers status change
- UI shows intervention overlay
- User can approve/deny
- Execution resumes/stops accordingly

---

## Next Steps After Implementation

1. **Enhance Architect:**
   - Add UI toggle: "Use existing graph" vs "Generate new"
   - Add graph editing capability (add/remove nodes/edges)
   - Save/load workflow templates

2. **Secure Tool Execution:**
   - Implement proper sandboxing (Docker, gVisor, etc.)
   - Add tool permissions system
   - Implement real web search (SerpAPI, Bing, etc.)

3. **Advanced Patterns:**
   - JSONPath condition evaluation
   - Pattern composition (AND/OR logic)
   - Dynamic pattern registration via UI

---

## Files to Create/Modify

### New Files (1):
- `apps/agent-service/src/intelligence/tools.py`

### Modified Files (5):
- `apps/web-console/src/lib/stores.ts` - Add overwriteGraphFromManifest
- `apps/web-console/src/components/ControlDeck.svelte` - Call Architect
- `apps/agent-service/src/core/llm.py` - Enable tool calling
- `apps/kernel-server/src/runtime.rs` - Add update_run_status, update loop
- `apps/kernel-server/src/server/handlers.rs` - Add resume/stop endpoints
- `apps/kernel-server/src/main.rs` - Wire RequestApproval, register routes

**Total Changes:** Surgical updates, low risk, high value.

---
