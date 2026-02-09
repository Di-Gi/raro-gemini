This is a significant architectural improvement that moves the system from "Additive Delegation" to "Surgical Graph Editing."

Currently, RARO uses a "Child" strategy that re-parents downstream nodes to the new sub-graph. This often leaves the original downstream nodes (e.g., a generic "Writer") dangling at the end, even if the new sub-graph included a specialized "Writer."

Here is the implementation guide to add **Pruning Capabilities** to the Delegation Protocol.

---

### Phase 1: The Protocol (Data Contract)

First, we update the shared data definitions to allow an agent to specify a "Kill List" of nodes.

**File:** `apps/agent-service/src/domain/protocol.py`

```python
class DelegationRequest(BaseModel):
    """Payload for an agent requesting dynamic graph expansion."""
    reason: str = Field(..., description="Justification for the delegation")
    strategy: DelegationStrategy = Field(DelegationStrategy.CHILD)
    new_nodes: List[AgentNodeConfig] = Field(..., description="Sub-agents to be spliced into the graph")
    
    # [NEW] Optional list of pending node IDs to remove from the graph
    prune_nodes: List[str] = Field(default_factory=list, description="IDs of pending nodes to remove/replace")
```

**File:** `apps/kernel-server/src/models.rs`

Update the Rust struct to match.

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DelegationRequest {
    pub reason: String,
    pub new_nodes: Vec<AgentNodeConfig>,
    #[serde(default = "default_strategy")]
    pub strategy: DelegationStrategy,
    
    // [NEW]
    #[serde(default)]
    pub prune_nodes: Vec<String>, 
}
```

---

### Phase 2: The Intelligence (Prompts)

We must explicitly teach the LLM that it has permission to delete nodes. We need to explain *why* (redundancy) and *what* it can delete (pending nodes only).

**File:** `apps/agent-service/src/intelligence/prompts.py`

Update `inject_delegation_capability`:

```python
def inject_delegation_capability(base_prompt: str) -> str:
    schema = get_schema_instruction(DelegationRequest)
    return f"""
{base_prompt}

[SYSTEM CAPABILITY: DYNAMIC GRAPH EDITING]
You are authorized to modify the workflow graph.
To edit the graph, output a JSON object wrapped in `json:delegation`.

1. **Expansion**: Define `new_nodes` to handle complexity you cannot solve alone.
2. **Pruning**: If your new nodes render existing future nodes redundant, list their IDs in `prune_nodes`.
   - You can only prune nodes listed as [PENDING] in your graph context.
   - Use this to replace generic downstream agents with specialized ones.

Example Format:
```json:delegation
{{
  "reason": "Standard writer is insufficient; spawning technical documentation specialist.",
  "strategy": "child",
  "new_nodes": [ ... ],
  "prune_nodes": ["writer_synthesis"] 
}}
```

[IDENTITY CONTRACT]
New node IDs must start with 'research_', 'analyze_', 'coder_', or 'writer_'.
"""
```

---

### Phase 3: The Kernel Core (Graph Logic)

We need to add a removal method to the DAG structure and then hook it up in the Runtime.

**File:** `apps/kernel-server/src/dag.rs`

Add a `remove_node` method that cleans up edges cleanly.

```rust
    impl DAG {
        // ... existing methods ...

        /// Remove a node and all connected edges
        pub fn remove_node(&mut self, node_id: &str) -> Result<(), DAGError> {
            if !self.nodes.contains(node_id) {
                return Err(DAGError::InvalidNode(node_id.to_string()));
            }

            // 1. Remove the node
            self.nodes.remove(node_id);

            // 2. Remove outgoing edges (Key in hashmap)
            self.edges.remove(node_id);

            // 3. Remove incoming edges (Value in other keys)
            for targets in self.edges.values_mut() {
                if let Some(pos) = targets.iter().position(|x| x == node_id) {
                    targets.remove(pos);
                }
            }

            Ok(())
        }
    }
