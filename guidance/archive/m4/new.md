This is a comprehensive implementation guide to bridge the gap between your backend’s dynamic capabilities and the frontend’s unawareness.

We will move from **Heuristic State Sync** (guessing based on what's running) to **Deterministic State Sync** (the Kernel broadcasting the source of truth).

---

### Phase 1: Kernel Server (The Source of Truth)

The Kernel holds the `DAG` in memory, which contains the edges created during delegation. We must expose this structure to the API layer so it can be broadcast over WebSocket.

#### 1.1 Expose DAG Structure (`apps/kernel-server/src/dag.rs`)
Add a method to export the graph topology in a format the frontend expects (`from` -> `to`).

```rust
// In impl DAG { ... }

/// Export edges as a flat vector for UI visualization
pub fn export_edges(&self) -> Vec<(String, String)> {
    let mut edge_list = Vec::new();
    for (source, targets) in &self.edges {
        for target in targets {
            edge_list.push((source.clone(), target.clone()));
        }
    }
    edge_list
}

/// Export all known node IDs
pub fn export_nodes(&self) -> Vec<String> {
    self.nodes.iter().cloned().collect()
}
```

#### 1.2 Update Runtime to expose Snapshot (`apps/kernel-server/src/runtime.rs`)
The handler needs a way to grab this data without locking the whole runtime for too long.

```rust
// In impl RARORuntime { ... }

/// Returns the current topology (nodes and edges) for visualization
pub fn get_topology_snapshot(&self, run_id: &str) -> Option<serde_json::Value> {
    if let Some(dag) = self.dag_store.get(run_id) {
        let edges = dag.export_edges();
        let nodes = dag.export_nodes();
        
        // Convert to the JSON structure the frontend expects
        Some(serde_json::json!({
            "nodes": nodes,
            "edges": edges.into_iter().map(|(from, to)| {
                serde_json::json!({ "from": from, "to": to })
            }).collect::<Vec<_>>()
        }))
    } else {
        None
    }
}
```

#### 1.3 Broadcast Topology in WebSocket (`apps/kernel-server/src/server/handlers.rs`)
Update the heartbeat loop to send the topology.

```rust
// Inside the loop in handle_runtime_stream ...

// ... existing select! / interval logic ...

_ = interval.tick() => {
    if let Some(state) = runtime.get_state(&run_id) {
        
        // === NEW: Fetch Topology ===
        let topology = runtime.get_topology_snapshot(&run_id);
        
        let update = json!({
            "type": "state_update",
            "state": state,
            "signatures": runtime.get_all_signatures(&run_id).map(|s| s.signatures),
            "topology": topology, // <--- THE BRIDGE
            "timestamp": chrono::Utc::now().to_rfc3339()
        });

        if sender.send(Message::Text(update.to_string())).await.is_err() {
            // ... break loop
        }
        
        // ... terminal state checks ...
    }
}
```

---

### Phase 2: Web Console (The Visualization)

Now that the frontend receives the `topology` object, we replace the "guessing" logic in `stores.ts` with a robust layout engine that positions new nodes relative to their parents.

#### 2.1 Update Logic (`apps/web-console/src/lib/stores.ts`)

Replace the `ingestDynamicNodes` function and modify `syncState` to use the new topology data.

```typescript
// Define the Topology Interface
interface TopologySnapshot {
    nodes: string[];
    edges: { from: string; to: string }[];
}

// === NEW: Deterministic Graph Sync ===
function syncTopology(topology: TopologySnapshot, activeAgents: string[]) {
    const currentNodes = get(agentNodes);
    const currentEdges = get(pipelineEdges);
    
    const nodeMap = new Map(currentNodes.map(n => [n.id, n]));
    const edgeSet = new Set(currentEdges.map(e => `${e.from}|${e.to}`));
    
    let hasChanges = false;
    let newNodesList = [...currentNodes];
    let newEdgesList = [...currentEdges];

    // 1. Process Edges First (to establish relationships)
    topology.edges.forEach(edge => {
        const edgeKey = `${edge.from}|${edge.to}`;
        if (!edgeSet.has(edgeKey)) {
            newEdgesList.push({
                from: edge.from,
                to: edge.to,
                active: false,
                finalized: false,
                pulseAnimation: false
            });
            edgeSet.add(edgeKey);
            hasChanges = true;
        }
    });

    // 2. Process Nodes (Layout Logic)
    topology.nodes.forEach(nodeId => {
        if (!nodeMap.has(nodeId)) {
            // It's a new dynamic node. We need to position it.
            // Find its parent(s) from the new edge list we just updated
            const parents = newEdgesList.filter(e => e.to === nodeId).map(e => e.from);
            
            let newX = 50;
            let newY = 50;

            if (parents.length > 0) {
                // Get parent position
                const primaryParent = nodeMap.get(parents[0]);
                if (primaryParent) {
                    newY = primaryParent.y + 20; // Move down 20% relative height
                    
                    // Intelligent Horizontal Spacing (Sibling check)
                    // Find other nodes that share this parent
                    const siblings = newEdgesList
                        .filter(e => e.from === parents[0])
                        .map(e => e.to)
                        .filter(sibId => sibId !== nodeId && nodeMap.has(sibId));
                    
                    // Offset based on existing siblings
                    // This creates a fan-out effect: Parent -> [Child1, Child2, Child3]
                    const offset = (siblings.length) * 15; 
                    newX = primaryParent.x + offset; 
                    
                    // Cap width to prevent flying off screen
                    if (newX > 90) newX = 90;
                }
            }

            const newNode: AgentNode = {
                id: nodeId,
                label: nodeId.toUpperCase().replace(/_/g, ' ').substring(0, 14),
                x: newX,
                y: newY,
                model: 'DYNAMIC',
                prompt: 'Dynamic Sub-Agent',
                status: activeAgents.includes(nodeId) ? 'running' : 'idle', // Key: Might be pending
                role: 'worker'
            };

            newNodesList.push(newNode);
            nodeMap.set(nodeId, newNode);
            hasChanges = true;
        }
    });

    if (hasChanges) {
        agentNodes.set(newNodesList);
        pipelineEdges.set(newEdgesList);
        // Trigger a re-render log
        console.log('[UI] Graph topology updated via Kernel snapshot');
    }
}

// === UPDATE: syncState ===
function syncState(state: any, signatures: Record<string, string> = {}, topology?: TopologySnapshot) {
    // 1. Sync Graph Structure if provided
    if (topology) {
        syncTopology(topology, state.active_agents || []);
    }
    
    // ... (Rest of existing syncState logic regarding statuses and logs) ...
}
```

---

### Phase 3: Agent Service (Robust Parsing)

The agent might output conversational filler before the JSON block, causing the `startswith` check to fail. We need a regex extractor.

#### 3.1 Robust Extraction (`apps/agent-service/src/main.py`)

Replace the simple `startswith` logic in `_execute_agent_logic` with this robust block.

```python
import re

# ... inside _execute_agent_logic ...

# 3. Parse Delegation Request (Flow B)
delegation_request = None

# Regex to find a JSON block: 
# Looks for { "delegation": ... } structure loosely or a markdown block
# This pattern matches ```json ... ``` or just raw JSON starting with {
json_pattern = r"```(?:json)?\s*(\{[\s\S]*?\})\s*```|(\{[\s\S]*\"delegation\"[\s\S]*\})"

try:
    match = re.search(json_pattern, response_text)
    
    extracted_json = None
    if match:
        # Group 1 is inside ```json ... ```
        # Group 2 is raw { ... }
        extracted_json = match.group(1) or match.group(2)
    elif response_text.strip().startswith("{"):
        # Fallback for clean JSON without markdown
        extracted_json = response_text
        
    if extracted_json:
        data = json.loads(extracted_json)
        
        if "delegation" in data:
            delegation_request = DelegationRequest(**data["delegation"])
            logger.info(f"Delegation parsed via Regex. Nodes: {len(delegation_request.new_nodes)}")
            
            # Clean the output? 
            # Optionally remove the JSON from the text shown to user
            # response_text = response_text.replace(extracted_json, "[Delegation Request Processed]")

except json.JSONDecodeError:
    logger.debug("Regex matched but JSON decode failed.")
except Exception as e:
    logger.warning(f"Delegation parsing error: {e}")
```

---

### Summary of Changes

| Component | File | Change | Impact |
| :--- | :--- | :--- | :--- |
| **Kernel** | `dag.rs` | Added `export_edges` | Allows graph serialization. |
| **Kernel** | `handlers.rs` | Added `topology` to WS payload | Broadcasts the "Source of Truth" to UI. |
| **Frontend** | `stores.ts` | Added `syncTopology` | deterministically draws new nodes and connects them to parents using backend edge data. |
| **Agents** | `main.py` | Added Regex Parsing | Ensures delegation works even if the LLM is "chatty". |

### How to Verify

1.  **Start the Stack:** Run Kernel, Agent Service, and Web Console.
2.  **Trigger a Complex Task:** Enter a prompt like *"Research the anatomy of the human spine"* (triggers your sample `RESEARCH_ANATOMY` delegation).
3.  **Watch the Console:**
    *   **Previously:** You would see the main agent run, then nothing, then suddenly 3 nodes pop in unrelated to the first one.
    *   **Now:** As soon as the Researcher finishes, the Kernel updates the graph. You will see 3 new gray (pending) nodes appear instantly, connected by lines to the Researcher node, *before* they even start running.
4.  **Layout Check:** The new nodes should be positioned below the Researcher, spaced out horizontally (Fan-out layout).