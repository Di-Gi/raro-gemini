Based on your current architecture and requirements, here is the implementation plan to enable full **User-Approval Support** (Flow C).

This involves updates to the **Kernel (Rust)** to handle the "Pause/Resume" lifecycle and the **Web Console (Svelte)** to render the interactive "Approval Request" card directly in the log stream.

---

### Phase 1: Kernel-Server Updates (Rust)

We need to modify the runtime loop to gracefully exit when paused and allow the API to restart it.

#### 1. Update `apps/kernel-server/src/runtime.rs`

We need a method to explicitly pause the run and an update to the execution loop to respect that pause.

```rust
// In impl RARORuntime

// 1. Add a method to trigger the pause
pub async fn request_approval(&self, run_id: &str, agent_id: Option<&str>, reason: &str) {
    if let Some(mut state) = self.runtime_states.get_mut(run_id) {
        state.status = RuntimeStatus::AwaitingApproval;
        
        // Log the intervention event
        self.emit_event(RuntimeEvent::new(
            run_id,
            EventType::SystemIntervention,
            agent_id.map(|s| s.to_string()),
            serde_json::json!({
                "action": "pause",
                "reason": reason
            }),
        ));
    }
    self.persist_state(run_id).await;
    tracing::info!("Run {} PAUSED for approval: {}", run_id, reason);
}

// 2. Modify execute_dynamic_dag to handle pausing
async fn execute_dynamic_dag(&self, run_id: String) {
    tracing::info!("Starting DYNAMIC DAG execution for run_id: {}", run_id);

    loop {
        // --- CHECK PAUSE STATE ---
        // If status changed to AwaitingApproval (via pattern match or API), break the loop.
        // The state is persisted, so resume_run can respawn this loop later.
        if let Some(state) = self.runtime_states.get(&run_id) {
            if state.status == RuntimeStatus::AwaitingApproval {
                tracing::info!("Execution loop for {} suspending (Awaiting Approval).", run_id);
                break;
            }
            if state.status == RuntimeStatus::Failed || state.status == RuntimeStatus::Completed {
                break;
            }
        } else {
            break; // Run vanished
        }
        
        // ... (Rest of existing logic: determine next agent, etc.) ...
    }
}
```

#### 2. Update `apps/kernel-server/src/server/handlers.rs`

Update `resume_run` to actually restart the execution loop, not just flip the flag.

```rust
pub async fn resume_run(
    State(runtime): State<Arc<RARORuntime>>, 
    Path(run_id): Path<String>
) -> StatusCode {
    // 1. Verify currently paused
    let is_paused = runtime.get_state(&run_id)
        .map(|s| s.status == RuntimeStatus::AwaitingApproval)
        .unwrap_or(false);

    if !is_paused { return StatusCode::BAD_REQUEST; }

    // 2. Flip to Running
    runtime.set_run_status(&run_id, RuntimeStatus::Running);

    // 3. RESPAWN THE EXECUTION LOOP
    // This is the critical missing piece. We fire the engine again.
    let rt_clone = runtime.clone();
    let rid_clone = run_id.clone();
    tokio::spawn(async move {
        rt_clone.execute_dynamic_dag(rid_clone).await;
    });

    // 4. Emit event for UI to update logs
    runtime.emit_event(crate::events::RuntimeEvent::new(
        &run_id,
        crate::events::EventType::SystemIntervention,
        None,
        serde_json::json!({ "action": "resume", "reason": "User approved execution" })
    ));

    StatusCode::OK
}
```

#### 3. Update `apps/kernel-server/src/main.rs`

Wire the Pattern Engine (Cortex) to actually call the new pause method.

