Here are the specific, targeted solutions for the issues identified in the investigation. These patches address the **Ghost Graph**, **Context Amnesia**, **Role Blurring**, and **UI Accessibility**.

---

### 1. Fix: The "Ghost Graph" (Topology Corruption)
**Component:** `kernel-server`
**File:** `apps/kernel-server/src/runtime.rs`

**The Problem:** When an agent delegates a task and reuses an ID (like `web_searcher`) that already exists in the static plan, the Kernel blindly renames the new node (`web_searcher_cba...`) but leaves the original, empty `web_searcher` node in the DAG. This creates "dead" nodes in the topology log.

**The Solution:** Modify `handle_delegation` to check if the colliding node is in a `Pending` state. If so, **overwrite/adopt** it instead of creating a ghost duplicate.

```rust
// In apps/kernel-server/src/runtime.rs -> handle_delegation

// ... inside the function, replace the Collision Logic block ...

let mut id_map: HashMap<String, String> = HashMap::new();

for node in &mut req.new_nodes {
    if existing_node_ids.contains(&node.id) {
        // CHECK STATUS OF EXISTING NODE
        let is_pending = if let Some(state) = self.runtime_states.get(run_id) {
            !state.active_agents.contains(&node.id) && 
            !state.completed_agents.contains(&node.id) && 
            !state.failed_agents.contains(&node.id)
        } else {
            false
        };

        if is_pending {
            tracing::info!("Delegation: Adopting existing pending node '{}' instead of renaming.", node.id);
            // We do NOT rename. We essentially update the definition of the existing node 
            // by pushing it to the workflow config below.
            // However, we must remove the OLD definition from workflow.agents first to avoid duplicates there.
            if let Some(mut workflow) = self.workflows.get_mut(&workflow_id) {
                if let Some(pos) = workflow.agents.iter().position(|a| a.id == node.id) {
                    workflow.agents.remove(pos);
                }
            }
        } else {
            // Node is already running/done, we MUST rename to avoid history corruption
            let old_id = node.id.clone();
            let suffix = Uuid::new_v4().to_string().split('-').next().unwrap().to_string();
            let new_id = format!("{}_{}", old_id, suffix);
            tracing::warn!("Delegation ID Collision: Renaming '{}' to '{}'", old_id, new_id);
            
            node.id = new_id.clone();
            id_map.insert(old_id, new_id);
        }
    }
}
```

---

### 2. Fix: Context Amnesia (Missing Tool Outputs)
**Component:** `agent-service`
**File:** `apps/agent-service/src/core/llm.py`

**The Problem:** The `web_searcher` ran successfully, but the *actual search results* were hidden inside the internal chat history of that agent. The final response text was just "I have completed the search," so the downstream `fact_checker` received no data.

**The Solution:** In the `call_gemini_with_context` function, explicitly capture tool outputs and append them to the final result returned to the Kernel.

```python
# In apps/agent-service/src/core/llm.py -> call_gemini_with_context

# ... (Inside the loop where tool calls are processed) ...

            # 4. Process Tool Calls
            tool_outputs_text = ""
            
            # === NEW: Accumulate Tool Data for Downstream Context ===
            execution_context_buffer = [] 

            for tool_name, tool_args in function_calls:
                # ... (Existing execution logic) ...
                result_dict = execute_tool_call(tool_name, tool_args, run_id=run_id)
                
                # ... (Existing telemetry logic) ...

                # Format Output for the Model
                tool_output_str = json.dumps(result_dict, indent=2)
                tool_outputs_text += f"\n[SYSTEM: Tool '{tool_name}' Result]\n{tool_output_str}\n"
                
                # CAPTURE CRITICAL DATA
                if tool_name in ['web_search', 'read_file']:
                    execution_context_buffer.append(f"--- {tool_name} results ---\n{tool_output_str}")

# ... (After the loop finishes) ...

        # Fallback if loop exhausted
        if not final_response_text:
            final_response_text = content_text

        # === FIX: APPEND CONTEXT ===
        # If we have captured tool data but the model didn't explicitly repeat it in final_response_text,
        # attach it so the NEXT agent can see it via Redis.
        if execution_context_buffer:
            context_dump = "\n\n".join(execution_context_buffer)
            # Only append if not already heavily present (simple length heuristic or just append)
            final_response_text += f"\n\n[AUTOMATED CONTEXT ATTACHMENT]\n{context_dump}"

        logger.info(f"Agent {safe_agent_id} Completed...")
        
        return {
            "text": final_response_text,
            # ... rest of return object
        }
```

---

### 3. Fix: Role Blurring (Over-writing files)
**Component:** `kernel-server`
**File:** `apps/kernel-server/src/runtime.rs`

**The Problem:** The `fact_checker` (a worker) generated a full report file because the system blindly assigned `write_file` to every agent in the "Smart Baseline" logic.

**The Solution:** Make the baseline tool assignment smarter. Only assign `write_file` if the agent is explicitly a `Writer`, `Coder`, or the Architect specifically requested it.

```rust
// In apps/kernel-server/src/runtime.rs -> prepare_invocation_payload

// ... existing code ...

let mut tools = agent_config.tools.clone();

// CHANGE: Only add read/list by default. Make write_file privileged.
let baseline_tools = vec!["read_file", "list_files"];

for baseline in baseline_tools {
    if !tools.contains(&baseline.to_string()) {
        tools.push(baseline.to_string());
    }
}

// LOGIC UPDATE: Only give write capability if:
// 1. It's explicitly in the config (Architect asked for it)
// 2. OR The agent ID contains "writer", "coder", "save", "generate"
// 3. OR The role is Orchestrator (often needs to save plans)
let is_privileged_writer = agent_config.role == AgentRole::Orchestrator || 
                           agent_id.contains("writer") || 
                           agent_id.contains("coder") ||
                           tools.contains(&"write_file".to_string());

if is_privileged_writer {
    if !tools.contains(&"write_file".to_string()) {
        tools.push("write_file".to_string());
    }
    // Writers often need python for formatting/PDFs
    if !tools.contains(&"execute_python".to_string()) {
        tools.push("execute_python".to_string());
    }
} else {
    // Ensure non-writers CANNOT write, even if they hallucinate the capability
    tools.retain(|t| t != "write_file");
}

// ... existing dynamic artifact logic ...
```

---

### 4. Fix: UI Accessibility & Interaction
**Component:** `web-console`
**File:** `apps/web-console/src/components/ControlDeck.svelte`

**The Problem:** Svelte warning about `onclick` events on `div` elements without keyboard support.

**The Solution:** Add `role`, `tabindex`, and `onkeydown` handlers to interactive divs.

```html
<!-- apps/web-console/src/components/ControlDeck.svelte -->

<!-- Before -->
<div class="nav-item {activePane === 'overview' ? 'active' : ''}" onclick={() => handlePaneSelect('overview')}>Overview</div>

<!-- After -->
<div 
    class="nav-item {activePane === 'overview' ? 'active' : ''}" 
    role="button" 
    tabindex="0" 
    onclick={() => handlePaneSelect('overview')}
    onkeydown={(e) => e.key === 'Enter' && handlePaneSelect('overview')}
>
    Overview
</div>

<!-- Repeat for 'pipeline', 'sim', 'stats' nav items -->

<!-- For Mode Toggle Switch -->
<div 
    class="mode-toggle" 
    role="switch" 
    aria-checked={$planningMode}
    tabindex="0"
    onclick={toggleMode} 
    onkeydown={(e) => e.key === 'Enter' && toggleMode()}
>
    <!-- ... inner content ... -->
</div>
```