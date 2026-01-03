### Executive Summary
**Infrastructure Status:** 🟢 **Ready**
**Integration Status:** 🔴 **Unwired**

You have a complete backend chain to generate DAGs from natural language, execute them, and handle dynamic graph mutations. The missing link is purely in the **handoff** between the Web Console's input mechanism and the Agent Service's `/plan` endpoint.

---

### 1. The Architect Flow (Flow A)
**Goal:** User inputs "Research Graphite" $\to$ System generates a DAG $\to$ User reviews/edits $\to$ Execution.

#### ✅ What is Supported (Infrastructure)
1.  **Generation API (`agent-service`):**
    *   Endpoint: `POST /plan` is fully implemented in `src/main.py`.
    *   Logic: `ArchitectEngine` uses `gemini-2.5-flash-lite` with a strict JSON schema (`WorkflowManifest`) to generate nodes and dependencies.
2.  **Client-Side Logic (`web-console`):**
    *   The function `generateWorkflowPlan(userQuery)` in `lib/api.ts` exists.
    *   It even includes fallback logic to calculate X/Y positions for the nodes if the AI doesn't provide them, ensuring the graph is renderable immediately.
3.  **Kernel Execution (`kernel-server`):**
    *   Endpoint: `POST /runtime/start` accepts the exact JSON structure (`WorkflowConfig`) produced by the Architect.

#### ❌ What is Missing (The wiring)
1.  **The "Planning" State:**
    *   Currently, `ControlDeck.svelte` assumes an existing graph and appends the user input to the *Orchestrator's* prompt via `executeRun`.
    *   **Required Logic:** You need a "Pre-Flight" mode where the input calls `generateWorkflowPlan` *first*. The result of this API call must overwrite the `agentNodes` and `pipelineEdges` Svelte stores.
2.  **Graph Translation:**
    *   The `WorkflowManifest` (backend) needs to be mapped to `AgentNode[]` and `PipelineEdge[]` (frontend stores) before rendering. `api.ts` does some of this, but ensuring the `depends_on` array correctly transforms into visual edges in `pipelineEdges` store is the critical data transformation step currently missing in the UI logic.

---

### 2. The Execution Flow (Flow B & C)
**Goal:** Run the DAG, handle tools, and allow for intervention.

#### ✅ What is Supported (Infrastructure)
1.  **Dynamic Splicing (Flow B):**
    *   **Agent:** The `core/llm.py` and prompts can generate `DelegationRequest` JSON.
    *   **Kernel:** `runtime.rs` has `handle_delegation` which physically modifies the in-memory DAG (adds nodes, rewires edges) and updates `WorkflowConfig`.
    *   **Console:** `stores.ts` has `ingestDynamicNodes` which detects new Agent IDs in the WebSocket stream and auto-injects them into the visual graph.
2.  **Visual Feedback:**
    *   WebSockets allow real-time status updates (Running/Complete/Failed).
    *   Output artifacts (results) are fetched and displayed.

#### ❌ What is Missing (Logic Gaps)
1.  **Tool Execution (Critical):**
    *   **Agent Service:** In `src/core/llm.py`, the `tools` parameter in `call_gemini_with_context` is commented out or marked TODO.
    *   **Kernel:** The Kernel stores `tools` strings (e.g., "fs_delete"), but has no logic to actually *run* them.
    *   **Requirement:** You need a Tool Execution layer (likely in Agent Service for Python/Sandboxing or Kernel for System ops) that intercepts specific tokens or function calls *before* the LLM response is finalized.
2.  **Safety Pattern Actions (Flow C):**
    *   **Kernel:** The `registry.rs` has default patterns (e.g., `guard_fs_delete`), but the action `RequestApproval` is logged as "Not yet implemented" in the Cortex loop (`main.rs`).
    *   **Requirement:** The Cortex loop in `main.rs` needs to explicitly switch the `RuntimeState` status to `AwaitingApproval` when that action triggers. This is the specific signal the Web Console's `ControlDeck.svelte` is listening for to show the "Intervention Overlay".

---

### Summary of Tasks (Non-UX)

To bridge the gap without touching UI design, the logic flow needs to support:

1.  **Overwrite Store on Plan:**
    Create a logic handler in the frontend controller that takes the JSON response from `POST /plan` and:
    *   Maps `manifest.agents` $\to$ `$agentNodes` (Store).
    *   Iterates `manifest.agents` dependencies $\to$ `$pipelineEdges` (Store).
    *   *This replaces the hardcoded `initialNodes` in `stores.ts`.*

2.  **Enable Tooling (Backend):**
    Uncomment/Implement the tool handling in `agent-service/src/core/llm.py`. Even if it's just a mock tool that returns "Action Executed", the infrastructure needs to pass the tool definition to Gemini.

3.  **Activate "AwaitingApproval" (Backend):**
    In `kernel-server/src/main.rs` (Cortex loop), update the `RequestApproval` match arm to actually mutate the runtime state via `runtime_ref.update_status(..., RuntimeStatus::AwaitingApproval)`. This unlocks the "Human-in-the-Loop" feature you've already built in the UI.