```rust
// Inside main() -> tokio::spawn(Cortex Pattern Engine)

match pattern.action {
    crate::registry::PatternAction::Interrupt { reason } => {
        // ... existing logic ...
    }
    crate::registry::PatternAction::RequestApproval { reason } => {
        tracing::warn!("✋ Safety Pattern Triggered: Approval Required - {}", reason);
        
        // CALL THE NEW PAUSE METHOD
        if let Some(agent) = &event.agent_id {
            runtime_ref.request_approval(&event.run_id, Some(agent), &reason).await;
        } else {
            runtime_ref.request_approval(&event.run_id, None, &reason).await;
        }
    }
    // ...
}
```

---

### Phase 2: Web Console Updates (Svelte)

We need a specific UI component for the "Approval Card" and logic to inject it into the log stream.

#### 1. New Component: `src/components/sub/ApprovalCard.svelte` [DONE]

This component renders inside the output pane when a pause happens.

```svelte
<!-- [[RARO]]/apps/web-console/src/components/sub/ApprovalCard.svelte -->
<script lang="ts">
  import { resumeRun, stopRun, runtimeStore } from '$lib/stores';
  import { fade } from 'svelte/transition';

  let { reason, runId }: { reason: string, runId: string } = $props();
  
  let processing = $state(false);
  let decision = $state<'APPROVED' | 'DENIED' | null>(null);

  async function handleApprove() {
      processing = true;
      await resumeRun(runId);
      decision = 'APPROVED';
      processing = false;
  }

  async function handleDeny() {
      processing = true;
      await stopRun(runId);
      decision = 'DENIED';
      processing = false;
  }
</script>

<div class="approval-card {decision ? decision.toLowerCase() : 'pending'}" transition:fade>
  <div class="header">
      <span class="icon">⚠</span>
      <span>INTERVENTION REQUIRED</span>
  </div>

  <div class="content">
      <div class="reason-label">REASONING:</div>
      <div class="reason-text">"{reason}"</div>
  </div>

  <div class="actions">
      {#if decision === 'APPROVED'}
          <div class="stamp success">AUTHORIZED</div>
      {:else if decision === 'DENIED'}
          <div class="stamp fail">TERMINATED</div>
      {:else}
          <button class="btn deny" onclick={handleDeny} disabled={processing}>
              STOP RUN
          </button>
          <button class="btn approve" onclick={handleApprove} disabled={processing}>
              {#if processing}PROCESSING...{:else}AUTHORIZE RESUME{/if}
          </button>
      {/if}
  </div>
</div>

<style>
  .approval-card {
      margin: 16px 0;
      border: 1px solid var(--alert-amber);
      background: color-mix(in srgb, var(--paper-bg), var(--alert-amber) 5%);
      font-family: var(--font-code);
      border-radius: 2px;
      overflow: hidden;
  }

  .approval-card.approved { border-color: var(--signal-success); opacity: 0.7; }
  .approval-card.denied { border-color: #d32f2f; opacity: 0.7; }

  .header {
      background: var(--alert-amber);
      color: #000;
      padding: 6px 12px;
      font-weight: 700;
      font-size: 10px;
      letter-spacing: 1px;
      display: flex; gap: 8px; align-items: center;
  }
  
  .approved .header { background: var(--signal-success); color: white; }
  .denied .header { background: #d32f2f; color: white; }

  .content { padding: 16px; border-bottom: 1px solid var(--paper-line); }
  .reason-label { font-size: 8px; color: var(--paper-line); font-weight: 700; margin-bottom: 4px; }
  .reason-text { font-size: 13px; color: var(--paper-ink); font-style: italic; }

  .actions { padding: 12px; display: flex; justify-content: flex-end; gap: 12px; height: 50px; align-items: center; }

  .btn {
      border: none; padding: 8px 16px; font-family: var(--font-code);
      font-size: 10px; font-weight: 700; cursor: pointer; border-radius: 2px;
  }

  .btn.deny { background: transparent; border: 1px solid var(--paper-line); color: var(--paper-ink); }
  .btn.deny:hover { background: #d32f2f; color: white; border-color: #d32f2f; }

  .btn.approve { background: var(--paper-ink); color: var(--paper-bg); }
  .btn.approve:hover { opacity: 0.9; }

  .stamp { font-weight: 900; letter-spacing: 2px; font-size: 12px; }
  .stamp.success { color: var(--signal-success); }
  .stamp.fail { color: #d32f2f; }
</style>
```

