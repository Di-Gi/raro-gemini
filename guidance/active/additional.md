Here are the fixes for `apps/kernel-server/src/runtime.rs`.

### Part 1: Relaxing the Circuit Breaker (Null/Protocol Violation)

Currently, the code treats `Semantic Null` and `Protocol Violation` as fatal errors (`fail_run`). We need to change this logic to trigger `request_approval` instead, keeping the agent in a "Pending" state so it can be retried or skipped by the user, rather than killing the workflow.

**In `apps/kernel-server/src/runtime.rs` -> `execute_dynamic_dag`:**

Find the `// 4. Circuit Breaker Decision` block (approx line 550) and replace the `if/else` logic with this:

```rust
                    // 4. Circuit Breaker Decision & Output Handling
                    if res.success && !is_semantic_null && protocol_violation.is_none() {
                        // ... [Existing Success Logic: Cache, Delegation, Artifacts, etc.] ...
                        // (Keep the existing success block exactly as it is)
                        
                        // ... inside success block ...
                        let _ = self.record_invocation(&run_id, invocation).await;

                        self.emit_event(RuntimeEvent::new(
                            &run_id,
                            EventType::AgentCompleted,
                            Some(agent_id.clone()),
                            serde_json::json!({"agent_id": agent_id, "tokens_used": res.tokens_used}),
                        ));

                    } else {
                        // === CIRCUIT BREAKER: PAUSE LOGIC (SOFT FAILURES) ===
                        let (pause_reason, is_fatal) = if is_semantic_null {
                            (format!("Agent '{}' reported a Semantic Null (found no data). Verification required.", agent_id), false)
                        } else if let Some(violation) = protocol_violation {
                            (violation.to_string(), false)
                        } else {
                            (res.error.unwrap_or_else(|_| "Unknown Execution Error".to_string()), true)
                        };

                        tracing::warn!("Circuit Breaker Triggered for {}: {}", agent_id, pause_reason);

                        if is_fatal {
                            // HARD FAILURE: Crash the run (Network errors, Panics)
                            self.emit_event(RuntimeEvent::new(
                                &run_id,
                                EventType::AgentFailed,
                                Some(agent_id.clone()),
                                serde_json::json!({"error": pause_reason}),
                            ));
                            self.fail_run(&run_id, &agent_id, &pause_reason).await;
                            self.trigger_remote_cleanup(&run_id).await;
                            break; 
                        } else {
                            // SOFT FAILURE: Pause for Human Intervention (Nulls, Policy Violations)
                            // We do NOT mark as failed. We simply remove from active and request approval.
                            // This allows the user to Resume (Retry) or Edit the state.
                            
                            {
                                if let Some(mut state) = self.runtime_states.get_mut(&run_id) {
                                    state.active_agents.retain(|a| a != &agent_id);
                                    
                                    // Record the "Paused" invocation so it appears in logs
                                    state.invocations.push(AgentInvocation {
                                        id: Uuid::new_v4().to_string(),
                                        agent_id: agent_id.clone(),
                                        model_variant: ModelVariant::Fast,
                                        thought_signature: None,
                                        tools_used: payload.tools.clone(),
                                        tokens_used: res.tokens_used,
                                        latency_ms: res.latency_ms as u64,
                                        status: InvocationStatus::Paused, // <--- NEW STATUS USE
                                        timestamp: Utc::now().to_rfc3339(),
                                        artifact_id: None,
                                        error_message: Some(pause_reason.clone()),
                                    });
                                }
                                self.persist_state(&run_id).await;
                            }

                            // Trigger the Pause
                            self.request_approval(&run_id, Some(&agent_id), &pause_reason).await;
                            
                            // Break the loop to suspend execution until resumed
                            break;
                        }
                    }
```

### Part 2: Sending Rich Metadata to Frontend (Fixing "Dynamic Agent")

The frontend currently receives only IDs because `get_topology_snapshot` only exports IDs. We need to look up the `AgentNodeConfig` for every node in the DAG and send the full prompts/metadata.

**In `apps/kernel-server/src/runtime.rs` -> `get_topology_snapshot`:**

Replace the existing function with this enriched version:

```rust
    pub fn get_topology_snapshot(&self, run_id: &str) -> Option<serde_json::Value> {
        // 1. Get the DAG
        let dag = self.dag_store.get(run_id)?;
        
        // 2. Get the Workflow Config (where prompts/metadata live)
        let workflow_id = self.runtime_states.get(run_id).map(|s| s.workflow_id.clone())?;
        let workflow = self.workflows.get(&workflow_id)?;

        let edges = dag.export_edges();
        let node_ids = dag.export_nodes();

        // 3. Enrich Nodes with Config Data
        let enriched_nodes: Vec<serde_json::Value> = node_ids.iter().map(|node_id| {
            // Find the config for this node
            let config = workflow.agents.iter().find(|a| a.id == *node_id);

            if let Some(c) = config {
                serde_json::json!({
                    "id": c.id,
                    "label": c.id, // Or human readable name if available
                    "prompt": c.prompt,
                    "model": match c.model {
                        ModelVariant::Fast => "fast",
                        ModelVariant::Reasoning => "reasoning",
                        ModelVariant::Thinking => "thinking",
                        ModelVariant::Custom(ref s) => s,
                    },
                    "role": c.role,
                    "tools": c.tools,
                    // If position is stored in config (from initial layout), pass it
                    // Dynamic nodes usually lack this, handled by frontend layout engine
                    "x": c.position.as_ref().map(|p| p.x).unwrap_or(0.0),
                    "y": c.position.as_ref().map(|p| p.y).unwrap_or(0.0)
                })
            } else {
                // Fallback for purely structural nodes (rare)
                serde_json::json!({
                    "id": node_id,
                    "label": node_id,
                    "prompt": "Dynamic Agent (Metadata Missing)",
                    "model": "fast"
                })
            }
        }).collect();
        
        Some(serde_json::json!({
            "nodes": enriched_nodes, // <--- Now returns rich objects, not just strings
            "edges": edges.into_iter().map(|(from, to)| {
                serde_json::json!({ "from": from, "to": to })
            }).collect::<Vec<_>>()
        }))
    }
```

### Frontend Adjustment (JavaScript)
You will need to update your frontend code (the snippet you pasted) to expect `topology.nodes` to be an **Array of Objects** rather than an Array of Strings. (stores.ts)

```javascript
// OLD: topology.nodes.forEach(nodeId => { ... })
// NEW:
topology.nodes.forEach(nodeData => {
    // Check if it's an object (Rich data) or string (Legacy support)
    const nodeId = typeof nodeData === 'string' ? nodeData : nodeData.id;
    
    if (nodeMap.has(nodeId)) {
        // Existing node update logic...
    } else {
        // NEW NODE
        newNodes.push({
            id: nodeId,
            label: nodeId.toUpperCase().substring(0, 12),
            x: nodeData.x || 0,
            y: nodeData.y || 0,
            // USE THE BACKEND DATA:
            prompt: nodeData.prompt || 'Dynamic Agent', 
            model: nodeData.model || 'fast',
            tools: nodeData.tools || deriveToolsFromId(nodeId),
            // ... rest of your logic
        });
    }
});
```