The error is a logic bug in `apps/kernel-server/src/runtime.rs` within the `handle_delegation` function.

### The Logic Flaw
When the Master Planner delegates to an agent ID that **already exists and is a direct child** (like `research_web`), the current code treats that node as **both** the "New Node" being inserted AND the "Existing Dependent" that needs to be rewired to wait for the new node.

1.  **Existing State:** `master_planner` -> `research_web` (Pending)
2.  **Delegation:** `master_planner` requests to delegateto `research_web`.
3.  **The Bug:** The code identifies `research_web` as an `existing_dependent`. It then iterates through dependents and forces them to depend on the `new_nodes`.
4.  **Result:** `research_web` is forced to depend on `research_web`. **Cycle detected.**

### The Fix
You need to filter the `existing_dependents` list to exclude any nodes that are currently being defined in `new_nodes`. You only want to rewire *downstream* nodes, not the nodes you are currently injecting/updating.

Here is the corrected `handle_delegation` function for `apps/kernel-server/src/runtime.rs`.

```rust
// In apps/kernel-server/src/runtime.rs

// ... inside RARORuntime impl ...

    /// Handles the "Graph Surgery" when an agent requests delegation
    async fn handle_delegation(&self, run_id: &str, parent_id: &str, mut req: DelegationRequest) -> Result<(), String> {
        let state = self.runtime_states.get(run_id).ok_or("Run not found")?;
        let workflow_id = state.workflow_id.clone();
        drop(state);  // Drop read lock

        // 2. PRE-FETCH DEPENDENTS & SANITIZE IDs
        let (existing_dependents, existing_node_ids) = if let Some(dag) = self.dag_store.get(run_id) {
            (dag.get_children(parent_id), dag.export_nodes())
        } else {
            return Err("DAG not found for pre-fetch".to_string());
        };

        // FIX 1: ID Collision Remapping with Ghost Prevention
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
                    tracing::info!("Delegation UPDATE: Adopting/Overwriting pending node '{}'.", node.id);

                    // UPDATE LOGIC: Remove old definition so we can replace it
                    if let Some(mut workflow) = self.workflows.get_mut(&workflow_id) {
                        if let Some(pos) = workflow.agents.iter().position(|a| a.id == node.id) {
                            workflow.agents.remove(pos);
                        }
                    }

                    // DAG LOGIC: Clear old incoming edges to allow "Rewiring"
                    if let Some(mut dag) = self.dag_store.get_mut(run_id) {
                        dag.clear_incoming_edges(&node.id);
                    }

                } else {
                    // Node is active/done, must rename
                    let old_id = node.id.clone();
                    let suffix = Uuid::new_v4().to_string().split('-').next().unwrap().to_string();
                    let new_id = format!("{}_{}", old_id, suffix);
                    tracing::warn!("Delegation ID Collision: Renaming '{}' to '{}'", old_id, new_id);

                    node.id = new_id.clone();
                    id_map.insert(old_id, new_id);
                }
            }
        }

        // Apply rewiring to new nodes' dependency lists
        for node in &mut req.new_nodes {
            node.depends_on = node.depends_on.iter().map(|dep| {
                id_map.get(dep).cloned().unwrap_or(dep.clone())
            }).collect();
        }

        // [[CRITICAL FIX START]]: Create a list of IDs being injected/updated
        // We must NOT treat these as "downstream dependents" to avoid self-referential cycles.
        let new_node_ids: Vec<String> = req.new_nodes.iter().map(|n| n.id.clone()).collect();
        
        // Filter existing dependents to finding TRUE downstream nodes
        let downstream_dependents: Vec<String> = existing_dependents
            .into_iter()
            .filter(|dep_id| !new_node_ids.contains(dep_id))
            .collect();
        // [[CRITICAL FIX END]]

        // 3. MUTATE WORKFLOW CONFIG
        if let Some(mut workflow) = self.workflows.get_mut(&workflow_id) {
            for node in &req.new_nodes {
                workflow.agents.push(node.clone());
            }

            if req.strategy == DelegationStrategy::Child {
                // Use the filtered list (downstream_dependents) instead of existing_dependents
                for dep_id in &downstream_dependents {
                    if let Some(dep_agent) = workflow.agents.iter_mut().find(|a| a.id == *dep_id) {
                        dep_agent.depends_on.retain(|p| p != parent_id);
                        for new_node in &req.new_nodes {
                            if !dep_agent.depends_on.contains(&new_node.id) {
                                dep_agent.depends_on.push(new_node.id.clone());
                            }
                        }
                        tracing::info!("Rewired Config: Agent {} now depends on {:?}", dep_id, dep_agent.depends_on);
                    }
                }
            }
        } else {
            return Err("Workflow config not found".to_string());
        }

        // 4. MUTATE DAG TOPOLOGY
        if let Some(mut dag) = self.dag_store.get_mut(run_id) {
            
            for node in &req.new_nodes {
                dag.add_node(node.id.clone()).map_err(|e| e.to_string())?;

                for dep in &node.depends_on {
                    if let Err(e) = dag.add_edge(dep.clone(), node.id.clone()) {
                        tracing::debug!("Adding dependency edge {} -> {}: {:?}", dep, node.id, e);
                    }
                }

                if node.depends_on.is_empty() || node.depends_on.contains(&parent_id.to_string()) {
                     let _ = dag.add_edge(parent_id.to_string(), node.id.clone());
                }

                // B. Connect New Nodes -> TRUE Downstream Dependents
                if req.strategy == DelegationStrategy::Child {
                    for dep in &downstream_dependents {
                        dag.add_edge(node.id.clone(), dep.clone()).map_err(|e| e.to_string())?;
                    }
                }
            }

            // C. Remove Old Edges (Parent -> TRUE Downstream Dependents)
            if req.strategy == DelegationStrategy::Child {
                for dep in &downstream_dependents {
                    let _ = dag.remove_edge(parent_id, dep);
                }
            }
            
            if let Err(e) = dag.topological_sort() {
                tracing::error!("Delegation created a cycle: {:?}", e);
                return Err("Delegation created a cycle in DAG".to_string());
            }
        } else {
            return Err("DAG not found".to_string());
        }

        Ok(())
    }
```

### Summary of Changes made above:
1.  **Created `new_node_ids` vector**: To track exactly which IDs are being touched by this delegation.
2.  **Created `downstream_dependents`**: This filters the `existing_dependents` by removing any ID present in `new_node_ids`.
3.  **Updated Loops**: Replaced usage of `existing_dependents` with `downstream_dependents` in both the `WorkflowConfig` rewiring loop (Step 3) and the `DAG` rewiring loop (Step 4).

This ensures that if the LLM says "Delegate to X", and X is already a child of the current node, X is simply updated/adopted, and only nodes *after* X (if any) are rewired to depend on the new version of X.