#### 2. Update `apps/web-console/src/lib/stores.ts`

Handle the `AWAITING_APPROVAL` status from WebSocket by injecting a special log entry.

```typescript
// In connectRuntimeWebSocket -> ws.onmessage

if (data.type === 'state_update' && data.state) {
    const currentState = get(runtimeStore);
    const newStateStr = data.state.status.toUpperCase();

    // TRIGGER: Status changed to AWAITING_APPROVAL
    if (newStateStr === 'AWAITING_APPROVAL' && currentState.status !== 'AWAITING_APPROVAL') {
        
        // Find the reason. Ideally, Kernel sends this in the state or a separate event.
        // For MVP, we'll check the last failed agent or a generic message.
        // Better yet: Listen for the 'SystemIntervention' event type below.
    }
    
    // ... update runtimeStore ...
} 

// DETECT SYSTEM INTERVENTION EVENT (Requires Kernel to send this event over WS)
// Assuming you update handlers.rs to forward "events" over WS or we infer from state.
// Simplest Phase 3 approach: If state is awaiting approval, assume it's the pattern engine.

if (data.state && data.state.status === 'AwaitingApproval') {
    // Check if we already logged this approval request to avoid duplicates
    const logsList = get(logs);
    const hasPending = logsList.some(l => l.metadata === 'INTERVENTION');
    
    if (!hasPending) {
         addLog(
            'CORTEX', 
            'SAFETY_PATTERN_TRIGGERED', 
            'INTERVENTION', // Metadata tag
            false,
            'approval-req-' + Date.now() // Custom ID
        );
    }
}
```

*Refined Store Logic*: Ideally, we update `LogEntry` to support a `type` field. For now, we will use the `metadata` field to trigger the rendering switch in `OutputPane`.

#### 3. Update `apps/web-console/src/components/OutputPane.svelte`

Wire up the new component based on log metadata.

```svelte
<script>
  import ApprovalCard from './sub/ApprovalCard.svelte';
  import { runtimeStore } from '$lib/stores';
</script>

<!-- Inside the {#each $logs} loop -->

<div class="log-content">
  {#if log.metadata === 'INTERVENTION'}
     <!-- RENDER APPROVAL CARD -->
     <ApprovalCard 
        reason={log.message === 'SAFETY_PATTERN_TRIGGERED' ? "System Policy Violation or Manual Pause Triggered" : log.message} 
        runId={$runtimeStore.runId || ''} 
     />
  {:else if log.isAnimated}
    <Typewriter ... />
  {:else}
    <SmartText ... />
  {/if}
</div>
```

### Summary of Workflow

1.  **Trigger:** A pattern in Kernel `main.rs` matches (e.g., "AgentFailed").
2.  **Action:** It calls `runtime.request_approval(...)`.
3.  **Kernel State:** Run status sets to `AwaitingApproval`. The `execute_dynamic_dag` loop sees this and exits (pauses).
4.  **Frontend Sync:** The WebSocket sends the new state.
5.  **UI Render:** `stores.ts` detects the state change and pushes a log with `metadata: 'INTERVENTION'`.
6.  **User Action:** `OutputPane.svelte` renders `<ApprovalCard />`. User clicks "Authorize Resume".
7.  **Resume:** `ApprovalCard` calls `resumeRun` -> Kernel API.
8.  **Kernel Restart:** `resume_run` handler sets status to `Running` and spawns a new `execute_dynamic_dag` task.
9.  **Continuity:** The loop re-reads the persisted DAG/State and picks up exactly where it left off.