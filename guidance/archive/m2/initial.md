This is the **System Integration Specification**. It translates the disconnected components into a unified reactive system.

### **Integration Architecture Overview**

1.  **Control Flow**: Web Console (UI) → Kernel Server (Rust) → Agent Service (Python) → Gemini API.
2.  **Data Flow**: 
    *   **Command**: JSON via HTTP POST.
    *   **Feedback**: Real-time State via WebSocket (Rust → UI).
    *   **Reasoning**: Thought Signatures passed via JSON (Python → Rust).

---

## **Phase 1: The Nervous System (Kernel Server)**

**Goal**: Transform the Rust Kernel from a passive state container into an active dispatcher that calls the Python Agent Service.

### **Step 1.1: Add HTTP Client Dependency**
**File**: `apps/kernel-server/Cargo.toml`
**Action**: Add `reqwest` to enable the Kernel to call the Agent Service.

```toml
[dependencies]
# ... existing dependencies ...
reqwest = { version = "0.11", features = ["json"] }
serde = { version = "1.0", features = ["derive"] }
```

### **Step 1.2: Implement Agent Dispatcher**
**File**: `apps/kernel-server/src/runtime.rs`
**Action**: Add a method to `RARORuntime` to execute the HTTP call to the Python service.

> **Directive**: Implement `invoke_remote_agent` which takes the `InvocationPayload`, sends it to `http://localhost:8000/invoke`, and returns the `AgentResponse`.

### **Step 1.3: Create the Execution Loop**
**File**: `apps/kernel-server/src/runtime.rs`
**Action**: Modify `start_workflow`. Instead of just returning a `run_id`, it must spawn a detached Tokio task that orchestrates the DAG.

**Logic to Implement:**
1.  Get topological sort from DAG.
2.  Iterate through nodes (agents).
3.  For each node:
    *   Update State -> `Running` (Broadcast via WS).
    *   Call `prepare_invocation_payload` (Existing function).
    *   Call `invoke_remote_agent` (New function from Step 1.2).
    *   Save `thought_signature` from response to `thought_signatures` map.
    *   Update State -> `Completed` (Broadcast via WS).
4.  Handle errors by marking Run as `Failed`.

---

## **Phase 2: The Interface (Web Console)**

**Goal**: Rip out the mocks and wire the UI to the Kernel API.

### **Step 2.1: Define API Client**
**File**: `apps/web-console/src/lib/api.ts` (New File)
**Action**: Create a centralized API handler.

```typescript
const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:3000';

export async function startRun(config: any) {
    const res = await fetch(`${API_BASE}/runtime/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
    });
    return await res.json();
}

export function getWebSocketURL(runId: string) {
    // Handle ws:// vs wss:// logic
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = new URL(API_BASE).host;
    return `${protocol}//${host}/ws/runtime/${runId}`;
}
```

### **Step 2.2: Connect Control Deck**
**File**: `apps/web-console/src/components/ControlDeck.svelte`
**Action**: Replace the `setTimeout` mock in `executeRun()`.

1.  **Construct Payload**: Map `$agentNodes` and `$pipelineEdges` to the `WorkflowConfig` JSON structure expected by Rust.
2.  **Call API**: `const response = await api.startRun(payload);`
3.  **Initialize Stream**: Call a new store action `initWebSocket(response.run_id)`.

### **Step 2.3: Live State Synchronization**
**File**: `apps/web-console/src/lib/stores.ts`
**Action**: Implement `initWebSocket` and handle incoming messages.

**Logic to Implement:**
1.  Open WS connection to `ws://localhost:3000/ws/runtime/{run_id}`.
2.  **On Message**:
    *   Parse JSON (`RuntimeState`).
    *   **Update Logs**: If `invocations` has new entries, add to `$logs`.
    *   **Update Nodes**: Map `state.active_agents` to `status: 'running'` and `state.completed_agents` to `status: 'complete'`.
    *   **Update Edges**: Animate edges connecting `completed_agents` to `active_agents`.
    *   **Update Telemetry**: Update `p99`, `cost`, `tokens` based on `state.total_tokens_used`.

---

## **Phase 3: The Engine (Agent Service)**

**Goal**: Ensure the Python service is reachable and correctly processing requests.

### **Step 3.1: Verify Docker Networking**
**File**: `docker-compose.yml`
**Action**: Ensure service discovery works.
*   Kernel environment var `AGENT_HOST` should be `http://agents:8000` (internal Docker DNS).
*   **Fix**: In `apps/kernel-server/src/runtime.rs`, ensuring the HTTP client uses the environment variable `AGENT_HOST` instead of hardcoded `localhost` if running inside Docker.

### **Step 3.2: Streaming (Optional but Recommended)**
**File**: `apps/agent-service/src/main.py`
**Action**: The Kernel doesn't currently support streaming response parsing (it expects full JSON). Keep `stream=False` for the MVP integration to ensure stability.

---

## **Phase 4: Telemetry & Verification**

**Goal**: Verify end-to-end data flow.

### **Step 4.1: Update Telemetry Panel**
**File**: `apps/web-console/src/components/ControlDeck.svelte`
**Action**: Remove hardcoded values in `#pane-stats`.
*   Bind values to a derived store based on the WebSocket `RuntimeState` updates.
*   Example: `cost = (state.total_tokens_used / 1000) * 0.0005`.

### **Implementation Checklist for the Agent**

1.  [ ] **Rust**: Add `reqwest` to `Cargo.toml`.
2.  [ ] **Rust**: Implement `invoke_remote_agent` in `runtime.rs`.
3.  [ ] **Rust**: Create `run_dag_execution` task in `start_workflow`.
4.  [ ] **TS**: Create `api.ts` in Web Console.
5.  [ ] **Svelte**: Update `ControlDeck.svelte` to use `api.startRun`.
6.  [ ] **Svelte**: Update `stores.ts` to handle WebSocket state syncing.
7.  [ ] **Docker**: Verify `KERNEL_HOST` and `AGENT_HOST` env vars align with Docker service names.

