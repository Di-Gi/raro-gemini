# Analysis of Patch 2: Topology-Driven Dynamic Graph Updates

**Analyzed on:** 2026-01-03
**Patch Document:** `apps/web-console/patches/2.md`

---

## Executive Summary

The patch claims that the Kernel already exposes topology in the WebSocket stream and that the UI should stop using heuristics. **This claim is VERIFIED as substantially correct**, with important nuances detailed below.

**Verdict:** The kernel infrastructure is complete and topology IS transmitted. However, the UI implementation is only partially connected.

---

## Detailed Findings

### 1. Claim: "The Kernel already exposes `topology` in the WebSocket stream"

**STATUS: ✅ VERIFIED**

**Evidence:**
- **File:** `apps/kernel-server/src/server/handlers.rs:220-228`
- **Code:**
```rust
// === NEW: Fetch Topology ===
let topology = runtime.get_topology_snapshot(&run_id);

let update = json!({
    "type": "state_update",
    "state": state,
    "signatures": runtime.get_all_signatures(&run_id).map(|s| s.signatures),
    "topology": topology, // <--- THE BRIDGE
    "timestamp": chrono::Utc::now().to_rfc3339()
});
```

The WebSocket handler **explicitly includes** the topology field in every state update message sent to the client at 250ms intervals.

---

### 2. Claim: "Topology is updated when an agent delegates"

**STATUS: ✅ VERIFIED**

**Evidence:**
- **File:** `apps/kernel-server/src/runtime.rs:480-545`
- **Function:** `handle_delegation()`

**Process Flow:**
1. When an agent returns a `DelegationRequest` (runtime.rs:386)
2. The `handle_delegation` function is called (runtime.rs:390)
3. New nodes are added to the workflow config (runtime.rs:491-499)
4. **DAG is mutated in-place:**
   - New nodes added to DAG (runtime.rs:509)
   - New edges created from parent to new nodes (runtime.rs:512)
   - Existing children are rewired to depend on new nodes (runtime.rs:516-521)
   - Old edges from parent to original children are removed (runtime.rs:527-530)
5. The DAG mutation is validated for cycles (runtime.rs:534-539)

**Code:**
```rust
// runtime.rs:509-521
dag.add_node(node.id.clone()).map_err(|e| e.to_string())?;
dag.add_edge(parent_id.to_string(), node.id.clone()).map_err(|e| e.to_string())?;

if req.strategy == DelegationStrategy::Child {
    for dep in &existing_dependents {
        dag.add_edge(node.id.clone(), dep.clone()).map_err(|e| e.to_string())?;
    }
}
```

---

### 3. Claim: "Topology snapshot reflects DAG state"

**STATUS: ✅ VERIFIED**

**Evidence:**
- **File:** `apps/kernel-server/src/runtime.rs:835-851`
- **Function:** `get_topology_snapshot()`

```rust
pub fn get_topology_snapshot(&self, run_id: &str) -> Option<serde_json::Value> {
    if let Some(dag) = self.dag_store.get(run_id) {
        let edges = dag.export_edges();
        let nodes = dag.export_nodes();

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

The topology snapshot is generated **directly from the DAG** using:
- **File:** `apps/kernel-server/src/dag.rs:160-174`
- `export_edges()` - Returns all edges as `Vec<(String, String)>`
- `export_nodes()` - Returns all node IDs as `Vec<String>`

This means the topology is **authoritative** and reflects the current graph structure including any delegation splices.

---

### 4. Claim: "We should stop using the heuristic `ingestDynamicNodes` function"

**STATUS: ⚠️ PARTIALLY IMPLEMENTED**

**Current UI State:**

**File:** `apps/web-console/src/lib/stores.ts`

The UI **has both systems**:

#### A. Topology-Based System (EXISTS but DISCONNECTED)
```typescript
// Line 218-301: syncTopology function
function syncTopology(topology: TopologySnapshot, activeAgents: string[]) {
    // Reconciles nodes and edges based on kernel topology
    // Adds new nodes with intelligent positioning
    // Updates edge list to match kernel
}

// Line 482: syncState has topology parameter
function syncState(state: any, signatures: Record<string, string> = {}, topology?: TopologySnapshot) {
    // Line 484-486: If topology provided, call syncTopology
    if (topology) {
        syncTopology(topology, state.active_agents || []);
    }
}
```

#### B. Heuristic System (ACTIVE)
```typescript
// Line 428-476: ingestDynamicNodes (heuristic approach)
function ingestDynamicNodes(activeAgents: string[], signatures: Record<string, string>) {
    // Detects new node IDs from active_agents
    // Infers position based on last node
    // Does NOT create edges (no parent knowledge)
}