```

---

### Phase 4: The Runtime (Execution Logic)

This is the critical logic. We process the `prune_nodes` list *before* adding new nodes. We must verify that the target nodes are not currently running or completed.

**File:** `apps/kernel-server/src/runtime.rs`

Modify `handle_delegation`:

```rust
async fn handle_delegation(&self, run_id: &str, parent_id: &str, mut req: DelegationRequest) -> Result<(), String> {
    let state = self.runtime_states.get(run_id).ok_or("Run not found")?;
    let workflow_id = state.workflow_id.clone();
    
    // Snapshot safe IDs to check status
    let active_agents = state.active_agents.clone();
    let completed_agents = state.completed_agents.clone();
    drop(state); // Release lock

    // === STEP 1: PRUNING ===
    // Handle removals first to clear the graph
    if !req.prune_nodes.is_empty() {
        tracing::info!("Delegation PRUNE: Removing nodes {:?}", req.prune_nodes);

        if let Some(mut dag) = self.dag_store.get_mut(run_id) {
            for node_to_remove in &req.prune_nodes {
                // Safety Check: Cannot remove Active or Completed nodes
                if active_agents.contains(node_to_remove) || completed_agents.contains(node_to_remove) {
                    tracing::warn!("Agent {} tried to prune active/completed node {}. Ignoring.", parent_id, node_to_remove);
                    continue;
                }

                // 1. Remove from DAG
                if let Err(e) = dag.remove_node(node_to_remove) {
                    tracing::warn!("Failed to prune node {}: {}", node_to_remove, e);
                }

                // 2. Remove from Workflow Config (Metadata)
                if let Some(mut workflow) = self.workflows.get_mut(&workflow_id) {
                    if let Some(pos) = workflow.agents.iter().position(|a| a.id == *node_to_remove) {
                        workflow.agents.remove(pos);
                    }
                }
            }
        }
    }

    // === STEP 2: PRE-FETCH DEPENDENTS (Re-fetch after pruning) ===
    let (existing_dependents, existing_node_ids) = if let Some(dag) = self.dag_store.get(run_id) {
        (dag.get_children(parent_id), dag.export_nodes())
    } else {
        return Err("DAG not found for pre-fetch".to_string());
    };

    // ... (Remainder of existing handle_delegation logic: ID collision, Node Injection, Edges) ...
```

---

### Phase 5: The UI (Feedback)

Update the Delegation Card to visualize the pruning action so the operator understands what happened.

**File:** `apps/web-console/src/components/sub/DelegationCard.svelte`

```svelte
<!-- Inside the .card-body -->

{#if data}
    <!-- REASONING BLOCK -->
    <div class="section">
        <div class="label">REASONING</div>
        <div class="content reasoning">"{data.reason || 'No reason provided'}"</div>
    </div>

    <!-- [NEW] PRUNING BLOCK -->
    {#if data.prune_nodes && data.prune_nodes.length > 0}
        <div class="section">
            <div class="label" style="color: #d32f2f;">PRUNING NODES ({data.prune_nodes.length})</div>
            <div class="node-list">
                {#each data.prune_nodes as nodeId}
                    <div class="node-chip prune">
                        <div class="chip-role">REMOVED</div>
                        <div class="chip-id strike">{nodeId}</div>
                    </div>
                {/each}
            </div>
        </div>
    {/if}

    <!-- INJECTING BLOCK (Existing) -->
    {#if data.new_nodes && Array.isArray(data.new_nodes)}
        <!-- ... existing code ... -->
    {/if}
{/if}

<style>
    /* Add specific styles for pruned nodes */
    .node-chip.prune {
        border-color: #d32f2f;
        opacity: 0.7;
    }
    .node-chip.prune .chip-role {
        background: #d32f2f;
    }
    .chip-id.strike {
        text-decoration: line-through;
        color: var(--paper-line);
    }
</style>
```

### Summary of Impact

1.  **Cleaner Graphs:** Agents can now say "I will handle the reporting myself, remove the default writer."
2.  **State Safety:** The Kernel strictly prevents pruning nodes that have already run or are currently running, preserving audit trails.
3.  **Visual Clarity:** The UI explicitly shows which nodes were cut, explaining *why* the graph changed shape.