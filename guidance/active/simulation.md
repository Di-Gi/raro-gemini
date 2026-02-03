This is a functionality gap in the **Simulation Mode**. Currently, the `ControlDeck.svelte` UI contains placeholder buttons that simply push text to the log, but they are detached from the powerful `MockWebSocket` logic residing in `mock-api.ts`.

To bring this to SOTA standards, we need to convert the **Simulation Pane** into a proper debugger that can:
1.  **Initialize** a specific test scenario.
2.  **Manually Step** through the `MockWebSocket` event loop (pausing after every log/state update).
3.  **Reset** the topology.

Here is the implementation guide to patch the `web-console`.

---

### Phase 1: Upgrade the Mock Engine (`mock-api.ts`)

We need to make the `MockWebSocket` controllable externally. Currently, it runs an automatic loop (`runLoop`). We will add a **Manual Mode** and expose a `step()` method.

**File:** `apps/web-console/src/lib/mock-api.ts`

**Changes:**
1.  Export the `activeSocket` so other modules can control it.
2.  Add `manualMode` to the class.
3.  Modify `runLoop` to halt if in manual mode.
4.  Add a `step()` method.

```typescript
// ... imports remain the same

// 1. Export the active instance for external control
export let activeSocket: MockWebSocket | null = null;

export class MockWebSocket {
    // ... existing properties
    
    // 2. Add control flags
    public manualMode: boolean = false;
    private stepResolve: (() => void) | null = null;

    constructor(url: string, manualMode: boolean = false) {
        this.url = url;
        this.manualMode = manualMode; // Store mode
        activeSocket = this;
        
        // ... (rest of constructor logic, topology setup)

        this.planSimulation();
        
        setTimeout(() => {
            if (this.onopen) this.onopen();
            this.runLoop();
        }, 500);
    }

    // 3. New Control Methods
    public async nextStep() {
        if (this.stepResolve) {
            console.log('[MOCK WS] Stepping...');
            const resolve = this.stepResolve;
            this.stepResolve = null;
            resolve(); // Release the lock in runLoop
        } else if (!this.isPaused && !this.manualMode) {
            console.warn('[MOCK WS] Simulation is running automatically. Pause it first.');
        } else {
            console.log('[MOCK WS] No pending steps or simulation finished.');
        }
    }

    // ... existing close() and emitLog()

    // 4. Refactored RunLoop with Manual Gate
    private async runLoop() {
        if (this.currentStep >= this.steps.length) {
            this.emitLog('KERNEL', 'INFO', 'Simulation Sequence Complete.', 'END');
            // Don't close immediately in manual mode so user can inspect
            return;
        }

        const step = this.steps[this.currentStep];

        // === MANUAL MODE GATE ===
        if (this.manualMode) {
            // Wait for nextStep() to be called
            await new Promise<void>((resolve) => {
                this.stepResolve = resolve;
            });
        }

        // 1. EXECUTE ACTION
        if (step.action) step.action();

        // 2. CONSTRUCT DYNAMIC STATE
        // ... (existing state construction logic) ...
        const dynamicState = {
            status: step.state.status || 'RUNNING',
            active_agents: [...this.activeAgents],
            completed_agents: [...this.completedAgents],
            failed_agents: [],
            total_tokens_used: this.totalTokens,
            invocations: JSON.parse(JSON.stringify(this.invocations))
        };

        const message = {
            type: 'state_update',
            state: dynamicState,
            signatures: { ...this.signatures },
            topology: JSON.parse(JSON.stringify(this.topology))
        };

        if (this.onmessage) {
            this.onmessage({ data: JSON.stringify(message) });
        }

        // 3. INTERVENTION CHECK (Keep existing logic)
        if (dynamicState.status === 'AWAITING_APPROVAL') {
            console.log('[MOCK WS] Paused for approval');
            this.isPaused = true;
            this.currentStep++;
            return; 
        }

        // 4. SCHEDULE NEXT (Auto mode uses timer, Manual mode loops immediately to wait gate)
        this.currentStep++;
        
        if (this.manualMode) {
            this.runLoop(); // Recursive call, will hit await Promise
        } else {
            this.timer = setTimeout(() => {
                this.runLoop();
            }, step.delay);
        }
    }
}
```

---

### Phase 2: Bridge the Store (`stores.ts`)

We need new actions in `stores.ts` to initialize the simulation in "Manual Mode" and trigger steps.

**File:** `apps/web-console/src/lib/stores.ts`

**Changes:**
1.  Update `connectRuntimeWebSocket` to accept a `manual` flag.
2.  Add `initSimulation` and `stepSimulation` functions.