// Line 492-494: STILL BEING CALLED
if (state.active_agents && Array.isArray(state.active_agents)) {
    ingestDynamicNodes(state.active_agents, signatures);
}
```

---

### 5. Critical Gap: WebSocket Handler Not Passing Topology

**STATUS: ❌ BUG IDENTIFIED**

**File:** `apps/web-console/src/lib/stores.ts:369-390`

```typescript
ws.onmessage = (event: any) => {
    const data = JSON.parse(event.data);
    if (data.type === 'state_update' && data.state) {
        // Line 379: PROBLEM - Not passing data.topology!
        syncState(data.state, data.signatures);

        // SHOULD BE:
        // syncState(data.state, data.signatures, data.topology);
    }
}
```

**Impact:**
- The kernel sends `topology` in every WebSocket message ✅
- The UI has a `syncTopology` function ready to handle it ✅
- But the WebSocket handler **ignores** `data.topology` ❌
- Falls back to `ingestDynamicNodes` heuristic instead ❌

This is why delegation doesn't render correctly - the authoritative topology data is being discarded!

---

### 6. Layout Engine Status

**STATUS: ✅ EXISTS**

**File:** `apps/web-console/src/lib/layout-engine.ts`

The `DagLayoutEngine` class already exists and implements:
- Rank assignment (longest path algorithm)
- BFS traversal for layering
- Normalized 0-100% coordinate system
- Sibling distribution logic

This matches the patch recommendation and is production-ready.

---

## Summary Table

| Component | Patch Claim | Actual Status | Evidence |
|-----------|-------------|---------------|----------|
| Kernel exposes topology | ✅ True | ✅ Verified | handlers.rs:227 |
| Topology updated on delegation | ✅ True | ✅ Verified | runtime.rs:509-530 |
| Topology in WebSocket payload | ✅ True | ✅ Verified | handlers.rs:223-228 |
| `get_topology_snapshot` exists | ✅ True | ✅ Verified | runtime.rs:836-850 |
| DAG exports nodes/edges | ✅ True | ✅ Verified | dag.rs:160-174 |
| UI `syncTopology` exists | ✅ True | ✅ Verified | stores.ts:218-301 |
| UI uses topology data | ❌ Should | ❌ **NOT CONNECTED** | stores.ts:379 (bug) |
| `ingestDynamicNodes` removed | ❌ Should be | ❌ Still active | stores.ts:493 |
| Layout engine exists | ✅ True | ✅ Verified | layout-engine.ts:4-96 |

---

## Root Cause Analysis

The patch is **architecturally correct** about the kernel capabilities. The issue is a **partial implementation** in the UI:

1. **Infrastructure Complete:** All the pieces exist (syncTopology, layout engine, topology in payload)
2. **Integration Incomplete:** The WebSocket handler doesn't wire `data.topology` to `syncState`
3. **Fallback Active:** The old heuristic system (`ingestDynamicNodes`) is still being called
4. **Result:** Dynamic nodes appear but without correct edges/layout because topology data is ignored

---

## Required Fix

**Single Line Change:**

**File:** `apps/web-console/src/lib/stores.ts:379`

```typescript
// CURRENT (WRONG):
syncState(data.state, data.signatures);

// FIXED (CORRECT):
syncState(data.state, data.signatures, data.topology);
```

**Additional Cleanup:**
- Remove `ingestDynamicNodes` function (lines 428-476)
- Remove call to `ingestDynamicNodes` from `syncState` (lines 492-494)

---

## Verification Test Plan

To verify the fix works:

1. **Start a workflow with delegation:**
   - Use an agent configured to delegate (e.g., Orchestrator with `tools: ["delegate"]`)

2. **Monitor WebSocket messages:**
   - Browser DevTools → Network → WS tab
   - Verify `topology.nodes` and `topology.edges` are present

3. **Check UI behavior:**
   - New nodes should appear at correct positions (via layout engine)
   - Edges should connect parent → delegated nodes → original children
   - No orphaned nodes

4. **Console Verification:**
   - Should see: `[UI] Graph topology updated via Kernel snapshot`
   - Should NOT see: `[UI] Detected dynamic agent:` (from heuristic)

---

## Conclusion

**The patch document is CORRECT:**
- The kernel DOES expose topology ✅
- The topology IS updated on delegation ✅
- The topology IS in the WebSocket stream ✅

**The implementation is INCOMPLETE:**
- The UI has the infrastructure but doesn't use it ❌
- One-line bug prevents topology data from being processed ❌
- Legacy heuristic system is still active as a fallback ❌

**Recommendation:** Apply the patch's suggested changes to stores.ts to complete the integration and remove the heuristic approach.