```typescript
// Import activeSocket from mock-api
import { MockWebSocket, activeSocket } from './mock-api'; 

// ... existing code ...

// Update function signature
export function connectRuntimeWebSocket(runId: string, manualMode: boolean = false) {
  if (ws) ws.close();

  const url = getWebSocketURL(runId);
  
  if (USE_MOCK) {
    addLog('SYSTEM', `Initializing MOCK environment (Manual: ${manualMode})...`, 'DEBUG');
    // Pass manual flag to constructor
    ws = new MockWebSocket(url, manualMode); 
  } else {
    ws = new WebSocket(url);
  }

  // ... rest of the function remains the same ...
}

// === NEW SIMULATION ACTIONS ===

export function initSimulation() {
    const simId = `sim-${Date.now()}`;
    // 1. Reset UI State
    logs.set([]);
    runtimeStore.set({ status: 'IDLE', runId: simId });
    
    // 2. Connect in Manual Mode
    connectRuntimeWebSocket(simId, true);
    addLog('SIMULATOR', 'Debug Session Initialized. Waiting for step...', 'READY');
}

export function stepSimulation() {
    if (activeSocket) {
        activeSocket.nextStep();
    } else {
        addLog('SIMULATOR', 'No active simulation. Click "INIT SESSION" first.', 'WARN');
    }
}

export function resetSimulation() {
    if (ws) {
        ws.close();
        ws = null;
    }
    logs.set([]);
    runtimeStore.set({ status: 'IDLE', runId: null });
    addLog('SIMULATOR', 'Context Cleared.', 'RESET');
}
```

---

### Phase 3: Wire the Control Deck (`ControlDeck.svelte`)

Now we update the UI to use these new capabilities.

**File:** `apps/web-console/src/components/ControlDeck.svelte`

**Changes:**
Replace the `pane-sim` block with:

```svelte
<script>
    // ... import new functions
    import { initSimulation, stepSimulation, resetSimulation } from '$lib/stores';
    
    // ... existing code
</script>

<!-- ... inside the template ... -->

{:else if activePane === 'sim'}
  <div id="pane-sim" class="deck-pane">
    <div class="sim-controls">
      <!-- Group 1: Session Management -->
      <div class="btn-group">
          <button class="input-std action-btn" onclick={initSimulation}>
              ⚡ INIT SESSION
          </button>
          <button class="input-std action-btn warn" onclick={resetSimulation}>
              ↺ RESET
          </button>
      </div>

      <!-- Group 2: Execution Control -->
      <div class="btn-group">
          <button class="input-std action-btn primary" onclick={stepSimulation}>
              ▶ STEP NEXT
          </button>
      </div>
    </div>

    <!-- Live Telemetry Display -->
    <div class="sim-terminal">
      <div class="term-line">&gt; SYSTEM_STATUS: {$runtimeStore.status}</div>
      <div class="term-line">&gt; ACTIVE_AGENTS: {$agentNodes.filter(n => n.status === 'running').length}</div>
      <div class="term-line">&gt; GRAPH_NODES: {$agentNodes.length}</div>
      {#if $runtimeStore.runId}
        <div class="term-line highlight">&gt; SESSION_ID: {$runtimeStore.runId}</div>
      {/if}
      <div class="term-cursor">_</div>
    </div>
  </div>
```

**Add Styles for Sim Pane:**

```css
<style>
/* Add to existing styles */

.sim-controls {
    display: flex;
    flex-direction: column;
    gap: 12px;
    margin-bottom: 16px;
}

.btn-group {
    display: flex;
    gap: 10px;
}

.action-btn.warn {
    border-color: #d32f2f;
    color: #d32f2f;
    background: transparent;
}
.action-btn.warn:hover {
    background: #d32f2f;
    color: var(--paper-bg);
}

.action-btn.primary {
    border-color: var(--arctic-cyan);
    color: var(--arctic-cyan);
    background: rgba(0, 240, 255, 0.05);
    font-weight: 900;
}
.action-btn.primary:hover {
    background: var(--arctic-cyan);
    color: #000;
}

.term-line {
    margin-bottom: 4px;
}
.term-line.highlight {
    color: var(--arctic-cyan);
}
.term-cursor {
    animation: blink 1s infinite;
}
</style>
```

### Verification Flow

1.  Open **Web Console**.
2.  Click **Simulation** tab in the Control Deck.
3.  Click **⚡ INIT SESSION**.
    *   *Result:* Console log shows `SIMULATOR: Debug Session Initialized...`. The `MockWebSocket` connects but waits.
4.  Click **▶ STEP NEXT**.
    *   *Result:* The first event (RUNNING state) triggers.
5.  Click **▶ STEP NEXT** again.
    *   *Result:* Log appears: `Analyzing workflow requirements...`.
6.  Repeat clicking **Step**.
    *   *Result:* You watch the `PipelineStage` animate node-by-node and see the logs populate in `OutputPane` sequentially.
7.  Click **↺ RESET**.
    *   *Result:* Logs clear, status returns to IDLE.